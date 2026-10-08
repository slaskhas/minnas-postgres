

# Minnas

> Fork of [Mnemosyne OS](https://github.com/gymaira1990-jpg/Mnemosyne-OS) — heavily altered.
> This project was developed and documented as "Mnemosyne OS" through 2026-10-08; it has since
> been renamed **Minnas**. Historical sections below (e.g. the Version History table) retain the
> old name where it's part of the accurate historical record.

---

## The Problem

Every AI agent today suffers from the same amnesia: conversations reset, context windows overflow, important decisions vanish into scrollback. We duct-tape solutions — vector DBs for retrieval, RAG for injection, prompt stuffing for continuity — but none of them **understand** memory. They store bytes. They don't know what matters and what doesn't.



---

## How It Works

Every conversation end triggers the distillation pipeline:

```
Dialogue flows through the palace pipeline automatically:

```
Conversation (Hermes)
     │
     ▼  state.db (lossless raw, Hermes native)
     │
     ▼  sync_turn → session memories (2,000/3,000 chars, lossless-ish)
     │
     ▼  🕵️ Fact extraction (DeepSeek) → structured facts
     │
     ▼  🏛️ Archive: classify (7 wings × 20 rooms) → archive-no → tome card
     │
     ▼  📚 Library: 3-channel summon (name / guide / resonate)
     │
     └  🍵 Medicine cabinet: high-frequency facts stay hot
```

Every step is **LLM-driven** — not templated. The same pipeline handles agent delegation events, memory writes, and context compression hooks.

---

## What Sets It Apart

| Feature | Minnas | Chroma/Pinecone | Mem0 |
|---|---|---|---|
| 🏰 Palace taxonomy (7 wings × 20 rooms) | ✅ | ❌ | ❌ |
| Archive-no system (number = position) | ✅ | ❌ | ❌ |
| Tome cards (standardized description) | ✅ | ❌ | ❌ |
| 3-channel summon (name/guide/resonate) | ✅ | ❌ | ❌ |
| Fact extraction (dialogue→facts) | ✅ LLM pipeline | ❌ | ✅ |
| Vector search (1536d HNSW) | ✅ | ✅ | ✅ |
| Full-text (BM25 + ILIKE) | ✅ | ❌ | ❌ |
| Retention tiers (permanent/long/short) | ✅ | ❌ | ❌ |
| Entity graph (table-based) | ✅ entities + memory_entities | ❌ | ❌ |
| Conversation history (lossless) | ✅ state.db → PG | ❌ | ❌ |
| Edge-cloud sync | ✅ SQLite ↔ PG | ❌ | ❌ |
| Agent-native hooks | ✅ 14 tools | ❌ | Limited |

### 🏰 Magic Memory Palace

Memory is organized like a real palace — inspired by library classification (Dewey Decimal), archive description standards (DA/T18), and the Chinese medicine cabinet (position registry). These systems served humanity for centuries without computers; Minnas brings them to AI.

```
Lobby       → high-frequency memories (always-injected)
Wing        → K knowledge · N network · D dev · O ops · A assets · P people · I ideas
Room        → 20 mid categories (proxy / deploy / secret / model / …)
Shelf       → sub-topic
Tome        → individual memory: description card + archive-no + content pointer
Vault       → raw conversations, lossless (Hermes state.db)
```

Every memory gets an **archive number** — `K·NET·PROXY·2026-0007` — so "number = position", exactly like a library call number. No more dumping everything into a flat vector pile.

### 🪄 Three-Channel Summon

Knowledge comes when you call it — three channels, each with a job:

| Channel | Mechanism | Latency |
|---|---|---|
| ① **Name** (exact) | archive-no / title / tag direct hit | <100ms |
| ② **Guide** (range) | taxonomy wing/room narrowing | ~200ms |
| ③ **Resonate** (fuzzy) | vector search (pgvector HNSW) | ~300ms |

```bash
# Summon: exact + guided + fuzzy, one call
curl "http://:8010/api/v1/palace/summon?q=xray&user_id=default&top_k=5"
```

### 🕵️ Three-Chamber Division

| Chamber | Role | Implementation |
|---|---|---|
| 🕵️ Research room | dialogue → structured facts | `/palace/extract` |
| 🏛️ Archive | classify + describe | `tome_cards` + archive-no |
| 📚 Library | retrieval | `/palace/summon` |
| 🍵 Medicine cabinet | high-frequency fast access | taxonomy guide + archive-no |

Conversation fragments (88% → 27% of storage) become **10,798 knowledge entries** — searchable, classifiable, referenceable knowledge instead of raw dialogue noise.

### ⏳ Retention Tiers

Not all memories live forever. Lifecycle-aware decay:

| Tier | Decay | Cleanup |
|---|---|---|
| permanent | never | rules / identity / red lines |
| long | 0.999 (very slow) | knowledge / projects |
| short | fast | auto-removed after 90 days |

### 💬 Permanent Conversation History

Hermes `state.db` (SQLite) syncs to PostgreSQL on every session end. Full exchanges — user, assistant, tool calls, reasoning — preserved with timestamps. The vault under the palace: raw truth, lossless.

### 🔌 Agent-Native Integration

**Memory Provider** (14 tools) — automatic, no manual `remember()` calls:

```
mnemosyne_palace_summon  → 3-channel summon (the magic front desk)
mnemosyne_search         · mnemosyne_recall      · mnemosyne_hot_memories
mnemosyne_remember       · mnemosyne_dialectic   · mnemosyne_wiki
mnemosyne_media          · session_search        · mnemosyne_tree
mnemosyne_skill_search   · mnemosyne_skill_wakeup · mnemosyne_injection_plan  (v7.7.0)
```

**WIKI knowledge base** (v7.4+, full-text snapshot archive for papers/plans):

```
mnemosyne_wiki search      → semantic search (vector HNSW + BM25 keywords, RRF fusion, default on)
mnemosyne_wiki by_source   → exact lookup by source path/URL (snapshot survives source loss)
mnemosyne_wiki get/list    → read full text by ID / list pages
Optional: rerank=true (doubao rerank, high-precision), graph=true (1-hop KG expansion, default off)
Eval (20 queries, v7.5): precision@3 100% / recall@3 98.3% / MRR 1.0
```

**🚀 Injection Scheduler** (v7.7.0) — procedural skill wing + context-aware injection:

```
POST /api/v1/skills/sync     → idempotent skill asset sync (state machine aligned with Hermes curator)
POST /api/v1/skills/search   → semantic skill summon (incl. dormant/archived, wakeable)
PATCH /api/v1/skills/{name}  → state transition (wake up / demote) — never deleted, only flowed
POST /api/v1/injection/plan  → scene-aware injection flow {skills + memories + hooks}
```
Skills are *procedural memory*: same palace philosophy as declarative memory (never DELETE, only
state-flow active → stale → archived → wake). Embedding layer optimized: concurrent calls +
standard LRU cache + exponential-backoff retry (7.7x faster cold batches, ~0ms cache hits).

```text
on_session_end   → sync + fact extraction     on_turn_start    → prefetch
on_pre_compress  → inject before compression  on_delegation    → log subtasks
on_memory_write  → mirror to Minnas           on_session_switch → flush queue
```

### ☁️ Edge-Cloud Resilience

WSL offline? Local SQLite cache. Back online? Silent push to PostgreSQL. Cron jobs maintain heat decay, dedup, fact extraction, session consolidation, and offline sync.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│            Minnas v7.0 · Magic Memory Palace          │
│                                                        │
│  FastAPI (50+ endpoints)                               │
│  ├── /api/v1/palace/*        🏰 Palace (core)         │
│  │   ├── status             palace state (coverage)   │
│  │   ├── summon             3-channel (name/guide/rs)  │
│  │   ├── archive            classify + archive-no     │
│  │   ├── extract            fact extraction pipeline  │
│  │   ├── refine             LLM card refinement       │
│  │   └── lifecycle          retention tiers           │
│  ├── /api/v1/memories       CRUD + search (legacy)    │
│  ├── /api/v1/sessions       Conversation history      │
│  ├── /api/v1/wiki           Knowledge base            │
│  └── /api/v1/echo           Health check              │
│                                                        │
│  PostgreSQL 16 · pgvector 1536d (HNSW)                │
│  Entity graph (entities + memory_entities tables)                     │
│  asyncpg connection pool                               │
│                                                        │
│  🏰 Palace data model                                   │
│  archive_taxonomy (7 wings × 20 rooms)                 │
│  tome_cards (description cards) + entities               │
│  memories.archive_no (K·NET·PROXY·2026-0007)          │
│                                                        │
│  Fact pipeline (dual LLM: DeepSeek / Doubao)           │
│  dialogue → facts → classify → archive-no → tome card  │
│                                                        │
│  Integrations                                          │
│  ├── Hermes Memory Provider (14 tools)                 │
│  ├── Hermes auto-extract (on_session_end)              │
│  └── Python SDK                                        │
└──────────────────────────────────────────────────────┘
```

---

## Quick Start

> **Start here** — pick your path:

| You want to… | Do this | Time |
|---|---|---|
| 🤖 Give your Hermes Agent long-term memory | `hermes config set memory.provider mnemosyne` | ~2 min |
| 🐍 Call Minnas from your own code | Install + Python SDK | ~15 min |
| 🏠 Self-host the full service | Follow [INSTALL.md](INSTALL.md) | ~15 min |

### Prerequisites

- Python 3.11+ · PostgreSQL 16 + pgvector
- 8GB+ RAM · Any OpenAI-compatible embedding/LLM backend

### Hermes Agent (one command)

The Minnas Memory Provider ships with Hermes — tools appear after `/reset`:

```bash
hermes config set memory.provider mnemosyne
# Tools: mnemosyne_palace_summon · mnemosyne_search · mnemosyne_recall · …
```

No other configuration needed.

### Standalone

> Full step-by-step guide: **[INSTALL.md](INSTALL.md)** (database setup, permissions, model backends, FAQ).

```bash
git clone https://github.com/slaskhas/minnas-postgres.git
cd minnas-postgres
pip install -r requirements.txt

# 1. PostgreSQL 16 + pgvector (Ubuntu example):
#    sudo apt install postgresql-16 postgresql-16-age postgresql-16-pgvector
# 2. Import schema (superuser required for CREATE EXTENSION):
#    sudo -u postgres psql -d mnemosyne -f docs/schema.sql
# 3. Configure model backend in .env (ARK / DeepSeek / any OpenAI-compatible):
cp .env.template .env   # fill in ARK_API_KEY (or OPENAI_API_KEY + MODEL_BACKEND=openai)

python main.py  # → :8010
```

### Python SDK

```python
from integrations.sdk import MinnasHermesMemory
m = MinnasHermesMemory(endpoint="http://127.0.0.1:18010")

m.add("pgvector HNSW outperforms IVFFlat for high-dimensional recall")
results = m.get_relevant("which pgvector index is better?")
# → top-5 with per-dimension score breakdown
```

---

## Performance

Single user + 5 agent workers, 7×24 on a modest cloud instance:

| Metric | Value |
|---|---|
| Memories archived | 16,500+ active |
| Tome cards | see `GET /api/v1/health/<user>` |
| Taxonomy | 7 wings × 20 rooms (30 nodes) |
| Summon latency | ~100-400ms (3-channel) |
| Embedding | 1536d OpenAI-compatible (`text-embedding-3-small` default) |
| Fact extraction LLM | DeepSeek V4 (dual-base: DeepSeek + Doubao) |

**Model-agnostic**: any OpenAI-compatible endpoint. Swap `EMBED_MODEL` / `LLM_MODEL_LITE` / `LLM_MODEL_PRO` env vars — zero code changes.

---

## Documentation

| | |
|---|---|
| [INSTALL.md](INSTALL.md) | Step-by-step installation guide (Linux / macOS / WSL) |
| [AGENTS.md](AGENTS.md) | AI agent manual — API reference, env vars, Hermes/MCP setup |
| [ROADMAP.md](ROADMAP.md) | Current priorities & next steps |
| [CHANGELOG.md](CHANGELOG.md) | Full version history |
| [docs/WHITEPAPER.md](docs/WHITEPAPER.md) | v7.0 product whitepaper — palace architecture |
| [docs/palace-architecture.md](docs/palace-architecture.md) | Magic Memory Palace detailed design |
| [docs/schema.sql](docs/schema.sql) | Full database schema (incl. palace tables) |
| [docs/design/](docs/design/) | Per-version design docs (v6.2-v6.4) |
| [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md) | Dev workflow, commit conventions |
| [.github/SECURITY.md](.github/SECURITY.md) | Vulnerability reporting |

---

## Version History

| Version | Date | Ships |
|---|---|---|
| [v7.8.4](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.8.4) | 2026-09-24 | 🩹 Fix: archive quality — final report & user messages kept whole (no more 2000-char chop), tool-call evidence signatures, short sessions no longer dropped; ➕ report cards (`category=worklog`) + 23 contract tests |
| [v7.8.3](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.8.3) | 2026-09-12 | 🔧 Fix: MNEMOSYNE_PORT / MNEMOSYNE_HOST env vars now effective (server entry no longer hardcodes 127.0.0.1:8010) + docs |
| [v7.8.2](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.8.2) | 2026-09-12 | 🩹 Fix: MCP bridge contract (feedback/delete/restore 422 → query params) + capabilities self-description + bridge contract tests |
| [v7.8.1](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.8.1) | 2026-08-24 | 🩹 Fix: write-time tokenization (no 24h BM25 blindness), MCP 2.0 adapter, prod↔repo sync |
| [v7.8.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.8.0) | 2026-08-18 | 🧹 Slim + AGE removal: cut 8 dead assets (memory_chunks/pointer/conversation_messages/halls/tools/projects/response/tome_links), real BM25, dedup fix |
| [v7.7.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.7.0) | 2026-08-18 | 🚀 Injection Scheduler: procedural skill wing (skill_assets) + /injection/plan + embedding optimization (7.7x faster) |
| [v7.6.2](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.6.2) | 2026-08-15 | 🛡 project_id type contract fix + knowledge never frozen (cool floor) |
| [v7.2.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.2.0) | 2026-08-09 | 🧠 Bjork S/R dual strength + prod tuning (pg_stat_statements/workers/perf alerts) |
| [v7.1.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.1.0) | 2026-08-09 | 🗄️ Drawerized memory: temp×time dual-track + forget candidates + update endpoint + drawers API |
| [v7.0.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v7.0.0) | 2026-08-06 | 🏰 Magic Memory Palace: taxonomy + archive-no + tome cards + 3-channel summon + fact extraction + retention tiers |
| [v6.4.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v6.4.0) | 2026-08-05 | Fact extraction: dialogue → personal facts (preference/knowledge) |
| [v6.3.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v6.3.0) | 2026-08-05 | Cognitive write signals: importance-boosted initial heat · protected decay |
| [v6.2.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v6.2.0) | 2026-08-05 | Cognitive heat engine: hit-heating · differential decay · distill heat |
| [v6.0.1](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v6.0.1) | 2026-08-02 | Production perf: uvicorn workers=2 · recall resilience |
| [v6.0.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v6.0.0) | 2026-08-02 | Concept model refactor · TMT pipeline fix · reflector 400x |
| [v5.5.2](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v5.5.2) | 2026-07-29 | NULL embedding search fix |
| [v5.5.1](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v5.5.1) | 2026-07-23 | TMT distillation fix · JSON parsing hardening |
| [v5.5.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v5.5.0) | 2026-07-23 | Temporal validity · 39 tests |
| [v5.4.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v5.4.0) | 2026-07-23 | Hall gate audit · suggestion API · pytest 18 cases |
| [v5.3.1](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v5.3.1) | 2026-07-16 | Time-ordered search · dual-axis retrieval · `sort=created_at` |
| [v5.3.0](https://github.com/gymaira1990-jpg/Mnemosyne-OS/releases/tag/v5.3.0) | 2026-07-06 | Repo governance · 10-hook Provider · 15-tool MCP |
| v5.2.3 | 2026-07-06 | Downtime alerts · MCP reconnect · L3 distillation |
| v5.2.2 | 2026-06-27 | Full Doubao migration · zero local-model |
| v5.2.1 | 2026-06-27 | Model-agnostic config · env-var backend |
| v5.0.0 | 2026-06-24 | First 7×24 deployment |

[Full changelog →](CHANGELOG.md)

---

<p align="center">
  <i>"Memory isn't for storing. It's for living."</i><br><br>
  🐾 <b>G-CAT</b> & <b>Hermes Agent</b> · MIT License · 2026
</p>
