# MCP Transport (Capability truth · Executable spec)

> Authoritative implementation: `integrations/hermes-mcp/mnemosyne_mcp.py` + `main.py` (`/mcp` mount)
> External entry: `GET /mcp` (streamable HTTP / SSE) · `GET /api/v1/capabilities` (single source of truth)
> Status: **active** (v8.1.0) | Origin: user instruction "do alternative A, implement" → normalized → made executable

## 1. Two transports, one Server

**A single low-level `mcp.server.lowlevel.Server(name="mnemosyne")`** serves both transports:

| transport | entry point | who starts it |
|---|---|---|
| stdio | `python3 integrations/hermes-mcp/mnemosyne_mcp.py` (`__main__`) | any stdio-capable MCP client / `scripts/mcp_adapt_test.py`; via the SSH tunnel 18010→8010 |
| streamable HTTP | `GET /mcp` (in-process mount, `main.py`) | the core uvicorn process itself; `stateless_http=True` |

Both share the **same** set of contract-tested handlers: `list_tools` (15 tools), `_dispatch`, `_call`.

## 2. The 15 tools

| family | tools |
|---|---|
| Memory core | `store_memory` · `search_memories` · `dialectic_search` · `get_hot_memories` |
| Memory management | `get_memory_stats` · `feedback_memory` · `get_memory_traces` · `delete_memory` · `restore_memory` |
| Knowledge graph | `search_graph` · `extract_entities` |
| Wiki | `create_wiki_page` · `search_wiki` |
| Beliefs | `store_belief` · `search_beliefs` |

## 3. Parameter positions and field names (red line)

- **Parameter positions follow the server**: the `user_id` on `feedback_memory`/`delete_memory`/`restore_memory` (and `feedback` on `feedback_memory`) are **query** parameters — put them in the JSON body or omit them → 422.
- **Field names follow capabilities/schema**: e.g. `heat-top` returns `heat_score` (not `heat`); **reading the wrong field does not raise an error — it silently falls back to the default**, which is more dangerous than an error.
- Single source of truth: `GET /api/v1/capabilities`. Any handler / integration change must run `tests/test_mcp_bridge_contract.py`.

## 4. In-process mount (added in v8.1)

`main.py` loads the bridge **by file path** at startup (the parent directory `hermes-mcp` contains a hyphen → not a legal import name).
If `_MCP_AVAILABLE` and `set_asgi_app`/`build_mcp_mount` are callable, then inside the app's **lifespan** (`asynccontextmanager`; `app = FastAPI(..., lifespan=_mcp_lifespan)`):

1. `set_asgi_app(app)` — so the handlers call the core through an **in-process ASGI transport** (`httpx.ASGITransport(app=app)`, no real socket; same Starlette routing + Pydantic request validation as a real HTTP call, no Nginx auth). `set_base_url("http://<host>:<port>")` is the sibling function for **real network** HTTP and remains the one the stdio transport uses.
2. `asgi_endpoint, run_cm = build_mcp_mount()` → `_app.router.add_route("/mcp", asgi_endpoint, methods=None, include_in_schema=False)`, and `async with run_cm():` in the lifespan (`run_cm = session_manager.run()`, which must be entered to initialize the task group, otherwise the endpoint 500s).
   - **Do not** use `app.mount("/mcp", build_http_app())`: a Starlette `Mount` **never runs the sub-app's lifespan** (→ "Task group is not initialized" 500), and the sub-app carries its own `/mcp` route, which nested under a `/mcp` prefix becomes `/mcp/mcp`. The raw per-endpoint ASGI app (method in the body, independent of the URL path) is pinned exactly to `/mcp` with `methods=None` (all methods, no trailing-slash 307) — isomorphic to the SDK's own standalone app (`Route(path, sm.asgi_app, methods=None)` + `lifespan=lambda app: sm.run()`).

**Graceful degradation**: `mcp` missing (minimal install) or a changed bridge → DEBUG-only log; the REST API is unaffected.
**`stateless_http=True`** (inside `build_mcp_mount` as `streamable_http_path="/mcp", stateless_http=True`): each request is independent → no cross-worker session drift under `uvicorn --workers N`.

## 5. Acceptance anchors (falsifiable)

1. `build_http_app()` returns a Starlette app whose routes include `/mcp`
   (`tests/test_mcp_http_mount.py::test_build_http_app_is_starlette_with_mcp_route`).
2. in-process `mcp.Client(build_server())` lists **exactly** 15 tools
   (`...::test_build_server_lists_all_tools`).
3. After `set_base_url("http://127.0.0.1:8010")`, `API_BASE == "http://127.0.0.1:8010/api/v1"`
   (`...::test_set_base_url_repoints_calling_layer`).
3b. After `set_asgi_app(app)`, `_call` round-trips through `app` with no real socket
   (`...::test_set_asgi_app_routes_in_process`).
4. Real HTTP (with DB): `POST /mcp` `initialize` (`Content-Type: application/json` + `Accept` containing `application/json, text/event-stream` + a valid Host) → 200; the SSE body contains `"mnemosyne"`.
5. `tests/test_mcp_bridge_contract.py` is unaffected by this change (`_dispatch`/`_call` are byte-identical).

## 6. What it does not do

- Does not touch the stdio entry (backwards-compatible with existing stdio-client usage / `mcp_adapt_test.py`).
- Does not touch the `/api/v1` endpoints / field names / parameter positions.
- Does not maintain MCP session state across workers (stateless first); if statefulness becomes a requirement → trigger ADR-0003 T-1.
