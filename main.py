"""
Mnemosyne memory core engine v5.0
Cognitive memory operating system — full seven-layer architecture

v5.0 upgrade:
  - Doubao API fully replaces local models (embedding-vision 1024d + seed-2.0)
  - Tiered model routing (Tier1-5)
  - Production server runs independently 24/7, no reverse-tunnel dependency
  - Three-hall closed-loop knowledge production pipeline (Phase 2)
"""
import os
import sys
import asyncio
import json
import asyncpg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os, sys, json, uuid, math, re, time, difflib, hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import logging
from contextlib import asynccontextmanager

# v7.8: real BM25 — jieba tokenization (query uses the same dictionary as memory_keywords), lazy-loaded to avoid slowing startup
try:
    import jieba as _jieba
    _WIKI_DICT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiki", "wiki_dict.txt")
    if os.path.exists(_WIKI_DICT):
        _jieba.load_userdict(_WIKI_DICT)
except ImportError:
    _jieba = None


def _query_tokens(text: str) -> list:
    """Query tokenization: uses jieba (consistent with the index); falls back to whitespace/punctuation splitting when jieba is unavailable"""
    if _jieba is None:
        return [w.strip() for w in re.split(r"[\s,，。.!?！？:：]+", text) if len(w.strip()) > 1]
    return [t.strip() for t in _jieba.cut(text)
            if len(t.strip()) >= 2 and not t.strip().isdigit() and t.strip() not in (" ", "\t")]
logger = logging.getLogger("mnemosyne")


# v7.8.1: tokenize-on-write — eliminates the same-day BM25 blind window for new memories (logic matches memory_tokenize.tokenize_memory, inlined here to avoid tmt.distill side effects)
async def _tokenize_on_write(conn, memory_id: int, content: str) -> None:
    """Write hook: immediately build memory_keywords for this memory (idempotent; failures only warn, never block the write)"""
    try:
        if _jieba is None or len(content or "") < 30:
            return
        from collections import Counter
        head = [t.strip() for t in _jieba.cut(content[:60])
                if 2 <= len(t.strip()) <= 20 and not t.strip().isdigit() and not t.strip().isspace()]
        body = [t.strip() for t in _jieba.cut(content[:20000])
                if 2 <= len(t.strip()) <= 20 and not t.strip().isdigit() and not t.strip().isspace()]
        cnt = Counter()
        for t in head:
            cnt[t] += 2
        for t in body:
            cnt[t] += 1
        toks = [(t, f) for t, f in cnt.items() if any(c.isalnum() for c in t)]
        if not toks:
            return
        await conn.execute("DELETE FROM memory_keywords WHERE memory_id=$1", memory_id)
        await conn.executemany(
            "INSERT INTO memory_keywords (memory_id, token, freq) VALUES ($1,$2,$3) "
            "ON CONFLICT (memory_id, token) DO UPDATE SET freq=EXCLUDED.freq",
            [(memory_id, t, f) for t, f in toks])
        await conn.execute(
            "UPDATE memories SET metadata = COALESCE(metadata,'{}'::jsonb) || '{\"kw_tokenized\":true}'::jsonb "
            "WHERE id=$1", memory_id)
    except Exception as e:
        logger.warning(f"Inline tokenization failed memory_id={memory_id}: {e}")

# ── v6.0: controlled category whitelist (the only legal `category` values) ──
# Single-user (user_id=default) semantic convergence: 10 categories, Chinese primary
# keys with English aliases for API compatibility.
# Memory lifecycle: write (tmt_level=1 raw fragment) → TMT distillation (L2 session /
# L3 daily / L4 weekly / L5 profile)
# Value tiering: `tier` is maintained by reflect based on heat (L1 core / L2 regular /
# L3 low-frequency / L4 pending cleanup)
CATEGORY_WHITELIST = {
    "knowledge":   ["knowledge", "架构", "architecture", "design-pattern", "设计", "概念", "知识", "fact", "pattern", "belief"],
    "pitfall":     ["pitfall", "踩坑", "坑", "教训", "故障", "experience"],
    "reference":   ["reference", "参考", "论文", "资料", "research"],
    "project":     ["project", "项目", "进度"],
    "ops":         ["ops", "运维", "monitoring", "healthcheck", "巡检", "监控", "健康"],
    "deploy":      ["deploy", "部署", "发布", "版本", "变更"],
    "preference":  ["preference", "偏好", "喜好", "人设", "习惯"],
    "session":     ["session", "会话", "对话", "chat"],
    "worklog":     ["worklog", "note", "日志", "汇报", "工作", "记录", "notes", "general", "work"],
    "temp":        ["temp", "临时", "提醒"],
}

# v7.7.0: three-way memory type tagging (aligned with the industry-standard episodic/semantic/procedural)
# Rule mapping: category → memory_type (stored in metadata['memory_type'])
CAT_MEMORY_TYPE = {
    "session": "episodic",     # session/event
    "chat": "episodic",        # conversation
    "worklog": "episodic",     # work log (event)
    "fact": "semantic",        # fact
    "preference": "semantic",  # preference
    "knowledge": "semantic",   # knowledge
    "reference": "semantic",   # reference
    "temp": "semantic",        # temporary
    "pitfall": "procedural",   # pitfall/lesson (operational)
    "ops": "procedural",       # ops
    "deploy": "procedural",    # deploy steps
    "project": "procedural",   # project process
}

def normalize_category(cat: str) -> str:
    """Category normalization: Chinese/legacy English → controlled-whitelist primary key. Unknown categories default to knowledge.
    Matching rules: ① exact match (key/alias) ② Chinese alias (>=2 chars) substring containment."""
    if not cat:
        return "knowledge"
    c = str(cat).strip().lower()
    # ① exact match
    for key, aliases in CATEGORY_WHITELIST.items():
        if c == key or c in [a.lower() for a in aliases]:
            return key
    # ② substring containment match (Chinese alias >=2 chars, e.g. "论文研究"→reference)
    for key, aliases in CATEGORY_WHITELIST.items():
        for a in aliases:
            a_l = a.lower()
            if len(a_l) >= 2 and (a_l in c or c in a_l):
                return key
    return "knowledge"

# ── v5.0: modular imports ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PG_USER, PG_PASSWORD, PG_DB, PG_HOST, PG_PORT, PG_SEARCH_PATH, HOST, PORT
from core.embedding import get_embedding_async
from core.llm import call_llm as llm_call
# TMT (compatible with the existing v2.1 routes)
import tmt.router as tmt_module
from tmt.router import router as tmt_router

# ── v8.1: MCP over streamable HTTP — lifespan-driven in-process mount at /mcp ──
# Reuses the hermes-mcp bridge's contract-tested handlers verbatim. Loaded by file
# path (the parent dir `hermes-mcp` has a hyphen → not a normal import path).
# Handlers reach the core over loopback REST via set_base_url() (direct uvicorn,
# no Nginx auth layer); stateless so it is safe under `uvicorn --workers N`.
#
# A Starlette Mount never runs a sub-app lifespan, and mounting a sub-app whose
# own route is /mcp nests to /mcp/mcp — so we (a) register the raw per-endpoint
# ASGI app as a plain Route at exact /mcp (methods=None → all methods; exact
# match → no double path, no trailing-slash 307 for /mcp) and (b) drive run()
# from this app's lifespan — mirroring the SDK's own standalone app
# (Route(path, endpoint, methods=None) + lifespan=lambda app: session_manager.run()).
# Skipped silently when the mcp SDK is absent so the REST API is never taken down.
#
# The asyncpg pool is also created here rather than in an `@app.on_event("startup")`
# handler: once a custom `lifespan=` is passed to FastAPI, Starlette no longer runs
# the legacy on_event handlers at all, so a separate on_event-based pool init is
# silently skipped — every pool-using route then fails with
# `NameError: name 'pool' is not defined`.
@asynccontextmanager
async def _mcp_lifespan(_app: "FastAPI"):
    global pool
    pool = await asyncpg.create_pool(
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_DB,
        host=PG_HOST,
        port=PG_PORT,
        min_size=2,
        max_size=10,
        server_settings={'search_path': PG_SEARCH_PATH}
    )
    # Inject into the TMT module
    tmt_module.pool = pool
    # Inject into the v5.0 modules
    security_module.pool = pool
    skills_module.pool = pool
    injection_module.pool = pool
    tmt_module.embed_fn = get_embedding
    tmt_module.llm_url = "http://127.0.0.1:11435/v1/chat/completions"
    # v7.0 Memory Palace: init (create tables + archive existing backlog, idempotent)
    try:
        import palace
        palace_result = await palace.init_palace(pool)
        logger.info(f"[palace] init complete: tables={palace_result['tables']} classified={palace_result['classified']} cards={palace_result['cards']}")
    except Exception as e:
        logger.warning(f"[palace] init skipped: {e}")

    try:
        run_cm = None
        try:
            import importlib.util as _mcp_importlib
            _mcp_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "integrations", "hermes-mcp", "mnemosyne_mcp.py")
            _mcp_spec = _mcp_importlib.spec_from_file_location(
                "_mnemosyne_mcp_bridge", _mcp_path)
            if _mcp_spec is not None and _mcp_spec.loader is not None:
                _mcp_mod = _mcp_importlib.module_from_spec(_mcp_spec)
                sys.modules["_mnemosyne_mcp_bridge"] = _mcp_mod
                _mcp_spec.loader.exec_module(_mcp_mod)
                if (getattr(_mcp_mod, "_MCP_AVAILABLE", False)
                        and callable(getattr(_mcp_mod, "set_base_url", None))
                        and callable(getattr(_mcp_mod, "build_mcp_mount", None))):
                    # Point handlers at the core's own loopback address. A `0.0.0.0`/
                    # empty host binds everywhere → clients should connect via
                    # 127.0.0.1.
                    _loopback_host = str(HOST).strip()
                    if _loopback_host in ("0.0.0.0", ""):
                        _loopback_host = "127.0.0.1"
                    _mcp_mod.set_base_url(f"http://{_loopback_host}:{PORT}")
                    asgi_endpoint, run_cm = _mcp_mod.build_mcp_mount()
                    # Raw per-endpoint ASGI app (path-agnostic; the JSON-RPC method
                    # lives in the request body). methods=None → accepts POST/DELETE/etc.
                    _app.router.add_route(
                        "/mcp", asgi_endpoint, methods=None, include_in_schema=False)
        except Exception as _e:  # mcp SDK absent / bridge changed → REST unaffected
            logger.debug("MCP mount skipped: %s", _e)

        if run_cm is None:
            # Not available — still yield so the host app starts (no /mcp).
            logger.debug("MCP (/mcp) not available; REST API unaffected")
            yield
        else:
            async with run_cm():
                logger.info("MCP (/mcp) mounted — 15 Mnemosyne tools via streamable HTTP")
                yield
            logger.info("MCP (/mcp) session manager stopped")
    finally:
        await pool.close()


pool: Optional[asyncpg.Pool] = None

app = FastAPI(title="Mnemosyne OS v8.1.0 — 认知型记忆操作系统", lifespan=_mcp_lifespan)

# ── mount v5.0 routes ──
app.include_router(tmt_router)

# Three-hall closed loop (Phase 2)

# Security modules (Phase 3)
import security.audit as audit_module
import security.purifier as purifier_module

import api.security as security_module
from api.security import router as security_router
app.include_router(security_router)
security_module.pool = None

# v7.7.0 procedural-memory wing (skill assets)
import api.skills as skills_module
from api.skills import router as skills_router
app.include_router(skills_router)
skills_module.pool = None

# v7.7.0 injection scheduling hall
import api.injection as injection_module
from api.injection import router as injection_router
app.include_router(injection_router)


# v8.0 S3-1 memory layering model (executable spec)
import core.layers as layers_mod
injection_module.pool = None

# Database connection pool

# ── Entity sync (v7.8: removed AGE graph sync, kept only the entities table + memory_entities relation) ──
async def sync_entities(conn, memory_id: int, entities: list, user_id: str):
    for name in entities:
        name = name.strip()
        if not name:
            continue
        row = await conn.fetchrow("SELECT id FROM entities WHERE user_id=$1 AND name=$2", user_id, name)
        if row:
            eid = row["id"]
        else:
            raw = (await get_embedding([name]))[0]
            e_str = "[" + ",".join(str(x) for x in raw) + "]"
            row = await conn.fetchrow(
                "INSERT INTO entities (user_id, name, type, description, embedding) VALUES ($1,$2,$3,$4,$5::vector) RETURNING id",
                user_id, name, "auto", "", e_str
            )
        eid = row["id"]
        await conn.execute("INSERT INTO memory_entities (memory_id, entity_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", memory_id, eid)

async def clean_entity_relations(conn, memory_id: int):
    await conn.execute("DELETE FROM memory_entities WHERE memory_id=$1", memory_id)

# ── Conflict detection ──
import difflib

def text_diff_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()

async def detect_conflict(conn, user_id: str, new_content: str, new_embedding_str: str) -> dict:
    """Detect whether a new memory conflicts with or duplicates an existing one"""
    rows = await conn.fetch(
        f"SELECT id, content, embedding <=> $1::vector AS dist, heat_score "
        "FROM memories WHERE user_id=$2 AND is_deleted=FALSE AND valid_to IS NULL "
        "ORDER BY embedding <=> $1::vector LIMIT 5",
        new_embedding_str, user_id
    )
    for r in rows:
        if r["dist"] > 0.15:  # semantically dissimilar, skip
            continue
        ratio = text_diff_ratio(new_content, r["content"])
        if ratio > 0.85:
            # near-exact duplicate → merge
            return {"action": "merge", "id": r["id"]}
        elif ratio < 0.5 and r["dist"] < 0.12:
            # semantically similar but conflicting content → mark the old memory as superseded
            return {"action": "conflict", "id": r["id"], "old_content": r["content"]}
    return {"action": "fresh"}

