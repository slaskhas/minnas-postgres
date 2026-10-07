#!/usr/bin/env python3
"""
Mnemosyne performance-watermark alerting (perf_alert.py)
Checks every 30 minutes: memory/disk/connection count/slow queries; writes an
alert log entry on threshold breach
Thresholds: memory>85% / disk>85% / PG connections>80 / slow query>2s
Usage: venv/bin/python perf_alert.py
"""
import os, sys, subprocess, json
from datetime import datetime

ALERT_LOG = "/tmp/perf_alert.log"
THRESHOLDS = {"mem_pct": 85, "disk_pct": 85, "pg_conns": 80, "slow_ms": 2000}


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


def main():
    alerts = []
    # Memory
    try:
        with open("/proc/meminfo") as f:
            d = {}
            for line in f:
                k, v = line.split(":")
                d[k.strip()] = int(v.strip().split()[0])
        total = d["MemTotal"]
        avail = d["MemAvailable"]
        mem_pct = (total - avail) / total * 100
        if mem_pct > THRESHOLDS["mem_pct"]:
            alerts.append(f"[MEM] {mem_pct:.0f}% used")
    except Exception:
        pass
    # Disk
    du = sh("df / | tail -1")
    if du:
        pct = int(du.split()[4].rstrip("%"))
        if pct > THRESHOLDS["disk_pct"]:
            alerts.append(f"[DISK] {pct}% used")
    # PG connection count
    conns = sh("sudo -u postgres psql -t -c \"SELECT count(*) FROM pg_stat_activity;\" 2>/dev/null")
    if conns.strip().isdigit():
        n = int(conns.strip())
        if n > THRESHOLDS["pg_conns"]:
            alerts.append(f"[PG_CONN] {n} connections")
    # Slow queries (pg_stat_statements) — only look at ones executed in the
    # last 30 minutes, to avoid false positives from a stale historical mean
    #   (fixed 2026-08-18: mean_exec_time is a cumulative average, so
    #   historical slow calls from before the reflect optimization would
    #   permanently inflate the mean → a constant false "31 queries" alarm)
    slow = sh("sudo -u postgres psql -d mnemosyne -t -c \"SELECT count(*) FROM pg_stat_statements WHERE mean_exec_time > %d AND last_exec_time > NOW() - INTERVAL '30 minutes';\" 2>/dev/null" % THRESHOLDS["slow_ms"])
    if slow.strip().isdigit() and int(slow.strip()) > 0:
        alerts.append(f"[SLOW] {slow.strip()} queries > {THRESHOLDS['slow_ms']}ms (recent 30min)")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if alerts:
        msg = f"[{ts}] ⚠️ Watermark alert: {'; '.join(alerts)}"
        # Alert dedup: don't spam if content is unchanged from last time (only
        # recorded in the state file); re-report after a change/recovery
        STATE = "/tmp/perf_alert.state"
        core = msg.split("]", 1)[1] if "]" in msg else msg
        try:
            last_core = open(STATE).read().strip()
        except FileNotFoundError:
            last_core = ""
        if core != last_core:
            with open(ALERT_LOG, "a") as f:
                f.write(msg + "\n")
            with open(STATE, "w") as f:
                f.write(core)
            print(msg)
    else:
        # Healthy: clear the state so the next alert can fire again
        try:
            os.remove("/tmp/perf_alert.state")
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
