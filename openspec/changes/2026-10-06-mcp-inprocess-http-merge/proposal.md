---
Proposal ID: P-20261006-03
Proposer: CLAUDE CODE
Date: 2026-10-06T00:00+00:00
Target: integrations/hermes-mcp + main.py
Action: ADDED (/mcp streamable-HTTP) ; MODIFIED (stdio bridge left in place)
Basis: user instruction "do alternative A, implement"; tests/test_mcp_bridge_contract.py pins the outbound shape; AGENTS.md integration-contract red line
Status: pending
Conflicts: []
Previous version: -
---

# Change proposal: in-process MCP bridge — merge the stdio bridge into the core process, expose the 15 tools over streamable HTTP at /mcp

- **Date**: 2026-10-06
- **Tier**: M (cross-file; adds an external endpoint `/mcp`; stdio unchanged)
- **Related**: `openspec/specs/mcp-transport.md` · ADR-0003 · v7.8.2 contract lesson (the 422 red line)

## Why (Why)

Current state: the 15 Mnemosyne MCP tools are hosted by an independent stdio subprocess
(`integrations/hermes-mcp/mnemosyne_mcp.py`), launched by an MCP client (e.g. the slask client) over
an SSH tunnel (18010 → core 8010). This carries three kinds of cost:

1. **Multiple processes + multiple tunnels**: the client has to spawn a subprocess and needs an SSH
   tunnel to the core; the bridge and core are two separate processes and two separate REST clients.
2. **Version drift**: when the bridge evolves independently, its `/api/v1` contract can drift out of sync
   with the core (the v7.8.2 422 incident was exactly this).
3. The slask TS client is **streamable-HTTP first** — natively supported; stdio is the secondary option.

**Alternative A (this proposal)**: mount the bridge's **same** contract-tested handlers
(`_dispatch`/`_call`/`list_tools`/`call_tool`) into the core's uvicorn process, exposed over
**streamable HTTP** at `/mcp`. stdio stays in place (existing stdio client config / `mcp_adapt_test.py`
continue to work); both transports share a single low-level `Server`. The handlers still call the core
over **loopback REST** (`set_base_url()`), with **no guessing field names or param positions** — fully
reusing the already-verified contract path.

## What changes (What)

- `integrations/hermes-mcp/mnemosyne_mcp.py`
  - Add `from __future__ import annotations` (deferred annotation evaluation, so `types.*` is only
    evaluated when mcp is actually present).
  - Add a guarded mcp import → `_MCP_AVAILABLE`; `main.py` can import this module without mcp installed.
  - Add `set_base_url(url)`: recomputes `MNEMOSYNE_URL`/`API_BASE`, rebuilds the `httpx` client (`_call`
    resolves absolute URLs from `API_BASE`).
  - Add `build_server() -> Server` (reuses the global low-level `Server`) and
    `build_http_app() -> Starlette app` (`server.streamable_http_app(streamable_http_path="/mcp",
    stateless_http=True)`).
  - Add a `__main__` guard (stdio entry point unchanged); without mcp, calling the stub raises
    `RuntimeError`.
  - **`_dispatch`/`_call`/`list_tools`/`call_tool` left byte-for-byte unchanged** (red line: contract
    code doesn't move).
- `main.py`
  - After `injection_router`, load the bridge **by file path** (the parent directory `hermes-mcp`
    contains a hyphen → not a valid import name), check that `_MCP_AVAILABLE` + `set_base_url`/
    `build_http_app` are callable, then call `set_base_url(http://<HOST>:<PORT>)` (`0.0.0.0`/empty →
    `127.0.0.1`) and `app.mount("/mcp", build_http_app())`.
  - Wrapped in `try/except`: if mcp is missing or the bridge has changed → DEBUG log only, REST API
    unaffected.
  - `stateless_http=True`: requests are stateless → no cross-worker session drift under
    `uvicorn --workers N`.
- `tests/test_mcp_http_mount.py` (new)
  - `test_set_base_url_repoints_calling_layer`: `set_base_url` recomputes `API_BASE`/`MNEMOSYNE_URL` and
    rebuilds the client.
  - `test_build_server_lists_all_tools`: driven by a real in-process `mcp.Client(server)`, lists exactly
    15 tool names.
  - `test_build_http_app_is_starlette_with_mcp_route`: `build_http_app()` returns a Starlette app with a
    `/mcp` route.
  - `pytest.importorskip("mcp")`: skipped when mcp is absent (minimal install).
- `requirements.txt`
  - Add `mcp>=2.0` (comment notes "the REST core runs fine without it, the mount is skipped
    automatically").

## Out of scope

- ❌ Not touching `tests/test_mcp_bridge_contract.py` (it loads the bridge by file path; `_dispatch`/
  `_call` are unchanged → still green).
- ❌ Not touching the stdio entry point (existing stdio client config / `scripts/mcp_adapt_test.py` keep
  working).
- ❌ Not touching any `/api/v1` endpoint, field name, or parameter position.
- ❌ Not touching the Nginx auth layer (`/mcp` uses the same loopback direct-connect as REST, with no
  auth, consistent with REST's direct-connect path).

## Acceptance criteria

- [x] All three syntax checks green (`py_compile` on the bridge / test / main).
- [x] Without mcp installed: the bridge still imports, the stub raises `RuntimeError`; `main.py`'s mount
      skips gracefully (REST unaffected).
- [ ] With mcp installed: `build_http_app()` returns a Starlette app containing `/mcp`; an in-process
      `Client(build_server())` lists 15 tools.
- [ ] With a DB available: `POST /mcp` (`initialize`, `Content-Type: application/json` + valid Host +
      SSE `Accept`) → 200, SSE body contains `"mnemosyne"`; one tool round-trips successfully.
- [x] Existing contract tests (`test_mcp_bridge_contract.py`) unaffected.
- [ ] Privacy scan (post-push) zero output; VERSION / README badge / CHANGELOG version numbers consistent
      across all three.

## Risk and rollback

- Risk surface: `set_base_url` points the handlers at loopback; if the core's direct-connect layer ever
  adds auth in the future (currently Nginx-only, direct-connect has none), this would break.
- Multiple workers: `stateless_http=True` guarantees no cross-worker state drift.
- Rollback: `git revert` this change; the stdio path exists independently and is unaffected, stdio
  clients can still use the tunnel.
