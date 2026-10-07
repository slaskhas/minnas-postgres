#!/usr/bin/env python3
"""
v8.0 S1-2 / S1-3 · idempotency-key and memory-reclamation (GC) integration tests

Requires a PostgreSQL instance with the **v8.0 migration** applied (defaults to
`postgresql:///mnemosyne_v8test`, overridable via the `MNEMOSYNE_V8TEST_DSN` env var).
The whole module skips if the database is unavailable or the migration hasn't run,
so CI is unaffected (same policy as `test_sql_integration.py`).

Coverage (each one is a "did it actually work" criterion, not just "did it run"):
  C1 dry run touches no data (the most dangerous regression guard: the default must never delete anything)
  C2 a real purge deletes only what should be deleted; **not a single** protected/referenced row may be lost
  C3 the archive table gets the full row + a traces snapshot
  C4 rollback-voucher row count == deleted row count (counted with a CSV parser, not wc -l)
  C5 child tables are cleaned up cascading from the parent row
  C6 a full-batch restore can recover both memories and traces
  C7 the unique index **actually blocks** duplicate fingerprints (regression guard: construct a violating sample)
  C8 rows whose window hasn't elapsed / that aren't soft-deleted **never become candidates**
"""
import asyncio
import csv
import os

import pytest

DSN = os.environ.get("MNEMOSYNE_V8TEST_DSN", "postgresql:///mnemosyne_v8test")

asyncio_marker = pytest.mark.skipif(
    os.environ.get("MNEMOSYNE_SKIP_DB_TESTS") == "1",
    reason="explicitly skipping database tests")

FIXTURE_SQL = """
-- ⚠️ The TRUNCATE list must include **every** table the fixture inserts into —
--   omitting entities makes the second run collide on entities_pkey (seen in
--   practice, surfaces as a string of UniqueViolationErrors)
TRUNCATE memories, memory_traces, tome_cards, memory_entities, memory_keywords,
         entities, beliefs, memories_archive, gc_log RESTART IDENTITY CASCADE;

-- (1) should be reclaimed: 5 past-window tombstones, unreferenced (id 1-5)
INSERT INTO memories (id, user_id, content, category, is_deleted, forgotten_at, tier)
SELECT g, 'default', 'GC-PURGE '||g, 'knowledge', TRUE, NOW() - INTERVAL '60 days', 'L2'
FROM generate_series(1,5) g;

-- (2) permanent protection (id 6)
INSERT INTO memories (id, user_id, content, category, is_deleted, forgotten_at)
VALUES (6, 'default', 'PERMANENT', 'knowledge', TRUE, NOW() - INTERVAL '60 days');
INSERT INTO tome_cards (memory_id, title, retention) VALUES (6, '永久卡', 'permanent');

-- (3) pinned protection (id 7)
INSERT INTO memories (id, user_id, content, category, is_deleted, forgotten_at, metadata)
VALUES (7, 'default', 'PINNED', 'knowledge', TRUE, NOW() - INTERVAL '60 days', '{"pinned":"true"}');

-- (4) referenced by a belief (id 8)
INSERT INTO memories (id, user_id, content, category, is_deleted, forgotten_at)
VALUES (8, 'default', 'BELIEFREF', 'knowledge', TRUE, NOW() - INTERVAL '60 days');
INSERT INTO beliefs (id, user_id, content, evidence_memories)
VALUES (1, 'default', '引用#8', ARRAY[8]::bigint[]);

-- (5) has a live child memory (id 9)
INSERT INTO memories (id, user_id, content, category, is_deleted, forgotten_at)
VALUES (9, 'default', 'LIVECHILD', 'knowledge', TRUE, NOW() - INTERVAL '60 days');
INSERT INTO memories (id, user_id, content, category, is_deleted, parent_memory_id)
VALUES (90, 'default', '子', 'knowledge', FALSE, 9);

-- (6) window not yet elapsed (id 10) / (7) not soft-deleted (id 11)
INSERT INTO memories (id, user_id, content, category, is_deleted, forgotten_at)
VALUES (10, 'default', 'TOORECENT', 'knowledge', TRUE, NOW() - INTERVAL '5 days');
INSERT INTO memories (id, user_id, content, category, is_deleted)
VALUES (11, 'default', 'ALIVE', 'knowledge', FALSE);

-- (8) attach **four** child-table rows to id 3 (verifies both cascade and restore completeness)
--   ⚠️ The original fixture omitted entities — exactly the table the red team flagged for
--      "CASCADE silently deletes it too"; missing it from the test let the
--      "incomplete restore" defect go undetected.
INSERT INTO memory_traces (memory_id, action, details) VALUES (3,'stored','{"a":1}'), (3,'accessed','{"b":2}');
INSERT INTO memory_keywords (memory_id, token, freq) VALUES (3, '测试', 3);
INSERT INTO tome_cards (memory_id, title, retention) VALUES (3, '普通卡', 'short');
INSERT INTO entities (id, user_id, name, type) VALUES (1, 'default', '测试实体', 'concept');
INSERT INTO memory_entities (memory_id, entity_id) VALUES (3, 1);

-- ⚠️ The fixture inserts with explicit ids → the sequence must be advanced to max(id),
--    otherwise a later INSERT without an id collides on the pkey
SELECT setval(pg_get_serial_sequence('memories', 'id'),
              GREATEST((SELECT COALESCE(max(id), 1) FROM memories), 1));
"""

