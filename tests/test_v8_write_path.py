#!/usr/bin/env python3
"""
v8.0 · write-path SQL contract test (**pulls the real SQL out of main.py's source and runs it**)

Why this test exists (a real incident — worth remembering)
------------------------------------------------------------
After v8.0 was deployed to production, **the very first write got a 500**:
    asyncpg.exceptions.InvalidColumnReferenceError:
    there is no unique or exclusion constraint matching the ON CONFLICT specification

Root cause: `dedup_fingerprint_key` is a **partial unique index**
(`WHERE dedup_fingerprint IS NOT NULL`), while the main write path used
`ON CONFLICT (dedup_fingerprint)` — **missing the predicate**.
PostgreSQL's partial-index inference requires the predicate to match explicitly,
otherwise it errors outright.

Why the unit tests didn't catch it: the existing tests only verified "the index blocks
duplicate fingerprints" (via a bare INSERT) — **they never ran the actual
INSERT ... ON CONFLICT statement from the production code**.
→ Lesson: **testing the index ≠ testing the SQL that uses the index**.
Whenever "SQL assembled in code" is coupled to "an object created in the database",
the **real SQL** must be pulled out and run.

What this test does: uses a regex to extract that INSERT statement (predicate
included) from `main.py`, binds values to the placeholders, and runs it against a real
test database — if the source and the index ever stop matching, this fails immediately.
"""
import asyncio
import os
import re

import pytest

DSN = os.environ.get("MNEMOSYNE_V8TEST_DSN", "postgresql:///mnemosyne_v8test")
MAIN_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


def _extract_insert_sql() -> str:
    """Extracts the real INSERT ... ON CONFLICT statement from main.py (joining adjacent string literals)."""
    src = open(MAIN_PY, encoding="utf-8").read()
    m = re.search(r"('INSERT INTO memories \(.*?RETURNING id',)", src, re.S)
    assert m, "couldn't extract the INSERT statement from main.py — its structure changed, please update this test"
    literals = re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))
    sql = "".join(literals)
    return sql


def _db_ready() -> bool:
    async def probe():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            return bool(await conn.fetchval(
                "SELECT to_regclass('public.gc_log') IS NOT NULL"))
        finally:
            await conn.close()
    try:
        return asyncio.run(probe())
    except Exception:
        return False


_needs_db = pytest.mark.skipif(not _db_ready(), reason=f"v8.0 test database unavailable: {DSN}")


def _vec() -> str:
    return "[" + ",".join(["0.01"] * 1536) + "]"


async def _sync_seq(conn) -> None:
    """Advances the memories.id sequence to max(id).

    Fixtures/historical cases insert with an **explicit id** but don't advance the
    sequence — a later INSERT without an id would then collide on `memories_pkey`
    (seen in practice).
    """
    await conn.execute(
        "SELECT setval(pg_get_serial_sequence('memories','id'), "
        "GREATEST((SELECT COALESCE(max(id),1) FROM memories), 1))")


def test_w1_extracted_sql_is_the_real_one():
    """Guard rail: the extraction logic must be self-verifying — what it extracts must be an INSERT + ON CONFLICT."""
    sql = _extract_insert_sql()
    assert "INSERT INTO memories" in sql
    assert "ON CONFLICT" in sql
    assert "dedup_fingerprint" in sql
    # v8.1.1: source_doc is a real column in the write; the legacy str project_id_old
    # was dropped (migrations/v8.1.1_drop_project_id_old.sql). Lock the transition.
    assert "source_doc" in sql, "v8.1.1 write INSERT dropped the source_doc column"
    assert "project_id_old" not in sql, "legacy project_id_old still in the write INSERT"