# ── Startup/shutdown ──


class DialecticRequest(BaseModel):
    query: str = ""
    user_id: str = "default"
    max_memories: int = 3


@app.post("/api/v1/dialectic")
async def dialectic_search(req: DialecticRequest):
    query = req.query.strip()
    if not query:
        return {"error": "query required"}
    user_id = req.user_id
    async with pool.acquire() as conn:
        r_q = (await get_embedding([query]))[0]
        q_str = "[" + ",".join(str(x) for x in r_q) + "]"
        # v7.8 real BM25 (same as main search): jieba tokenization → memory_keywords TF weighting
        q_tokens = _query_tokens(query)
        if q_tokens:
            bm25_sql = ("(SELECT LEAST(1.0, 0.5 + COALESCE(SUM(k.freq),0)/8.0) FROM memory_keywords k "
                        "WHERE k.memory_id = m.id AND k.token = ANY($4::text[]))")
        else:
            bm25_sql = "0"
        temporal_sql = "CASE WHEN m.created_at > NOW() - INTERVAL '7 days' THEN 0.15 WHEN m.created_at > NOW() - INTERVAL '30 days' THEN 0.08 ELSE 0 END"
        rows = await conn.fetch(
            "SELECT m.id, m.content, m.category, m.tier, m.heat_score, m.reliability, m.created_at, m.session_id "
            "FROM memories m WHERE m.user_id=$1 AND m.is_deleted=FALSE AND (m.valid_to IS NULL OR m.valid_to > NOW()) AND m.embedding IS NOT NULL "
            "ORDER BY (0.50 * (1.0 - (m.embedding <=> $2::vector)) "
            "  + 0.15 * (" + bm25_sql + ") "
            "  + 0.15 * (" + temporal_sql + ") "
            "  + 0.10 * m.reliability "
            "  + 0.10 * GREATEST(0.0, m.heat_score)) DESC "
            "LIMIT $3", user_id, q_str, req.max_memories * 2, q_tokens
        )
        if not rows:
            return {"query": query, "memories": [], "context": [], "total_memories": 0}
        memories = []
        session_ids = set()
        for r in rows:
            mem = {"id": r["id"], "content": r["content"][:300], "category": r["category"],
                   "tier": r["tier"], "heat": r["heat_score"], "reliability": r["reliability"],
                   "created": str(r["created_at"])[:19]}
            memories.append(mem)
            if r["session_id"]:
                session_ids.add(str(r["session_id"]))
        context = []
        if session_ids:
            session_list = list(session_ids)
            phs = ",".join("${}".format(2 + i) for i in range(len(session_list)))
            s_rows = await conn.fetch(
                "SELECT s.id::text, s.session_label, s.summary, s.heat_score, s.fragment_ids, s.start_time, s.created_at "
                "FROM mnemosyne.tmt_sessions s WHERE s.user_id=$1 AND s.id::text = ANY(ARRAY[" + phs + "])",
                user_id, *session_list
            )
            for s in s_rows:
                context.append({
                    "type": "L2_session", "id": s["id"], "label": s["session_label"] or "",
                    "summary": (s["summary"] or "")[:500], "heat": s["heat_score"],
                    "fragment_count": len(s["fragment_ids"] or []),
                    "start_time": str(s["start_time"])[:19] if s["start_time"] else "",
                    "created": str(s["created_at"])[:19],
                })
        return {"query": query, "memories": memories, "context": context, "total_memories": len(memories)}


@app.get("/api/v1/memories/{memory_id}/tiered")
async def tiered_read(memory_id: int, level: str = "L3", user_id: str = "default"):
    """Three-tier read: L5 summary / L3 overview / L1 full text + context"""
    level = level.upper().strip()
    if level not in ("L5", "L3", "L1"):
        return {"error": "level must be L5, L3, or L1"}
    
    async with pool.acquire() as conn:
        # 1. Fetch the memory
        row = await conn.fetchrow(
            "SELECT m.id, m.content, m.category, m.tier, m.tmt_level, m.heat_score, "
            "m.reliability, m.access_count, m.created_at, m.session_id "
            "FROM memories m WHERE m.id=$1 AND m.user_id=$2 AND m.is_deleted=FALSE",
            memory_id, user_id
        )
        if not row:
            return {"error": f"memory {memory_id} not found"}
        
        base = {
            "id": row["id"],
            "category": row["category"],
            "tier": row["tier"],
            "heat": row["heat_score"],
            "reliability": row["reliability"],
            "created": str(row["created_at"])[:19],
        }
        
        if level == "L5":
            # Summary: truncate to 200 chars + session label
            base["summary"] = (row["content"] or "")[:200]
            base["content_truncated"] = True
            if row["session_id"]:
                s = await conn.fetchrow(
                    "SELECT session_label FROM mnemosyne.tmt_sessions WHERE id=$1",
                    row["session_id"]
                )
                if s and s["session_label"]:
                    base["session_label"] = s["session_label"]
            return base
        
        elif level == "L3":
            # Overview: 800 chars + session summary
            content_full = row["content"] or ""
            base["content"] = content_full[:800]
            base["content_length"] = len(content_full)
            base["content_truncated"] = len(content_full) > 800
            if row["session_id"]:
                s = await conn.fetchrow(
                    "SELECT session_label, summary FROM mnemosyne.tmt_sessions "
                    "WHERE id=$1", row["session_id"]
                )
                if s:
                    base["session"] = {
                        "label": s["session_label"] or "",
                        "summary": (s["summary"] or "")[:500],
                    }
            return base
        
        else:  # L1
            # Full text + complete session info + fragment list
            base["content"] = row["content"] or ""
            base["content_length"] = len(row["content"] or "")
            base["access_count"] = row["access_count"]
            
            if row["session_id"]:
                sid = row["session_id"]
                s = await conn.fetchrow(
                    "SELECT session_label, summary, heat_score, fragment_ids, "
                    "start_time, end_time, token_count "
                    "FROM mnemosyne.tmt_sessions WHERE id=$1", sid
                )
                if s:
                    base["session"] = {
                        "id": str(sid),
                        "label": s["session_label"] or "",
                        "summary": s["summary"] or "",
                        "heat": s["heat_score"],
                        "fragment_count": len(s["fragment_ids"] or []),
                        "token_count": s["token_count"],
                        "start": str(s["start_time"])[:19] if s["start_time"] else "",
                        "end": str(s["end_time"])[:19] if s["end_time"] else "",
                    }
                    # Other fragments in the same session
                    fids = s["fragment_ids"] or []
                    if fids:
                        others = await conn.fetch(
                            "SELECT id, content, category, heat_score, created_at "
                            "FROM memories WHERE id = ANY($1::bigint[]) AND id != $2 "
                            "ORDER BY created_at LIMIT 10",
                            fids, memory_id
                        )
                        if others:
                            base["related_fragments"] = []
                            for o in others:
                                base["related_fragments"].append({
                                    "id": o["id"],
                                    "content": (o["content"] or "")[:150],
                                    "category": o["category"],
                                    "heat": o["heat_score"],
                                })
            
            # Daily summary (if it belongs to a given day)
            try:
                d = await conn.fetchrow(
                    "SELECT d.date, d.summary FROM mnemosyne.tmt_daily d "
                    "WHERE d.user_id=$1 AND $2::date >= d.date "
                    "ORDER BY d.date DESC LIMIT 1",
                    user_id, str(row["created_at"])[:10]
                )
                if d:
                    base["daily"] = {
                        "date": str(d["date"]),
                        "summary": (d["summary"] or "")[:300],
                    }
            except Exception:
                pass
            
            return base


@app.get("/api/v1/memories/conflicts")
async def list_conflicts(user_id: str = "default", limit: int = 20):
    """Query memories with conflict metadata"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, category, tier, heat_score, reliability, metadata, created_at "
            "FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW()) "
            "AND metadata->>'conflicts_with' IS NOT NULL "
            "ORDER BY created_at DESC LIMIT $2", user_id, limit
        )
        if not rows:
            return {"conflicts": [], "total": 0}
        result = []
        for r in rows:
            meta = r["metadata"] or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            result.append({
                "id": r["id"],
                "content": r["content"][:200],
                "category": r["category"],
                "tier": r["tier"],
                "heat": r["heat_score"],
                "reliability": r["reliability"],
                "conflicts_with": meta.get("conflicts_with"),
                "conflict_type": meta.get("conflict_type", "unknown"),
                "created": str(r["created_at"])[:19],
            })
        return {"conflicts": result, "total": len(result)}



# ── WIKI full-text snapshots (v7.4) ──
class WikiPageCreate(BaseModel):
    title: str
    content: str = ""
    user_id: str = "default"
    tags: list = None
    source_path: str = ""
    source_url: str = ""
    source_type: str = "memo"
    content_hash: str = ""
    skip_embedding: bool = False


class WikiSearchRequest(BaseModel):
    query: str
    user_id: str = "default"
    top_k: int = 5
    hybrid: bool = True
    rerank: bool = False
    graph: bool = False  # Graph expansion off by default (A/B testing showed it introduces noise; kept as an optional enhancement)


@app.get("/api/v1/wiki")
async def list_wiki_pages(user_id: str = "default", limit: int = 20):
    """List wiki knowledge pages"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, content, created_at, updated_at "
            "FROM wiki_pages WHERE user_id=$1 ORDER BY updated_at DESC LIMIT $2",
            user_id, limit
        )
        return [{"id": r["id"], "title": r["title"],
                  "content_preview": (r["content"] or "")[:200],
                  "content_length": len(r["content"] or ""),
                  "created": str(r["created_at"])[:19] if r["created_at"] else "",
                  "updated": str(r["updated_at"])[:19] if r["updated_at"] else "",
                 } for r in rows]

@app.get("/api/v1/wiki/by-source")
async def get_wiki_by_source(source_path: str = "", source_url: str = "", user_id: str = "default"):
    """Quick lookup: find the exact snapshot by source path/URL (v7.4 tamper-resistant archive)"""
    async with pool.acquire() as conn:
        if source_path:
            row = await conn.fetchrow(
                "SELECT id, title, content, source_path, source_url, source_type, content_hash, version, "
                "source_lost, created_at, updated_at FROM wiki_pages WHERE user_id=$1 AND source_path=$2",
                user_id, source_path
            )
        else:
            row = await conn.fetchrow(
                "SELECT id, title, content, source_path, source_url, source_type, content_hash, version, "
                "source_lost, created_at, updated_at FROM wiki_pages WHERE user_id=$1 AND source_url=$2",
                user_id, source_url
            )
        if not row:
            return {"found": False}
        return {"found": True,
                "id": row["id"], "title": row["title"], "content": row["content"] or "",
                "source_path": row["source_path"], "source_url": row["source_url"],
                "source_type": row["source_type"], "content_hash": row["content_hash"],
                "version": row["version"], "source_lost": row["source_lost"],
                "created": str(row["created_at"])[:19] if row["created_at"] else "",
                "updated": str(row["updated_at"])[:19] if row["updated_at"] else ""}

