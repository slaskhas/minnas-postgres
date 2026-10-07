# Task list · P-20261006-03 (in-process MCP bridge)

- [x] **Bridge rewrite** (`integrations/hermes-mcp/mnemosyne_mcp.py`)
  - [x] add `from __future__ import annotations` at the top
  - [x] guarded mcp import → `_MCP_AVAILABLE`; stub when absent (`build_server`/`build_http_app`/`main`
        raise `RuntimeError`)
  - [x] add `set_base_url(url)` (recomputes `MNEMOSYNE_URL`/`API_BASE`, rebuilds the `httpx` client)
  - [x] add `build_server()` / `build_http_app(streamable_http_path="/mcp", stateless_http=True)`
  - [x] `__main__` guard; stdio entry point unchanged; `_dispatch`/`_call`/`list_tools`/`call_tool` left
        byte-for-byte unchanged
- [x] **`main.py` mount**
  - [x] load the bridge by file path (`importlib.util.spec_from_file_location`)
  - [x] capability check (`_MCP_AVAILABLE` + two callables) → only then `set_base_url` +
        `app.mount("/mcp", ...)`
  - [x] loopback host: `0.0.0.0`/empty → `127.0.0.1`
  - [x] `try/except`: mcp missing/bridge changed → DEBUG and skip, REST unaffected; INFO on success
- [x] **Tests** (`tests/test_mcp_http_mount.py`)
  - [x] `test_set_base_url_repoints_calling_layer`
  - [x] `test_build_server_lists_all_tools` (in-process `mcp.Client` → 15 tools)
  - [x] `test_build_http_app_is_starlette_with_mcp_route`
  - [x] `pytest.importorskip("mcp")`
- [x] **Dependency** (`requirements.txt`) add `mcp>=2.0`
- [x] **Spec/docs**
  - [x] `openspec/specs/mcp-transport.md` (capability truth)
  - [x] `docs/adr/0003-mcp-bridge-inprocess-tradeoffs-and-triggers.md`
  - [x] `AGENTS.md` (version + How to run + contract addendum)
  - [x] `docs/INTEGRATION.md` (new /mcp section)
  - [x] `INSTALL.md` (verify install, add an /mcp example)
  - [x] README badge version → 8.1.0
- [x] **Version number consistent in all three places**: VERSION=8.1.0 / README badge / CHANGELOG
      (new v8.1.0 section)

## Acceptance (run when an env is available)

- [ ] With mcp: `pytest tests/test_mcp_http_mount.py` green.
- [ ] With a DB: start `main.py`, `POST /mcp initialize` returns 200 + SSE body contains `"mnemosyne"`;
      one `tools/call` round-trip.
- [ ] Without mcp: `main.py` still starts, `/mcp` has no route, REST all green.
- [ ] `pytest tests/` all green; privacy scan zero output.
