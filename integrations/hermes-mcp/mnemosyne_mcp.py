#!/usr/bin/env python3
"""
Mnemosyne MCP Server — bridging the agent to the memory palace

Connects to the Mnemosyne REST API and exposes memory store/retrieve tools via
MCP. Two transports share the *same* handlers:

  * **stdio** — the classic form: a generic stdio-to-HTTP bridge, launched as a
    subprocess by any MCP client's stdio config (e.g. the slask client), optionally
    over an SSH tunnel (localhost:18010 → core 8010). Entered via
    `python3 integrations/hermes-mcp/mnemosyne_mcp.py` (see `__main__`).
  * **streamable HTTP** — the in-process merge (v8.1). `main.py` mounts the 15
    tools at `/mcp` inside the core FastAPI process. Handlers reach the core via
    `set_asgi_app()` — an in-process ASGI transport (no real socket, same
    Starlette routing + Pydantic validation as a real HTTP call) — so the *same*
    contract-tested `_dispatch`/`_call` serve both transports.

Contract iron rules (bitten once before, see tests/test_mcp_bridge_contract.py):
  * Parameter positions follow the server — feedback/delete/restore `user_id`, and
    `feedback`, are **query** parameters; sending them as a JSON body or omitting
    them gives 422 Unprocessable Entity (from the user's side the tool "just
    breaks").
  * Response field names follow capabilities/schema — never guess from memory —
    heat-top returns `heat_score`.
  * Contract tests MUST be run after any handler change; silent failure (no error
    = always falls back to the default value) is more dangerous than an error.

How to start
  * Any stdio-capable MCP client:
      mcp_servers:
        mnemosyne:
          command: "python3"
          args: ["/path/to/integrations/hermes-mcp/mnemosyne_mcp.py"]
  * In-process HTTP (v8.1): nothing to do — `main.py` mounts `/mcp` itself when
    the `mcp` SDK is installed, calling `set_asgi_app(app)` with its own FastAPI
    app object before mounting (no loopback socket needed).
"""
from __future__ import annotations

import json
import os
import sys
import httpx
from typing import Any, Optional


# ── Mnemosyne API address ──────────────────────────────
# Defaults to the local SSH-tunnel endpoint (the stdio transport's real-network
# case). When mounted in-process by `main.py`, `set_asgi_app()` repoints the
# bridge at the core's own FastAPI app object via an in-process ASGI transport
# instead (no socket, no Nginx auth layer — same as today's loopback).
MNEMOSYNE_URL = os.getenv("MNEMOSYNE_URL", "http://127.0.0.1:18010")
API_BASE = f"{MNEMOSYNE_URL}/api/v1"


# ── MCP SDK import (guarded so `main.py` can be imported without it) ──
try:
    from mcp.server.stdio import stdio_server
    from mcp.server.lowlevel import Server
    import mcp.types as types
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


# ── HTTP client ────────────────────────────────────────
import asyncio

_transport: Optional["httpx.AsyncBaseTransport"] = None  # None => real network (stdio)


def _mk_client() -> "httpx.AsyncClient":
    """Fresh client per call/reconnect, to avoid the connection pool caching dead
    connections. Respects whichever transport mode is active (see set_base_url /
    set_asgi_app below) so `_call` never needs to know which one is in effect."""
    if _transport is not None:
        return httpx.AsyncClient(timeout=30, base_url=MNEMOSYNE_URL, transport=_transport)
    return httpx.AsyncClient(timeout=30, base_url=MNEMOSYNE_URL,
                              limits=httpx.Limits(max_keepalive_connections=2))


http = _mk_client()


def set_base_url(url: str) -> None:
    """Point the bridge at a new Mnemosyne REST base over real network HTTP.

    The stdio default (`MNEMOSYNE_URL` env / `http://127.0.0.1:18010`) is the
    SSH-tunnel endpoint; this is also the function the stdio transport uses.
    It recomputes `API_BASE` and rebuilds the `httpx` client (clearing any
    previously configured in-process ASGI transport), because `_call` resolves
    absolute URLs from `API_BASE`.
    """
    global MNEMOSYNE_URL, API_BASE, http, _transport
    MNEMOSYNE_URL = url
    API_BASE = f"{MNEMOSYNE_URL}/api/v1"
    _transport = None
    http = _mk_client()