@app.get("/api/v1/wiki/{page_id}")
async def get_wiki_page(page_id: int):
    """Get wiki page full content"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, content, user_id, created_at, updated_at "
            "FROM wiki_pages WHERE id=$1", page_id
        )
        if not row:
            return {"error": "not found"}
        return {"id": row["id"], "title": row["title"], "content": row["content"] or "",
                "user_id": row["user_id"], "created": str(row["created_at"])[:19] if row["created_at"] else "",
                "updated": str(row["updated_at"])[:19] if row["updated_at"] else ""}

@app.post("/api/v1/wiki")
async def create_wiki_page(body: WikiPageCreate):
    """Create a wiki page (full-text snapshot archive). v7.4: supports source/fingerprint/version history."""
    import json as _json
    title = body.title
    content = body.content
    user_id = body.user_id
    tags = body.tags or []
    source_path = body.source_path or ""
    source_url = body.source_url or ""
    source_type = body.source_type or "memo"
    content_hash = body.content_hash or ""
    skip_embedding = body.skip_embedding
    async with pool.acquire() as conn:
        # Idempotent: if the same source_path already exists, return the existing page (no duplicate creation)
        if source_path:
            existing = await conn.fetchrow(
                "SELECT id, content_hash, version FROM wiki_pages WHERE user_id=$1 AND source_path=$2",
                user_id, source_path
            )
            if existing:
                if existing["content_hash"] == content_hash and content_hash:
                    return {"status": "exists", "id": existing["id"], "version": existing["version"], "unchanged": True}
                # hash differs → update content and write version history
                v = existing["version"] + 1
                if not skip_embedding:
                    r_v = (await get_embedding([content]))[0]
                    v_str = "[" + ",".join(str(x) for x in r_v) + "]"
                    await conn.execute(
                        "UPDATE wiki_pages SET content=$1, tags=$2, embedding=$3::vector, version=$4, content_hash=$5, "
                        "source_url=$6, source_type=$7, updated_at=now() WHERE id=$8",
                        content, _json.dumps(tags, ensure_ascii=False), v_str, v, content_hash, source_url, source_type, existing["id"]
                    )
                    await conn.execute(
                        "INSERT INTO wiki_versions (page_id, version, content, embedding) VALUES ($1,$2,$3,$4::vector)",
                        existing["id"], v, content, v_str
                    )
                else:
                    await conn.execute(
                        "UPDATE wiki_pages SET content=$1, tags=$2, version=$3, content_hash=$4, "
                        "source_url=$5, source_type=$6, updated_at=now() WHERE id=$7",
                        content, _json.dumps(tags, ensure_ascii=False), v, content_hash, source_url, source_type, existing["id"]
                    )
                return {"status": "updated", "id": existing["id"], "version": v}
        row = await conn.fetchrow(
            "INSERT INTO wiki_pages (title, content, user_id, tags, source_path, source_url, source_type, content_hash) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
            title, content, user_id, _json.dumps(tags, ensure_ascii=False), source_path, source_url, source_type, content_hash
        )
        page_id = row["id"]
        if not skip_embedding and content:
            r_v = (await get_embedding([content]))[0]
            v_str = "[" + ",".join(str(x) for x in r_v) + "]"
            await conn.execute("UPDATE wiki_pages SET embedding=$1::vector WHERE id=$2", v_str, page_id)
            await conn.execute(
                "INSERT INTO wiki_versions (page_id, version, content, embedding) VALUES ($1,1,$2,$3::vector)",
                page_id, content, v_str
            )
        # v7.5: sync the keyword index (needed by the BM25 channel, otherwise new pages won't surface in hybrid search)
        if content and len(content) >= 20:
            try:
                import jieba
                import os as _os
                _dict_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "wiki", "wiki_dict.txt")
                if _os.path.exists(_dict_path):
                    jieba.load_userdict(_dict_path)
                from collections import Counter as _Counter
                def _clean_tok(t):
                    return len(t) >= 2 and len(t) <= 20 and not t.isdigit() and not t.isspace()
                title_toks = [t.strip() for t in jieba.cut(title) if _clean_tok(t.strip())]
                body_toks = [t.strip() for t in jieba.cut(content[:20000]) if _clean_tok(t.strip())]
                cnt = _Counter()
                for t in title_toks:
                    cnt[t] += 3
                for t in body_toks:
                    cnt[t] += 1
                if cnt:
                    await conn.executemany(
                        "INSERT INTO wiki_keywords (page_id, token, freq) VALUES ($1,$2,$3) "
                        "ON CONFLICT (page_id, token) DO UPDATE SET freq=EXCLUDED.freq",
                        [(page_id, t, f) for t, f in cnt.items()]
                    )
            except Exception as e:
                logger.warning(f"wiki keyword index sync failed: {e}")
        return {"status": "created", "id": page_id, "version": 1}



@app.get("/api/v1/media")
async def list_media(user_id: str = "default", limit: int = 20, media_type: str = ""):
    """List media memories (files/images/links, etc.)"""
    async with pool.acquire() as conn:
        if media_type:
            rows = await conn.fetch(
                "SELECT id, content, media_type, media_url, importance, reliability, metadata, created_at "
                "FROM media_memories WHERE user_id=$1 AND media_type=$2 "
                "ORDER BY created_at DESC LIMIT $3", user_id, media_type, limit
            )
        else:
            rows = await conn.fetch(
                "SELECT id, content, media_type, media_url, importance, reliability, metadata, created_at "
                "FROM media_memories WHERE user_id=$1 "
                "ORDER BY created_at DESC LIMIT $2", user_id, limit
            )
        return [{"id": r["id"], "content": (r["content"] or "")[:200],
                 "media_type": r["media_type"], "media_url": r["media_url"],
                 "importance": r["importance"], "reliability": r["reliability"],
                 "created": str(r["created_at"])[:19]} for r in rows]

@app.get("/api/v1/media/{media_id}")
async def get_media(media_id: int):
    """Get the full content of a media memory"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, content, media_type, media_url, media_hash, importance, "
            "reliability, metadata, created_at FROM media_memories WHERE id=$1", media_id
        )
        if not row:
            return {"error": "not found"}
        return {"id": row["id"], "content": row["content"] or "",
                "media_type": row["media_type"], "media_url": row["media_url"],
                "media_hash": row["media_hash"], "importance": row["importance"],
                "reliability": row["reliability"],
                "metadata": row["metadata"] or {},
                "created": str(row["created_at"])[:19]}

