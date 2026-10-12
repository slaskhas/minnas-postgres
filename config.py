"""
Mnemosyne v5.0 — unified config center
Replaces the v2.1 inline CONFIG dict; supports env vars + multiple backends
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Doubao API (Volcengine ARK) ──
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"

# ── OpenAI-compatible vars ─────────────────────────────────────────────────────
# Shared by the dedicated embedding path below AND the "openai" chat backend
# (core/backends.py, only active when MODEL_BACKEND=openai — the default is "ark",
# which does NOT use these). Keep these if you want embedding to fall back to the
# same endpoint as the chat model.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# ── Embedding: its own service, independent of the chat model ─────────────────
# Point the embedding at a service different from the LLM backend (e.g. Ollama for
# vectors while ARK/DeepSeek handles chat). Both vars fall back to the OPENAI_*
# values above, so existing configs keep working; set them directly to decouple.
#   EMBED_BASE_URL — base of the embedding service (the code appends "/embeddings");
#                    Ollama needs the /v1 suffix (http://host:11434/v1).
#   EMBED_MODEL    — the embedding model name (must match the DB vector(N) width).
EMBED_BASE_URL = os.getenv("EMBED_BASE_URL") or OPENAI_BASE_URL
EMBED_MODEL = os.getenv("EMBED_MODEL") or OPENAI_EMBED_MODEL
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))
EMBED_URL = f"{EMBED_BASE_URL.rstrip('/')}/embeddings"

# ── Decision model (Jev-style; Ollama POST <base>/systemone) ────────────────────
# Independent of the chat LLM — the decision model is its own service. Falls back
# to the embedding/Ollama base so it works out of the box when embedding and
# decision run on the same instance; set DECISION_* to point at a different one.
# Reuses OPENAI_API_KEY for the Bearer header unless DECISION_API_KEY is set
# (Ollama ignores the key; it just keeps the header well-formed).
DECISION_BASE_URL = os.getenv("DECISION_BASE_URL") or EMBED_BASE_URL
DECISION_API_KEY = os.getenv("DECISION_API_KEY") or OPENAI_API_KEY
DECISION_MODEL = os.getenv("DECISION_MODEL", "tev1:0.8b")   # nimble | tev1 | tev1:0.8b
DECISION_URL = f"{DECISION_BASE_URL.rstrip('/')}/systemone"

# LLM tiers
DOUBAO_MINI = "doubao-seed-2-0-mini-260215"     # Tier 2: fast/cheap
DOUBAO_LITE = "doubao-seed-2-0-lite-260215"      # Tier 3: primary, supports JSON/tool calls
DOUBAO_CODE = "doubao-seed-2-0-code-preview-260215"  # Tier 4: deep reasoning

# DeepSeek (Tier 4 heterogeneous audit)
DEEPSEEK_PRO = os.getenv("DEEPSEEK_PRO", "deepseek-v4-pro")
DEEPSEEK_FLASH = os.getenv("DEEPSEEK_FLASH", "deepseek-v4-flash")

# ── Database ──
PG_USER = os.getenv("PGUSER", "postgres")
PG_PASSWORD = os.getenv("PGPASSWORD", "")
PG_DB = os.getenv("PGDATABASE", "mnemosyne")
PG_HOST = os.getenv("PGHOST", "127.0.0.1")
PG_PORT = int(os.getenv("PGPORT", "5432"))
# Target schema (isolates this app via a non-public schema when sharing a database
# with other apps; must match the `CREATE SCHEMA` / `SET search_path` target in
# docs/schema.sql)
PG_SCHEMA = os.getenv("PGSCHEMA", "public")
# Actual search_path used on connect: the pgvector extension's `vector` type/operators
# themselves live in `public` (CREATE EXTENSION ... WITH SCHEMA public), and application
# code casts bare `::vector` in many places, so if PG_SCHEMA != public, `public` must
# also be included as a type-resolution fallback — but since PG_SCHEMA comes first,
# table-name resolution always prefers a same-named table that already exists in this
# schema, and won't accidentally fall through to another app's same-named table under
# `public` (unless the table is missing from this schema)
PG_SEARCH_PATH = PG_SCHEMA if PG_SCHEMA == "public" else f"{PG_SCHEMA}, public"

# ── Service ──
HOST = os.getenv("MNEMOSYNE_HOST", "127.0.0.1")
PORT = int(os.getenv("MNEMOSYNE_PORT", "8010"))

# ── Search weights ──
SEARCH_WEIGHTS = {
    "vector": 0.45,
    "bm25": 0.15,
    "time": 0.15,
    "reliability": 0.15,
    "heat": 0.10,
}

# ── Heat decay ──
HEAT_DECAY_ALPHA = 0.95   # daily decay coefficient
HEAT_BOOST_ACCESS = 0.05   # increment per access

# ── TMT distillation ──
TMT_LLM_TIER = "lite"       # distillation model: mini/lite/pro
TMT_MAX_RETRIES = 3          # max retries for LLM calls