def set_asgi_app(app, internal_base_url: str = "http://mcp-internal") -> None:
    """Point the bridge at the core's own FastAPI app object, in-process.

    Used only by the in-process `/mcp` mount (`main.py`'s `_mcp_lifespan`): the
    bridge's calls to the core's own REST handlers are routed through
    `httpx.ASGITransport(app=app)` — same Starlette routing + Pydantic request
    validation as a real HTTP call, but no socket. `internal_base_url` is just a
    well-formed placeholder origin (ASGITransport never resolves it over
    DNS/TCP; Starlette routes on path, not Host header) so `_call`'s
    `f"{API_BASE}{path}"` string-join keeps working unchanged.

    `raise_app_exceptions=False` makes an unhandled exception in a handler
    surface as a plain 500 Response (as a real ASGI server would produce)
    rather than propagating the raw Python exception into `_call` — keeping
    `_call`'s existing `raise_for_status()` / `{"error","detail"}` handling
    identical between the stdio (real HTTP) and in-process (ASGI) transports.
    """
    global MNEMOSYNE_URL, API_BASE, http, _transport
    MNEMOSYNE_URL = internal_base_url
    API_BASE = f"{MNEMOSYNE_URL}/api/v1"
    _transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    http = _mk_client()


async def _call(method: str, path: str, **kwargs) -> dict:
    """Call the Mnemosyne REST API with exponential backoff reconnect."""
    global http
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = await http.request(method, f"{API_BASE}{path}", **kwargs)
            r.raise_for_status()
            return r.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            # Connection failed: backoff retry + rebuild client (discard a possibly corrupt pool)
            # Unreachable under the in-process ASGI transport (no real socket) — real-network only.
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s -> 2s -> 4s
                await asyncio.sleep(wait)
                try:
                    await http.aclose()
                except Exception:
                    pass
                http = _mk_client()
                continue
            return {"error": f"Mnemosyne unreachable after {max_retries} retries", "detail": str(e)}
        except httpx.HTTPError as e:
            detail = ""
            if hasattr(e, "response") and e.response is not None:
                detail = e.response.text
            return {"error": str(e), "detail": detail}


# ── Tool list ──────────────────────────────────────────
async def list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[
        # ── Memory core ──
        types.Tool(
            name="store_memory",
            description="Store a memory in Mnemosyne. User ID defaults to 'default'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Memory content"},
                    "category": {"type": "string", "description": "Category: fact|experience|belief|chat|work|note|test", "default": "fact"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "importance": {"type": "number", "description": "Importance 0-1", "default": 0.5},
                },
                "required": ["content"],
            },
        ),
        types.Tool(
            name="search_memories",
            description="Search memories on 4 dimensions (semantic + keyword + temporal + graph). Returns the best matching memories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "top_k": {"type": "integer", "description": "Number of results to return", "default": 5},
                    "category": {"type": "string", "description": "Optional: filter by category"},
                    "mode": {"type": "string", "description": "hybrid|semantic|fulltext", "default": "hybrid"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="dialectic_search",
            description="Deep semantic search over memories, with L2/L3 session context (multi-factor scoring + BM25 + temporal + heat + reliability). More thorough than search_memories; returns related session summaries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "max_results": {"type": "integer", "description": "Number of results to return", "default": 3},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_hot_memories",
            description="Get the hottest memories (L1 tier). A quick view of the currently most important information.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "limit": {"type": "integer", "description": "Number of results to return", "default": 10},
                    "min_heat": {"type": "number", "description": "Minimum heat threshold", "default": 0.0},
                },
            },
        ),
        # ── Memory management ──
        types.Tool(
            name="get_memory_stats",
            description="Get a memory-store health report: total count, category distribution, heat tiers, average heat, deleted count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                },
            },
        ),
        types.Tool(
            name="feedback_memory",
            description="Credibility feedback on a memory: positive = heat up, negative = cool down. Affects ranking in later retrieval.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "Memory ID"},
                    "feedback": {"type": "string", "description": "positive|negative"},
                },
                "required": ["memory_id", "feedback"],
            },
        ),
        types.Tool(
            name="get_memory_traces",
            description="View a memory's lifecycle history: stored, recalled, feedback, deleted, restored, etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "Memory ID"},
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="delete_memory",
            description="Soft-delete a memory (reversible).",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "Memory ID"},
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="restore_memory",
            description="Restore a soft-deleted memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "Memory ID"},
                },
                "required": ["memory_id"],
            },
        ),
        # ── Knowledge graph ──
        types.Tool(
            name="search_graph",
            description="Search entities and relations in the knowledge graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "limit": {"type": "integer", "description": "Graph traversal hops (kept as 'limit' for backward compatibility)", "default": 2},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="extract_entities",
            description="Extract entities from the user's existing stored memories that don't yet have linked entities (operates on already-stored memories, not arbitrary free text).",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "max_memories": {"type": "integer", "description": "Max number of unlinked memories to scan", "default": 50},
                },
            },
        ),
        # ── Wiki ──
        types.Tool(
            name="create_wiki_page",
            description="Create or update a Wiki page (versioned knowledge document).",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Wiki title"},
                    "content": {"type": "string", "description": "Wiki content"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                },
                "required": ["title", "content"],
            },
        ),
        types.Tool(
            name="search_wiki",
            description="Search Wiki pages (fuzzy matching supported).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "limit": {"type": "integer", "description": "Number of results to return", "default": 5},
                },
                "required": ["query"],
            },
        ),
        # ── Belief system ──
        types.Tool(
            name="store_belief",
            description="Store a belief in the belief system.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Belief content"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                },
                "required": ["content"],
            },
        ),
        types.Tool(
            name="search_beliefs",
            description="Search the belief store.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                    "top_k": {"type": "integer", "description": "Number of results to return", "default": 5},
                },
                "required": ["query"],
            },
        ),
    ])