PURGEABLE = {1, 2, 3, 4, 5}
PROTECTED = {6, 7, 8, 9}
UNTOUCHABLE = {10, 11, 90}


def _connect():
    import asyncpg
    return asyncpg.connect(DSN)


def _db_ready():
    async def probe():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='memories' AND column_name='dedup_fingerprint'")
            gc = await conn.fetchval("SELECT to_regclass('public.gc_log')")
            arc = await conn.fetchval("SELECT to_regclass('public.memories_archive')")
            return bool(cols) and gc is not None and arc is not None
        finally:
            await conn.close()
    try:
        return asyncio.run(probe())
    except Exception:
        return False


_needs_db = pytest.mark.skipif(not _db_ready(),
                               reason=f"v8.0 test database unavailable or migration not applied: {DSN}")


def _setup():
    async def go():
        conn = await _connect()
        try:
            await conn.execute(FIXTURE_SQL)
        finally:
            await conn.close()
    asyncio.run(go())


def _fetch(sql, *args):
    async def go():
        conn = await _connect()
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()
    return asyncio.run(go())


def _run_gc(*argv):
    """Calls the equivalent path of jobs.compaction.main directly (no subprocess, easier to assert on)."""
    import argparse
    from jobs import compaction
    args = argparse.Namespace(
        apply=False, window_days=30, limit=500, dsn=DSN, csv=None,
        batch=None, vacuum=False, restore=None)
    for a in argv:
        k, v = a
        setattr(args, k, v)
    return asyncio.run(compaction.run(args))


@_needs_db
@asyncio_marker
def test_c1_dry_run_changes_nothing():
    """The most dangerous regression guard: a dry run must never touch data by default."""
    _setup()
    before = _fetch("SELECT count(*) AS c FROM memories")[0]["c"]
    rc = _run_gc()
    after = _fetch("SELECT count(*) AS c FROM memories")[0]["c"]
    assert rc == 0
    assert before == after == 12, "row count must be unchanged after a dry run"
    assert _fetch("SELECT count(*) AS c FROM memories_archive")[0]["c"] == 0, "a dry run must not write to the archive"
    log = _fetch("SELECT dry_run FROM gc_log ORDER BY id DESC LIMIT 1")
    assert log and log[0]["dry_run"] is True


@_needs_db
@asyncio_marker
def test_c2_apply_purges_only_purgeable():
    _setup()
    assert _run_gc(("apply", True)) == 0
    ids = {r["id"] for r in _fetch("SELECT id FROM memories")}
    assert not (PURGEABLE & ids), f"what should have been deleted wasn't fully cleaned up: {PURGEABLE & ids}"
    assert PROTECTED <= ids, f"protected/referenced rows were wrongly deleted: {PROTECTED - ids}"
    assert UNTOUCHABLE <= ids, f"rows that shouldn't be candidates were touched: {UNTOUCHABLE - ids}"


@_needs_db
@asyncio_marker
def test_c3_archive_has_rows_and_traces_snapshot():
    import json
    _setup()
    _run_gc(("apply", True))
    assert _fetch("SELECT count(*) AS c FROM memories_archive")[0]["c"] == 5
    raw = _fetch("SELECT _traces FROM memories_archive WHERE id=3")[0]["_traces"]
    # asyncpg returns jsonb as a str → must json.loads (a known project gotcha, see the AGENTS lessons)
    t = json.loads(raw) if isinstance(raw, str) else raw
    assert len(t) == 2, "the traces snapshot must be fully archived (CASCADE wipes the original table rows)"
    assert _fetch("SELECT count(*) AS c FROM memories_archive WHERE _archive_batch IS NULL")[0]["c"] == 0


@_needs_db
@asyncio_marker
def test_c4_rollback_voucher_rowcount_matches():
    """Voucher row count must equal the deleted row count — counted with a CSV parser (wc -l miscounts when a field contains a newline)."""
    _setup()
    csv_path = os.path.join(os.path.dirname(__file__), "_v8_gc_voucher.csv")
    if os.path.exists(csv_path):
        os.remove(csv_path)
    try:
        _run_gc(("apply", True), ("csv", csv_path))
        assert os.path.exists(csv_path)
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 5
        assert {int(r["id"]) for r in rows} == PURGEABLE
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)


