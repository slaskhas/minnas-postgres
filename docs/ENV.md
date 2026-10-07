# Full Environment Variable Table

> Lifted out of `AGENTS.md`.
> Back to [AGENTS.md](../AGENTS.md)

## Full Environment Variable Table

Copy `.env.template` to `.env` and fill it in. All configuration is injected via environment variables — **zero code changes**.

| Variable | Required | Default | Description |
|------|:---:|------|------|
| `ARK_API_KEY` | Recommended | - | Volcano Engine ARK (Doubao): everyday chat LLM (not used for embedding, see below) |
| `DEEPSEEK_API_KEY` | Recommended | - | DeepSeek: distillation/audit (dual-backend setup) |
| `MODEL_BACKEND` | No | `ark` | `ark` \| `openai` (switches the OpenAI-compatible endpoint) |
| `OPENAI_API_KEY` | Yes | - | Required for embedding (embedding is fixed to an OpenAI-compatible endpoint, independent of `MODEL_BACKEND`) |
| `OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | Local vLLM: `http://localhost:8000/v1` |
| `OPENAI_EMBED_MODEL` | No | `text-embedding-3-small` | Embedding model |
| `OPENAI_CHAT_MINI` | No | `gpt-4o-mini` | Lightweight chat |
| `OPENAI_CHAT_LITE` | No | `gpt-4o` | Primary chat |
| `EMBED_DIM` | No | `1536` | Vector dimension; must match both the target table's `vector(N)` column width and the chosen embedding model's **actual native output dimension** (a mismatch is caught by dimension validation, see below) |
| `PGUSER` | Yes | `postgres` | Database user |
| `PGPASSWORD` | Yes | - | Database password |
| `PGDATABASE` | Yes | `mnemosyne` | Database name |
| `PGHOST` | No | `127.0.0.1` | Database host |
| `PGPORT` | No | `5432` | Database port |
| `MNEMOSYNE_HOST` | No | `127.0.0.1` | Service listen address |
| `MNEMOSYNE_PORT` | No | `8010` | Service listen port (actually effective since v7.8.3) |

> Pluggable-model principle: switching models/backends only changes environment variables, never code.

## Ollama / other OpenAI-compatible endpoints (local or dev server)

`OPENAI_BASE_URL` can point at any service that implements `/v1/embeddings`, including Ollama. Example (dev server):

```bash
OPENAI_BASE_URL=http://<host>:11434/v1   # note: must include /v1
OPENAI_EMBED_MODEL=mxbai-embed-large     # requires `ollama pull mxbai-embed-large` first
OPENAI_API_KEY=unused                    # Ollama doesn't validate it, but the code wants it non-empty to distinguish "configured" from "not configured"
EMBED_DIM=1024                           # mxbai-embed-large's native dimension is 1024
```

⚠️ **Ollama's dimension trap**: the `dimensions` parameter in the request body only takes effect for
models that support Matryoshka truncation, and can only **truncate down from** the native dimension.
If `EMBED_DIM` is set **larger than the model's native dimension** (e.g. setting `EMBED_DIM=1536` for
`mxbai-embed-large`, which is natively 1024-dim), Ollama will **silently ignore** the parameter and
return the native-dimension vector as-is, with no error. `core/embedding.py` has a runtime check for
this: if the returned vector's length doesn't match `EMBED_DIM`, it immediately raises
`EmbeddingDimensionError` (no retry), rather than letting a wrong-dimension vector silently land in
the database or surface later as a hard-to-diagnose Postgres error at `INSERT` time.

Native dimensions of common Ollama embedding models: `nomic-embed-text`=768, `mxbai-embed-large`=1024,
`bge-m3`=1024. No common Ollama model currently outputs 1536 dimensions natively (1536 is the default
for OpenAI's `text-embedding-3-small`) — if using Ollama in a dev environment, set `EMBED_DIM` and the
target table's `vector(N)` column width to match the chosen model's native dimension, kept separate
from production (OpenAI, 1536-dim).

---
