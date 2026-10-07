# ADR-0003 · MCP Bridge In-Process Merge (stdio folded into core, `/mcp` streamable-HTTP)

- **Status**: Accepted
- **Date**: 2026-10-06
- **Decision-makers**: User (Claes)
- **Related**: Proposal [P-20261006-03](../../openspec/changes/2026-10-06-mcp-inprocess-http-merge/proposal.md)

## Background

The 15 Mnemosyne MCP tools have long been hosted by an independent stdio subprocess
(`integrations/hermes-mcp/mnemosyne_mcp.py`), launched via an MCP client (e.g. the slask
client) over an SSH tunnel (18010 → core 8010) since v5.3.

The user's instruction "do alternative A, implement" required merging the bridge **into the same
uvicorn/FastAPI process**, exposing the 15 tools over **streamable HTTP** at `/mcp`.

## Options

| Option | Description | Trade-offs |
|---|---|---|
| **A (adopted)**: in-process mount at `/mcp`, stdio retained | The bridge's contract-tested handlers are mounted into core; `stateless_http=True`; `set_base_url` points at loopback | Single process, no tunnel; stdio and HTTP share one `Server`; reuses the contract path (no field guessing); a native fit for slask's HTTP-first design. Cost: core gains an `mcp` dependency (can be made optional) |
| B: add the HTTP mount only, drop stdio | Remove the stdio entry point outright | Breaks existing stdio-based client deployments and `mcp_adapt_test.py`; high regression risk for no benefit |
| C: core calls the handler directly (bypassing REST) | The bridge process calls core handler objects in-process | Large change, bypasses the REST contract; reintroduces the risk of "guessing" fields/parameter positions — contradicts the v7.8.2 lesson |
| D: keep the status quo (stdio) | No change | Multiple processes, multiple tunnels; doesn't fit slask's HTTP-first design |

## Decision

Adopt **A**: keep stdio (for backward compatibility) and add an in-process `/mcp` mount
(streamable HTTP). Both transports share a single low-level `Server`; handlers still call core
over loopback REST (`set_base_url`), fully reusing the contract-test path.

### Key constraints (hard lines)

1. **Parameter positions/field names follow the server verbatim**: `_dispatch`/`_call` are
   unchanged character-for-character (the `user_id`/`feedback` fields for feedback/delete/restore
   are query parameters); `GET /api/v1/capabilities` remains the single source of truth.
2. **`stateless_http=True`**: requests are stateless → `uvicorn --workers N` has no cross-worker
   session drift.
3. **Graceful degradation**: if `mcp` is missing (minimal install) → the bridge still imports, the
   stub raises, `main.py` skips the mount, and REST is unaffected.
4. **No auth on loopback**: matches current behavior (auth lives at the Nginx layer; direct
   connections to uvicorn are unauthenticated); if core's direct-connect layer gains auth in the
   future, `/mcp` will follow suit.

## Consequences

**Positive**:
- Single process, single deployment: MCP clients no longer need a stdio subprocess + SSH tunnel
  (still supported, but HTTP becomes the default).
- Eliminates the bridge/core version-drift surface (same process, same loopback, same contract
  path).
- slask's HTTP-first client can connect to `/mcp` with zero changes.

**Negative / risks**:
- Core gains an `mcp` dependency (mitigation: optional, skipped if missing).
- Loopback calls add an extra localhost round trip (sub-millisecond; handlers still go through
  REST, not in-process calls).
- If core's direct-connect layer adds auth in the future, `/mcp` will break along with it
  (mitigation: Nginx-layer auth is unchanged; the direct-connect layer currently has no auth).

## Triggers (must not be extended until tripped)

> **None of the mechanisms above may be added until their trigger fires.** When a trigger fires,
> return to this ADR first, re-evaluate, and record a new ADR.

| Trigger | Threshold | Unlocks |
|---|---|---|
| T-1 Cross-worker state requirement | MCP state emerges that **must** persist across requests/workers (e.g. session-level cache) | Revert to `stateless_http=False` + sticky routing, or a separate process |
| T-2 Direct-connect auth | Core's direct-connect layer (loopback) gains auth | `/mcp` needs its own auth layer or must fall back to a tunnel |

**Current status as tested (2026-10-06)**: no cross-worker MCP state requirement; the
direct-connect layer has no auth → **neither trigger has fired**.
