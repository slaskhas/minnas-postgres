# Architecture Overview

> Lifted out of `AGENTS.md`. Detailed design: see [WHITEPAPER.md](WHITEPAPER.md) and [palace-architecture.md](palace-architecture.md).
> Back to [AGENTS.md](../AGENTS.md)

## Architecture Overview

```
Mnemosyne OS (FastAPI, 50+ endpoints)
  ├── main.py            Service entry point + core routes (memories/search/palace/wiki/...)
  ├── palace.py          🏰 Palace core (categorization/accession numbers/cards/summon/lifecycle)
  ├── core/              LLM / Embedding / Chunker engines
  ├── api/               REST API modules
  ├── tmt/               Distillation engine (factextract/distill)
  ├── wiki/              WIKI knowledge base module (BM25/graph/extraction/eval)
  ├── security/          Audit and purification
  ├── integrations/      Hermes integration (Memory Provider + MCP)
  │   └── hermes-provider/  Memory Provider v7 (11 tools, palace_summon)
  ├── sync/              Edge-cloud sync (SQLite ↔ PostgreSQL)
  └── docs/              Whitepaper + palace design + schema

Data layer: PostgreSQL 16 + pgvector 1536d (HNSW) (v7.8: Apache AGE graph removed — entity association now goes through the entities/memory_entities/wiki_entities tables)
Model layer: embedding is fixed to an OpenAI-compatible endpoint (1536d); chat/LLM is pluggable — Doubao ARK / DeepSeek / any OpenAI-compatible endpoint
```

---