# ── Tool call dispatch ─────────────────────────────────
async def _dispatch(name: str, arguments: dict) -> list[types.TextContent]:
    user_id = arguments.pop("user_id", "default")

    try:
        if name == "store_memory":
            data = await _call("POST", "/memories", json={
                "user_id": user_id,
                "content": arguments["content"],
                "category": arguments.get("category", "fact"),
                "importance": arguments.get("importance", 0.5),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_memories":
            data = await _call("POST", "/memories/search", json={
                "user_id": user_id,
                "query": arguments["query"],
                "top_k": arguments.get("top_k", 5),
                "category": arguments.get("category"),
                "mode": arguments.get("mode", "hybrid"),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "dialectic_search":
            data = await _call("POST", "/dialectic", json={
                "user_id": user_id,
                "query": arguments["query"],
                "max_memories": arguments.get("max_results", 3),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "get_hot_memories":
            params = {"user_id": user_id, "limit": arguments.get("limit", 10)}
            if arguments.get("min_heat"):
                params["min_heat"] = arguments["min_heat"]
            data = await _call("GET", "/memories/heat-top", params=params)
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "get_memory_stats":
            data = await _call("GET", "/memories/stats", params={"user_id": user_id})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "feedback_memory":
            # API requires query params (user_id + feedback); JSON body gives 422 (observed 2026-09-12)
            data = await _call("POST", f"/memories/{arguments['memory_id']}/feedback",
                         params={"user_id": user_id, "feedback": arguments["feedback"]})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "get_memory_traces":
            data = await _call("GET", f"/memories/{arguments['memory_id']}/traces")
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "delete_memory":
            # user_id is a required query param; missing = 422 (observed 2026-09-12)
            data = await _call("DELETE", f"/memories/{arguments['memory_id']}", params={"user_id": user_id})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "restore_memory":
            data = await _call("POST", f"/memories/{arguments['memory_id']}/restore", params={"user_id": user_id})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_graph":
            # main.py's graph_search takes query/user_id/max_hops as query params
            # (bare scalar args, no Pydantic body) — json= here 422s. Keep the
            # tool's public arg name `limit` for backward compat; only the wire
            # name changes (mirrors dialectic_search's max_results mapping above).
            data = await _call("POST", "/graph/search", params={
                "user_id": user_id,
                "query": arguments["query"],
                "max_hops": arguments.get("limit", 2),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "extract_entities":
            # main.py's extract_entities takes user_id/max_memories as query params
            # and extracts from the user's own unlinked stored memories — it does
            # not accept free text. A stale `text` arg from an old client is
            # accepted-but-ignored (the MCP SDK doesn't validate `arguments`
            # against inputSchema before calling us).
            data = await _call("POST", "/extract-entities", params={
                "user_id": user_id,
                "max_memories": arguments.get("max_memories", 50),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "create_wiki_page":
            data = await _call("POST", "/wiki", json={
                "title": arguments["title"],
                "content": arguments["content"],
                "user_id": user_id,
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_wiki":
            data = await _call("POST", "/wiki/search", json={
                "query": arguments["query"],
                "user_id": user_id,
                "limit": arguments.get("limit", 5),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "store_belief":
            data = await _call("POST", "/beliefs", json={
                "user_id": user_id,
                "content": arguments["content"],
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_beliefs":
            data = await _call("POST", "/beliefs/search", json={
                "user_id": user_id,
                "query": arguments["query"],
                "top_k": arguments.get("top_k", 5),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        else:
            return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


# ── MCP 2.0 callback adapter ───────────────────────────
async def call_tool(ctx, params) -> types.CallToolResult:
    return types.CallToolResult(content=await _dispatch(params.name, dict(params.arguments or {})))


# ── Server builders ────────────────────────────────────
if _MCP_AVAILABLE:
    # The single low-level Server backing both transports (stdio + streamable
    # HTTP). Created once per process; each uvicorn worker imports this module
    # separately, so there is exactly one per worker.
    server = Server("mnemosyne", on_list_tools=list_tools, on_call_tool=call_tool)

    def build_server() -> Server:
        """Return the low-level MCP Server used by stdio and streamable HTTP."""
        return server

    def build_http_app(streamable_http_path: str = "/mcp", stateless_http: bool = True):
        """Return the mountable Starlette app for streamable HTTP.

        `stateless_http=True` keeps every request independent — the right choice
        when the app is mounted behind `uvicorn --workers N` (no per-connection
        state to lose or bounce across workers). All 15 Mnemosyne tools are
        stateless anyway.
        """
        return server.streamable_http_app(
            streamable_http_path=streamable_http_path,
            stateless_http=stateless_http,
        )

    def build_mcp_mount():
        """Return the raw pieces for mounting MCP at ``/mcp`` in a host app.

        Returns a 2-tuple ``(asgi_endpoint, run_cm)``:

        - ``asgi_endpoint``: the raw per-endpoint ASGI app (path-agnostic; the
          JSON-RPC method lives in the request body, not the URL path). This is
          ``session_manager.asgi_app`` — the same app the SDK's standalone
          ``streamable_http_app()`` routes ``/mcp`` to.
        - ``run_cm``: an async context manager (``session_manager.run()``) that
          initializes the session manager's task group. It must be entered on
          startup and exited on shutdown; without it the endpoint 500s
          ("Task group is not initialized"). A Starlette ``Mount`` never runs a
          sub-app lifespan, so the host app must drive this itself — mirroring
          the SDK's own ``lifespan=lambda app: session_manager.run()``.
        """
        # Build via the SDK's own helper so transport-security / stateless
        # settings match the standalone app; this also populates
        # server.session_manager. The path only names the sub-app's route, which
        # we do not use here (the host registers the raw endpoint at /mcp
        # directly as a plain Route).
        _ = server.streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True
        )
        sm = server.session_manager
        return sm.asgi_app, sm.run

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

else:
    # Stubs so the module imports cleanly and `main.py`'s capability checks pass
    # without the mcp SDK (minimal install). Calling them raises a clear error.
    def build_server() -> Server:
        raise RuntimeError("mcp SDK not installed; cannot build the MCP server")

    def build_http_app(streamable_http_path: str = "/mcp", stateless_http: bool = True):
        raise RuntimeError("mcp SDK not installed; cannot build the MCP HTTP app")

    def build_mcp_mount():
        raise RuntimeError("mcp SDK not installed; cannot build the MCP mount")

    async def main():
        raise RuntimeError("mcp SDK not installed")


if __name__ == "__main__":
    if not _MCP_AVAILABLE:
        print("ERROR: mcp package not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)
    import asyncio
    asyncio.run(main())