def test_w2_on_conflict_predicate_matches_partial_index():
    """Core assertion: a partial unique index → ON CONFLICT must carry the matching predicate.

    This is the direct regression test for the production incident. If someone removes
    the predicate, this fails first.
    """
    sql = _extract_insert_sql()
    assert re.search(r"ON CONFLICT\s*\(\s*dedup_fingerprint\s*\)\s+WHERE\s+dedup_fingerprint IS NOT NULL",
                     sql), f"ON CONFLICT is missing the partial-index predicate, production writes would 500:\n{sql}"


@_needs_db
def test_w3_real_sql_runs_and_is_idempotent():
    """Runs the real SQL against the database: the first insert returns an id, a second insert
    with the same fingerprint returns nothing (idempotent). Since v8.1.1 the write also
    carries `source_doc` (the 14th column / 12th bind arg) — verify it round-trips."""
    import asyncpg
    sql = _extract_insert_sql()
    doc = "doc://W3-source-doc"

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            await _sync_seq(conn)
            await conn.execute(
                "DELETE FROM memories WHERE user_id='default' AND content LIKE 'W3-%'")
            # positional bind args ($1..$12): user_id, project_id, content, category,
            # embedding, metadata, session_id, heat_score, storage, retrieval, fingerprint, source_doc
            args = ("default", None, "W3-probe", "knowledge", _vec(), "{}",
                    None, 0.5, 3, 3, "FP-W3-CONTRACT", doc)
            r1 = await conn.fetchrow(sql, *args)
            assert r1 is not None and r1["id"] is not None, "the first insert must return an id"
            # source_doc must actually have persisted through the real write SQL
            row = await conn.fetchrow(
                "SELECT source_doc FROM memories WHERE id=$1", r1["id"])
            assert row["source_doc"] == doc, (
                f"source_doc not persisted by the real write SQL; got {row['source_doc']!r}")
            r2 = await conn.fetchrow(sql, *args)
            assert r2 is None, "a second insert with the same fingerprint must be blocked by ON CONFLICT (returns nothing)"
            n = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE dedup_fingerprint='FP-W3-CONTRACT'")
            assert n == 1
        finally:
            await conn.execute(
                "DELETE FROM memories WHERE dedup_fingerprint='FP-W3-CONTRACT'")
            await conn.close()
    asyncio.run(go())


@_needs_db
def test_w5_source_doc_default_stores_null():
    """Writing without a `source_doc` must store NULL (the column is optional), not raise
    on a missing bind arg."""
    import asyncpg
    sql = _extract_insert_sql()

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            await _sync_seq(conn)
            await conn.execute("DELETE FROM memories WHERE content LIKE 'W5-%'")
            # last bind arg = None → source_doc stored as NULL
            args = ("default", None, "W5-probe", "knowledge", _vec(), "{}",
                    None, 0.5, 3, 3, "FP-W5-NULLDOC", None)
            r1 = await conn.fetchrow(sql, *args)
            assert r1 is not None and r1["id"] is not None, "insert with source_doc=None must succeed"
            row = await conn.fetchrow("SELECT source_doc FROM memories WHERE id=$1", r1["id"])
            assert row["source_doc"] is None, f"source_doc should be NULL when omitted, got {row['source_doc']!r}"
        finally:
            await conn.execute("DELETE FROM memories WHERE content LIKE 'W5-%'")
            await conn.close()
    asyncio.run(go())


@_needs_db
def test_w4_partial_index_leaves_null_fingerprints_alone():
    """The partial index only constrains rows that have a fingerprint — historical NULL rows can coexist in multiple rows (the premise of the "don't backfill" design)."""
    import asyncpg

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            await _sync_seq(conn)
            await conn.execute("DELETE FROM memories WHERE content LIKE 'W4-%'")
            await conn.executemany(
                "INSERT INTO memories (user_id, content, category) VALUES ('default', $1, 'knowledge')",
                [("W4-a",), ("W4-b",)])
            n = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE content LIKE 'W4-%' AND dedup_fingerprint IS NULL")
            assert n == 2
        finally:
            await conn.execute("DELETE FROM memories WHERE content LIKE 'W4-%'")
            await conn.close()
    asyncio.run(go())