@app.post("/api/v1/media")
async def create_media(content: str, media_type: str = "file", media_url: str = "",
                       media_hash: str = "", user_id: str = "default", importance: float = 0.5):
    """Create a media memory (link a file/image/link to the memory system)"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO media_memories (user_id, content, media_type, media_url, media_hash, importance) "
            "VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
            user_id, content, media_type, media_url, media_hash, importance
        )
        return {"status": "created", "id": row["id"]}

@app.delete("/api/v1/media/{media_id}")
async def delete_media(media_id: int, user_id: str = "default"):
    """Delete a media memory"""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM media_memories WHERE id=$1 AND user_id=$2", media_id, user_id
        )
        deleted = result.split()[-1] if result else "0"
        return {"status": "deleted", "id": media_id, "affected": int(deleted)}

# ── OpenAI-compatible embedding API (replaces the local Qwen3-Embedding) ──
async def get_embedding(texts: List[str]) -> List[List[float]]:
    """Call the OpenAI-compatible embeddings API — 1536-dim vectors"""
    return await get_embedding_async(texts)

async def rerank_docs(query: str, documents: List[str], top_k: int = 5) -> List[str]:
    """
    v5.1 Reranker: primarily uses OpenAI-compatible embeddings (cosine-similarity ranking),
    with local Qwen3-Embed as a fallback
    """
    RERANK_URL = "http://127.0.0.1:11436/v1/embeddings"

    async def _embed_local(texts):
        """Fallback: local Qwen3-Embedding"""
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(RERANK_URL, json={"input": texts})
            resp.raise_for_status()
            data = resp.json()
            return [d["embedding"] for d in data["data"]]

    try:
        # Primary path: Doubao embedding
        from core.backends import rerank_by_similarity
        q_emb = (await get_embedding([query]))[0]
        d_embs = await get_embedding(documents)
        return rerank_by_similarity(q_emb, documents, d_embs, top_k)
    except Exception:
        # Fallback: local Qwen3-Embedding
        try:
            q_emb = (await _embed_local([query]))[0]
            d_embs = await _embed_local(documents)
            from core.backends import rerank_by_similarity
            return rerank_by_similarity(q_emb, documents, d_embs, top_k)
        except Exception:
            return documents[:top_k]

# ── Belief model ──
class BeliefCreate(BaseModel):
    user_id: str
    content: str
    confidence: float = 0.5
    evidence_memories: List[int] = []
    status: str = "tentative"

class BeliefSearch(BaseModel):
    user_id: str
    query: str
    top_k: int = 5
    status_filter: Optional[str] = None

# ── Core memory API ──
class MemoryCreate(BaseModel):
    user_id: str = "default"
    project_id: Optional[int] = None  # v7.7.0: str→int type contract fix (passing a proj_xxx accession-number string now gives a friendly 422 instead of a 500)
    content: str
    category: str = "knowledge"
    metadata: dict = {}
    entities: Optional[List[str]] = None
    session_id: Optional[str] = None
    source: Optional[str] = None  # v7.6: write-source identifier (hermes-precompress/hermes-delegation/...), stored in metadata['source']

SIGNAL_WEIGHTS = [
    (("待办", "下一步", "TODO", "pending", "未完成", "接着", "继续做"), 0.15, "未完成任务"),
    (("不是", "错了", "不要", "应该改", "修正", "记住"), 0.10, "用户纠正"),
    (("坑", "教训", "报错", "失败", "注意", "踩过", "小心"), 0.10, "踩坑教训"),
    (("决定", "方案", "采用", "架构", "设计", "选择"), 0.08, "决策方案"),
    (("路径", "端口", "API", "配置", "key", "密钥"), 0.05, "路径/API"),
    (("重要", "关键", "核心", "必须"), 0.05, "重要标记"),
]
# Inherently-important categories
IMPORTANT_CATS = {"preference", "knowledge", "pitfall"}


def compute_write_heat(content: str, category: str) -> float:
    """v6.3 cognitive write signal: initial heat gets a bonus based on content importance (drawer-cascade temperature design, pure regex, no LLM call)"""
    heat = 0.5
    for keywords, weight, _name in SIGNAL_WEIGHTS:
        if any(k in content for k in keywords):
            heat += weight
    if category in IMPORTANT_CATS:
        heat += 0.10
    return round(min(max(heat, 0.3), 0.8), 2)


def should_run_conflict_detection(layer: str) -> bool:
    """Whether this layer should run `detect_conflict`'s semantic merge/supersede.

    **Why L0 doesn't run it** (a real problem caught by the 2026-09-25 P4 production test):
      After fixing the "layered idempotency key", production testing showed that L0
      same-source retries did dedup correctly, but **identical text from different
      sources was still being merged** — because `detect_conflict` merges near-duplicate
      content (`text_diff_ratio > 0.85` → merge) *before* the fingerprint check ever runs.
      L0's contract is "append-only, contradictions allowed" → semantic merging on L0 is
      effectively **compacting the log**, which violates that contract.
    Conclusion: both fingerprinting and conflict detection must be layer-aware — fixing
    only one of them is a half-fix.
    """
    return layer != "L0"


def compute_write_fingerprint(content: str, category: str, user_id: str, *,
                              session_id=None, source=None, layer=None,
                              now_ts: float | None = None) -> str:
    """Write idempotency key (v8.0.1, layer-aware).

    **Why it has to be layer-aware** (raised by red-team review → confirmed on re-check):
      The first version used `sha256(content|category|user_id)` for **every** category,
      which directly contradicts the L0 layer's own contract — L0 is "append-only,
      contradictions allowed" — yet saying "ok, got it" three times in one day produced
      three identical fingerprints → the last two got swallowed as duplicates.
      Idempotency (a retry of the same request only takes effect once) is not the same
      thing as dedup (multiple pieces of identical content collapse into one); the first
      version conflated the two.

    Rules:
      · L1-L4 (versioned, latest-wins families): **content fingerprint** — a repeated
        write of the same content is treated as the same logical write → idempotent
      · L0 (append-only log layer): a content fingerprint would wrongly kill legitimate
        repeats, so the idempotency key must include **source context**:
          - session_id / source present → use them to distinguish (retries within the
            same session still dedup)
          - neither present (a bare temp note) → use an **hourly bucket**: dedups
            second-level retries, preserved across hour boundaries
    """
    import hashlib as _h
    import time as _t
    base = f"{content}|{category}|{user_id}"
    if layer == "L0":
        ctx = f"{session_id or ''}|{source or ''}".strip("|")
        if ctx:
            return _h.sha256(f"{base}|ctx:{ctx}".encode("utf-8")).hexdigest()
        bucket = int((now_ts if now_ts is not None else _t.time()) // 3600)
        return _h.sha256(f"{base}|h{bucket}".encode("utf-8")).hexdigest()
    return _h.sha256(base.encode("utf-8")).hexdigest()


@app.post("/api/v1/memories")
async def create_memory(mem: MemoryCreate):
    raw_vec = (await get_embedding([mem.content]))[0]
    vec_str = "[" + ",".join(str(x) for x in raw_vec) + "]"
    # v6.0: category normalization + user_id convergence to a single user
    cat = normalize_category(mem.category)
    # v7.6: removed mnemosyne-agent/website-agent from the convergence list → full per-persona
    #       memory isolation (partitioned to prevent cross-contamination); content-agent/
    #       catnest-agent were already independent; g-cat/noah/system/test/audit still converge
    uid = "default" if mem.user_id in ("g-cat", "noah", "system", "test", "audit") else (mem.user_id or "default")
    # v6.3: cognitive write signal — initial heat gets a bonus based on content importance (drawer-cascade temperature design, pure regex, no LLM call)
    heat_init = compute_write_heat(mem.content, cat)
    # v8.0.1: idempotency key is **layer-aware** — the L0 log layer can't use a content fingerprint (it would wrongly kill legitimate repeats)
    _layer_info = layers_mod.classify_layer(cat, has_artifact=bool(mem.source), source=mem.source)
    fingerprint = compute_write_fingerprint(
        mem.content, cat, uid, session_id=mem.session_id, source=mem.source,
        layer=_layer_info["layer"])
    async with pool.acquire() as conn:
        # ══════════ v8.0 S1-1: wrap the write path in a single transaction ══════════
        # Prior problem (observed in production): sequential execute calls + asyncpg
        # autocommit → a crash mid-write could leave a half-finished state ("a memories
        # row exists but entities / memory_keywords don't").
        # Fix: detect_conflict read + main write + entity sync + tokenization all run
        # inside the same transaction — if any step fails, the whole thing rolls back,
        # no half-finished state.
        async with conn.transaction():
            # ══════════ v8.0 S1-2: idempotency short-circuit (crash retries / reconnect resends no longer double-insert) ══════════
            # idempotency key = sha256(content|category|user_id), unique index dedup_fingerprint_key
            dup = await conn.fetchrow(
                "SELECT id FROM memories WHERE dedup_fingerprint=$1 LIMIT 1", fingerprint)
            if dup:
                await conn.execute(
                    "UPDATE memories SET access_count = access_count + 1, last_accessed = NOW() WHERE id = $1",
                    dup["id"])
                return {"status": "duplicate", "id": dup["id"], "action": "idempotent"}
            # Conflict detection (v8.0.1: layer-aware — the L0 log layer skips this, see should_run_conflict_detection)
            if should_run_conflict_detection(_layer_info["layer"]):
                conflict = await detect_conflict(conn, uid, mem.content, vec_str)
            else:
                conflict = {"action": "fresh"}
            if conflict["action"] == "merge":
                # Merge: bump the access count, don't create a new record
                await conn.execute(
                    "UPDATE memories SET access_count = access_count + 1, last_accessed = NOW() WHERE id = $1",
                    conflict["id"]
                )
                return {"status": "merged", "id": conflict["id"], "action": "merged_with_existing"}
            elif conflict["action"] == "conflict":
                # Conflict: mark the old memory as superseded, tag the new memory with the conflict source
                old_id = conflict["id"]
                await conn.execute(
                    "UPDATE memories SET valid_to = NOW(), invalid_at = NOW() WHERE id = $1",
                    old_id
                )
                await conn.execute(
                    "INSERT INTO memory_traces (memory_id, action, details) VALUES ($1, 'superseded', $2)",
                    old_id, json.dumps({"new_content": mem.content[:200]})
                )
                # Tag the new memory with the conflict source
                meta = dict(mem.metadata) if isinstance(mem.metadata, dict) else {}
                meta["conflicts_with"] = old_id
                meta["conflict_type"] = "superseded"
            # Normal insert (including valid_from); v6.0: raw fragments get tmt_level=1, tier is maintained by reflect
            # v6.3: write heat_score = the cognitive write signal (initial heat)
            # v7.2: initial S is mapped from the write signal (heat_init>=0.7→7 / >=0.6→5 / else→3), R=S; the 4 dimensions are tagged into metadata
            s_init = 7 if heat_init >= 0.7 else (5 if heat_init >= 0.6 else 3)
            meta_extra = dict(locals().get("meta", mem.metadata)) if isinstance(locals().get("meta", mem.metadata), dict) else {}
            meta_extra.setdefault("novelty", 1)        # new content
            meta_extra.setdefault("valence", 0)        # neutral
            meta_extra.setdefault("relevance", 0)      # pending task binding
            meta_extra.setdefault("repetition", 0)     # access count (linked to access_count)
            if mem.source:                             # v7.6: source goes into metadata, supports source-batch recall
                meta_extra["source"] = mem.source
            meta_extra["memory_type"] = CAT_MEMORY_TYPE.get(cat, "semantic")  # v7.7.0: three-way type tagging
            # v8.0 S3-1: the layering model **actually runs on the write path** — every
            #   memory's layer is determinable and auditable (this is the test for
            #   "spec, not vaporware": if layering were just documentation, `layer`
            #   wouldn't show up in metadata)
            _layer = _layer_info
            meta_extra["layer"] = _layer["layer"]
            meta_extra["layer_family"] = _layer["family"]
            if _layer["requires_source"] and not _layer["source_provided"]:
                meta_extra["layer_note"] = "L1 cognitive-layer writes should include source (for conflict provenance)"
            row = await conn.fetchrow(
                'INSERT INTO memories (user_id, project_id, content, category, embedding, metadata, valid_from, session_id, tmt_level, heat_score, storage_strength, retrieval_strength, dedup_fingerprint) '
                'VALUES ($1,$2,$3,$4,$5::vector,$6,NOW(),$7,1,$8,$9,$10,$11) '
                # ⚠️ Must include `WHERE dedup_fingerprint IS NOT NULL`:
                #   dedup_fingerprint_key is a **partial unique index** (only applies to
                #   rows that have a fingerprint, so the ~16k pre-existing NULL rows are
                #   exempt from the constraint and need no backfill).
                #   PostgreSQL ON CONFLICT inference **requires the predicate to match
                #   explicitly**, otherwise it raises
                #   `InvalidColumnReferenceError: there is no unique or exclusion
                #    constraint matching the ON CONFLICT specification` → every write 500s.
                #   (Observed: this tripped on the very first write right after the
                #   2026-09-25 production deploy; caught by the third-party functional check.)
                'ON CONFLICT (dedup_fingerprint) WHERE dedup_fingerprint IS NOT NULL DO NOTHING '
                'RETURNING id',
                uid, mem.project_id, mem.content, cat, vec_str,
                json.dumps(meta_extra), mem.session_id, heat_init, s_init, s_init, fingerprint
            )
            if row is None:
                # Concurrent same-content race fallback: the unique index already blocked
                # it, so read back the existing row and return it as idempotent
                row = await conn.fetchrow(
                    "SELECT id FROM memories WHERE dedup_fingerprint=$1 LIMIT 1", fingerprint)
                if row is None:
                    raise HTTPException(status_code=409, detail="duplicate write race")
                await conn.execute(
                    "UPDATE memories SET access_count = access_count + 1, last_accessed = NOW() WHERE id = $1",
                    row["id"])
                return {"status": "duplicate", "id": row["id"], "action": "idempotent"}
            mid = row["id"]
            if mem.entities:
                await sync_entities(conn, mid, mem.entities, uid)
            # v7.8.1: tokenize-on-write — new memories are immediately searchable via BM25 (eliminates the same-day blind window)
            await _tokenize_on_write(conn, mid, mem.content)
    return {"status": "stored", "id": mid, "category": cat}

class MemorySearch(BaseModel):
    user_id: str
    project_id: Optional[int] = None  # v7.7.0: str→int type contract fix (passing a proj_xxx accession-number string now gives a friendly 422 instead of a 500)
    query: str
    top_k: int = 5
    category_filter: Optional[str] = None
    tier_filter: Optional[str] = None
    sort: str = "hybrid"  # hybrid (default), created_at — time-ordered search
    include_frozen: bool = False  # v7.3: whether to include the frozen zone (excluded by default, zoned retrieval)

@app.post("/api/v1/memories/search")
async def search_memories(req: MemorySearch):
    """Full search: hybrid (default) or time-ordered.
    
    sort=hybrid: BM25 + embedding + rerank + trust_score
    sort=created_at: keyword ILIKE + created_at DESC (pure time order)
    """
    
    async def heat_hits(conn, ids, delta: float = 0.05) -> None:
        """v6.2 cognitive heat: a search hit → access_count+1 + heat weighting (noah's dual-weight frequency component)"""
        ids = [int(i) for i in ids]
        if not ids:
            return
        await conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_accessed = NOW(), "
            "heat_score = LEAST(1.0, heat_score + $2), "
            "retrieval_strength = GREATEST(retrieval_strength, storage_strength), "
            "mention_count = mention_count + 1, "
            "last_mention = NOW(), "
            "storage_strength = LEAST(10.0, storage_strength + FLOOR((mention_count + 1) / 5.0) - FLOOR(mention_count / 5.0)), "
            "metadata = COALESCE(metadata,'{}'::jsonb) || "
            "jsonb_build_object('repetition', COALESCE((metadata->>'repetition')::int, 0) + 1, 'last_access_ts', EXTRACT(EPOCH FROM NOW())::int) "
            "WHERE user_id = $1 AND id = ANY($3::bigint[]) AND is_deleted = FALSE",
            req.user_id, delta, ids,
        )
    
    # Time-ordered mode: skip embedding, just keyword + time sort
    if req.sort == "created_at":
        async with pool.acquire() as conn:
            query_sql = ("SELECT id, content, category, tier, heat_score, reliability, access_count, created_at "
                        "FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW()) ")
            params = [req.user_id]
            idx = 2
            if req.query.strip():
                keywords = [w.strip() for w in req.query.replace("?", "").replace("!", "")
                           .replace("\uff0c", " ").replace("\u3002", " ").split() if len(w.strip()) > 1]
                if keywords:
                    ilike_clauses = []
                    for kw in keywords:
                        ilike_clauses.append(f"content ILIKE ${idx}")
                        params.append(f"%{kw}%")
                        idx += 1
                    query_sql += "AND (" + " OR ".join(ilike_clauses) + ") "
            if req.category_filter:
                query_sql += f"AND category = ${idx} "
                params.append(req.category_filter)
                idx += 1
            if req.tier_filter:
                query_sql += f"AND tier = ${idx} "
                params.append(req.tier_filter)
                idx += 1
            query_sql += f"ORDER BY created_at DESC LIMIT ${idx}"
            params.append(req.top_k)
            rows = await conn.fetch(query_sql, *params)
            if rows:  # v6.2: heat the hits
                await heat_hits(conn, [r["id"] for r in rows[:5]])
        if not rows:
            return {"memories": [], "sort": "created_at"}
        return {"memories": [{
            "id": str(r["id"]), "content": r["content"][:300],
            "category": r["category"], "tier": r["tier"],
            "heat_score": r["heat_score"], "reliability": r["reliability"],
            "access_count": r["access_count"],
            "created_at": str(r["created_at"])[:19] if r["created_at"] else None,
        } for r in rows], "sort": "created_at"}
    
    # Default: hybrid search (embedding + BM25 + rerank)
    r_q = (await get_embedding([req.query]))[0]
    q_str = "[" + ",".join(str(x) for x in r_q) + "]"
    
    # v7.8 real BM25: jieba-tokenize the query → memory_keywords TF weighting (replaces the old fake ILIKE-based BM25)
    q_tokens = _query_tokens(req.query)
    if q_tokens:
        bm25_sql = ("(SELECT LEAST(1.0, 0.5 + COALESCE(SUM(k.freq),0)/8.0) FROM memory_keywords k "
                    "WHERE k.memory_id = m.id AND k.token = ANY($4::text[]))")
    else:
        bm25_sql = "0"
    temporal_sql = "CASE WHEN m.created_at > NOW() - INTERVAL '7 days' THEN 0.15 WHEN m.created_at > NOW() - INTERVAL '30 days' THEN 0.08 ELSE 0 END"
    
    async with pool.acquire() as conn:
        # v7.3 zoned retrieval: search the high-Rank zone (hot+normal) first → expand to the
        # full table if that's not enough (frozen excluded by default)
        # v7.3 review fix: include_frozen is an optional toggle (can be turned on when the user wants to search cold memories)
        region_filter = "AND m.temp_drawer IN ('hot','normal','cool') " if not req.include_frozen else ""
        rows = await conn.fetch(
            "SELECT m.id, m.content, m.category, m.tier, m.heat_score, m.reliability, m.access_count, m.created_at "
            "FROM memories m WHERE m.user_id=$1 AND m.is_deleted=FALSE AND (m.valid_to IS NULL OR m.valid_to > NOW()) AND m.embedding IS NOT NULL "
            + region_filter +
            "ORDER BY (0.50 * (1.0 - (m.embedding <=> $2::vector)) "
            "  + 0.15 * (" + bm25_sql + ") "
            "  + 0.15 * (" + temporal_sql + ") "
            "  + 0.10 * m.reliability "
            "  + 0.10 * GREATEST(0.0, m.heat_score)) DESC "
            "LIMIT $3",
            req.user_id, q_str, req.top_k, q_tokens
        )
        if len(rows) < req.top_k:
            # Fallback: the full table (including frozen) — expands when the zoned result set is too small
            fallback = await conn.fetch(
                "SELECT m.id, m.content, m.category, m.tier, m.heat_score, m.reliability, m.access_count, m.created_at "
                "FROM memories m WHERE m.user_id=$1 AND m.is_deleted=FALSE AND (m.valid_to IS NULL OR m.valid_to > NOW()) AND m.embedding IS NOT NULL "
                "ORDER BY (0.50 * (1.0 - (m.embedding <=> $2::vector)) "
                "  + 0.15 * (" + bm25_sql + ") "
                "  + 0.15 * (" + temporal_sql + ") "
                "  + 0.10 * m.reliability "
                "  + 0.10 * GREATEST(0.0, m.heat_score)) DESC "
                "LIMIT $3",
                req.user_id, q_str, req.top_k, q_tokens
            )
            seen = {r["id"] for r in rows}
            rows = list(rows) + [r for r in fallback if r["id"] not in seen][:req.top_k - len(rows)]
        if rows:  # v6.2: heat the hits
            await heat_hits(conn, [r["id"] for r in rows[:5]])
    
    if not rows:
        return {"memories": []}
    
    # Rerank with fallback
    try:
        docs = [r["content"] for r in rows]
        ids = [r["id"] for r in rows]
        ranked = await rerank_docs(req.query, docs, req.top_k)
        ranked_memories = []
        for rc in ranked:
            for r in rows:
                if r["content"] == rc:
                    ranked_memories.append({
                        "id": str(r["id"]), "content": r["content"],
                        "category": r["category"], "tier": r["tier"],
                        "heat_score": r["heat_score"], "reliability": r["reliability"],
                        "access_count": r["access_count"],
                        "created_at": str(r["created_at"]) if r["created_at"] else None,
                    })
                    break
        return {"memories": ranked_memories}
    except Exception:
        pass  # fallback to original order
    
    return {"memories": [
        {"id": str(r["id"]), "content": r["content"],
         "category": r["category"], "tier": r["tier"],
         "heat_score": r["heat_score"], "reliability": r["reliability"],
         "access_count": r["access_count"],
         "created_at": str(r["created_at"]) if r["created_at"] else None}
        for r in rows]
    }

@app.get("/api/v1/memories")
async def list_memories(user_id: str, limit: int = 20, tier: Optional[str] = None, 
                        category: Optional[str] = None, sort: str = "created_at",
                        search: Optional[str] = None, source: Optional[str] = None):
    """List memories with optional search and sort.
    
    sort: created_at (default), heat, updated_at
    search: optional keyword filter (ILIKE match)
    source: v7.6 — filter by metadata->>'source' (compaction archive / sub-agent batch recall)
    """
    query = "SELECT id, content, category, tier, heat_score, access_count, created_at, updated_at, valid_to FROM memories WHERE user_id = $1 AND is_deleted = FALSE AND (valid_to IS NULL OR valid_to > NOW())"
    params = [user_id]
    idx = 2
    if tier:
        query += f" AND tier = ${idx}"
        params.append(tier)
        idx += 1
    if category:
        query += f" AND category = ${idx}"
        params.append(category)
        idx += 1
    if search:
        query += f" AND content ILIKE ${idx}"
        params.append(f"%{search}%")
        idx += 1
    if source:
        query += f" AND metadata->>'source' = ${idx}"
        params.append(source)
        idx += 1
    
    # Sort: time (default) or heat
    if sort == "heat":
        query += f" ORDER BY heat_score DESC, created_at DESC LIMIT ${idx}"
    elif sort == "updated_at":
        query += f" ORDER BY updated_at DESC NULLS LAST LIMIT ${idx}"
    else:
        query += f" ORDER BY created_at DESC LIMIT ${idx}"
    params.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return {"memories": [{
        "id": r["id"], "content": r["content"][:300],
        "category": r["category"], "tier": r["tier"],
        "heat_score": r["heat_score"], "access_count": r["access_count"],
        "created_at": str(r["created_at"])[:19] if r["created_at"] else None,
        "updated_at": str(r["updated_at"])[:19] if r["updated_at"] else None,
        "expired": r["valid_to"] is not None and r["valid_to"] < __import__("datetime").datetime.now(),
    } for r in rows], "total": len(rows), "sort": sort}

