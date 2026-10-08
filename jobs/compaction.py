#!/usr/bin/env python3
"""
Minnas v8.0 · Memory reclamation job (GC / compaction)
==============================================================

Problem (observed 2026-09-25): nowhere in the repo is there a `DELETE FROM memories`
  or a `VACUUM` → soft-delete (is_deleted=TRUE) is the end of the line, tombstones
  accumulate forever, and the table only ever grows.
  Filesystem analogy: there's an unlink (the directory entry disappears) but no GC
  (the space is never actually reclaimed).

This job fills in the tombstone → purged step, while making it **impossible to
delete the wrong thing**:

Safety design (five gates)
  Gate 1 window     : only processes rows where COALESCE(forgotten_at, updated_at, created_at)
                       is older than N days
  Gate 2 protection  : tome_cards.retention='permanent' or metadata->>'pinned'='true' → never reclaimed
  Gate 3 references  : referenced by beliefs.evidence_memories[], or referenced by a live
                        child memory's parent_memory_id → reclamation refused
  Gate 4 archive     : before deletion, snapshot the full row + traces into memories_archive
                        (restorable as a whole batch)
  Gate 5 voucher     : before deletion, export a CSV rollback voucher; its path is recorded
                        in gc_log

Defaults to **dry-run** (--dry-run). Actual deletion requires explicit --apply.

Usage:
  python3 jobs/compaction.py                          # dry run, 30-day window
  python3 jobs/compaction.py --window-days 90         # dry run, 90-day window
  python3 jobs/compaction.py --apply                  # real delete (only runs once flagged)
  python3 jobs/compaction.py --apply --vacuum         # real delete + VACUUM(ANALYZE)
  python3 jobs/compaction.py --restore BATCH-xxxx     # restore a whole batch from the archive

Exit codes: 0 ok / 2 bad args or failed safety check / 3 database error
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone

# ⚠️ Fix for a defect found in production (2026-09-26, hit on a real 549-row batch):
#   the csv module defaults to `field_size_limit = 131072` bytes, while the longest
#   `content` field seen in production reaches 270448 characters
#   ⇒ while **counting the rows just written** to the voucher, it raised
#     `Error: field larger than field limit (131072)`,
#     and the whole `--apply` batch exited with 3 having deleted nothing (the
#     transaction rolled back, but the CSV had already been written to disk = a
#     lying voucher).
#   Lesson: all fixtures used short text ⇒ 267 green local tests never caught this path.
#   Regression test: tests/test_v8_compaction.py::test_c10_voucher_survives_oversized_field
csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))

try:
    import asyncpg
except ImportError:  # pragma: no cover
    print("asyncpg is required (run inside the project venv)", file=sys.stderr)
    sys.exit(2)

DEFAULT_WINDOW_DAYS = 30
DEFAULT_REFUSE_TIER = ("L4",)


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


# ─────────────────────────────────────────────────────────────────────────────
# Candidate selection + the five gates
# ─────────────────────────────────────────────────────────────────────────────
CANDIDATES_SQL = """
WITH cand AS (
    SELECT m.id,
           COALESCE(m.forgotten_at, m.updated_at, m.created_at) AS tomb_at
    FROM memories m
    WHERE m.is_deleted = TRUE
      AND COALESCE(m.forgotten_at, m.updated_at, m.created_at)
          < NOW() - make_interval(days => $1)
    ORDER BY tomb_at ASC
    LIMIT $2
)
SELECT c.id,
       c.tomb_at,
       -- Gate 2: protection flags
       EXISTS (SELECT 1 FROM tome_cards tc
               WHERE tc.memory_id = c.id AND tc.retention = 'permanent') AS is_permanent,
       COALESCE(m.metadata->>'pinned', 'false') = 'true'               AS is_pinned,
       -- Gate 3: referential integrity
       (SELECT count(*) FROM beliefs b WHERE c.id = ANY(b.evidence_memories)) AS belief_refs,
       (SELECT count(*) FROM memories ch
        WHERE ch.parent_memory_id = c.id AND ch.is_deleted = FALSE)            AS child_refs,
       (SELECT count(*) FROM memory_traces t WHERE t.memory_id = c.id)         AS trace_rows
