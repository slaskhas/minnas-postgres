#!/usr/bin/env python3
"""
v8.1.1 · `source_doc` field tests

Covers the new `source_doc` column (URL or file path naming the document a memory's
content was derived from) that landed in v8.1.1 (PR #5 `addSrc`, migration
`migrations/v8.1.1_add_source_doc.sql`).

The real SQL round-trip against a database is the canonical write-path test and lives
in `test_v8_write_path.py` (the INSERT now carries a 14th column / `$12` — a test that
still bound 11 args would TypeError on a real DB). This file adds the no-DB guards that
can run everywhere:

  * the request model (`MemoryCreate`) accepts the field, defaulting to `None`;
  * the single-memory `GET /api/v1/memories/{id}` SELECT actually selects `source_doc`
    (a removed column would silently drop it from the response);
  * the real `create_memory` handler passes `source_doc` through as the last positional
    INSERT arg — both the explicit-value and the default-`None` cases.

The handler tests mock only the DB layer (as `tests/conftest.py`'s `mock_pool` does)
and run the *real* `create_memory` logic (embedding + layering + fingerprint), so they
catch a handler that forgets to forward the new parameter.
"""
import asyncio
import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # noqa: F401 — kept for future async/fixture additions

import main
from main import MemoryCreate

MAIN_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


# ── request model contract ─────────────────────────────────────────────────────

def test_memory_create_accepts_source_doc_default_none():
    """`source_doc` must be an optional field, defaulting to `None` (not required)."""
    mem = MemoryCreate(user_id="u", content="hello")
    assert mem.source_doc is None, (
        "MemoryCreate.source_doc should default to None; the pull added it as "
        "Optional[str] = None"
    )


def test_memory_create_carries_source_doc_value():
    """An explicit `source_doc` (URL or path) must be stored on the model unchanged."""
    for value in ("https://docs.example.com/page", "/mnt/c/repo/notes.md"):
        mem = MemoryCreate(user_id="u", content="hello", source_doc=value)
        assert mem.source_doc == value, f"source_doc not carried through: {mem.source_doc!r}"


# ── GET /api/v1/memories/{id} response contract ─────────────────────────────────

def test_get_memory_query_selects_source_doc():
    """The single-memory GET must SELECT `source_doc`; otherwise the response silently
    omits it and consumers can't read it back (v7.8.2 lesson: wrong response field →
    no error, just a silently dropped value)."""
    src = open(MAIN_PY, encoding="utf-8").read()
    i = src.index("async def get_memory(memory_id")
    j = src.index("return dict(row)", i)
    block = src[i:j]
    m = re.search(r"SELECT .*?FROM memories WHERE id=\$1", block, re.S)
    assert m, "couldn't find the GET /memories/{id} SELECT from main.py (structure changed)"
    sel = m.group(0)
    assert "source_doc" in sel, (
        "GET /memories/{id} no longer selects source_doc; the response would drop it: "
        f"{sel!r}"
    )
    assert "is_deleted" in sel and "metadata" in sel  # sanity: still the full-row SELECT


# ── create_memory handler behavior (DB mocked, real write logic runs) ───────────

def _vec():
    return ["0.01"] * 1536


@patch.object(main, "get_embedding", new=AsyncMock(return_value=[_vec()]))
@patch.object(main, "detect_conflict", new=AsyncMock(return_value={"action": "fresh"}))
@patch.object(main, "_tokenize_on_write", new=AsyncMock())
@patch.object(main, "sync_entities", new=AsyncMock())
@patch.object(main, "pool")
def test_write_handler_persists_source_doc_value(mock_pool):
    """`create_memory` must forward a supplied `source_doc` as the last INSERT bind arg
    (the 12th placeholder, `memories.source_doc`)."""
    mock_conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=mock_conn)
    tx.__aexit__ = AsyncMock(return_value=False)
    # The handler does `async with conn.transaction():` (no `await` on the call),
    # so `transaction` must be a plain Mock that *returns* an async context manager.
    mock_conn.transaction = MagicMock(return_value=tx)
    # first fetchrow = dup-check SELECT → None; second = INSERT ... RETURNING
    mock_conn.fetchrow = AsyncMock(side_effect=[None, {"id": 42}])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire.return_value = cm

    doc = "https://docs.example.com/p"
    mem = MemoryCreate(user_id="u", content="x memory", source_doc=doc)
    result = asyncio.run(main.create_memory(mem))
    assert result == {"status": "stored", "id": 42, "category": "knowledge"}, result

    fetch_calls = mock_conn.fetchrow.call_args_list
    assert len(fetch_calls) >= 2, (
        f"expected dup-check + INSERT, got {len(fetch_calls)} fetchrow calls"
    )
    insert_args = fetch_calls[1].args
    assert "INSERT INTO memories" in insert_args[0], "no INSERT INTO memories observed"
    assert "source_doc" in insert_args[0], (
        "the write INSERT no longer lists source_doc as a column"
    )
    bind = list(insert_args[1:])
    assert len(bind) == 12, f"expected 12 bind args, got {len(bind)}: {bind!r}"
    assert bind[-1] == doc, f"source_doc not forwarded as the last arg: {bind!r}"
    assert bind[0] == "u" and bind[2] == "x memory", (
        f"user_id/content positions shifted: {bind!r}"
    )


@patch.object(main, "get_embedding", new=AsyncMock(return_value=[_vec()]))
@patch.object(main, "detect_conflict", new=AsyncMock(return_value={"action": "fresh"}))
@patch.object(main, "_tokenize_on_write", new=AsyncMock())
@patch.object(main, "sync_entities", new=AsyncMock())
@patch.object(main, "pool")
def test_write_handler_source_doc_defaults_to_none(mock_pool):
    """With no `source_doc` supplied (the default), the last INSERT arg must be `None`
    (stored as NULL), not an empty string or a missing parameter."""
    mock_conn = AsyncMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=mock_conn)
    tx.__aexit__ = AsyncMock(return_value=False)
    # The handler does `async with conn.transaction():` (no `await` on the call),
    # so `transaction` must be a plain Mock that *returns* an async context manager.
    mock_conn.transaction = MagicMock(return_value=tx)
    mock_conn.fetchrow = AsyncMock(side_effect=[None, {"id": 42}])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire.return_value = cm

    asyncio.run(main.create_memory(MemoryCreate(user_id="u", content="y memory")))

    insert_args = mock_conn.fetchrow.call_args_list[1].args
    assert "INSERT INTO memories" in insert_args[0]
    bind = list(insert_args[1:])
    assert bind[-1] is None, f"source_doc should default to None, got {bind[-1]!r}"