@app.post("/api/v1/memories/evolve")
async def evolve_memories(user_id: str, strategy: str = "consolidate", limit: int = 50):
    async with pool.acquire() as conn:
        if strategy == "cleanup":
            # Remove very old L3 memories with low heat
            r = await conn.execute("UPDATE memories SET is_deleted=TRUE, forgotten_at=NOW() WHERE user_id=$1 AND tier='L3' AND heat_score<0.05 AND last_accessed<NOW()-INTERVAL '60 days'", user_id)
            return {"strategy": "cleanup", "affected": int(r.split()[-1])}
        elif strategy == "boost":
            # Boost frequently accessed low-tier memories
            r = await conn.execute("UPDATE memories SET heat_score=LEAST(1.0, heat_score+0.15) WHERE user_id=$1 AND access_count>5 AND heat_score<0.3 AND is_deleted=FALSE", user_id)
            return {"strategy": "boost", "affected": int(r.split()[-1])}
        elif strategy == "consolidate":
            # Merge duplicate-ish memories (same content, keep the newest)
            dups = await conn.fetch("SELECT id, content, created_at, ROW_NUMBER() OVER (PARTITION BY content ORDER BY created_at DESC) as rn FROM memories WHERE user_id=$1 AND is_deleted=FALSE ORDER BY content", user_id)
            merged = 0
            seen = {}
            for row in dups:
                if row["content"] not in seen:
                    seen[row["content"]] = row["id"]
                else:
                    keep_id = seen[row["content"]]
                    # Transfer entities
                    await conn.execute("UPDATE memory_entities SET memory_id=$1 WHERE memory_id=$2 AND entity_id NOT IN (SELECT entity_id FROM memory_entities WHERE memory_id=$1)", keep_id, row["id"])
                    await conn.execute("UPDATE memories SET is_deleted=TRUE WHERE id=$1", row["id"])
                    merged += 1
            return {"strategy": "consolidate", "merged": merged}
    return {"strategy": strategy, "status": "done"}
@app.get("/api/v1/memories/heat-top")
async def heat_top_memories(user_id: str, limit: int = 10, min_heat: float = 0.0):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, content, category, tier, heat_score, access_count, created_at FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW()) AND heat_score>=$2 ORDER BY heat_score DESC LIMIT $3", user_id, min_heat, limit)
    return {"memories": [dict(r) for r in rows]}

@app.get("/api/v1/memories/stats")
async def get_memory_stats(user_id: str = "default"):
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW())", user_id)
        by_cat = await conn.fetch(
            "SELECT category, COUNT(*) AS cnt FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW()) GROUP BY category ORDER BY cnt DESC",
            user_id
        )
        by_tier = await conn.fetch(
            "SELECT tier, COUNT(*) AS cnt FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW()) GROUP BY tier ORDER BY tier",
            user_id
        )
        avg_h = await conn.fetchval("SELECT COALESCE(AVG(heat_score), 0) FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW())", user_id)
        deleted = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE user_id=$1 AND is_deleted=TRUE", user_id)
        total_all = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE user_id=$1", user_id)
    return {
        "total": total, "total_including_deleted": total_all, "deleted": deleted,
        "avg_heat_score": float(avg_h),
        "by_category": {r["category"]: r["cnt"] for r in by_cat},
        "by_tier": {r["tier"]: r["cnt"] for r in by_tier},
    }

@app.get("/api/v1/memories/tree")
async def get_memory_tree(user_id: str = "default", limit: int = 5):
    async with pool.acquire() as conn:
        tiers = await conn.fetch(
            "SELECT tier, COUNT(*) AS cnt FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW()) GROUP BY tier ORDER BY tier",
            user_id
        )
        l1s = await conn.fetch(
            "SELECT id, content, category, heat_score FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND (valid_to IS NULL OR valid_to > NOW()) AND tier='L1' ORDER BY heat_score DESC LIMIT $2",
            user_id, limit
        )
    return {
        "tree": {r["tier"]: r["cnt"] for r in tiers},
        "l1_previews": [{"id": r["id"], "content": r["content"][:100], "category": r["category"], "heat": r["heat_score"]} for r in l1s],
    }

@app.delete("/api/v1/memories/{memory_id}")
async def delete_memory(memory_id: int, user_id: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memories SET is_deleted = TRUE, forgotten_at = NOW() WHERE id = $1 AND user_id = $2",
            memory_id, user_id
        )
        # v7.3 review fix: clean up pointers in sync on delete (prevents staleness)
        await clean_entity_relations(conn, memory_id)
    return {"status": "soft-deleted"}

# ── Update API (v7.1 drawer model: adds the ability to edit a memory in place — content correction/replacement, instead of delete-and-recreate) ──
class MemoryUpdate(BaseModel):
    content: str | None = None
    category: str | None = None
    importance: float | None = None
    heat_score: float | None = None
    metadata: dict | None = None
    pin: bool | None = None          # True = pin as a permanent volume, False = unpin

@app.put("/api/v1/memories/{memory_id}")
async def update_memory(memory_id: int, user_id: str, update: MemoryUpdate):
    """Update a memory's content/attributes. Keeps the original vector when not rebuilding the embedding; if content changes, recomputes the vector + reclassifies + refreshes the accession number."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, content, category, embedding, metadata, archive_no FROM memories WHERE id = $1 AND user_id = $2 AND is_deleted = FALSE",
            memory_id, user_id
        )
        if not row:
            return {"status": "not_found", "memory_id": memory_id}

        sets = []
        params = [memory_id, user_id]
        idx = 3
        new_content = None

        if update.content is not None:
            if len(update.content.strip()) < 1:
                return {"status": "error", "message": "content cannot be empty"}
            new_content = update.content.strip()
            sets.append(f"content = ${idx}")
            params.append(new_content)
            idx += 1
        if update.category is not None:
            sets.append(f"category = ${idx}")
            params.append(update.category)
            idx += 1
        if update.importance is not None:
            sets.append(f"importance = ${idx}")
            params.append(max(0.0, min(1.0, update.importance)))
            idx += 1
        if update.heat_score is not None:
            sets.append(f"heat_score = ${idx}")
            params.append(max(0.0, min(1.0, update.heat_score)))
            idx += 1
        if update.pin is not None:
            # pin status is stored in metadata (consistent with palace/pin)
            pin_flag = "true" if update.pin else "false"
            sets.append(f"metadata = COALESCE(metadata,'{{}}'::jsonb) || ('{{\"pinned\":\"{pin_flag}\"}}')::jsonb")
            if update.pin:
                sets.append("heat_score = GREATEST(heat_score, 0.5)")  # pinning brings it back to at least room temperature

        if update.metadata is not None:
            sets.append(f"metadata = COALESCE(metadata,'{{}}'::jsonb) || ${idx}::jsonb")
            params.append(json.dumps(update.metadata))
            idx += 1

        if not sets:
            return {"status": "no_changes"}

        sets.append("updated_at = NOW()")
        await conn.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE id = $1 AND user_id = $2",
            *params
        )

        # content changed → recompute the vector + reclassify + refresh the accession number
        if new_content is not None:
            try:
                emb = await get_embedding([new_content])
                await conn.execute(
                    "UPDATE memories SET embedding = $1::vector WHERE id = $2",
                    emb[0], memory_id
                )
            except Exception:
                pass  # embedding failure doesn't block the content update
            # Reclassify (reuses palace's classification sniffing; skipped if unavailable)
            try:
                from palace import classify
                cls = classify(new_content)
                if cls and cls.get("room") and cls.get("room") != "unfiled":
                    new_cat = cls["room"]
                    await conn.execute("UPDATE memories SET category = $1 WHERE id = $2", new_cat, memory_id)
            except Exception:
                pass
            # Record the trace
            try:
                await conn.execute(
                    "INSERT INTO memory_traces (memory_id, action, details) VALUES ($1, 'update', $2)",
                    memory_id, json.dumps({"old_len": len(row["content"]), "new_len": len(new_content)})
                )
            except Exception:
                pass

        # v7.3 review fix: sync pointers after update (rank/mention/archive_no changes)
        try:
            await conn.execute("""
                SELECT m.id, m.rank_score, m.archive_no, m.mention_count, COALESCE(m.last_mention, m.last_accessed, m.created_at)
                FROM memories m WHERE m.id = $1
                ON CONFLICT (memory_id) DO UPDATE SET
                  rank_score = EXCLUDED.rank_score,
                  archive_no = EXCLUDED.archive_no,
                  mention_count = EXCLUDED.mention_count,
                  last_mention = EXCLUDED.last_mention,
                  updated_at = NOW()
            """, memory_id)
        except Exception:
            pass

    return {"status": "updated", "memory_id": memory_id}

@app.patch("/api/v1/memories/{memory_id}")
async def patch_memory(memory_id: int, user_id: str, update: MemoryUpdate):
    """PATCH alias: same semantics as PUT (partial update)."""
    return await update_memory(memory_id, user_id, update)

# ── Pointer API (v7.3 composite algorithm: fast whole-table pointer pass + mention-triggered) ──
@app.post("/api/v1/memories/{memory_id}/feedback")
async def feedback_memory(memory_id: int, user_id: str, feedback: str):
    async with pool.acquire() as conn:
        if feedback == "positive":
            await conn.execute("UPDATE memories SET reliability = LEAST(1.0, reliability + 0.1) WHERE id = $1 AND user_id = $2", memory_id, user_id)
        elif feedback == "negative":
            await conn.execute("UPDATE memories SET reliability = GREATEST(0.0, reliability - 0.1) WHERE id = $1 AND user_id = $2", memory_id, user_id)
        await conn.execute(
            "INSERT INTO memory_traces (memory_id, action, details) VALUES ($1, 'feedback', $2)",
            memory_id, f'{{"feedback": "{feedback}"}}'
        )
    return {"status": "feedback recorded"}

@app.get("/api/v1/memories/{memory_id}/traces")
async def get_memory_trace(memory_id: int):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM memory_traces WHERE memory_id = $1 ORDER BY executed_at", memory_id)
    return {"traces": [dict(r) for r in rows]}

# ── Multimodal memory (HERMES passes a description text; no vision API is called) ──
class MultiModalCreate(BaseModel):
    user_id: str
    content: str
    media_urls: List[str]
    media_type: str = "image"

@app.post("/api/v1/media-memories")
async def create_multimodal(mem: MultiModalCreate):
    raw_v = (await get_embedding([mem.content]))[0]
    v_str = "[" + ",".join(str(x) for x in raw_v) + "]"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO media_memories (user_id, content, media_type, media_url, embedding) VALUES ($1,$2,$3,$4,$5::vector)",
            mem.user_id, mem.content, mem.media_type, mem.media_urls[0] if mem.media_urls else "", v_str
        )
    return {"status": "stored"}

@app.get("/api/v1/media-memories")
async def search_media(user_id: str, query: str, top_k: int = 5):
    v = (await get_embedding([query]))[0]
    v_str = "[" + ",".join(str(x) for x in v) + "]"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, media_type, media_url, metadata, created_at, "
            "1 - (embedding <=> $2::vector) AS score "
            "FROM media_memories WHERE user_id=$1 "
            "ORDER BY score DESC LIMIT $3",
            user_id, v_str, top_k
        )
    return [dict(r) for r in rows]


# ── Belief API ──
@app.post("/api/v1/beliefs")
async def create_belief(bel: BeliefCreate):
    raw_vec = (await get_embedding([bel.content]))[0]
    vec_str = "[" + ",".join(str(x) for x in raw_vec) + "]"
    async with pool.acquire() as conn:
        # Check whether the same belief already exists
        existing = await conn.fetchrow(
            "SELECT id, confidence, trajectory FROM beliefs WHERE user_id=$1 AND content=$2 AND status!='contradicted'",
            bel.user_id, bel.content
        )
        if existing:
            # Update confidence (average the two)
            new_conf = (existing["confidence"] + bel.confidence) / 2
            await conn.execute(
                "UPDATE beliefs SET confidence=$1, updated_at=NOW() WHERE id=$2",
                new_conf, existing["id"]
            )
            return {"status": "updated_confidence", "id": existing["id"], "confidence": new_conf}
        row = await conn.fetchrow(
            "INSERT INTO beliefs (user_id, content, confidence, evidence_memories, embedding, status) "
            "VALUES ($1,$2,$3,$4,$5::vector,$6) RETURNING id",
            bel.user_id, bel.content, bel.confidence, bel.evidence_memories, vec_str, bel.status
        )
    return {"status": "created", "id": row["id"]}

@app.post("/api/v1/beliefs/search")
async def search_beliefs(req: BeliefSearch):
    r_q = (await get_embedding([req.query]))[0]
    q_str = "[" + ",".join(str(x) for x in r_q) + "]"
    conditions = ["user_id = $1"]
    params = [req.user_id]
    idx = 2
    if req.status_filter:
        conditions.append(f"status = ${idx}")
        params.append(req.status_filter)
        idx += 1
    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT id, content, confidence, status, trajectory, valid_from, "
            f"embedding <=> ${idx}::vector AS dist FROM beliefs WHERE {where} "
            f"ORDER BY dist LIMIT ${{}}".format(idx+1),
            *params, q_str, req.top_k
        )
    return [dict(r) for r in rows]

@app.get("/api/v1/beliefs/{belief_id}")
async def get_belief(belief_id: int, user_id: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM beliefs WHERE id=$1 AND user_id=$2", belief_id, user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Belief not found")
    return dict(row)

@app.post("/api/v1/beliefs/{belief_id}/evolve")
async def evolve_belief(belief_id: int, user_id: str, new_confidence: float = None, evidence_id: int = None):
    """Update a belief: adjust confidence/add evidence/auto-evolve status"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, confidence, evidence_memories, status FROM beliefs WHERE id=$1 AND user_id=$2",
            belief_id, user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Belief not found")
        new_status = row["status"]
        new_conf = row["confidence"] if new_confidence is None else new_confidence
        evidences = row["evidence_memories"] or []
        if evidence_id and evidence_id not in evidences:
            evidences.append(evidence_id)
            new_conf = min(1.0, new_conf + 0.1)  # new evidence +0.1
        # Auto-evolve status
        if new_conf >= 0.7:
            new_status = "established"
        elif new_conf >= 0.4:
            new_status = "tentative"
        elif new_conf < 0.3:
            new_status = "hypothesis"
        trajectory = row.get("trajectory") or []
        if new_status != row["status"]:
            trajectory.append(f"{row['status']}→{new_status}")
        await conn.execute(
            "UPDATE beliefs SET confidence=$1, evidence_memories=$2, status=$3, trajectory=$4 WHERE id=$5",
            new_conf, evidences, new_status, trajectory, belief_id
        )
    return {"id": belief_id, "confidence": new_conf, "status": new_status}


