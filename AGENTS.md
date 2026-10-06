# Mnemosyne OS · Agent Handbook

> An operational manual for AI coding assistants. **Keep it lean**: details in `docs/`, the truth of capabilities in `openspec/specs/`, history in `CHANGELOG.md`.
> **Goal**: any agent (Hermes / Claude Code / Cursor / Codex) completes onboarding in 5 minutes.

**Positioning**: cognitive memory operating system —— a long-term memory palace for AI agents. **It is not** a vector database, **not** a RAG pipeline.
**Current version**: v8.1.0 (all test suites green locally, incl. new in-process `/mcp` mount) | production running v8.1.0 (re-verify self-reported version after deployment) | details in [PROJECT.md](PROJECT.md)

## How to run

```bash
pip install -r requirements.txt
pytest tests/          # all green; MCP bridge contract + in-process /mcp mount tests require the mcp SDK (auto-skip if absent)
```

## Integration contract (must read before touching handlers / integration code)

> v7.8.2 lesson: the three MCP bridge tools all returned 422 in production (`feedback` sent as a JSON body + `user_id` missing); the same kind of error on **response fields** produces no error at all. **Semantic equivalence ≠ usable**.

1. **Parameter positions follow the server**: endpoints that take query params (the `user_id` of `feedback`/`delete`/`restore`) sent as a JSON body → 422. `GET /api/v1/capabilities` is the single source of truth.
2. **Never guess response field names**: e.g. `heat-top` returns `heat_score` (not `heat`). Reading the wrong field **does not error** — it just silently falls back to the default value, which is more dangerous than an error.
3. **Integration changes must run the contract tests**: `tests/test_mcp_bridge_contract.py` (locks the outbound shape; verified proven-red before). Add cases for new endpoints the same way.
4. **Projections/caches are judged by final values**: consumer-side injected files (MEMORY.md etc.) are projections of server-side data — verification must be re-tested **after write-back**.
5. **If you can't stop generation, add insurance**: constraints (canonical shapes / idempotency / fixed anchors) + review (positive/negative comparison, contract tests) + final-value re-test.
6. **Two transports, one contract**: stdio (`__main__` entry) and the in-process `/mcp` streamable-HTTP mount (added v8.1, wired in `main.py`) share the *same* `_dispatch`/`_call`/`list_tools` handlers. Change one handler and the contract tests cover both. The mount self-skips when the `mcp` SDK is absent, so the REST API is never taken down.

## Development contributions

```bash
git clone https://github.com/gymaira1990-jpg/Mnemosyne-OS.git
```

- Commits: `feat:` / `fix:` / `docs:` / `chore:` / `release:`
- Behavior changes → write `openspec/changes/<name>/proposal.md` first; after completion, fold into `openspec/specs/` and archive
- Architecture trade-offs → add `docs/adr/NNNN-*.md`

**Red lines**
- ❌ Integration code changes must ship with contract tests (capabilities is the single source of truth; no guessing field names/parameter positions from memory)
- ❌ Never hardcode API Keys / real IPs / domains / passwords
- ❌ Privacy scan required before push (criterion: zero output)
- ❌ Scan/gate pattern changes must be **reproduced locally, step by step per the workflow** (criterion: old pattern hits → new pattern zero hits) **and** pass the pattern's bidirectional self-check (legal usage green / hardcoded red); local reproduction must include counter-evidence (planting a real sample must trip the red flag, then clean up) — the gate only runs after push, so local reproduction is the only pre-push evidence. What trips red must be "hardcoded values", not "keyword presence"
- ❌ Version number consistent in three places (VERSION / README badge / CHANGELOG)

## Documentation map

| Document | Contents |
|---|---|
| [PROJECT.md](PROJECT.md) | What it is / why / where it stands ← **read first when taking over** |
| [README.md](README.md) · [README_CN.md](README_CN.md) | Product overview (EN / CN) |
| [INSTALL.md](INSTALL.md) | Per-environment installation |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture overview |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | Integration (REST / SDK / Hermes Provider + hooks) |
| [docs/API.md](docs/API.md) | Endpoint cheat sheet (authoritative: `GET /api/v1/capabilities`) |
| [docs/ENV.md](docs/ENV.md) | Full environment variable table |
| [docs/AGENT-USAGE.md](docs/AGENT-USAGE.md) | Agent usage best practices |
| [openspec/specs/](openspec/specs/) | **Truth of capabilities** (what the system does now) — includes [memory-layers.md](openspec/specs/memory-layers.md) layering model |
| [openspec/changes/](openspec/changes/) | In-progress changes |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [docs/schema.sql](docs/schema.sql) · [docs/WHITEPAPER.md](docs/WHITEPAPER.md) | Database schema · Design philosophy |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
