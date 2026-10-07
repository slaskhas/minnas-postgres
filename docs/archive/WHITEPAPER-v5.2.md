# Mnemosyne OS v5.2 · Product Whitepaper

**Version**: v5.2.0
**Updated**: 2026-06-25
**Positioning**: Cognitive memory operating system · personal AI long-term memory infrastructure
**Site**: [GitHub](https://github.com/gymaira1990-jpg/Mnemosyne-OS)

---

## Implementation status

| Module | Status | Notes |
|------|:---:|------|
| PostgreSQL + pgvector + AGE | ✅ Running | GZ 7×24, 1024d vectors |
| TMT 5-tier memory distillation | ✅ Running | L1 fragment → L2 session → L3 daily → L4 weekly → L5 profile |
| Three-hall pipeline | ✅ Running | Research hall → Engineering hall → Archive hall |
| RAG smart chunking | ✅ Running | 330 memories → 765 chunks |
| AGE knowledge graph | ✅ Running | Cypher graph queries |
| Edge-cloud incremental sync | ✅ Running | WSL↔GZ, pushed every 10 min |
| Automatic session archival | ✅ Running | Hermes conversations → palace, every 30 min |
| Project memory binding | ✅ Running | 9 projects registered, automatic keyword tagging |
| Doubao + DeepSeek driven | ✅ Running | Model-swappable via environment variable |
| Qwen3 Reranker | ✅ Running | GZ:11436 local reranking |
| Docker deployment | 📝 Planned | |
| Clustering/sharding | 📝 Long-term | |
| Redis cache | 📝 Long-term | |
| Multimodal memory | 📝 Long-term | |

---

## 1. What this is

Mnemosyne OS is a memory system a cat built for its AI butler. It's not a bolt-on vector database — it's a memory OS that organizes, distills, and discovers patterns on its own.

After every conversation, the system automatically distills fragments into sessions → daily digests → weekly digests → a profile. Knowledge matures as it flows through the three halls. When offline, it caches locally and silently pushes back to the cloud once reconnected. Once a self-trained model is plugged in, this palace becomes that model's native memory cortex.

---

## 2. Core architecture

```
L5 Profile  — who you are, what you prefer
L4 Weekly   — what happened this week
L3 Daily    — today's takeaways
L2 Session  — the thread of one conversation
L1 Fragment — a specific memory

🏛️ Three-hall flow    🔍 5-dimension search    🔗 Knowledge graph    ✂️ RAG chunking    ☁️ Edge-cloud dual-active
```

### 2.1 TMT temporal memory tree

A 5-tier distillation pipeline organizing memory along the time dimension. Memories aren't flat — fragments naturally converge into sessions, sessions settle into daily digests, daily digests distill into weekly digests, eventually forming a user profile. A heat-decay mechanism lets unimportant memories cool off naturally while frequently-used memories surface automatically.

### 2.2 Three-hall pipeline

Knowledge flows like brewing wine: Research hall (unverified) → Engineering hall (lessons learned) → Archive hall (settled truth). Three gates ensure quality: an intake gate filters noise, a proposal gate validates feasibility, an archival gate verifies outcomes.

### 2.3 5-dimension search

Vector semantics + BM25 keywords + time decay + reliability + heat — five signals searched simultaneously. Chunk-level precision retrieval splits long memories into small pieces to find the most relevant fragment.

### 2.4 Knowledge graph

Apache AGE Cypher graph engine. Entities in memories (project names, tech names, people) are automatically extracted as graph nodes, with relationships forming a knowledge network. Not isolated memory cards, but a web.

### 2.5 Edge-cloud sync

When the WSL laptop is offline, memories are automatically cached to local SQLite. Once reconnected, they're silently pushed back to the GZ cloud every 10 minutes. GZ is the single source of truth; WSL is just an offline buffer.

---

## 3. Tech stack

| Component | Technology | Notes |
|------|------|------|
| Database | PostgreSQL 16 + pgvector 0.8 | 1024-dim vectors |
| Graph | Apache AGE 1.6.0 | Cypher graph queries |
| Embedding | Doubao Embedding-Vision | 1024d, swappable |
| LLM | Doubao Seed-2.0 + DeepSeek V4 | Tiered dispatch |
| Reranker | Qwen3-Embed 0.6B | GZ:11436, local |
| Framework | FastAPI + asyncpg | Python |
| Deployment | GZ Tencent Cloud 7×24 | systemd |

---

## 4. API overview

All endpoints are relative to `http://127.0.0.1:8010`.

### Memories
- `POST /api/v1/memories` — store a memory
- `POST /api/v1/memories/search` — 5-dimension search
- `POST /api/v1/memories/search-chunks` — chunk-level precision search
- `POST /api/v1/memories/chunk-all` — bulk RAG chunking
- `GET /api/v1/memories/chunks/stats` — chunk statistics

### TMT distillation
- `POST /api/v1/tmt/consolidate/session` — L1→L2
- `POST /api/v1/tmt/consolidate/daily` — L2→L3
- `POST /api/v1/tmt/consolidate/weekly` — L3→L4
- `POST /api/v1/tmt/consolidate/monthly` — L4→L5
- `GET /api/v1/tmt/tree/{user_id}` — view the memory tree

### Session archival
- `POST /api/v1/sessions/archive` — ingest a full conversation into the palace

### Projects
- `POST /api/v1/projects/register` — register a workspace project
- `GET /api/v1/projects/` — list projects
- `GET /api/v1/projects/by-name/{name}` — look up a project's memories

### Graph
- `POST /api/v1/graph/search` — knowledge graph search

---

## 5. SDK

```python
from integrations.sdk import MnemosyneHermesMemory
m = MnemosyneHermesMemory(endpoint="http://127.0.0.1:18010")
m.add("remember this", category="note")
m.get_relevant("how do I use that thing again")
```

Hermes Agent: `skill_view("mnemosyne-os-usage")`

---

## 6. Deployment

Currently running on GZ Tencent Cloud (Ubuntu 24.04, PostgreSQL 16), resident under systemd, 7×24.

```bash
# GZ startup
sudo systemctl start mnemosyne

# WSL sync (automatic via cron)
python3 sync/memory_gateway.py push

# Session archival (automatic via cron)
python3 scripts/archive_session.py --auto
```

Environment-variable driven — swapping models only requires editing `.env`:
```bash
EMBEDDING_MODEL=your-model
LLM_MODEL_LITE=your-model
LLM_MODEL_PRO=your-deep-model
```

---

## 7. Future

- [x] AGE knowledge graph
- [x] Three-hall pipeline
- [x] TMT 5-tier distillation
- [x] RAG smart chunking
- [x] Edge-cloud sync
- [x] Session archival
- [x] Project memory binding
- [ ] Self-trained model integration — the palace becomes a native memory cortex
- [ ] Multimodal memory — images, video, audio
- [ ] One-click Docker deployment
- [ ] Obsidian human-facing dashboard

---

*"Memory isn't meant to be stored — it's meant to be lived."*
🐾 G-CAT & Hermes Agent · MIT · 2026