# ── Reflection and self-evolution ──
@app.post("/api/v1/reflect")
async def reflect(user_id: str, mode: str = "light"):
    async with pool.acquire() as conn:
        # 0. User-activity awareness (v7.2 forgetting throttle): a write/access in the last
        #    7 days = active. While the owner is away, decay is paused — memories wait for
        #    their owner instead of cooling down alone (user's hard rule: don't let everything
        #    go cold while they're on vacation).
        last_active = await conn.fetchval("""
            SELECT GREATEST(MAX(created_at), MAX(last_accessed)) FROM memories
            WHERE user_id = $1 AND is_deleted = FALSE
        """, user_id)
        user_active = last_active is not None and last_active >= datetime.now(timezone.utc) - timedelta(days=7)
        absence_days = (datetime.now(timezone.utc) - last_active).days if (last_active is not None and not user_active) else 0
        # 1. Heat v2: multi-dimensional decay
        # Time decay (the longer since last access, the colder); v6.3: protected decay — pinned/preference barely decay
        # v7.2: all decay is paused while the user is inactive (just transparently marks pause)
        if user_active:
            await conn.execute("""
                UPDATE memories SET heat_score = GREATEST(0.0, heat_score -
                    CASE
                        WHEN metadata->>'pinned' = 'true' OR category = 'preference' THEN 0.005
                        WHEN last_accessed IS NULL THEN 0.02
                        WHEN last_accessed < NOW() - INTERVAL '90 days' THEN 0.08
                        WHEN last_accessed < NOW() - INTERVAL '30 days' THEN 0.04
                        WHEN last_accessed < NOW() - INTERVAL '7 days' THEN 0.02
                        ELSE 0.01
                    END
                ) WHERE user_id = $1 AND is_deleted = FALSE AND heat_score > 0.02
            """, user_id)
            # Access weighting (recent high-frequency access +0.05)
            await conn.execute("""
                UPDATE memories SET heat_score = LEAST(1.0, heat_score + 0.05)
                WHERE user_id = $1 AND is_deleted = FALSE AND access_count >= 5
                  AND last_accessed > NOW() - INTERVAL '7 days'
            """, user_id)
        else:
            # Owner absent: no decay, set the pause flag (transparent, cleared on return)
            await conn.execute("""
                UPDATE memories SET metadata = COALESCE(metadata,'{}'::jsonb) ||
                  ('{"paused_absence":true,"paused_days":' || $2::text || '}')::jsonb
                WHERE user_id = $1 AND is_deleted = FALSE
            """, user_id, absence_days)
            # Return detection: if a pause flag was set previously but the user is active now, clear it (triggered on the next active round)
        # Active return: clear the pause flag (the user is back, memories resume independent decay)
        if user_active:
            await conn.execute("""
                UPDATE memories SET metadata = metadata - 'paused_absence' - 'paused_days'
                WHERE user_id = $1 AND is_deleted = FALSE
                  AND metadata ? 'paused_absence'
            """, user_id)
        # Accelerated decay for conflicting memories (always runs — a conflict is a data-quality issue, not something to preserve just because the owner is away)
        await conn.execute("""
            UPDATE memories SET heat_score = GREATEST(0.0, heat_score - 0.1)
            WHERE user_id = $1 AND is_deleted = FALSE AND invalid_at IS NOT NULL
        """, user_id)
        # 2. Automatic tier migration (v6.0: tier = value tiering, decoupled from the TMT time-tree's tmt_level)
        #    L4 no longer deletes directly — just marks pending cleanup, recoverable; actual cleanup is left to the cleanup API
        await conn.execute("UPDATE memories SET tier = 'L1' WHERE user_id = $1 AND heat_score > 0.7 AND tier != 'L1'", user_id)
        await conn.execute("UPDATE memories SET tier = 'L2' WHERE user_id = $1 AND heat_score BETWEEN 0.2 AND 0.7 AND tier NOT IN ('L2','L3','L4')", user_id)
        await conn.execute("UPDATE memories SET tier = 'L3' WHERE user_id = $1 AND heat_score < 0.2 AND last_accessed < NOW() - INTERVAL '30 days' AND tier NOT IN ('L3','L4')", user_id)
        await conn.execute("UPDATE memories SET tier = 'L4', forgotten_at = NOW() WHERE user_id = $1 AND heat_score < 0.05 AND last_accessed < NOW() - INTERVAL '90 days' AND is_deleted = FALSE AND tier != 'L4'", user_id)
        # 2.5 Dual-drawer flow (v7.1 drawer model: temperature drawer × time drawer)
        # Temperature: hot>=0.7 / normal 0.3-0.7 / cool 0.1-0.3 / frozen<0.1 (aligned with tier L1-L4 but an independent dimension)
        # Time: recent<30d / mid 30-90d / long>=90d (based on last_accessed)
        # v7.3: temp_drawer is now uniformly set by Rank percentile bands, so the old heat-based rule is removed here (avoids a full-table no-op rewrite)
        await conn.execute("""
            UPDATE memories SET time_drawer = CASE
                WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '30 days' THEN 'recent'
                WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '90 days' THEN 'mid'
                ELSE 'long'
            END
            WHERE user_id = $1 AND is_deleted = FALSE
              AND time_drawer IS DISTINCT FROM (
                CASE
                  WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '30 days' THEN 'recent'
                  WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '90 days' THEN 'mid'
                  ELSE 'long'
                END)
        """, user_id)
        # 2.6 Bjork S/R separation (v7.2): storage strength S doesn't decay / retrieval strength R decays exponentially (30-day half-life)
        #    R = R0 * 0.5^(days/30), floor 1; reset to R=S on access; pinned items floor at R>=5
        #    Rollback switch: skipped when metadata->>'use_sr' = 'false' (keeps the pure-heat mode)
        #    v7.2 throttle: R doesn't decay while the user is inactive (user_active=False) — only the access reset is kept, drawers stay as-is
        if user_active:
            await conn.execute("""
                UPDATE memories SET
                  retrieval_strength = GREATEST(1.0,
                    CASE
                      WHEN COALESCE(metadata->>'pinned','false') = 'true' THEN GREATEST(5.0, retrieval_strength * POW(0.5, (EXTRACT(EPOCH FROM (NOW() - COALESCE(last_accessed, created_at)))/86400.0)/30.0))
                      WHEN last_accessed IS NOT NULL AND last_accessed >= NOW() - INTERVAL '7 days'
                        THEN GREATEST(storage_strength, retrieval_strength * POW(0.5, (EXTRACT(EPOCH FROM (NOW() - last_accessed))/86400.0)/30.0))
                      ELSE retrieval_strength * POW(0.5, (EXTRACT(EPOCH FROM (NOW() - COALESCE(last_accessed, created_at)))/86400.0)/30.0)
                    END),
                  temp_drawer = CASE
                    WHEN storage_strength >= 7 AND retrieval_strength >= 5 THEN 'hot'
                    WHEN storage_strength >= 5 OR retrieval_strength >= 3 THEN 'normal'
                    WHEN storage_strength >= 3 THEN 'cool'
                    ELSE 'frozen'
                  END
                WHERE user_id = $1 AND is_deleted = FALSE
                  AND COALESCE(metadata->>'use_sr','true') = 'true'
                  AND retrieval_strength > 1.05
            """, user_id)
        else:
            # Owner absent: only keep the "recent access reset", no time decay; clearing the pause flag is handled on return
            await conn.execute("""
                UPDATE memories SET retrieval_strength = GREATEST(retrieval_strength, storage_strength)
                WHERE user_id = $1 AND is_deleted = FALSE
                  AND last_accessed IS NOT NULL AND last_accessed >= NOW() - INTERVAL '7 days'
            """, user_id)
        # Forget-candidate flag: frozen + long + not pinned + not preference → forget_candidate=true (no physical delete; waits for a 30-day grace period or user confirmation)
        await conn.execute("""
            UPDATE memories SET metadata = COALESCE(metadata,'{}'::jsonb) || '{"forget_candidate":true}'::jsonb
            WHERE user_id = $1 AND is_deleted = FALSE
              AND temp_drawer = 'frozen' AND time_drawer = 'long'
              AND COALESCE(metadata->>'pinned','false') != 'true'
              AND category != 'preference'
        """, user_id)
        # Forget-candidate cooldown: memories that were hit before but are no longer relevant get an extra -0.03 each reflect round (accelerated settling, analogous to Mem0's salience idea)
        await conn.execute("""
            UPDATE memories SET heat_score = GREATEST(0.0, heat_score - 0.03)
            WHERE user_id = $1 AND is_deleted = FALSE
              AND COALESCE(metadata->>'forget_candidate','false') = 'true'
              AND COALESCE(metadata->>'pinned','false') != 'true'
        """, user_id)
        # 2.7 Composite Rank (v7.3 tidy-up optimization): shifts from forgetting to dynamic tidying
        #    Rank = 0.3S + 0.3R + 0.2ln(mention+1)/ln(1001)*10 + 0.2heat*10
        #    Drawer tiers are set by Rank percentile: hot top 10% / normal 10-30% / cool 30-70% / frozen 70%+
        await conn.execute("""
            WITH ranked AS (
              SELECT id,
                ROUND((0.3*storage_strength + 0.3*retrieval_strength +
                       0.2*10.0*(LN(mention_count+1)/LN(1001)) +
                       0.2*heat_score*10)::numeric, 4) AS rank_score
              FROM memories WHERE user_id=$1 AND is_deleted=FALSE
            ),
            percentile AS (
              SELECT id, rank_score,
                PERCENT_RANK() OVER (ORDER BY rank_score DESC) AS pr
              FROM ranked
            )
            UPDATE memories m SET
              rank_score = p.rank_score,
              temp_drawer = CASE
                WHEN ABS(p.rank_score - COALESCE(m.rank_score, 0)) < 2.0 THEN m.temp_drawer
                WHEN m.category IN ('knowledge','pitfall','reference','preference') THEN
                  CASE WHEN p.pr <= 0.10 THEN 'hot' WHEN p.pr <= 0.30 THEN 'normal' ELSE 'cool' END
                WHEN p.pr <= 0.10 THEN 'hot'
                WHEN p.pr <= 0.30 THEN 'normal'
                WHEN p.pr <= 0.70 THEN 'cool'
                ELSE 'frozen'
              END
            FROM percentile p WHERE m.id = p.id
              AND m.rank_score IS DISTINCT FROM p.rank_score
        """, user_id)
        # v7.3 review fix: force a full drawer re-sync every Sunday (clears jitter-buffer boundary stragglers)
        today = datetime.now(timezone.utc)
        if today.weekday() == 6:  # Sunday
            await conn.execute("""
                WITH ranked AS (
                  SELECT id,
                    ROUND((0.3*storage_strength + 0.3*retrieval_strength +
                           0.2*10.0*(LN(mention_count+1)/LN(1001)) +
                           0.2*heat_score*10)::numeric, 4) AS rank_score
                  FROM memories WHERE user_id=$1 AND is_deleted=FALSE
                ),
                percentile AS (
                  SELECT id, rank_score,
                    PERCENT_RANK() OVER (ORDER BY rank_score DESC) AS pr
                  FROM ranked
                )
                UPDATE memories m SET
                  rank_score = p.rank_score,
                  temp_drawer = CASE
                    WHEN m.category IN ('knowledge','pitfall','reference','preference') THEN
                      CASE WHEN p.pr <= 0.10 THEN 'hot' WHEN p.pr <= 0.30 THEN 'normal' ELSE 'cool' END
                    WHEN p.pr <= 0.10 THEN 'hot'
                    WHEN p.pr <= 0.30 THEN 'normal'
                    WHEN p.pr <= 0.70 THEN 'cool'
                    ELSE 'frozen'
                  END,
                  metadata = COALESCE(metadata,'{}'::jsonb) - 'auto_mention_today'
                FROM percentile p WHERE m.id = p.id
            """, user_id)
        # (v7.8: the memory_pointer table has been removed — it was built but never used, and upkeep cost was high)
        # 3. Deep mode: entity extraction
        if mode == "deep":
            unproc = await conn.fetch("SELECT m.id, m.content FROM memories m LEFT JOIN memory_entities me ON m.id = me.memory_id WHERE m.user_id = $1 AND me.memory_id IS NULL AND m.is_deleted = FALSE LIMIT 100", user_id)
            extracted = 0
            import re
            for row in unproc:
                cand = set()
                for m in re.finditer(r'[\u201c\u201d\u300c\u300d]([^\u201c\u201d\u300c\u300d]{2,15})[\u201c\u201d\u300c\u300d]', row["content"]):
                    cand.add(m.group(1).strip())
                if not cand:
                    for p in re.split(r'[、，．！？,.!?\s的和在是了]+', row["content"]):
                        p = p.strip()
                        if 2 <= len(p) <= 15:
                            cand.add(p)
                for name in cand:
                    try:
                        ex = await conn.fetchrow("SELECT id FROM entities WHERE user_id=$1 AND name=$2", user_id, name)
                        if not ex:
                            await sync_entities(conn, row["id"], [name], user_id)
                            extracted += 1
                    except Exception:
                        pass
            if extracted > 0:
                await conn.execute("UPDATE memories SET heat_score = heat_score + 0.1 WHERE user_id = $1 AND is_deleted = FALSE AND id IN (SELECT memory_id FROM memory_entities)", user_id)
    return {"status": f"Reflection ({mode}) completed"}

