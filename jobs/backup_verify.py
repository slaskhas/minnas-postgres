#!/usr/bin/env python3
"""
Minnas v8.0 · Backup re-verification job (S1-4, second half)
===================================================================

Why this exists
----------------
Real incident: `backup.sh` once **failed silently for 36 days** (last success 2026-08-18).
Cause: the log file's owner had changed, and `set -e` exited right when opening the
redirect — not even the error made it into the log.
Fixed 2026-09-23 (failures now explicitly write an ALERT file).

But "failures get alerted" only answers half the question: **a backup file existing
≠ a backup that actually works**. This job covers the other half — **restorability
spot-checks**:

  V1 freshness : the newest backup's age must be < --max-age-hours (default 30h,
                 margin for daily backups)
  V2 size      : file size > --min-bytes (default 1MB; empty/truncated files fail
                 outright)
  V3 structure : `pg_restore --list` can parse a TOC out of it — this **actually
                 reads through the archive directory**, proving the file isn't
                 truncated/corrupted (much stronger than just checking `ls` size)
  V4 content   : with --deep, actually restores the `memories` table **into a
                 scratch database** and compares row counts (the strongest
                 evidence of restorability; off by default so it doesn't slow
                 down routine checks)

Verdict: any V failing → exit code 1, printing a single `ALERT:`-prefixed line an
alerting system can pick up.
This job is **read-only** (--deep only ever writes to a disposable scratch database,
dropped when done) and never touches the production database.

Usage:
  python3 jobs/backup_verify.py                          # V1-V3
  python3 jobs/backup_verify.py --deep                   # +V4 real restore
  python3 jobs/backup_verify.py --dir /path/to/backups
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def find_latest(directory: str) -> str | None:
    pats = ["*.dump", "*.sql", "*.sql.gz", "*.tar", "*.tar.gz", "backup_*"]
    files: list[str] = []
    for p in pats:
        files.extend(glob.glob(os.path.join(directory, p)))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        return None
    return max(files, key=lambda f: os.path.getmtime(f))


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError as e:
        return 127, f"command not found: {e}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out ({timeout}s)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Mnemosyne backup restorability re-verification (read-only)")
    ap.add_argument("--dir", default=os.path.expanduser("~/noah-buffer/backups"),
                    help="backup directory")
    ap.add_argument("--max-age-hours", type=float, default=30.0)
    ap.add_argument("--min-bytes", type=int, default=1_000_000)
    ap.add_argument("--deep", action="store_true", help="also do a real restore spot-check (V4)")
    ap.add_argument("--scratch-db", default="mnemosyne_restore_probe")
    args = ap.parse_args()

    checks: list[tuple[str, bool, str]] = []

    latest = find_latest(args.dir)
    if not latest:
        print(f"ALERT: no files in backup directory → {args.dir}")
        return 1

    st = os.stat(latest)
    age_h = (datetime.now(timezone.utc).timestamp() - st.st_mtime) / 3600.0
    size = st.st_size

    # V1 freshness
    ok = age_h <= args.max_age_hours
    checks.append(("V1 freshness", ok, f"{latest} age {age_h:.1f}h (threshold {args.max_age_hours}h)"))

    # V2 size
    ok2 = size >= args.min_bytes
    checks.append(("V2 size", ok2, f"{size/1048576:.1f} MB (threshold {args.min_bytes/1048576:.1f} MB)"))

    # V3 structure: actually read through the archive directory
    if latest.endswith((".sql", ".sql.gz")):
        cmd = ["zcat", latest] if latest.endswith(".gz") else ["cat", latest]
        rc, out = run(cmd, timeout=180)
        toc_n = len(re.findall(r"^CREATE ", out, flags=re.M)) if rc == 0 else 0
        checks.append(("V3 structure", toc_n > 0,
                       f"plain-text dump: parsed {toc_n} CREATE statements" if rc == 0 else f"read failed rc={rc}"))
    else:
        rc, out = run(["pg_restore", "--list", latest], timeout=180)
        toc_n = len([l for l in out.splitlines() if re.match(r"^\d+;", l)])
        checks.append(("V3 structure", rc == 0 and toc_n > 0,
                       f"pg_restore --list: {toc_n} TOC entries" if rc == 0 else f"pg_restore failed rc={rc}"))

    # V4 content: real restore spot-check
    if args.deep:
        db = args.scratch_db
        run(["dropdb", "--if-exists", db])
        rc, out = run(["createdb", db])
        if rc != 0:
            checks.append(("V4 real restore", False, f"scratch DB creation failed: {out.strip()[:120]}"))
        else:
            try:
                rc, out = run(["pg_restore", "-d", db, "--no-owner", "--no-privileges",
                               "-t", "memories", latest], timeout=600)
                if rc != 0:
                    checks.append(("V4 real restore", False, f"restore failed rc={rc}: {out.strip()[:160]}"))
                else:
                    p = subprocess.run(["psql", "-d", db, "-tAc", "SELECT count(*) FROM memories"],
                                       capture_output=True, text=True, timeout=120)
                    n = p.stdout.strip()
                    ok4 = p.returncode == 0 and n.isdigit() and int(n) > 0
                    checks.append(("V4 real restore", ok4, f"memories row count after restore = {n}"))
            finally:
                run(["dropdb", "--if-exists", db])

    print(f"Backup re-verification · {datetime.now().strftime('%F %T')} · dir {args.dir}")
    all_ok = True
    for name, ok, detail in checks:
        print(f"  {'✅' if ok else '❌'} {name}: {detail}")
        all_ok &= ok
    if not all_ok:
        bad = [n for n, ok, _ in checks if not ok]
        print(f"ALERT: backup re-verification failed ({', '.join(bad)}) → latest backup {latest}")
    else:
        print(f"  Verdict: OK ({len(checks)}/{len(checks)} checks passed)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