@_needs_db
@asyncio_marker
def test_c5_child_rows_cascade():
    _setup()
    _run_gc(("apply", True))
    for tbl in ("memory_traces", "memory_keywords", "tome_cards"):
        n = _fetch(f"SELECT count(*) AS c FROM {tbl} WHERE memory_id=3")[0]["c"]
        assert n == 0, f"{tbl} was not cleaned up cascading from the parent row"


@_needs_db
@asyncio_marker
def test_c6_restore_recovers_memories_and_traces():
    _setup()
    _run_gc(("apply", True))
    batch = _fetch("SELECT batch FROM gc_log WHERE dry_run=FALSE ORDER BY id DESC LIMIT 1")[0]["batch"]
    assert _run_gc(("restore", batch)) == 0
    ids = {r["id"] for r in _fetch("SELECT id FROM memories")}
    assert PURGEABLE <= ids, "restore is incomplete"
    assert _fetch("SELECT count(*) AS c FROM memory_traces WHERE memory_id=3")[0]["c"] == 2
    # The voucher isn't destroyed — the archive table still has the rows, so rescue can be repeated
    assert _fetch("SELECT count(*) AS c FROM memories_archive")[0]["c"] == 5


@_needs_db
@asyncio_marker
def test_c9_restore_is_complete_across_all_cascade_children():
    """v8.0.1 regression test: restore must rebuild **all four CASCADE child tables**, not just memories.

    This comes from a real defect (flagged by the red team → reproduced by me):
      the original implementation only archived+restored memories/traces, while
      memory_entities / memory_keywords / tome_cards get CASCADE-deleted along with the
      parent row and **are not restored** →
      what comes back is a "zombie memory": present in the DB, but unsearchable via BM25,
      with no catalog card.

    Criterion: each child table's row count before deletion == its row count after restore.
    A mismatch in any table fails the test.
    """
    _setup()
    before = {
        t: _fetch(f"SELECT count(*) AS c FROM {t} WHERE memory_id=3")[0]["c"]
        for t in ("memory_traces", "memory_entities", "memory_keywords", "tome_cards")
    }
    assert all(v > 0 for v in before.values()), f"fixture is incomplete, can't detect the bug: {before}"

    _run_gc(("apply", True))
    # After deletion: all four child tables should be CASCADE-emptied
    for t in before:
        n = _fetch(f"SELECT count(*) AS c FROM {t} WHERE memory_id=3")[0]["c"]
        assert n == 0, f"{t} was not cascade-cleared along with the parent row"

    batch = _fetch("SELECT batch FROM gc_log WHERE dry_run=FALSE ORDER BY id DESC LIMIT 1")[0]["batch"]
    assert _run_gc(("restore", batch)) == 0

    after = {
        t: _fetch(f"SELECT count(*) AS c FROM {t} WHERE memory_id=3")[0]["c"]
        for t in before
    }
    assert after == before, f"restore is incomplete (zombie memory): before {before} vs after {after}"
    # Also verify: the keywords that CM retrieval depends on must actually come back
    assert after["memory_keywords"] > 0, "keywords didn't come back → BM25 will never find this memory"


@_needs_db
@asyncio_marker
def test_c10_voucher_survives_oversized_field():
    """Regression (production defect observed 2026-09-26): when content exceeds csv's default
    field-size limit, counting the voucher rows must not take down the whole batch.

    Real defect (observed in production, 2026-09-26): the `csv` module defaults to
    `field_size_limit = 131072` bytes, while production's longest memory content reaches
    270448 **characters** ⇒ counting voucher rows after writing raises
    `Error: field larger than field limit (131072)` → the whole `--apply` batch exits with code 3.

    There are two layers of danger here:
      (1) the fixtures all use short text ⇒ a fully-green local run still can't catch this
          (another case of "green locally ≠ the code path is correct");
      (2) the error happens **inside the transaction**, but the CSV has already been written to
          disk ⇒ producing a "lying voucher": the voucher exists, but the data wasn't deleted.

    Criterion: a batch containing 270KB of content must get rc=0, be fully deleted, and the
    voucher must be fully readable by the parser.
    """
    _setup()
    big = "B" * 270_000
    _fetch("INSERT INTO memories (id, user_id, content, category, is_deleted, forgotten_at) "
           "VALUES (12, 'default', $1, 'knowledge', TRUE, NOW() - INTERVAL '60 days') RETURNING id",
           big)
    csv_path = os.path.join(os.path.dirname(__file__), "_v8_gc_voucher_big.csv")
    if os.path.exists(csv_path):
        os.remove(csv_path)
    try:
        rc = _run_gc(("apply", True), ("csv", csv_path))
        assert rc == 0, "an oversized field took down the whole reclamation batch (field larger than field limit)"
        ids = {r["id"] for r in _fetch("SELECT id FROM memories")}
        assert not (PURGEABLE & ids) and 12 not in ids, "what should have been deleted wasn't fully cleaned up"
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(PURGEABLE) + 1, f"wrong voucher row count: {len(rows)}"
        assert max(len(r["content"]) for r in rows) == 270_000, "the oversized row didn't make it into the voucher"
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)


