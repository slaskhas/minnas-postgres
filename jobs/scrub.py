#!/usr/bin/env python3
"""
Minnas v8.0 · Integrity scrub job
========================================

Filesystem analogy: Btrfs csum tree / ZFS checksum + scrub — a background job that
**actively** finds anomalies, rather than waiting for a retrieval to fail first
(read-time checking is passive; scrubbing is active).

This job is **read-only**: it only reports, never modifies data. Any fix requires a
human decision and a separate process. This matches gcat-std's red line that
"scrubbers are always read-only".

Checks (four classes of orphans/anomalies):
  O1 orphan inode       : a memories row exists with no tome_cards entry
  O2 orphan reference    : memory_entities / memory_keywords point at a memory that no longer exists
  O3 dangling reference  : an id in beliefs.evidence_memories no longer exists
  O4 overdue reclamation : soft-deleted past the retention window but still not reclaimed by compaction (is GC running?)
  O5 missing fingerprint : a non-soft-deleted memory has an empty dedup_fingerprint (rows written since v8.0 should have one)
  O6 live child, dead parent : parent_memory_id points at a memory that has been soft-deleted

Usage:
  python3 jobs/scrub.py                # full scrub
  python3 jobs/scrub.py --json         # machine-readable output
  python3 jobs/scrub.py --window-days 30
Exit codes: 0 no anomalies / 1 anomalies found (usable for cron alerting) / 3 database error
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

try:
    import asyncpg
except ImportError:  # pragma: no cover
    print("asyncpg is required (run inside the project venv)", file=sys.stderr)
    sys.exit(2)


def dsn_from_env() -> str:
    user = os.getenv("PGUSER", "postgres")
    pwd = os.getenv("PGPASSWORD", "")
    db = os.getenv("PGDATABASE", "mnemosyne")
    host = os.getenv("PGHOST", "127.0.0.1")
    port = os.getenv("PGPORT", "5432")
    schema = os.getenv("PGSCHEMA", "public")
    auth = f"{user}:{pwd}@" if pwd else f"{user}@"
    dsn = f"postgresql://{auth}{host}:{port}/{db}"
    if schema != "public":
        # All SQL in this file uses bare table names with no schema prefix, resolved
        # via search_path — it must be set explicitly on the DSN, otherwise it falls
        # back to the role's default search_path (which usually includes public),
        # reading/writing the wrong tables when sharing a database/public schema with
        # another application.
        # Keeping public at the end is for pgvector's vector type resolution (the
        # extension lives in public) — table name resolution still prefers a
        # same-named table in the target schema first, so it won't land on public by
        # mistake.
        dsn += f"?options=-csearch_path%3D{schema}%2Cpublic"
    return dsn


CHECKS = [
    ("O1_orphan_tome_card", "memory exists with no tome_cards entry (orphan inode)",
     "SELECT count(*) FROM memories m "
     "LEFT JOIN tome_cards c ON c.memory_id = m.id "
     "WHERE c.memory_id IS NULL AND m.is_deleted = FALSE"),
    ("O2_orphan_entity_ref", "entity association points at a nonexistent memory",
     "SELECT count(*) FROM memory_entities e "
     "LEFT JOIN memories m ON m.id = e.memory_id WHERE m.id IS NULL"),
    ("O2_orphan_keyword_ref", "keyword index points at a nonexistent memory",
     "SELECT count(*) FROM memory_keywords k "
     "LEFT JOIN memories m ON m.id = k.memory_id WHERE m.id IS NULL"),
    ("O3_dangling_belief_ref", "belief evidence points at a nonexistent memory",
     "SELECT COALESCE(SUM(n),0) FROM ("
     "  SELECT (SELECT count(*) FROM unnest(b.evidence_memories) e "
     "          LEFT JOIN memories m ON m.id = e WHERE m.id IS NULL) AS n"
     "  FROM beliefs b WHERE b.evidence_memories IS NOT NULL) t"),
    ("O5_missing_fingerprint", "non-soft-deleted memory missing its idempotency fingerprint (should be set for rows written since v8.0)",
     "SELECT count(*) FROM memories WHERE is_deleted = FALSE "
     "AND dedup_fingerprint IS NULL AND created_at > TIMESTAMPTZ '2026-09-25T06:25:00+08'"),
    ("O6_live_child_dead_parent", "live child memory hangs off a soft-deleted parent",
     "SELECT count(*) FROM memories ch JOIN memories pa ON pa.id = ch.parent_memory_id "
     "WHERE ch.is_deleted = FALSE AND pa.is_deleted = TRUE"),
]


async def run(args) -> int:
    conn = await asyncpg.connect(args.dsn)
    try:
        results = {}
        for key, desc, sql in CHECKS:
            try:
                results[key] = {"desc": desc, "count": int(await conn.fetchval(sql))}
            except Exception as e:  # noqa: BLE001
                results[key] = {"desc": desc, "count": -1, "error": f"{type(e).__name__}: {e}"}

        # O4 needs a parameter
        o4 = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE is_deleted = TRUE "
            "AND COALESCE(forgotten_at, updated_at, created_at) < NOW() - make_interval(days => $1)",
            args.window_days)
        results["O4_overdue_tombstone"] = {
            "desc": f"soft-deleted over {args.window_days} days ago and still not reclaimed (is GC running?)", "count": int(o4)}

        # Baseline scale
        scale = await conn.fetchrow(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE is_deleted) AS tombstone, "
            "pg_total_relation_size('memories') AS bytes FROM memories")

        problems = {k: v for k, v in results.items() if v["count"] > 0}
        out = {
            "checked_at": str(await conn.fetchval("SELECT NOW()")),
            "window_days": args.window_days,
            "scale": {"total": scale["total"], "tombstone": scale["tombstone"],
                      "table_bytes": scale["bytes"],
                      "tombstone_ratio": round(scale["tombstone"] / max(scale["total"], 1), 4)},
            "checks": results,
            "problems": sorted(problems.keys()),
            "verdict": "OK" if not problems else "ATTENTION",
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"🔍 Mnemosyne integrity scrub · {out['checked_at']}")
            print(f"   Scale: {scale['total']} rows (soft-deleted {scale['tombstone']} = "
                  f"{out['scale']['tombstone_ratio']:.1%}) · table {scale['bytes']/1048576:.1f} MB")
            print(f"   Window: {args.window_days} days\n")
            for k, v in results.items():
                flag = "⚠️" if v["count"] > 0 else "✅"
                extra = f"  [{v['error']}]" if "error" in v else ""
                print(f"   {flag} {k:26s} {v['count']:>6}  {v['desc']}{extra}")
            print(f"\n   Verdict: {out['verdict']}")
        return 0 if not problems else 1
    except Exception as e:  # noqa: BLE001
        print(f"❌ Failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    finally:
        await conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Minnas v8.0 integrity scrub (read-only)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--window-days", type=int, default=30, help="overdue-reclamation check window (default 30 days)")
    p.add_argument("--dsn", default=None)
    args = p.parse_args()
    args.dsn = args.dsn or dsn_from_env()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
