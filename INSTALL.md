# Mnemosyne OS · Installation Guide

> The complete guide from zero to running. Three integration options — pick what you need.
> Version: v8.0.0 | Updated: 2026-09-25

---

## Table of Contents

- [Three Integration Options](#three-integration-options)
- [Prerequisites](#prerequisites)
- [Option 1: Hermes Agent One-Click Onboarding (Fastest)](#option-1-hermes-agent-one-click-onboarding-fastest)
- [Option 2: Standalone Deployment (Full Version)](#option-2-standalone-deployment-full-version)
  - [1. Prepare PostgreSQL](#1-prepare-postgresql)
  - [2. Import Database Schema](#2-import-database-schema)
  - [3. Configure Model Backend](#3-configure-model-backend)
  - [4. Install Python Dependencies](#4-install-python-dependencies)
  - [5. Start the Service](#5-start-the-service)
  - [6. Verify Installation](#6-verify-installation)
- [Option 3: Python SDK Integration](#option-3-python-sdk-integration)
- [Per-Environment Notes](#per-environment-notes)
  - [Ubuntu/Debian](#ubuntudebian)
  - [macOS](#macos)
  - [Windows (WSL2)](#windows-wsl2)
- [FAQ](#faq)
- [Next Steps](#next-steps)

---

## Three Integration Options

| Option | Best for | Time | Notes |
|--------|----------|------|-------|
| ① Hermes one-click | Hermes Agent users | ~2 min | Set one config, tools appear automatically |
| ② Standalone deploy | Self-hosting / other agent frameworks | ~15 min | Full service, REST API + SDK |
| ③ Python SDK | If you want to call it from your own code | Depends on ② | One import line on top of the standalone deployment |

> ⚠️ All three options depend on a **PostgreSQL 16 database** (with the pgvector extension) and at least one **LLM/Embedding API** (Volcano Engine ARK / DeepSeek / any OpenAI-compatible endpoint).

---

## Prerequisites

| Component | Requirement |
|-----------|-------------|
| OS | Linux / macOS / Windows (WSL2) |
| Python | 3.11+ |
| PostgreSQL | 16.x (with pgvector ≥ 0.7) |
| Memory | 8GB+ (recommended) |
| Model API | Pick one: Volcano Engine ARK (Doubao), DeepSeek, or an OpenAI-compatible endpoint |
| Disk | 2GB+ (not including database growth) |

---

## Option 1: Hermes Agent One-Click Onboarding (Fastest)

If you already use [Hermes Agent](https://hermes-agent.nousresearch.com/docs), the Mnemosyne Memory Provider ships built into Hermes:

```bash
# 1. Configure mnemosyne as the memory provider
hermes config set memory.provider mnemosyne

# 2. Restart Hermes (or /reset); the tools appear automatically:
#    mnemosyne_palace_summon · mnemosyne_search · mnemosyne_recall
#    mnemosyne_remember    · mnemosyne_dialectic · mnemosyne_hot_memories
#    mnemosyne_wiki        · mnemosyne_tree      · mnemosyne_media
```

> Prerequisite: you already have an accessible Mnemosyne service (local port 18010 or remote). Don't have one? Deploy the service itself first via [Option 2](#option-2-standalone-deployment-full-version).

**Remote services (e.g., deployed on a server)** are accessed through an SSH tunnel:

```bash
ssh -L 18010:127.0.0.1:8010 your-server
# Afterward, Hermes' mnemosyne tools connect through 127.0.0.1:18010 automatically
```

---

## Option 2: Standalone Deployment (Full Version)

### 1. Prepare PostgreSQL

#### Ubuntu / Debian 24.04+

```bash
# Install PostgreSQL 16 + pgvector extension
sudo apt install postgresql-16 postgresql-16-pgvector

# Start the service
sudo systemctl enable --now postgresql

# Create the database and user
sudo -u postgres psql <<'SQL'
CREATE USER mnemosyne WITH PASSWORD 'your-strong-password';
CREATE DATABASE mnemosyne OWNER mnemosyne;
SQL
```

#### macOS (Homebrew)

```bash
brew install postgresql@16
brew install pgvector
```

#### Windows (WSL2)

```bash
# WSL2 is just Ubuntu inside — follow the Ubuntu steps as-is
```

### 2. Import Database Schema

```bash
git clone https://github.com/gymaira1990-jpg/Mnemosyne-OS.git
cd Mnemosyne-OS

# Import the full table structure (21 tables, including palaces / knowledge base)
# ⚠️ Must run as a database superuser (e.g., postgres): CREATE EXTENSION requires elevated privileges
# schema.sql ships with CREATE EXTENSION (pg_trgm + vector); the extensions are created automatically on import
sudo -u postgres psql -d mnemosyne -f docs/schema.sql

# After import, hand the permissions over to the application user
sudo -u postgres psql -d mnemosyne -c "GRANT ALL ON ALL TABLES IN SCHEMA public, mnemosyne TO mnemosyne;"
sudo -u postgres psql -d mnemosyne -c "GRANT ALL ON ALL SEQUENCES IN SCHEMA public, mnemosyne TO mnemosyne;"

# Verify the table structure
PGPASSWORD=your-strong-password psql -h 127.0.0.1 -U mnemosyne -d mnemosyne -c "\dt"
```

> schema.sql is structure only (zero data) and is safe to import. Starting the service for the first time also creates the palace-related tables automatically (idempotent).

### 3. Configure Model Backend

Copy the environment template and edit:

```bash
cp .env.template .env
```

**Minimal config (recommended combo: DeepSeek main inference + Doubao embeddings)**:

```dotenv
# ── Required: at least one model backend ──
ARK_API_KEY=<your Volcano Engine ARK key>        # Doubao: embeddings + everyday LLM (recommended)
DEEPSEEK_API_KEY=<your DeepSeek key>      # DeepSeek: distillation / audit (optional but recommended)

# ── PostgreSQL ──
PGUSER=mnemosyne
PGPASSWORD=your-strong-password
PGDATABASE=mnemosyne
PGHOST=127.0.0.1
PGPORT=5432
```

**OpenAI-compatible backend only** (OpenAI / local vLLM / any compatible endpoint):

```dotenv
# Turn off Doubao, enable OpenAI-compatible
MODEL_BACKEND=openai
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1   # or local vLLM: http://localhost:8000/v1
OPENAI_EMBED_MODEL=text-embedding-3-small
OPENAI_CHAT_MINI=gpt-4o-mini
```

> Models are pluggable: swapping models means only changing environment variables — zero code changes. See [.env.template](.env.template) for the full field list.

### 4. Install Python Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 5. Start the Service

```bash
python main.py
# → FastAPI service listening on http://127.0.0.1:8010
```

> Port and bind address are controlled by `MNEMOSYNE_PORT` / `MNEMOSYNE_HOST` (defaults `8010` / `127.0.0.1`); effective since v7.8.3 (carried into v8.0.0). No code changes needed.

> For production, use systemd or a process manager. See [deploy/mnemosyne.service](deploy/mnemosyne.service) (uvicorn, two workers).

### 6. Verify Installation

```bash
# Health check
curl http://127.0.0.1:8010/api/v1/echo
# → {"status":"ok","service":"Mnemosyne OS","version":"8.1.0"}

# Store a memory
curl -X POST http://127.0.0.1:8010/api/v1/memories \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"default","content":"My first test memory","category":"knowledge"}'

# Search for it
curl -X POST http://127.0.0.1:8010/api/v1/memories/search \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"default","query":"test memory","top_k":3}'

# Three-channel summoning (the palace's core ability)
curl "http://127.0.0.1:8010/api/v1/palace/summon?q=test&user_id=default&top_k=3"

# MCP in-process endpoint (/mcp, streamable HTTP — v8.1; stdio also still available)
curl -i -N -X POST http://127.0.0.1:8010/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}'
# → 200; SSE data contains "server":{"name":"mnemosyne"}
```

---

## Option 3: Python SDK Integration

On top of the service deployed via Option 2, any Python application can integrate:

```python
from integrations.sdk import MnemosyneHermesMemory

m = MnemosyneHermesMemory(endpoint="http://127.0.0.1:8010")

# Store
m.add("pgvector HNSW beats IVFFlat on high-dim recall")

# Retrieve (5-D retrieval: vector + BM25 + time + trust + heat)
results = m.get_relevant("which pgvector index is better?")

# Query by hall
m.search_by_hall("archive")       # verified knowledge
m.search_by_hall("engineering")   # pitfall log
m.search_by_hall("research")      # unverified
```

---

## Per-Environment Notes

### Ubuntu/Debian

✅ The least-hassle path (this guide's test environment): one apt command installs PG16+pgvector.

```bash
sudo apt install postgresql-16 postgresql-16-pgvector
```

### macOS

- PostgreSQL 16 + pgvector: install directly via Homebrew.

### Windows (WSL2)

- Recommend WSL2 + Ubuntu, following the Ubuntu steps exactly.
- Running PostgreSQL natively on Windows requires manually installing extensions; not recommended.

---

## FAQ

**Q: Startup reports `column ... does not exist`?**
A: The database schema hasn't been imported. Make sure you ran `psql -f docs/schema.sql` and that the version matches the repo.

**Q: Search returns embedding-related errors?**
A: The model API key is not configured or invalid. Check `ARK_API_KEY` / `OPENAI_API_KEY` in `.env` and verify with `curl` that the endpoint is reachable.

**Q: Can I use a purely local model?**
A: Yes — any OpenAI-compatible endpoint works (e.g., vLLM / Ollama's `/v1` API). Set `MODEL_BACKEND=openai` + `OPENAI_BASE_URL=http://localhost:8000/v1`.

**Q: Multi-user support?**
A: Isolated natively via the `user_id` field (e.g., `user_id=alice` / `user_id=bob`); memories are invisible to each other.

**Q: Data backup?**
A: Standard `pg_dump` works: `pg_dump -U mnemosyne -d mnemosyne -Fc > backup.dump`.

---

## Next Steps

- 📖 [README.md](README.md) — Product overview and design philosophy
- 🤖 [AGENTS.md](AGENTS.md) — AI Agent handbook (endpoint quick reference / environment variables / Hermes config)
- 🏰 [docs/palace-architecture.md](docs/palace-architecture.md) — Design of the magical memory palace
- 📜 [docs/WHITEPAPER.md](docs/WHITEPAPER.md) — Product whitepaper
- 📚 [docs/schema.sql](docs/schema.sql) — Full database schema

---

*Mnemosyne OS · MIT License · Build thinking agents with memory*
