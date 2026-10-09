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

# Embedding (OpenAI-compatible endpoint — OpenAI / Azure OpenAI / local vLLM/Ollama)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))
EMBED_URL = f"{OPENAI_BASE_URL}/embeddings"

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
