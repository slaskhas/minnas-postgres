"""
test_sql_integration.py — real SQL integration tests (added in v7.8, 2026-08-18)

Pain point: the original 190 all-mock unit tests could never catch SQL syntax/logic errors
(like the drawer_pipeline dedup JSON-concatenation crash). This file connects to a real
PostgreSQL (the mnemosyne_itest database) and runs the production SQL.

Prerequisite: a local PG with the mnemosyne_itest database already set up
    psql -d mnemosyne_itest: CREATE EXTENSION vector + the memories/memory_keywords tables
Auto-skips when the database is unavailable.

Coverage:
1. Dedup merge SQL (the fixed jsonb_build_object syntax) end-to-end + merged_from append chain
2. The old concatenation syntax (pre-fix) must error — regression guard (:: precedence > ||)
3. The main search's real BM25 subquery (hit score + ANY parameter)
4. BM25 with no hits returns 0 (doesn't break vector ranking)
5. reflect's change filter (IS DISTINCT FROM doesn't update unchanged rows)
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager

import pytest

import asyncpg

ITEST_DSN = os.environ.get("MNEMOSYNE_ITEST_DSN", "postgresql:///mnemosyne_itest")


@asynccontextmanager
async def _conn():
    """A separate connection per test (used within the same event loop, to avoid cross-loop ownership errors)"""
    c = await asyncpg.connect(ITEST_DSN)
    try:
        yield c
    finally:
        await c.close()


def _run(coro):
    """Synchronous wrapper (the project has no pytest-asyncio; keeps the pure-function test style)"""
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def pool():
    """Connects to the local test database; skips the whole module if it can't connect.
    Note: only used for an availability check + truncating tables (done within the same loop);
    each test opens its own connection internally.
    """
    async def _init():
        conn = await asyncpg.connect(ITEST_DSN)
        try:
            await conn.execute("TRUNCATE memories RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    try:
        _run(_init())
    except Exception as e:  # pragma: no cover
        pytest.skip(f"local integration test database unavailable: {e}")
    yield True


def test_dedup_merge_sql_works(pool):
    """The fixed merge SQL: builds metadata with jsonb_build_object, appends merged_from, soft-deletes"""
    async def _run_test():
        async with _conn() as conn:
            old_id = await conn.fetchval(
                "INSERT INTO memories (content, category) VALUES ($1,'knowledge') RETURNING id",
                "检查下记忆宫殿是否正常运行的旧记忆")
            new_id = await conn.fetchval(
                "INSERT INTO memories (content, category) VALUES ($1,'knowledge') RETURNING id",
                "重新全面检查记忆宫殿使用情况的记忆")
            # Merge into the old memory (production SQL, post-fix)
            await conn.execute("""
                UPDATE memories SET
                  content = $1,
                  heat_score = $2,
                  access_count = $3,
                  parent_memory_id = COALESCE(parent_memory_id, $4),
                  metadata = COALESCE(metadata,'{}'::jsonb) || jsonb_build_object(
                    'merged_from', COALESCE(metadata->'merged_from','[]'::jsonb) || $5::jsonb),
                  updated_at = NOW()
                WHERE id = $6
            """, "合并后的长内容", 0.9, 2, new_id, "[%d]" % new_id, old_id)
            # Soft-delete the new memory
            await conn.execute("""
                UPDATE memories SET is_deleted = TRUE, forgotten_at = NOW(),
                  metadata = COALESCE(metadata,'{}'::jsonb) || jsonb_build_object('merged_into', $2::int)
                WHERE id = $1
            """, new_id, old_id)
            old = await conn.fetchrow("SELECT metadata, is_deleted FROM memories WHERE id=$1", old_id)
            new = await conn.fetchrow("SELECT metadata, is_deleted FROM memories WHERE id=$1", new_id)
            # asyncpg returns jsonb as a str (a known gotcha: needs json.loads)
            old_md = json.loads(old["metadata"])
            new_md = json.loads(new["metadata"])
            assert old["is_deleted"] is False
            assert new["is_deleted"] is True
            assert new_md["merged_into"] == old_id
            assert old_md["merged_from"] == [new_id]
            # Second merge: merged_from should append rather than overwrite (preserve the merge chain)
            third = await conn.fetchval(
                "INSERT INTO memories (content, category) VALUES ('第三条相似记忆','knowledge') RETURNING id")
            await conn.execute("""
                UPDATE memories SET
                  content = $1, heat_score = 0.9, access_count = 3,
                  parent_memory_id = COALESCE(parent_memory_id, $2),
                  metadata = COALESCE(metadata,'{}'::jsonb) || jsonb_build_object(
                    'merged_from', COALESCE(metadata->'merged_from','[]'::jsonb) || $3::jsonb),
                  updated_at = NOW()
                WHERE id = $4
            """, "合并后的长内容", third, "[%d]" % third, old_id)
            old2 = await conn.fetchrow("SELECT metadata->'merged_from' AS mf FROM memories WHERE id=$1", old_id)
            mf = json.loads(old2["mf"]) if isinstance(old2["mf"], str) else old2["mf"]
            assert mf == [new_id, third], f"merged_from should append to preserve the merge chain, got {mf}"
    _run(_run_test())


def test_dedup_old_syntax_raises(pool):
    """The pre-fix concatenation syntax `'...' || x || ']}'::jsonb` must error — regression guard (:: precedence > ||)"""
    async def _run_test():
        async with _conn() as conn:
            mid = await conn.fetchval(
                "INSERT INTO memories (content) VALUES ('回归测试记忆') RETURNING id")
            with pytest.raises(Exception) as exc:
                await conn.execute(
                    "UPDATE memories SET metadata = COALESCE(metadata,'{}'::jsonb) || "
                    "'{\"merged_from\":[' || $1::text || ']}'::jsonb WHERE id = $2",
                    "[123]", mid)
            assert "json" in str(exc.value).lower(), f"should raise a JSON syntax error, got: {exc.value}"
    _run(_run_test())


def test_bm25_keyword_score(pool):
    """Main search's real BM25 subquery: memory_keywords SUM(freq) hit score"""
    async def _run_test():
        async with _conn() as conn:
            mid = await conn.fetchval(
                "INSERT INTO memories (content, category) VALUES ('代理架构 xray 配置优化','knowledge') RETURNING id")
            await conn.executemany(
                "INSERT INTO memory_keywords (memory_id, token, freq) VALUES ($1,$2,$3)",
                [(mid, "代理", 2.0), (mid, "架构", 1.0), (mid, "xray", 1.0)])
            # The main search's bm25_sql verbatim (production SQL subquery + ANY tokens)
            score = await conn.fetchval(
                "SELECT (SELECT LEAST(1.0, COALESCE(SUM(k.freq),0)/4.0) FROM memory_keywords k "
                "WHERE k.memory_id = m.id AND k.token = ANY($2::text[])) "
                "FROM memories m WHERE m.id = $1", mid, ["代理", "架构", "xray"])
            assert score == 1.0, f"3 tokens hit, sum=4 → LEAST(1.0, 4/4)=1.0, got {score}"
            score2 = await conn.fetchval(
                "SELECT (SELECT LEAST(1.0, COALESCE(SUM(k.freq),0)/4.0) FROM memory_keywords k "
                "WHERE k.memory_id = m.id AND k.token = ANY($2::text[])) "
                "FROM memories m WHERE m.id = $1", mid, ["代理"])
            assert score2 == 0.5, f"1 token hit, freq=2 → 2/4=0.5, got {score2}"
    _run(_run_test())


