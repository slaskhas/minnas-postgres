#!/usr/bin/env python3
"""In-process MCP merge test (v8.1).

Proves the streamable-HTTP merge without a network or a database: the same
contract-tested handlers that already serve stdio now (a) build a low-level
MCP Server that a real `mcp.Client` can drive in-process, and (b) build a
mountable Starlette app carrying the `/mcp` route. `set_base_url()` — the new
prerequisite for in-process mounting — must repoint the call layer at the core.

Skipped if the mcp SDK is absent (minimal install), mirroring
tests/test_mcp_bridge_contract.py.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

import httpx

BRIDGE = (Path(__file__).resolve().parents[1]
          / "integrations" / "hermes-mcp" / "mnemosyne_mcp.py")

ALL_TOOLS = {
    "store_memory", "search_memories", "dialectic_search", "get_hot_memories",
    "get_memory_stats", "feedback_memory", "get_memory_traces", "delete_memory",
    "restore_memory", "search_graph", "extract_entities", "create_wiki_page",
    "search_wiki", "store_belief", "search_beliefs",
}


@pytest.fixture(scope="module")
def bridge():
    pytest.importorskip("mcp")      # minimal install (no Hermes) → skip
    pytest.importorskip("httpx")
    spec = importlib.util.spec_from_file_location("_mnemosyne_bridge_http", BRIDGE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_mnemosyne_bridge_http"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_set_base_url_repoints_calling_layer(bridge):
    """set_base_url must recompute API_BASE + MNEMOSYNE_URL and rebuild the client,
    because _call resolves absolute URLs from API_BASE."""
    base = "http://127.0.0.1:8010"
    bridge.set_base_url(base)
    assert bridge.MNEMOSYNE_URL == base
    assert bridge.API_BASE == f"{base}/api/v1"
    # A fresh client was built for the new base. Its origin — what `_call` resolves
    # absolute request URLs against — must point at the core's loopback. Check
    # scheme+netloc (the origin) rather than a byte-exact string, so the test
    # is immune to httpx's optional trailing-slash normalization of pathless base URLs.
    from urllib.parse import urlsplit
    _split = urlsplit(str(bridge.http.base_url))
    assert f"{_split.scheme}://{_split.netloc}" == base


def test_build_server_lists_all_tools(bridge):
    """A real in-process Client drives the same Server our /mcp mount would,
    and all 15 Mnemosyne tools are exposed."""
    from mcp import Client

    async def run():
        async with Client(bridge.build_server()) as client:
            return await client.list_tools()

    result = asyncio.run(run())
    names = {t.name for t in result.tools}
    assert names == ALL_TOOLS, f"missing/extra: {names ^ ALL_TOOLS}"


def test_build_http_app_is_starlette_with_mcp_route(bridge):
    """build_http_app must return a mountable Starlette app exposing /mcp."""
    app = bridge.build_http_app()
    assert hasattr(app, "router"), f"not an ASGI app: {type(app)!r}"
    paths = {str(r.path) for r in app.router.routes if hasattr(r, "path")}
    assert "/mcp" in paths, f"/mcp route missing; routes={paths}"