FROM cand c JOIN memories m ON m.id = c.id
"""


async def select_batch(conn, window_days: int, limit: int):
    rows = await conn.fetch(CANDIDATES_SQL, window_days, limit)
    purge, refused = [], []
    for r in rows:
        if r["is_permanent"] or r["is_pinned"] or r["belief_refs"] > 0 or r["child_refs"] > 0:
            refused.append(r)
        else:
            purge.append(r)
    return purge, refused


# ─────────────────────────────────────────────────────────────────────────────
# Archive / restore
# ─────────────────────────────────────────────────────────────────────────────
async def table_columns(conn, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position", table)
    return [r["column_name"] for r in rows]


async def archive_columns(conn) -> list[str]:
    return await table_columns(conn, "memories")


async def do_archive(conn, ids: list[int], batch: str) -> int:
    """Snapshot the full row + **four child tables** into memories_archive. Returns the
    number of archived rows.

    ⚠️ v8.0.1 correction (defect found in production): this used to only capture
    memories + traces, missing memory_entities / memory_keywords / tome_cards — all
    three are ON DELETE CASCADE, so they'd get silently deleted along with the
    parent, and --restore didn't rebuild them either, leaving "zombie memories"
    after restore (present in the DB but unreachable via BM25 search, with no
    tome_cards entry).
    """
    cols = await archive_columns(conn)
    collist = ", ".join(cols)
    sql = (
        f"INSERT INTO memories_archive ({collist}, _archived_at, _archive_batch, "
        f"                                 _traces, _entities, _keywords, _tome_cards) "
        f"SELECT m.*, NOW(), $2, "
        f"  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM memory_traces   t WHERE t.memory_id = m.id), '[]'::jsonb), "
        f"  COALESCE((SELECT jsonb_agg(to_jsonb(e)) FROM memory_entities e WHERE e.memory_id = m.id), '[]'::jsonb), "
        f"  COALESCE((SELECT jsonb_agg(to_jsonb(k)) FROM memory_keywords k WHERE k.memory_id = m.id), '[]'::jsonb), "
        f"  COALESCE((SELECT jsonb_agg(to_jsonb(c)) FROM tome_cards      c WHERE c.memory_id = m.id), '[]'::jsonb) "
        f"FROM memories m WHERE m.id = ANY($1::bigint[])"
    )
    status = await conn.execute(sql, ids, batch)
    return int(status.split()[-1]) if status else 0


async def write_csv_voucher(conn, ids: list[int], path: str) -> int:
    """Rollback voucher: a full snapshot of the rows before deletion, in CSV format
    (written via the csv module, so fields containing newlines are still safe)."""
    rows = await conn.fetch("SELECT * FROM memories WHERE id = ANY($1::bigint[])", ids)
    if not rows:
        return 0
    fields = list(rows[0].keys())
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, default=str, ensure_ascii=False)
                            if isinstance(v, (list, dict)) else v) for k, v in dict(r).items()})
            n += 1
    # Row count is tallied **while writing**: stays correct even when fields contain
    # newlines, and avoids reading the whole voucher back
    #   (reading it back would re-trigger the csv field-size limit — the production
    #   defect found on 2026-09-26, see the file-header comment).
    return n


# ─────────────────────────────────────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────────────────────────────────────
async def run(args) -> int:
    conn = await asyncpg.connect(args.dsn)
    csv_tmp = None
    try:
        if args.restore:
            return await do_restore(conn, args.restore)

        batch = args.batch or f"GC-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        purge, refused = await select_batch(conn, args.window_days, args.limit)

        summary = {
            "batch": batch,
            "dry_run": not args.apply,
            "window_days": args.window_days,
            "candidates": len(purge) + len(refused),
            "purgeable": len(purge),
            "refused_by_ref": len(refused),
            "traces_to_archive": sum(r["trace_rows"] for r in purge),
        }

        if not purge:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print("\nNo candidates → nothing to reclaim")
            await _log(conn, batch, not args.apply, summary, 0, csv_path=None)
            return 0

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\nWill be reclaimed (first 10):")
        for r in purge[:10]:
            print(f"  #{r['id']}  tomb={r['tomb_at']:%Y-%m-%d}  traces={r['trace_rows']}")
        if refused:
            print(f"\n**Refused** reclamation for {len(refused)} rows still referenced/protected (first 5):")
            for r in refused[:5]:
                why = []
                if r["is_permanent"]:
                    why.append("permanent")
                if r["is_pinned"]:
                    why.append("pinned")
                if r["belief_refs"]:
                    why.append(f"belief×{r['belief_refs']}")
                if r["child_refs"]:
                    why.append(f"livechild×{r['child_refs']}")
                print(f"  #{r['id']}  reason: {', '.join(why) or 'unknown'}")

        if not args.apply:
            print("\n[dry run] No changes made. Pass --apply to actually delete.")
            await _log(conn, batch, True, summary, 0, csv_path=None)
            return 0

        # ── Real delete (gate 4 archive → gate 5 voucher → delete), all in one transaction ──
        ids = [r["id"] for r in purge]
        csv_path = args.csv or os.path.join(
            os.path.expanduser("~"), ".hermes", "reports", "gc", f"{batch}.csv")
        # The voucher is first written as a "draft" (.part), and only renamed to final
        # **after the transaction commits** (see the comment at os.replace below).
        csv_tmp = csv_path + ".part"

        async with conn.transaction():
            archived = await do_archive(conn, ids, batch)
            if archived != len(ids):
                raise RuntimeError(f"archived row count {archived} != {len(ids)} pending delete → aborting (better not to delete)")
            vouchered = await write_csv_voucher(conn, ids, csv_tmp)
            if vouchered != len(ids):
                raise RuntimeError(f"voucher row count {vouchered} != {len(ids)} pending delete → aborting (better not to delete)")
            status = await conn.execute("DELETE FROM memories WHERE id = ANY($1::bigint[])", ids)
            deleted = int(status.split()[-1]) if status else 0

        # Voucher "finalization": **moving the side effect out of the transaction** —
        # the official voucher only appears on disk once the delete has actually
        # committed.
        # Why (production incident, 2026-09-26): the old code wrote the final voucher
        # directly inside the transaction ⇒ after a rollback, disk was left with a
        # lying voucher claiming rows were deleted when none were (batch GC-20260926
        # actually left an 8.4MB orphan). Regression tests:
        # tests/test_v8_compaction.py::test_c11 / test_c12
        os.replace(csv_tmp, csv_path)

        vacuum_msg = "skipped"
        if args.vacuum:
            # VACUUM can't run inside a transaction block; asyncpg defaults to
            # autocommit, so this runs outside the transaction.
            await conn.execute("VACUUM (ANALYZE) memories")
            await conn.execute("VACUUM (ANALYZE) memory_traces")
            vacuum_msg = "VACUUM (ANALYZE) memories + memory_traces completed"

        out = dict(summary, purged=deleted, archived=archived, rollback_csv=csv_path,
                   vacuum=vacuum_msg)
        print("\n" + json.dumps(out, ensure_ascii=False, indent=2))
        await _log(conn, batch, False, summary, deleted,
                   csv_path=csv_path, traces_kept=archived and summary["traces_to_archive"])
        return 0
    except Exception as e:  # noqa: BLE001
        # On failure, always clean up the draft voucher: never leave a file on disk
        # that could be mistaken for "already deleted".
        if csv_tmp and os.path.exists(csv_tmp):
            try:
                os.unlink(csv_tmp)
            except OSError:
                pass
        print(f"❌ Failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    finally:
        await conn.close()


async def do_restore(conn, batch: str) -> int:
    """Restore a whole batch from memories_archive (accidental-deletion rescue).

    Column names are always taken dynamically from information_schema — never
    hardcoded. Reason (lesson learned in production): `memory_traces`'s timestamp
    column is `executed_at`, not `created_at`; hardcoding column names would make
    the restore fail right at the critical moment (mid-rescue).
    """
    cols = await archive_columns(conn)
    collist = ", ".join(cols)
    # The four CASCADE child tables: (archive column, target table, primary key column in the snapshot)
    CHILDREN = [("_traces", "memory_traces", "id"),
                ("_entities", "memory_entities", None),
                ("_keywords", "memory_keywords", None),
                ("_tome_cards", "tome_cards", None)]

    async def _restore_child(c, col: str, table: str, pk: str | None) -> int:
        tcols = await table_columns(conn, table)
        tlist = ", ".join(tcols)
        conflict = f"ON CONFLICT ({pk}) DO NOTHING " if pk else ""
        return await conn.fetchval(
            f"WITH ins AS ("
            f"  INSERT INTO {table} ({tlist}) "
            f"  SELECT (jsonb_populate_record(NULL::{table}, j)).* "
            f"  FROM memories_archive a, jsonb_array_elements(a.{col}) j "
            f"  WHERE a._archive_batch=$1 {conflict} RETURNING 1) "
            f"SELECT count(*) FROM ins", batch)

    async with conn.transaction():
        n = await conn.fetchval(
            f"WITH ins AS ("
            f"  INSERT INTO memories ({collist}) "
            f"  SELECT {collist} FROM memories_archive WHERE _archive_batch=$1 "
            f"  ON CONFLICT (id) DO NOTHING RETURNING 1) SELECT count(*) FROM ins", batch)
        counts = {}
        for col, table, pk in CHILDREN:
            try:
                counts[table] = await _restore_child(conn, col, table, pk)
            except Exception as e:  # noqa: BLE001
                counts[table] = f"ERR:{type(e).__name__}"

        # ── Integrity assertion (added in v8.0.1): every child-table record present
        #    in the archive must exist after restore too ──
        #   This turns "false sense of safety" into something that's impossible to miss:
        #   missing a table during restore raises an error instead of silently
        #   producing a zombie memory.
        problems = []
        for col, table, _pk in CHILDREN:
            want = await conn.fetchval(
                f"SELECT COALESCE(SUM(jsonb_array_length(a.{col})),0) "
                f"FROM memories_archive a WHERE a._archive_batch=$1", batch)
            got = counts.get(table)
            if want != got:
                problems.append(f"{table}: {want} rows in archive → {got} rows restored (mismatch)")
        if problems:
            raise RuntimeError("Incomplete restore, rolled back: " + "; ".join(problems))

    print(json.dumps({"restored_memories": n, **counts, "batch": batch,
                      "integrity": "OK (all four child tables consistent)"},
                     ensure_ascii=False, indent=2))
    return 0


async def _log(conn, batch, dry_run, summary, purged, csv_path, traces_kept=None):
    try:
        await conn.execute(
            "INSERT INTO gc_log (batch, dry_run, candidates, purged, refused_ref, traces_kept, rollback_csv) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            batch, dry_run, summary["candidates"], purged, summary["refused_by_ref"],
            traces_kept or 0, csv_path)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ gc_log write failed (doesn't affect the reclamation result): {e}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description="Minnas v8.0 memory reclamation job (GC)")
    p.add_argument("--apply", action="store_true", help="actually perform the deletion (dry run by default)")
    p.add_argument("--dry-run", action="store_true",
                   help="explicitly declare a dry run (this is already the default; a self-documenting flag for gate scripts)")
    p.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                   help=f"how many days after soft-delete before reclamation is allowed (default {DEFAULT_WINDOW_DAYS})")
    p.add_argument("--limit", type=int, default=500, help="per-batch cap (default 500)")
    p.add_argument("--dsn", default=None, help="PG DSN (defaults to the PG* environment variables)")
    p.add_argument("--csv", default=None, help="rollback voucher CSV path")
    p.add_argument("--batch", default=None, help="batch ID (generated from the timestamp by default)")
    p.add_argument("--vacuum", action="store_true", help="run VACUUM (ANALYZE) after reclaiming")
    p.add_argument("--restore", default=None, metavar="BATCH", help="restore a whole batch from the archive")
    args = p.parse_args()
    args.dsn = args.dsn or dsn_from_env()
    if args.window_days < 0:
        print("--window-days cannot be negative", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