@app.post("/api/v1/cleanup")
async def cleanup(user_id: str, threshold: float = 0.1):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE memories SET is_deleted = TRUE, forgotten_at = NOW() WHERE user_id = $1 AND heat_score < $2", user_id, threshold)
    return {"status": "cleanup done"}

@app.get("/api/v1/health/{user_id}")
async def health_report(user_id: str):
    async with pool.acquire() as conn:
        tiers = await conn.fetch("SELECT tier, COUNT(*) as cnt FROM memories WHERE user_id = $1 AND is_deleted = FALSE GROUP BY tier", user_id)
    return {"tiers": {r["tier"]: r["cnt"] for r in tiers}}


# ══════════════════════════════════════════════════════════════════════════════
# v8.0 S1-4 · Observability: write/recall latency instrumentation + reliability check
# ══════════════════════════════════════════════════════════════════════════════
# Prior state (as measured): `perf_alert.py` has a 2000ms threshold written in, but there's
#   **no instrumentation at all** → the 100-400ms README claims were never actually
#   measured, so the alert threshold was meaningless in practice.
# Fix: an in-process ring buffer records every endpoint's latency, exposing p50/p95/p99 + counts.
#   Cost ~0 (no external dependency, no I/O); capped at _LATENCY_CAP entries/route, oldest dropped when full.
_LATENCY: dict = {}
_LATENCY_CAP = 500
_LATENCY_ROUTES = (
    "POST /api/v1/memories",
    "POST /api/v1/memories/search",
    "GET /api/v1/palace/summon",
    "POST /api/v1/dialectic",
)


def _latency_key(method: str, path: str) -> str:
    """Only scores **registered key routes** (avoids high-cardinality paths eating up memory)."""
    for r in _LATENCY_ROUTES:
        m, p = r.split(" ", 1)
        if method == m and (path == p or path.startswith(p + "/") or path.startswith(p + "?")):
            return r
    return "other"


def _percentile(sorted_vals: list, q: float):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return round(sorted_vals[idx], 1)


@app.middleware("http")
async def latency_middleware(request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    key = _latency_key(request.method, request.url.path)
    buf = _LATENCY.get(key)
    if buf is None:
        from collections import deque
        buf = _LATENCY[key] = deque(maxlen=_LATENCY_CAP)
    buf.append(dt_ms)
    response.headers["X-Resp-Ms"] = f"{dt_ms:.1f}"
    return response


@app.get("/api/v1/metrics")
async def metrics():
    """v8.0 S1-4: measured latency + a reliability check, all in one request.

    Design principle: expose **actual measured numbers**, not just "status: ok".
    Latency stats are per-process (each worker keeps its own), so the PID is
    included to tell them apart.
    """
    lat = {}
    for k, buf in _LATENCY.items():
        vals = sorted(buf)
        if not vals:
            continue
        lat[k] = {"n": len(vals), "p50": _percentile(vals, 0.50),
                  "p95": _percentile(vals, 0.95), "p99": _percentile(vals, 0.99),
                  "max": round(vals[-1], 1)}
    async with pool.acquire() as conn:
        db = await conn.fetchrow(
            "SELECT count(*) AS total, count(*) FILTER (WHERE is_deleted) AS tombstone, "
            "pg_total_relation_size('memories') AS bytes FROM memories")
        gc = await conn.fetchrow(
            "SELECT run_at, batch, purged, refused_ref, dry_run FROM gc_log "
            "ORDER BY id DESC LIMIT 1") if await conn.fetchval(
            "SELECT to_regclass('mnemosyne.gc_log') IS NOT NULL") else None
        dup = await conn.fetchval(
            "SELECT count(*) FROM (SELECT dedup_fingerprint FROM memories "
            "WHERE dedup_fingerprint IS NOT NULL GROUP BY dedup_fingerprint "
            "HAVING count(*) > 1) t")
    return {
        "version": _read_version(),
        "pid": os.getpid(),
        "latency_ms": lat,
        "latency_note": "进程内环形缓冲(size≤%d/路由)；多 worker 各自统计" % _LATENCY_CAP,
        "db": {
            "total": db["total"],
            "tombstone": db["tombstone"],
            "tombstone_ratio": round(db["tombstone"] / max(db["total"], 1), 4),
            "table_mb": round(db["bytes"] / 1048576, 1),
            # v8.0 S1-2 self-check: if the idempotency key's unique index is working, this is always 0
            "duplicate_fingerprint_groups": dup,
            "idempotency_index_effective": dup == 0,
        },
        "last_gc": dict(gc) if gc else None,
    }


# ── v8.0 S3-1 · Memory layering model (public entry point for the executable spec) ────────────────────────
@app.get("/api/v1/layers")
async def layers_spec():
    """The full layering model + **self-check results** (not documentation — a runnable assertion)."""
    return {"layers": layers_mod.LAYERS,
            "artifact_index": layers_mod.ARTIFACT_INDEX,
            "category_to_layer": layers_mod.CATEGORY_TO_LAYER,
            "self_check": layers_mod.self_check()}


@app.get("/api/v1/layers/classify")
async def layers_classify(category: str = "knowledge", has_artifact: bool = False,
                          source: str = ""):
    """Determine which layer a pending memory write falls into, and return that layer's write rules and conflict strategy."""
    return layers_mod.classify_layer(category, has_artifact=has_artifact,
                                     source=source or None)


# ── Self-describing API ──
def _read_version() -> str:
    try:
        return open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")).read().strip()
    except Exception:
        return "unknown"

@app.get("/")
async def root():
    return {"service": f"Mnemosyne OS v{_read_version()}", "docs": "/api/v1/capabilities"}

@app.get("/api/v1/capabilities")
async def capabilities():
    _ver = _read_version()
    return {
        "service": f"Mnemosyne OS v{_ver}",
        "version": _ver,
        "description": "个人AI记忆库 — 存入、搜索、追溯、演化",
        "auth": "X-API-Token (Nginx层)",
        "base_url": "https://your-server.example.com/mnemosyne",
        "endpoints": [
            {"path": "POST /api/v1/memories", "purpose": "存入一条记忆。自动向量化+实体提取+矛盾检测(相似内容合并/冲突标记时间窗口)", "params": {"user_id": "str", "content": "str", "category": "knowledge|pitfall|reference|project|ops|deploy|preference|session|worklog|temp (受控词表 10 类, 非法值自动归一化为 knowledge; 权威见 docs/schema.sql)"}, "example": "curl -X POST https://your-server.example.com/mnemosyne/api/v1/memories -H 'X-API-Token: <token>' -H 'Content-Type: application/json' -d '{\"user_id\":\"default\",\"content\":\"要记住的内容\"}'", "tags": ["core", "write"]},
            {"path": "POST /api/v1/memories/search", "purpose": "4维检索(语义向量+BM25关键词+时序加权+图遍历) + 交叉编码重排", "params": {"user_id": "str", "query": "str", "top_k": "int(5)"}, "example": "curl -X POST https://your-server.example.com/mnemosyne/api/v1/memories/search -H 'X-API-Token: <token>' -H 'Content-Type: application/json' -d '{\"user_id\":\"default\",\"query\":\"搜索内容\"}'", "tags": ["core", "read"]},
            {"path": "GET /api/v1/memories", "purpose": "按热度/分类列出记忆", "params": {"user_id": "str", "limit": "int(20)", "tier": "str?", "category": "str?"}, "tags": ["core", "read"]},
            {"path": "GET /api/v1/memories/{id}", "purpose": "获取单条记忆详情", "tags": ["core", "read"]},
            {"path": "DELETE /api/v1/memories/{id}", "purpose": "软删除记忆", "params": {"user_id": "str (query 必填)"}, "tags": ["core", "write"]},
            {"path": "POST /api/v1/memories/{id}/feedback", "purpose": "记录反馈(positive/negative), 影响reliability评分", "params": {"user_id": "str (query 必填)", "feedback": "positive|negative (query 必填)"}, "tags": ["core", "write"]},
            {"path": "POST /api/v1/memories/{id}/restore", "purpose": "恢复已删除的记忆", "params": {"user_id": "str (query 必填)"}, "tags": ["core", "write"]},
            {"path": "POST /api/v1/memories/evolve", "purpose": "触发记忆进化(合并重复/清理/提升)", "tags": ["system"]},
            {"path": "GET /api/v1/memories/heat-top", "purpose": "热度排行", "params": {"user_id": "str", "limit": "int", "min_heat": "float(0)"}, "returns": "memories[].heat_score (字段名是 heat_score, 不是 heat)", "tags": ["core", "read"]},
            {"path": "POST /api/v1/reflect", "purpose": "手动触发Reflector: 热度衰减+层级迁移+实体提取", "params": {"user_id": "str", "mode": "light|deep"}, "tags": ["system"]},
            {"path": "POST /api/v1/beliefs", "purpose": "创建信念。自动与已有信念合并置信度", "params": {"user_id": "str", "content": "str", "confidence": "float(0.5)", "status": "tentative|established"}, "tags": ["belief"]},
            {"path": "POST /api/v1/beliefs/search", "purpose": "语义搜索信念", "tags": ["belief"]},
            {"path": "GET /api/v1/beliefs/{id}", "purpose": "获取信念详情(含置信度/轨迹/证据)", "tags": ["belief"]},
            {"path": "POST /api/v1/beliefs/{id}/evolve", "purpose": "演化信念: 调整置信度+添加证据, 状态自动演进", "tags": ["belief"]},
            {"path": "POST /api/v1/graph/search", "purpose": "AGE知识图谱多跳搜索(通过实体关联发现记忆)", "tags": ["graph"]},
            {"path": "POST /api/v1/wiki", "purpose": "创建Wiki页面(手动知识库)", "tags": ["wiki"]},
            {"path": "POST /api/v1/wiki/search", "purpose": "语义搜索Wiki", "tags": ["wiki"]},
            {"path": "POST /api/v1/extract-entities", "purpose": "从未处理记忆中批量提取实体到AGE图", "tags": ["system"]},
            {"path": "POST /api/v1/media-memories", "purpose": "存入多模态记忆", "tags": ["media"]},
            {"path": "POST /api/v1/skills/sync", "purpose": "技能资产批量同步(幂等, v7.7.0)", "tags": ["skills"]},
            {"path": "POST /api/v1/skills/search", "purpose": "语义召唤技能(含沉寂可唤醒, v7.7.0)", "tags": ["skills"]},
            {"path": "PATCH /api/v1/skills/{skill_name}", "purpose": "技能状态流转(唤醒/降级, v7.7.0)", "tags": ["skills"]},
            {"path": "POST /api/v1/skills/{skill_name}/touch", "purpose": "技能使用计数(v7.7.0)", "tags": ["skills"]},
            {"path": "POST /api/v1/injection/plan", "purpose": "注入调度: 按场景返回注入流(v7.7.0)", "tags": ["injection"]},
            {"path": "GET /api/v1/echo", "purpose": "连通性测试", "tags": ["system"]},
            {"path": "GET /api/v1/capabilities", "purpose": "本能力清单", "tags": ["meta"]},
            {"path": "GET /api/v1/health/{user_id}", "purpose": "健康检查(层级统计)", "tags": ["system"]}
        ],
        "graceful_degradation": {
            "rerank_unavailable": "降级为纯向量+BM25+时序混合搜索(不经过交叉编码)",
            "embed_unavailable": "全部API不可用(需修复llama-embed.service)"
        },
        "quick_start": "1. 存记忆 POST /api/v1/memories → 2. 搜记忆 POST /api/v1/memories/search → 3. 触反思 POST /api/v1/reflect → 4. 看健康 GET /api/v1/health/{user_id}"
    }

@app.get("/api/v1/echo")
async def echo():
    # v7.8: read the version from the VERSION file, fixing the root cause of hardcoded-version drift
    try:
        ver = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")).read().strip()
    except Exception:
        ver = "unknown"
    return {"status": "ok", "service": "Mnemosyne OS", "version": ver}

# ── v7.0 Memory Palace API ──
@app.get("/api/v1/palace/status")
async def palace_status(user_id: str = "default"):
    """Palace status: category-tree stats + card count + accession-number coverage"""
    import palace
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE user_id=$1 AND is_deleted=FALSE", user_id)
        archived = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE user_id=$1 AND is_deleted=FALSE AND archive_no IS NOT NULL", user_id)
        cards = await conn.fetchval(
            "SELECT count(*) FROM tome_cards", )
        dist = await conn.fetch(
            "SELECT wing, room, count(*) n FROM tome_cards GROUP BY wing, room ORDER BY wing, room LIMIT 30")
    return {
        "total_memories": total,
        "archived": archived,
        "archive_coverage": round(100.0 * (archived or 0) / max(total, 1), 1),
        "tome_cards": cards,
        "taxonomy": [{"wing": r["wing"], "room": r["room"], "count": r["n"]} for r in dist],
    }

@app.post("/api/v1/palace/archive")
async def palace_archive(user_id: str = "default", limit: int = 500):
    """Manually trigger backlog archiving (idempotent, batched)"""
    import palace
    result = await palace.init_palace(pool)
    # Return this run's archive count (counts only newly processed items)
    return {"classified": result["classified"], "cards": result["cards"]}

@app.get("/api/v1/palace/summon")
async def palace_summon(q: str, user_id: str = "default", top_k: int = 5,
                        fused: bool = False, candidate_k: int = 50):
    """Magic summon: three channels (exact name match / guided scope / resonant semantics)

    v8.0 S2-1: added `fused=true` → runs a four-channel **RRF fusion** (each channel first
    takes candidate_k candidates, then the fused result is truncated to top_k).
    **Default false = behaves identically to v7.8.4** — the default isn't changed until
    the evaluation numbers are in (R3: measure first, change later).
    """
    import palace
    if fused:
        return await palace.summon_fused(pool, q, user_id, top_k, candidate_k=candidate_k)
    result = await palace.summon(pool, q, user_id, top_k)
    return result

@app.post("/api/v1/palace/refine")
async def palace_refine(limit: int = 20):
    """Archive-room refinement: LLM generates titles/summaries/tags"""
    import palace
    return await palace.refine_cards(pool, limit=limit)

@app.post("/api/v1/palace/extract")
async def palace_extract(batch: int = 20):
    """Archive-room fact extraction: conversation → facts → auto-filing (idempotent)"""
    import palace
    return await palace.extract_facts_pipeline(pool, batch=batch)

@app.post("/api/v1/palace/lifecycle")
async def palace_lifecycle():
    """Eternal tiering: short-term expiry removal + heat protection for permanent volumes"""
    import palace
    return await palace.apply_lifecycle(pool)

@app.post("/api/v1/palace/pin")
async def palace_pin(memory_id: int, retention: str = "permanent"):
    """Pin a memory as a permanent volume (rules/hard-limits/identity-type content)"""
    if retention not in ("permanent", "long", "short"):
        raise HTTPException(status_code=400, detail="retention must be permanent/long/short")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tome_cards SET retention=$1 WHERE memory_id=$2", retention, memory_id)
    return {"memory_id": memory_id, "retention": retention}

@app.post("/api/v1/graph/search")
async def graph_search(query: str, user_id: str, max_hops: int = 2):
    """Entity-linked memory retrieval (v7.8: removed AGE multi-hop — the graph has been cut; max_hops is kept for compatibility)"""
    r_q = (await get_embedding([query]))[0]
    q_str = "[" + ",".join(str(x) for x in r_q) + "]"
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, type, embedding <=> $1::vector AS dist FROM entities WHERE user_id = $2 ORDER BY dist LIMIT 5", q_str, user_id)
        entity_ids = [r["id"] for r in rows]
        if not entity_ids:
            return {"nodes": [], "memories": []}
        mems = await conn.fetch(
            "SELECT m.content FROM memories m "
            "JOIN memory_entities me ON m.id = me.memory_id "
            "WHERE me.entity_id = ANY($1) LIMIT 10",
            entity_ids
        )
    return {"nodes": [dict(r) for r in rows], "memories": [m["content"] for m in mems]}

@app.post("/api/v1/wiki/search")
async def search_wiki(body: WikiSearchRequest):
    """Semantic search over Wiki (v7.5: hybrid = vector HNSW + BM25 keywords + graph expansion, RRF-fused)"""
    query = body.query
    user_id = body.user_id
    top_k = body.top_k
    hybrid = body.hybrid if hasattr(body, "hybrid") else True
    do_rerank = body.rerank if hasattr(body, "rerank") else False
    use_graph = body.graph if hasattr(body, "graph") else True

    r_q = (await get_embedding([query]))[0]
    q_str = "[" + ",".join(str(x) for x in r_q) + "]"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT wp.id, wp.title, wp.content, wp.source_path, wp.source_url, wp.source_type, "
            "wp.embedding <=> $1::vector AS dist "
            "FROM wiki_pages wp WHERE wp.user_id = $2 AND wp.content IS NOT NULL AND wp.embedding IS NOT NULL "
            "ORDER BY wp.embedding <=> $1::vector LIMIT $3",
            q_str, user_id, 50  # v7.5: candidate pool of 50, so new pages that BM25 hits aren't crowded out of the vector ranking
        )
        vec_ranked = [(r["id"], r["dist"]) for r in rows]

        # BM25 keyword channel
        bm25_scores = {}
        if hybrid:
            try:
                import jieba
                import os as _os
                _dict_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "wiki", "wiki_dict.txt")
                if _os.path.exists(_dict_path):
                    jieba.load_userdict(_dict_path)  # v7.5: domain dictionary (expert review P1)
                from wiki.wiki_bm25 import compute_bm25_scores, rrf_fuse
                query_tokens = [t.strip() for t in jieba.cut(query) if len(t.strip()) >= 2]
                if query_tokens:
                    kw_rows = await conn.fetch(
                        "SELECT wk.page_id, wk.token, wk.freq, "
                        "(SELECT count(DISTINCT page_id) FROM wiki_keywords wk2 WHERE wk2.token=wk.token) AS pages_with_token "
                        "FROM wiki_keywords wk WHERE wk.token = ANY($1::text[]) ORDER BY wk.freq DESC",
                        query_tokens
                    )
                    total_pages = await conn.fetchval("SELECT count(*) FROM wiki_pages WHERE user_id=$1 AND content IS NOT NULL", user_id)
                    bm25_scores = compute_bm25_scores(list(kw_rows), query_tokens, total_pages or 71)
            except Exception as e:
                logger.warning(f"wiki BM25 channel failed (degraded): {e}")

        # Graph expansion channel (v7.5 P1: entity anchoring + 1 hop)
        graph_scores = {}
        if use_graph:
            try:
                from wiki.wiki_graph import graph_expand
                gres = await graph_expand(conn, query, user_id, top_k)
                graph_scores = gres.get("page_scores", {})
            except Exception as e:
                logger.warning(f"wiki graph channel failed (degraded): {e}")

        # Three-way RRF fusion
        if bm25_scores or graph_scores:
            fused = rrf_fuse(vec_ranked, bm25_scores, graph_scores)
            id2row = {r["id"]: r for r in rows}
            ranked = []
            missing_ids = []
            for pid, _ in fused[:top_k]:
                if pid in id2row:
                    ranked.append(id2row[pid])
                else:
                    missing_ids.append(pid)  # Pages unique to BM25 (outside the vector candidates)
            # Look up the BM25-only pages
            if missing_ids:
                try:
                    extra = await conn.fetch(
                        "SELECT id, title, content, source_path, source_url, source_type, 0 AS dist "
                        "FROM wiki_pages WHERE user_id=$1 AND id = ANY($2::bigint[])",
                        user_id, missing_ids
                    )
                    ranked.extend(extra)
                except Exception as e:
                    logger.warning(f"wiki BM25-only page lookup failed: {e}")
        else:
            ranked = rows[:top_k]

        # rerank (optional): Doubao embedding similarity re-ranking
        if do_rerank and ranked:
            try:
                docs = [r["content"][:3000] for r in ranked]
                reordered = await rerank_docs(query, docs, top_k)
                text2row = {r["content"][:3000]: r for r in ranked}
                ranked = [text2row[d] for d in reordered if d in text2row] or ranked
            except Exception as e:
                logger.warning(f"wiki rerank failed (keeping original order): {e}")

        return [{
            "id": r["id"], "title": r["title"],
            "content_preview": (r["content"] or "")[:300],
            "content_length": len(r["content"] or ""),
            "source_path": r["source_path"], "source_url": r["source_url"],
            "source_type": r["source_type"], "distance": round(r["dist"], 4),
            "bm25": round(bm25_scores.get(r["id"], 0), 4) if bm25_scores else 0,
            "graph": round(graph_scores.get(r["id"], 0), 4) if graph_scores else 0,
        } for r in ranked]

