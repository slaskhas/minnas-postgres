# API Endpoint Cheat Sheet

> Lifted out of `AGENTS.md`. **The complete and up-to-date endpoint list is the service's own self-description**: `GET /api/v1/capabilities`.
> Back to [AGENTS.md](../AGENTS.md)

## API Endpoint Cheat Sheet

### Core (memories)

| Endpoint | Method | Purpose |
|------|------|------|
| `/api/v1/echo` | GET | Health check + version |
| `/api/v1/memories` | GET | List memories (`?sort=created_at` timeline / `?sort=heat` heat ranking) |
| `/api/v1/memories` | POST | Store a memory (auto vectorization + entity extraction + conflict detection) |
| `/api/v1/memories/{id}` | GET | Single-record detail |
| `/api/v1/memories/{id}` | PUT | Update |
| `/api/v1/memories/{id}` | DELETE | Soft delete |
| `/api/v1/memories/{id}/restore` | POST | Restore a deleted record |
| `/api/v1/memories/search` | POST | Five-dimension search (vector + BM25 + time + trust + heat) |
| `/api/v1/memories/{id}/feedback` | POST | Feedback (positive/negative, affects reliability) |
| `/api/v1/memories/heat-top` | GET | Heat ranking |
| `/api/v1/memories/stats` | GET | Memory store statistics |
| `/api/v1/memories/tree` | GET | Memory tier tree |
| `/api/v1/memories/{id}/traces` | GET | Lifecycle trace |
| `/api/v1/memories/{id}/tiered` | GET | Three-tier read (L5/L3/L1) |

### Palace (v7 core)

| Endpoint | Method | Purpose |
|------|------|------|
| `/api/v1/palace/status` | GET | Palace status (archival rate / card count / category tree) |
| `/api/v1/palace/summon` | GET | Three-channel summon (call-by-name / guided / resonance) |
| `/api/v1/palace/archive` | POST | Categorized archival (auto-generates accession number + catalog card) |
| `/api/v1/palace/extract` | POST | Fact extraction (conversation → facts) |
| `/api/v1/palace/refine` | POST | Card refinement (LLM) |
| `/api/v1/palace/lifecycle` | POST | Lifecycle transition |
| `/api/v1/palace/pin` | POST | Pin (prevents decay) |

### Graph / Wiki / Beliefs

| Endpoint | Method | Purpose |
|------|------|------|
| `/api/v1/graph/search` | POST | Entity-linked memory retrieval (entities vector → memory_entities association) |
| `/api/v1/wiki` | GET/POST | Knowledge base page read/write |
| `/api/v1/wiki/search` | POST | Semantic search (vector + BM25 RRF fusion) |
| `/api/v1/wiki/by-source` | GET | Exact lookup by source path/URL |
| `/api/v1/beliefs` | POST | Create a belief (auto-merges confidence) |
| `/api/v1/beliefs/search` | POST | Semantic search over beliefs |
| `/api/v1/beliefs/{id}/evolve` | POST | Evolve a belief (add evidence / adjust confidence) |

### Sessions / Distillation / Ops

| Endpoint | Method | Purpose |
|------|------|------|
| `/api/v1/sessions/archive` | POST | Session archival (v7.8: message-list/sync endpoints removed; raw text goes through Hermes state.db) |
| `/api/v1/reflect` | POST | TMT reflection (`?mode=light` heat decay / `?mode=deep` LLM distillation) |
| `/api/v1/extract-entities` | POST | Batch entity extraction into the graph |
| `/api/v1/health/{user_id}` | GET | Per-user health report |
| `/api/v1/capabilities` | GET | Full capability list (self-describing) |
| `/api/v1/dialectic` | POST | Dialectical search (with context) |

> 📋 Full endpoint list and parameters: `GET /api/v1/capabilities` (the service's own self-description, always current).

---
