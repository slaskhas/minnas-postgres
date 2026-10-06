#!/usr/bin/env python3
"""
Mnemosyne MCP Server — bridging Hermes to the memory palace

Connects to the Mnemosyne REST API on the production server over the SSH tunnel
(localhost:18010), exposing memory store/retrieve tools to the Hermes agent via MCP.

Contract iron rules (bitten once before, see tests/test_mcp_bridge_contract.py):
  * Parameter positions follow the server — feedback/delete/restore user_id, and
    feedback are **query** parameters; sending them as a JSON body or omitting them
    gives 422 Unprocessable Entity (from the user's side the tool "just breaks").
  * Response field names follow capabilities/schema — never guess from memory —
    heat-top returns heat_score.
  * Contract tests MUST be run after any handler change; silent failure (no error
    = always falls back to the default value) is more dangerous than an error.

How to start (registered in Hermes config.yaml):
  mcp_servers:
    mnemosyne:
      command: "python3"
      args: ["/path/to/hermes/tools/mnemosyne_mcp.py"]
"""

import json
import os
import sys
import httpx
from typing import Any, Optional
from mcp.server.stdio import stdio_server

# ── Mnemosyne API address ──────────────────────────────
MNEMOSYNE_URL = os.getenv("MNEMOSYNE_URL", "http://127.0.0.1:18010")
API_BASE = f"{MNEMOSYNE_URL}/api/v1"


# ── MCP SDK import ─────────────────────────────────────
try:
    import mcp.server as mcp_server
    import mcp.types as types
    from mcp.server.lowlevel import Server
except ImportError:
    print("ERROR: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)


# ── HTTP client ────────────────────────────────────────
import time as _time

def _mk_client():
    """Fresh client per call, to avoid the connection pool caching dead connections."""
    return httpx.Client(timeout=30, base_url=MNEMOSYNE_URL, limits=httpx.Limits(max_keepalive_connections=2))

http = _mk_client()


def _call(method: str, path: str, **kwargs) -> dict:
    """Call the Mnemosyne REST API with exponential backoff reconnect."""
    global http
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = http.request(method, f"{API_BASE}{path}", **kwargs)
            r.raise_for_status()
            return r.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            # Connection failed: backoff retry + rebuild client (discard a possibly corrupt pool)
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s -> 2s -> 4s
                _time.sleep(wait)
                try:
                    http.close()
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



# ── MCP server definition ──────────────────────────────
# (app is registered via constructor callback after handler definitions, mcp>=2.0 API)


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
                    "limit": {"type": "integer", "description": "Number of results to return", "default": 10},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="extract_entities",
            description="Automatically extract entities from text and store them in the knowledge graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to extract entities from"},
                    "user_id": {"type": "string", "description": "User ID (actual DB uses default)", "default": "default"},
                },
                "required": ["text"],
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
            data = _call("POST", "/memories", json={
                "user_id": user_id,
                "content": arguments["content"],
                "category": arguments.get("category", "fact"),
                "importance": arguments.get("importance", 0.5),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_memories":
            data = _call("POST", "/memories/search", json={
                "user_id": user_id,
                "query": arguments["query"],
                "top_k": arguments.get("top_k", 5),
                "category": arguments.get("category"),
                "mode": arguments.get("mode", "hybrid"),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "dialectic_search":
            data = _call("POST", "/dialectic", json={
                "user_id": user_id,
                "query": arguments["query"],
                "max_memories": arguments.get("max_results", 3),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "get_hot_memories":
            params = {"user_id": user_id, "limit": arguments.get("limit", 10)}
            if arguments.get("min_heat"):
                params["min_heat"] = arguments["min_heat"]
            data = _call("GET", "/memories/heat-top", params=params)
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "get_memory_stats":
            data = _call("GET", "/memories/stats", params={"user_id": user_id})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "feedback_memory":
            # API requires query params (user_id + feedback); JSON body gives 422 (observed 2026-09-12)
            data = _call("POST", f"/memories/{arguments['memory_id']}/feedback",
                         params={"user_id": user_id, "feedback": arguments["feedback"]})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "get_memory_traces":
            data = _call("GET", f"/memories/{arguments['memory_id']}/traces")
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "delete_memory":
            # user_id is a required query param; missing = 422 (observed 2026-09-12)
            data = _call("DELETE", f"/memories/{arguments['memory_id']}", params={"user_id": user_id})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "restore_memory":
            data = _call("POST", f"/memories/{arguments['memory_id']}/restore", params={"user_id": user_id})
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_graph":
            data = _call("POST", "/graph/search", json={
                "user_id": user_id,
                "query": arguments["query"],
                "limit": arguments.get("limit", 10),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "extract_entities":
            data = _call("POST", "/extract-entities", json={
                "text": arguments["text"],
                "user_id": user_id,
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "create_wiki_page":
            data = _call("POST", "/wiki", json={
                "title": arguments["title"],
                "content": arguments["content"],
                "user_id": user_id,
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_wiki":
            data = _call("POST", "/wiki/search", json={
                "query": arguments["query"],
                "user_id": user_id,
                "limit": arguments.get("limit", 5),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "store_belief":
            data = _call("POST", "/beliefs", json={
                "user_id": user_id,
                "content": arguments["content"],
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        elif name == "search_beliefs":
            data = _call("POST", "/beliefs/search", json={
                "user_id": user_id,
                "query": arguments["query"],
                "top_k": arguments.get("top_k", 5),
            })
            return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))]

        else:
            return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


# ── MCP 2.0 callback adapters + startup ─────────────────
async def call_tool(ctx, params) -> types.CallToolResult:
    return types.CallToolResult(content=await _dispatch(params.name, dict(params.arguments or {})))


app = Server("mnemosyne", on_list_tools=list_tools, on_call_tool=call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