@app.post("/api/v1/extract-entities")
async def extract_entities(user_id: str, max_memories: int = 50):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT m.id, m.content FROM memories m LEFT JOIN memory_entities me ON m.id = me.memory_id WHERE m.user_id = $1 AND me.memory_id IS NULL AND m.is_deleted = FALSE LIMIT " + str(max_memories), user_id)
        extracted = 0
        import re
        for row in rows:
            cand = set()
            for m in re.finditer(r'[""“”「」]([^"“”「」]{2,10})["“”「」]', row["content"]):
                cand.add(m.group(1).strip())
            if not cand:
                for p in re.split(r'[、，．！？,.!?\s的和在是了]+', row["content"]):
                    p = p.strip()
                    if 2 <= len(p) <= 15:
                        cand.add(p)
            for name in cand:
                try:
                    ex = await conn.fetchrow("SELECT id FROM entities WHERE user_id=$1 AND name=$2", user_id, name)
                    if not ex:
                        await sync_entities(conn, row["id"], [name], user_id)
                        extracted += 1
                except Exception:
                    pass
    return {"status": "done", "extracted": extracted, "from": len(list(rows))}



@app.get("/api/v1/memories/{memory_id}")
async def get_memory(memory_id: int, user_id: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, user_id, content, category, tier, heat_score, importance, reliability, metadata, created_at, last_accessed, access_count, is_deleted FROM memories WHERE id=$1 AND user_id=$2", memory_id, user_id)
        if not row:
            raise HTTPException(status_code=404, detail="Memory not found")
    return dict(row)

@app.post("/api/v1/memories/{memory_id}/restore")
async def restore_memory(memory_id: int, user_id: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("UPDATE memories SET is_deleted=FALSE, forgotten_at=NULL, heat_score=0.3, tier='L2' WHERE id=$1 AND user_id=$2 AND is_deleted=TRUE RETURNING id, content, tier, heat_score", memory_id, user_id)
        if not row:
            raise HTTPException(status_code=404, detail="Memory not found or not deleted")
    return {"status": "restored", "memory": dict(row)}


class SessionArchiveRequest(BaseModel):
    user_id: str = "default"
    session_id: str = ""
    title: str = ""
    content: str  # full conversation text


@app.post("/api/v1/sessions/archive")
async def archive_session(req: SessionArchiveRequest):
    """Archive a full conversation into the Memory Palace — auto-vectorized + fed into TMT distillation"""
    content = req.content.strip()
    if not content:
        return {"archived": False, "reason": "empty_content"}
    
    async with pool.acquire() as conn:
        # Generate the embedding
        raw = (await get_embedding([content[:2000]]))[0]
        vec_str = "[" + ",".join(str(x) for x in raw) + "]"
        
        # Detect conflicts
        conflict = await detect_conflict(conn, req.user_id, content, vec_str)
        
        if conflict["action"] == "merge":
            return {"archived": False, "reason": "duplicate", "merged_into": conflict["id"]}
        
        # Store the memory
        row = await conn.fetchrow(
            "INSERT INTO memories (user_id, content, category, embedding, heat_score, "
            "metadata, tmt_level) VALUES ($1,$2,$3,$4::vector,$5,$6,$7) RETURNING id",
            req.user_id, content, "session", vec_str, 0.6,
            json.dumps({"session_id": req.session_id, "title": req.title}),
            1  # tmt_level=1, included in distillation
        )
        memory_id = row["id"]
        
        # v7.8.1: session archiving also tokenizes immediately on write
        await _tokenize_on_write(conn, memory_id, content)
        
        # Entity extraction (async, non-blocking)
        try:
            from core.llm import call_llm_json
            entities_prompt = f"从以下对话中提取关键实体(项目名/人名/技术名/概念)，输出JSON: {{\"entities\": [\"实体1\", \"实体2\"]}}\n\n对话片段:\n{content[:1500]}"
            entities_result = call_llm_json(entities_prompt, tier=2)
            entities_data = json.loads(entities_result.get("content", "{}"))
            entities = entities_data.get("entities", [])
            if entities:
                await sync_entities(conn, memory_id, entities, req.user_id)
        except Exception:
            pass
        
        # Generate a one-line summary
        summary = ""
        try:
            from core.llm import call_llm_fast
            summary_result = call_llm_fast(f"用一句话概括这段对话(不超过30字):\n{content[:1000]}")
            summary = summary_result.get("content", "")[:100]
        except Exception:
            summary = content[:100]
        
        return {
            "archived": True,
            "memory_id": memory_id,
            "summary": summary,
            "content_length": len(content)
        }
# ── Session message sync (Hermes state.db → Mnemosyne) ──

class SessionMessagesUpload(BaseModel):
    messages: list  # [{role, content, tool_call_id, tool_calls, tool_name, timestamp, token_count, finish_reason, reasoning}, ...]


def _run_server() -> None:
    """Start the service — listen address/port come from config (MNEMOSYNE_HOST / MNEMOSYNE_PORT), never hardcoded.

    Pulled out into its own function so it's testable: a contract test pins down that
    "the entry point actually uses the config values" (main.py once hardcoded
    127.0.0.1:8010, which silently made both env vars no-ops).
    """
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, workers=4, log_level="info")


if __name__ == "__main__":
    _run_server()