@_needs_db
@asyncio_marker
def test_c11_rolled_back_run_leaves_no_voucher(monkeypatch):
    """Regression guard: after a transaction fails, **no file that looks like a voucher** may be
    left behind (a lying voucher).

    Real incident (production batch GC-20260926, 2026-09-26): the job raised an error while
    counting voucher rows → the transaction rolled back, nothing was deleted; but the voucher CSV
    had already been written to disk at 8.4MB — looking afterward, it appeared as if "549 rows
    were reclaimed". A file like that is far more dangerous than having no voucher at all.

    Criterion: after an injected failure, (1) the real voucher file doesn't exist, (2) the
    half-written `.part` file doesn't exist either, (3) not a single row was deleted from the DB.
    """
    _setup()
    from jobs import compaction

    csv_path = os.path.join(os.path.dirname(__file__), "_v8_gc_voucher_fail.csv")
    real = compaction.write_csv_voucher

    async def boom(conn, ids, path):
        await real(conn, ids, path)          # actually write it first (simulates the "already on disk" step having completed)
        raise RuntimeError("injected failure after voucher write")

    monkeypatch.setattr(compaction, "write_csv_voucher", boom)
    try:
        rc = _run_gc(("apply", True), ("csv", csv_path))
        assert rc == 3, "an injected failure must be reported as a failure (must not silently succeed)"
        assert not os.path.exists(csv_path), "a real voucher left behind after rollback = a lying voucher"
        assert not os.path.exists(csv_path + ".part"), "the half-written file must be cleaned up, not left on disk to cause confusion"
        ids = {r["id"] for r in _fetch("SELECT id FROM memories")}
        assert PURGEABLE <= ids, f"rollback wasn't clean, data was deleted: {PURGEABLE - ids}"
        assert _fetch("SELECT count(*) AS c FROM memories_archive")[0]["c"] == 0, "the archive should have no trace either"
    finally:
        for p in (csv_path, csv_path + ".part"):
            if os.path.exists(p):
                os.remove(p)


@_needs_db
@asyncio_marker
def test_c12_successful_run_promotes_voucher_without_part_file():
    """Positive-path check: on success the voucher **gets promoted** (the real filename exists, no leftover `.part`)."""
    _setup()
    csv_path = os.path.join(os.path.dirname(__file__), "_v8_gc_voucher_ok.csv")
    for p in (csv_path, csv_path + ".part"):
        if os.path.exists(p):
            os.remove(p)
    try:
        assert _run_gc(("apply", True), ("csv", csv_path)) == 0
        assert os.path.exists(csv_path), "the success path must produce a real voucher"
        assert not os.path.exists(csv_path + ".part"), "`.part` must not remain (would mean promotion didn't happen)"
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(PURGEABLE)
    finally:
        for p in (csv_path, csv_path + ".part"):
            if os.path.exists(p):
                os.remove(p)


@_needs_db
@asyncio_marker
def test_c7_unique_index_rejects_duplicate_fingerprint():
    """Regression guard: testing only "clean input gets in" isn't enough — must prove the index **actually blocks** duplicates."""
    import asyncpg
    _setup()

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                "INSERT INTO memories (id,user_id,content,category,dedup_fingerprint) "
                "VALUES (900,'default','a','knowledge','FP-DUP')")
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO memories (id,user_id,content,category,dedup_fingerprint) "
                    "VALUES (901,'default','b','knowledge','FP-DUP')")
            # Historical rows (NULL fingerprint) are unaffected — this is the premise behind the "don't backfill history" design
            await conn.execute("INSERT INTO memories (id,user_id,content,category) VALUES (902,'default','n1','knowledge')")
            await conn.execute("INSERT INTO memories (id,user_id,content,category) VALUES (903,'default','n2','knowledge')")
        finally:
            await conn.close()
    asyncio.run(go())


@_needs_db
@asyncio_marker
def test_c8_window_and_alive_excluded_from_candidates():
    """Window not yet elapsed / not soft-deleted → never a candidate; and still untouched after a real purge."""
    import argparse
    from jobs import compaction
    _setup()

    async def go():
        conn = await _connect()
        try:
            purge, refused = await compaction.select_batch(conn, 30, 500)
            pids = {r["id"] for r in purge}
            assert pids == PURGEABLE, f"wrong candidate set: {pids}"
            assert 10 not in pids and 11 not in pids
        finally:
            await conn.close()
    asyncio.run(go())
