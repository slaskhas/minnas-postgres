#!/usr/bin/env python3
"""
scripts/prod_smoke.py — production smoke test (P4-stage gate command)
===========================================================

Why this exists
------------
2026-09-25: the P4 verification I typed by hand in the terminal was, the first time,
**wrong in its own test design** (the test content I used for L1 differed from the L0
one by only 2 characters, so detect_conflict judged it a near-duplicate, producing a
false failure of "L1 first write should be stored"). Hand-typed tests are neither
reproducible nor enforceable as a gate.
So this is codified as a script: **re-verifiable with one command after every
deployment, with a non-zero exit code on failure**.

Coverage (layered-write contract)
  S1 L0 same-source retry     → duplicate with the same id (idempotent)
  S2 L0 cross-source          → **each stored separately** (L0 only appends, never
                                  edits, and allows contradictions; the old version
                                  would wrongly collapse these)
  S3 L1 first write           → stored
  S4 L1 duplicate             → duplicate with the same id
  S5 stored layer tag correct (L0/L0/L1)
  S6 test-artifact cleanup (soft delete)

⚠️ Test content must be **mutually dissimilar**: if S1/S2 differ from S3 by only a few
   characters, semantic merging will swallow them into a false failure
   (this isn't a bug — it's detect_conflict's normal behavior — but it would make the
   test lie to you).

Usage: python3 scripts/prod_smoke.py [BASE_URL]     defaults to http://127.0.0.1:18010
Exit codes: 0 all passed / 1 some failed / 2 environment unreachable
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18010"


def req(method: str, path: str, body=None, timeout: int = 45):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    s, v = req("GET", "/", timeout=15)
    if s != 200 or not isinstance(v, dict):
        print(f"❌ Service unreachable: {s} {v}")
        return 2
    print(f"  Service self-reported: {json.dumps(v, ensure_ascii=False)}")

    ts = time.strftime("%H%M%S")
    # The two content strings are **deliberately dissimilar** to avoid semantic merging interfering with the checks
    c_l0 = (f"smoke-alpha-{ts}: 记录一条临时便签，用于验证日志层的跨来源保留行为与幂等重试语义")
    c_l1 = (f"smoke-beta-{ts}: 关于向量索引维护窗口的技术结论，属知识类，须版本化只认最新")

    _, a1 = req("POST", "/api/v1/memories", {"user_id": "default", "content": c_l0,
                                            "category": "temp", "source": "smoke-A"})
    _, a2 = req("POST", "/api/v1/memories", {"user_id": "default", "content": c_l0,
                                            "category": "temp", "source": "smoke-A"})
    _, a3 = req("POST", "/api/v1/memories", {"user_id": "default", "content": c_l0,
                                            "category": "temp", "source": "smoke-B"})
    _, b1 = req("POST", "/api/v1/memories", {"user_id": "default", "content": c_l1,
                                            "category": "knowledge"})
    _, b2 = req("POST", "/api/v1/memories", {"user_id": "default", "content": c_l1,
                                            "category": "knowledge"})

    for tag, r in (("L0·same-source #1", a1), ("L0·same-source #2", a2), ("L0·cross-source #1", a3),
                   ("L1·knowledge #1", b1), ("L1·knowledge #2", b2)):
        print(f"    {tag:<14}: {json.dumps(r, ensure_ascii=False)}")

    def ok_id(x, y, status):
        if not (isinstance(x, dict) and isinstance(y, dict)):
            return False
        return x.get("status") == status and x.get("id") is not None and x.get("id") == y.get("id")

    checks = [
        ("S1 L0 same-source retry → duplicate, same id", ok_id(a2, a1, "duplicate")),
        ("S2 L0 cross-source → each stored separately (L0 allows contradictions)",
         isinstance(a3, dict) and a3.get("status") == "stored" and a3.get("id") != a1.get("id")),
        ("S3 L1 first write → stored", isinstance(b1, dict) and b1.get("status") == "stored"),
        ("S4 L1 duplicate → duplicate, same id", ok_id(b2, b1, "duplicate")),
    ]

    ids = ([i for i in {x.get("id") for x in (a1, a3, b1)
                        if isinstance(x, dict) and isinstance(x.get("id"), int)}]
           if all(c for _, c in checks[:4]) else [])
    ids.sort()
    if ids:
        q = ("SELECT id||'|'||COALESCE(metadata->>'layer','NULL') FROM memories WHERE id IN ("
             + ",".join(map(str, ids)) + ") ORDER BY id")
        p = subprocess.run(["ssh", "gz", f'sudo -u postgres psql -d mnemosyne -tAc "{q}"'],
                           capture_output=True, text=True, timeout=60)
        rows = [l.strip() for l in p.stdout.splitlines()
                if l.strip() and not any(k in l for k in ("perl", "LANG", "LC_", "supported", "Falling"))]
        layers = [r.split("|")[-1] for r in rows]
        print(f"    stored layer: {rows}")
        checks.append(("S5 stored layer tag = L0/L0/L1", len(layers) == 3 and
                       layers.count("L0") == 2 and layers.count("L1") == 1))

    # Cleanup (always runs regardless of outcome)
    cleaned = 0
    for mid in ids:
        st, _ = req("DELETE", f"/api/v1/memories/{mid}?user_id=default")
        cleaned += 1 if st == 200 else 0
    checks.append((f"S6 test-artifact cleanup ({cleaned}/{len(ids)})", ids and cleaned == len(ids)))

    print()
    bad = [n for n, c in checks if not c]
    for n, c in checks:
        print(f"  {'✅' if c else '❌'} {n}")
    if bad:
        print(f"\n  ══ Production smoke test: FAILED ({len(bad)} item(s)) ══")
        return 1
    print(f"\n  ══ Production smoke test: all passed ({len(checks)}/{len(checks)}) ══")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