def test_bm25_no_match_zero(pool):
    """BM25 with no hits returns 0 (doesn't break pure vector ranking)"""
    async def _run_test():
        async with _conn() as conn:
            mid = await conn.fetchval(
                "INSERT INTO memories (content) VALUES ('完全无关的测试记忆') RETURNING id")
            score = await conn.fetchval(
                "SELECT (SELECT LEAST(1.0, COALESCE(SUM(k.freq),0)/4.0) FROM memory_keywords k "
                "WHERE k.memory_id = m.id AND k.token = ANY($2::text[])) "
                "FROM memories m WHERE m.id = $1", mid, ["不存在的词", "另一个"])
            assert score == 0.0
    _run(_run_test())


def test_change_filter_noop(pool):
    """reflect's change filter: IS DISTINCT FROM skips updates when the value is unchanged (avoids a full-table rewrite)"""
    async def _run_test():
        async with _conn() as conn:
            await conn.fetchval(
                "INSERT INTO memories (content, time_drawer) VALUES ('时间抽屉测试','recent') RETURNING id")
            r = await conn.execute("""
                UPDATE memories SET time_drawer = CASE
                    WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '30 days' THEN 'recent'
                    WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '90 days' THEN 'mid'
                    ELSE 'long'
                END
                WHERE user_id = 'default' AND is_deleted = FALSE
                  AND time_drawer IS DISTINCT FROM (
                    CASE
                      WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '30 days' THEN 'recent'
                      WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '90 days' THEN 'mid'
                      ELSE 'long'
                    END)
            """)
            assert "0" in r, f"unchanged value should not be updated, got: {r}"
    _run(_run_test())
