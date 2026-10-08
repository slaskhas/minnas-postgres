# Minnas

> Fork of [Mnemosyne OS](https://github.com/gymaira1990-jpg/Mnemosyne-OS), heavily altered.

> Project charter — read this first when taking over any session; in 3 minutes you'll know what it is, why it exists, and where it stands.

## 1. One-Sentence Positioning

**A long-term memory operating system for AI agents**: it captures from conversations whatever is worth keeping, distills it into knowledge, files it under the "palace" system, and recalls via multiple channels (semantic / keyword / time / trust / heat) when needed — so the agent remembers, across sessions, who you are, what it has done, and why it made the decision.

**Not** a vector database, **not** a RAG pipeline (this is the easiest thing to misunderstand: it does **capture → distill → age → forget → surface** on its own).

## 2. Why It Exists

General LLMs have no cross-session memory: every conversation starts from zero, user preferences, pitfalls stepped on, project decisions — all lost. The mainstream solutions (RAG / vector stores / simple key-value memory) only solve "storing and fetching"; they don't solve **memory governance**:

- Garbage stored in is never forgotten → the store only grows and never shrinks, and recall is drowned in noise
- No heat / timeliness concept → a transient state from three months ago and today's core decision carry the same weight
- No distillation → raw fragments pile up, never forming knowledge
- No structure → you can't find or explain "why the system is the way it is"

Minnas turns this "memory lifecycle governance" into a system: classification tree + accession numbers + catalog cards, three-channel summoning, cognitive heat and permanence tiers.

## 3. Scope Boundaries

- ✅ **Do**: memory capture & distillation · palace classification & filing (nine wings / rooms / accession numbers / catalog cards) · multi-channel recall (vector+BM25+time+trust+heat) · three-channel summoning (named / guided / resonant) · lifecycle (heat decay / forgetting / permanent protection) · end-cloud dual-live buffer · multi-agent integration (REST / SDK / Hermes Memory Provider / MCP)
- ❌ **Do not**: GUI client · general-purpose RAG framework · multi-tenant SaaS · in-house vector index (use pgvector/HNSW) · storing code and binaries (that's git's job)
  > Guard the boundaries strictly: unbounded feature bloat = the start of a big mess.

## 4. Current Status & Roadmap (Living)

| Version | Status | Content | Date |
|---|---|---|---|
| v8.0.0 | ✅ Implemented, awaiting deployment | **Memory Palace OS 8.0**: write right · recover back · find precisely · clarify — write atomicity + idempotency key / memory GC / integrity inspection / observability / four-channel RRF fusion / executable spec for the layered model / release pipeline state machine → `openspec/changes/2026-09-25-v8-memory-os/` | 2026-09-25 |
| v7.8.4 | ✅ Released | Archive quality: wrap-up reports / user messages no longer truncated + tool evidence signatures + short sessions no longer discarded + report cards (worklog) | 2026-09-24 |
| v7.8.3 | ✅ Production | Service port / bind-address environment variables actually take effect | 2026-09-12 |
| v7.8.2 | ✅ Released | MCP bridge contract fixes back into repo + capabilities self-description alignment | 2026-09-12 |
| v7.8.1 | ✅ Released | Same-day blindness fix (tokenize on write) | 2026-08-24 |
| v7.8.0 | ✅ Released | Precise defusing + architecture slimming (Apache AGE graph removal) | 2026-08-18 |

> Only the last 5 rows are kept; see [CHANGELOG.md](CHANGELOG.md) for the full history.

## 5. Key Decisions Index

| Date | Decision | Record |
|---|---|---|
| 2026-09-24 | Adopt G-CAT project governance standard (AGENTS.md slimming + openspec living specs + ADR) | [ADR-0001](docs/adr/0001-adopt-project-governance-standard.md) |
| 2026-09-25 | Filesystem mechanism trade-offs: only do the 4 items PostgreSQL doesn't provide that we genuinely need; the remaining 17 dimensions go into triggers (>5M rows / body >4KB / true multi-tenancy / accession-number collision / deletion incident) | [ADR-0002](docs/adr/0002-filesystem-mechanism-tradeoffs-and-triggers.md) |

## 6. Core Asset Locations

| Thing | Where |
|---|---|
| This fork | https://github.com/slaskhas/minnas-postgres |
| Upstream origin | https://github.com/gymaira1990-jpg/Mnemosyne-OS |
| Production deployment | GZ server `/opt/mnemosyne` (see `DEPLOY` notes; production git is frozen — do not pull) |
| Capabilities truth | `openspec/specs/` |
| Data layer | PostgreSQL 16 + pgvector (1536d HNSW) |
| Documentation | `docs/` |
| Design philosophy | `docs/WHITEPAPER.md` · `docs/palace-architecture.md` |

## 7. Current Scale (measured 2026-09-24)

- **16,595** memories (user_id: default dominant + a few independent partitions), **697** soft-deleted
- Production backup daily 03:00 (8 copies retained) + HK offsite replica (3 copies retained) — the backup chain once silently broke for 36 days; fixed 2026-09-23; from v8.0, **recoverability re-verification** was added
- **Measured drift now zero**: local = GitHub = production (after v8.0.0 deployment)

### Capabilities Truth Index (`openspec/specs/`)

| Spec | Content |
|---|---|
| [memory-layers.md](openspec/specs/memory-layers.md) | Memory layering model (L0-L4 + cross-cutting artifact index, three-family conflict strategies) — executable spec, write path wired in |
