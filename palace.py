#!/usr/bin/env python3
"""palace.py — Magic Memory Palace core module v7.0
Design: memory-palace spatial encoding (wing/room/shelf/tome) + archival
cataloguing (archive number)
Features:
  1. Classification tree: wing/room/shelf definitions + classification
  2. Archive number generation: K·NET·PROXY·2026-0007
  3. Backfill classification: auto-classify by category/content
  4. Tome cards: title/summary/tags/retention (tome_cards)
  5. Lifecycle: permanent/long/short tiers
"""
import os
import sys
import re
import json
from datetime import datetime

# ── Wing definitions — modeled on Chinese Library Classification major
# categories, adapted to a personal knowledge system ──
WINGS = {
    "K": {"name": "知识", "rooms": ["arch", "memory", "ai", "tools"]},
    "N": {"name": "网络", "rooms": ["proxy", "domain", "dns", "server"]},
    "D": {"name": "开发", "rooms": ["repo", "deploy", "code", "database"]},
    "O": {"name": "运维", "rooms": ["ops", "cron", "monitor", "backup"]},
    "A": {"name": "资产", "rooms": ["secret", "key", "account", "vault"]},
    "P": {"name": "人物", "rooms": ["user", "ai", "contact", "org"]},
    "I": {"name": "灵感", "rooms": ["idea", "design", "brand", "content"]},
    "S": {"name": "技能", "rooms": ["skill", "workflow", "procedure", "lesson"]},
    "M": {"name": "元", "rooms": ["meta", "config", "roadmap", "decision"]},
}

# Category → wing mapping (from existing category)
CATEGORY_TO_WING = {
    "knowledge": "K", "reference": "K", "wiki": "K",
    "session": "M", "worklog": "O", "chat": "M",
    "preference": "P", "ops": "O", "pitfall": "S",
    "deploy": "D", "fact": "K",
}

# Room keywords → room (sniffed from content) — expanded in v7.0.1 to improve
# classification accuracy
ROOM_KEYWORDS = [
    ("proxy", ["xray", "代理", "proxy", "2081", "2082", "1080", "clash", "分流", "geosite"]),
    ("deploy", ["部署", "deploy", "发布", "rsync", "scp", "systemd", "重启", "升级"]),
    ("secret", ["密钥", "key", "token", "保险柜", "password", "pat", "凭证", "api_key"]),
    ("security", ["安全", "审计", "漏洞", "扫描", "权限", "ssh", "入侵", "加固"]),
    ("domain", ["域名", "domain", "dns", "解析", "cdn"]),
    ("repo", ["仓库", "repo", "github", "git", "commit", "push", "分支"]),
    ("cron", ["cron", "定时", "任务", "调度", "schedule"]),
    ("database", ["数据库", "postgres", "psql", "pg", "sqlite", "sql", "表"]),
    ("memory", ["记忆", "mnemosyne", "memory", "蒸馏", "tmt", "宫殿", "召回", "检索"]),
    ("server", ["服务器", "server", "gz", "hk", "云", "vps", "lighthouse", "腾讯云"]),
    ("design", ["设计", "定妆", "海报", "形象", "brand", "立绘", "娘化"]),
    ("skill", ["技能", "skill", "流程", "workflow", "sop"]),
    ("lesson", ["坑", "教训", "pitfall", "踩过", "注意", "报错", "失败"]),
    ("idea", ["灵感", "idea", "方向", "迭代", "规划", "roadmap", "设想"]),
    ("user", ["用户", "user", "偏好", "喜欢", "主人", "想要"]),
    ("content", ["内容", "文章", "公众号", "小红书", "文案", "视频"]),
    ("billing", ["余额", "充值", "账单", "billing", "费用", "价格", "额度"]),
    ("model", ["模型", "model", "llm", "deepseek", "豆包", "doubao", "ark", "prompt"]),
    ("backup", ["备份", "backup", "归档", "存档"]),
    ("workspace", ["工作区", "workspace", "箱子", "桌面", "文件"]),
]


def classify(content: str, category: str = "") -> dict:
    """Classify one memory: returns {wing, room, shelf}"""
    # 1. Wing: prefer mapping from category
    wing = CATEGORY_TO_WING.get(category, "")
    # 2. Room: sniffed from content keywords
    room = ""
    text = (content or "").lower()
    for r, kws in ROOM_KEYWORDS:
        if any(k in text for k in kws):
            room = r
            break
    # 3. Fallback: no mapping → Knowledge wing · unfiled
    if not wing:
        wing = "K"
    if not room:
        room = "unfiled"
    # 4. Shelf: generated from tags/keywords (simple version: take the first room keyword)
    shelf = ""
    return {"wing": wing, "room": room, "shelf": shelf}


def gen_archive_no(wing: str, room: str, shelf: str, year: int = 0, seq: int = 0) -> str:
    """Generate archive number: K·NET·PROXY·2026-0007"""
    if not year:
        year = datetime.now().year
    return f"{wing}·{room.upper()}·{shelf.upper() + '·' if shelf else ''}{year}-{seq:04d}"


def build_taxonomy_table_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS archive_taxonomy (
    id SERIAL PRIMARY KEY,
    wing TEXT NOT NULL,
    room TEXT NOT NULL,
    shelf TEXT DEFAULT '',
    name TEXT DEFAULT '',
    UNIQUE(wing, room, shelf)
);
"""


def add_archive_no_column_sql() -> str:
    return """
ALTER TABLE memories ADD COLUMN IF NOT EXISTS archive_no TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_archive_no ON memories(archive_no) WHERE archive_no IS NOT NULL;
"""


def build_tome_cards_sql() -> str:
    return """
CREATE TABLE IF NOT EXISTS tome_cards (
    memory_id BIGINT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    archive_no TEXT UNIQUE,
    wing TEXT, room TEXT, shelf TEXT,
    tags TEXT[] DEFAULT '{}',
    retention TEXT DEFAULT 'long',
    source_session TEXT DEFAULT '',
    created_by TEXT DEFAULT 'auto',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tome_wing_room ON tome_cards(wing, room);
CREATE INDEX IF NOT EXISTS idx_tome_tags ON tome_cards USING GIN(tags);
"""


async def init_palace(pool) -> dict:
    """Palace init: create tables + auto-classify and catalogue backfill
    memories (idempotent, safe to re-run)
    Returns: {tables: [...], classified: N, cards: N}
    """
    import asyncpg
    result = {"tables": [], "classified": 0, "cards": 0}
    async with pool.acquire() as conn:
        # 1. Create tables
        for name, sql in [
            ("archive_taxonomy", build_taxonomy_table_sql()),
            ("archive_no_col", add_archive_no_column_sql()),
            ("tome_cards", build_tome_cards_sql()),
        ]:
            try:
                await conn.execute(sql)
                result["tables"].append(name)
            except Exception as e:
                result["tables"].append(f"{name}(err:{e})")

        # 2. Backfill classification: memories without an archive number →
        # classify + generate archive number + create card
        rows = await conn.fetch(
            "SELECT id, content, category FROM memories "
            "WHERE user_id=$1 AND is_deleted=FALSE AND (archive_no IS NULL OR archive_no='') "
            "ORDER BY id DESC LIMIT 500",
            "default"
        )
        for r in rows:
            cls = classify(r["content"] or "", r["category"] or "")
            # Generate archive number: wing·room·year-sequence (id used as the
            # sequence to guarantee uniqueness)
            archive_no = f"{cls['wing']}·{cls['room'].upper()}·{datetime.now().year}-{r['id']:04d}"
            try:
                await conn.execute(
                    "UPDATE mnemosyne.memories SET archive_no=$1 WHERE id=$2 AND archive_no IS NULL",
                    archive_no, r["id"])
                # Tome card
                title = (r["content"] or "")[:30].replace("\n", " ")
                summary = (r["content"] or "")[:120].replace("\n", " ")
                await conn.execute(
                    "INSERT INTO tome_cards (memory_id, title, summary, archive_no, wing, room, shelf, tags, retention, source_session, created_by) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'long','', 'backfill') "
                    "ON CONFLICT (memory_id) DO NOTHING",
                    r["id"], title, summary, archive_no, cls["wing"], cls["room"], cls["shelf"], [r["category"] or "unfiled"])
                result["classified"] += 1
                result["cards"] += 1
            except Exception:
                pass
    return result


# ── Three-channel summon (apothecary-cabinet lookup) ──
async def summon(pool, query: str, user_id: str = "default", top_k: int = 5) -> dict:
    """Three-channel recall:
    ① Call (exact): direct hit on archive number/title/tags
    ② Guide (scoped): narrowed by classification-tree wing/room
    ③ Resonate (semantic): vector-similarity fallback
    Returns hits grouped by channel
    """
    result = {"query": query, "summon": [], "guide": [], "resonate": []}

    # ① Call: archive number/title/tags ILIKE
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT m.id, m.content, c.archive_no, c.title, c.wing, c.room, c.tags, m.heat_score "
            "FROM memories m JOIN tome_cards c ON c.memory_id = m.id "
            "WHERE m.user_id=$1 AND m.is_deleted=FALSE AND "
            "(c.archive_no ILIKE '%'||$2||'%' OR c.title ILIKE '%'||$2||'%' OR $2 = ANY(c.tags) OR m.content ILIKE '%'||$2||'%') "
            "ORDER BY m.heat_score DESC NULLS LAST LIMIT $3",
            user_id, query, top_k)
        result["summon"] = [{"id": r["id"], "content": r["content"][:120], "archive_no": r["archive_no"],
                             "title": r["title"], "wing": r["wing"], "room": r["room"],
                             "tags": r["tags"], "heat": r["heat_score"]} for r in rows]

        # ② Guide: classification-tree match (query contains wing/room keywords)
        cls = classify(query, "")
        if cls["room"] != "unfiled":
            rows = await conn.fetch(
                "SELECT m.id, m.content, c.archive_no, c.title, c.wing, c.room, c.tags "
                "FROM memories m JOIN tome_cards c ON c.memory_id = m.id "
                "WHERE m.user_id=$1 AND m.is_deleted=FALSE AND c.room=$2 "
                "ORDER BY m.heat_score DESC NULLS LAST LIMIT $3",
                user_id, cls["room"], top_k)
            result["guide"] = [{"id": r["id"], "content": r["content"][:120], "archive_no": r["archive_no"],
                                "title": r["title"], "room": r["room"], "tags": r["tags"]} for r in rows]

    # ③ Resonate: vector search (needs embedding; skipped on failure)
    try:
        from core.embedding import get_embedding_async
        vec = (await get_embedding_async([query]))[0]
        q_str = "[" + ",".join(str(x) for x in vec) + "]"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT m.id, m.content, c.archive_no, c.title, c.room, c.tags, (m.embedding <=> $2::vector) AS dist "
                "FROM memories m JOIN tome_cards c ON c.memory_id = m.id "
                "WHERE m.user_id=$1 AND m.is_deleted=FALSE AND m.embedding IS NOT NULL "
                "ORDER BY dist LIMIT $3",
                user_id, q_str, top_k)
            result["resonate"] = [{"id": r["id"], "content": r["content"][:120], "archive_no": r["archive_no"],
                                   "title": r["title"], "room": r["room"], "tags": r["tags"],
                                   "dist": round(float(r["dist"]), 4)} for r in rows]
    except Exception:
        pass

    # ④ Archive (v7.5): also query the WIKI knowledge base — full-text snapshots
    # of papers/proposals
    try:
        from core.embedding import get_embedding_async as _ge
        w_vec = (await _ge([query]))[0]
        w_str = "[" + ",".join(str(x) for x in w_vec) + "]"
        async with pool.acquire() as conn:
            w_rows = await conn.fetch(
                "SELECT id, title, content, (embedding <=> $2::vector) AS dist "
                "FROM wiki_pages WHERE user_id=$1 AND content IS NOT NULL AND embedding IS NOT NULL "
                "ORDER BY embedding <=> $2::vector LIMIT $3",
                user_id, w_str, top_k)
            result["wiki"] = [{"id": r["id"], "title": r["title"], "content": (r["content"] or "")[:120],
                               "dist": round(float(r["dist"]), 4)} for r in w_rows]
    except Exception:
        pass

    return result


# ── v8.0 S2-1: four-channel RRF-fused recall (coexists with summon(); caller
# picks via A/B) ──
async def _summon_channels(pool, query: str, user_id: str, limit: int) -> dict:
    """Fetch candidates from all four channels (`limit` each), keeping each
    channel's own ordering.

    Same SQL as summon(), just with LIMIT parameterized to candidate_k — so
    fusion has enough candidates to work with.
    Return shape matches summon(), so callers can reuse it.
    """
    out = {"summon": [], "guide": [], "resonate": [], "wiki": []}

    async with pool.acquire() as conn:
        # ① Call: archive number/title/tags/content ILIKE (descending by heat)
        rows = await conn.fetch(
            "SELECT m.id, m.content, c.archive_no, c.title, c.wing, c.room, c.tags, m.heat_score "
            "FROM memories m JOIN tome_cards c ON c.memory_id = m.id "
            "WHERE m.user_id=$1 AND m.is_deleted=FALSE AND "
            "(c.archive_no ILIKE '%'||$2||'%' OR c.title ILIKE '%'||$2||'%' OR $2 = ANY(c.tags) OR m.content ILIKE '%'||$2||'%') "
            "ORDER BY m.heat_score DESC NULLS LAST LIMIT $3",
            user_id, query, limit)
        out["summon"] = [{"id": r["id"], "content": r["content"][:120], "archive_no": r["archive_no"],
                          "title": r["title"], "wing": r["wing"], "room": r["room"],
                          "tags": r["tags"], "heat": r["heat_score"]} for r in rows]

        # ② Guide: classification-tree match (descending by heat)
        cls = classify(query, "")
        if cls["room"] != "unfiled":
            rows = await conn.fetch(
                "SELECT m.id, m.content, c.archive_no, c.title, c.wing, c.room, c.tags "
                "FROM memories m JOIN tome_cards c ON c.memory_id = m.id "
                "WHERE m.user_id=$1 AND m.is_deleted=FALSE AND c.room=$2 "
                "ORDER BY m.heat_score DESC NULLS LAST LIMIT $3",
                user_id, cls["room"], limit)
            out["guide"] = [{"id": r["id"], "content": r["content"][:120], "archive_no": r["archive_no"],
                             "title": r["title"], "wing": r["wing"], "room": r["room"],
                             "tags": r["tags"]} for r in rows]

    # ③ Resonate: vector nearest-neighbors (ascending by distance)
    try:
        from core.embedding import get_embedding_async
        vec = (await get_embedding_async([query]))[0]
        q_str = "[" + ",".join(str(x) for x in vec) + "]"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT m.id, m.content, c.archive_no, c.title, c.room, c.tags, (m.embedding <=> $2::vector) AS dist "
                "FROM memories m JOIN tome_cards c ON c.memory_id = m.id "
                "WHERE m.user_id=$1 AND m.is_deleted=FALSE AND m.embedding IS NOT NULL "
                "ORDER BY dist LIMIT $3",
                user_id, q_str, limit)
            out["resonate"] = [{"id": r["id"], "content": r["content"][:120], "archive_no": r["archive_no"],
                                "title": r["title"], "room": r["room"], "tags": r["tags"],
                                "dist": round(float(r["dist"]), 4)} for r in rows]
    except Exception:
        pass

    # ④ Archive: WIKI vectors (ascending by distance) — note wiki id and
    # memory id are two separate id spaces
    try:
        from core.embedding import get_embedding_async as _ge
        w_vec = (await _ge([query]))[0]
        w_str = "[" + ",".join(str(x) for x in w_vec) + "]"
        async with pool.acquire() as conn:
            w_rows = await conn.fetch(
                "SELECT id, title, content, (embedding <=> $2::vector) AS dist "
                "FROM wiki_pages WHERE user_id=$1 AND content IS NOT NULL AND embedding IS NOT NULL "
                "ORDER BY embedding <=> $2::vector LIMIT $3",
                user_id, w_str, limit)
            out["wiki"] = [{"id": r["id"], "title": r["title"], "content": (r["content"] or "")[:120],
                            "dist": round(float(r["dist"]), 4)} for r in w_rows]
    except Exception:
        pass

    return out


def _ns(channel: str, item_id) -> str:
    """Namespace prefix: memories channel m: / wiki channel w: (two id spaces
    whose numeric values would otherwise collide)."""
    return f"{'w' if channel == 'wiki' else 'm'}:{item_id}"


async def summon_fused(pool, query: str, user_id: str = "default", top_k: int = 5,
                       candidate_k: int = 50, k_rrf: int = 60) -> dict:
    """Four-channel **RRF-fused** recall (v8.0 S2-1).

    What this solves
    -----------------
    Status quo (measured in `palace.py:180-250`): each channel returns its own
    `LIMIT top_k` directly — no fusion, no unified cutoff, so callers get four
    separate blobs of results they can't compare, and **can't tell which hits
    are multi-channel consensus**.

    Approach
    --------
    Each channel first takes `candidate_k` (default 50) candidates → RRF fusion
    (rank-only, dimensionless) → unified cutoff at `top_k`. Items hit by
    multiple channels naturally rank higher (a mathematical result, not a
    tuned parameter).

    Compatibility
    -------------
    **Does not change the original `summon()`** — old and new coexist, and the
    caller picks via the `fused` flag. So default behavior is unchanged until
    the eval numbers are in (R3 principle: measure before changing).

    Returns
    -------
    {"query", "fused": [{id, kind, score, channels, item}], "channels": {the
    original four channels}}
    The `channels` field answers "why did this item rank here" — the carrier
    of fusion explainability.
    """
    from core.rrf import rrf_fuse_ranked, fuse_within_topk

    raw = await _summon_channels(pool, query, user_id, candidate_k)

    ranked = {ch: [item["id"] for item in items] for ch, items in raw.items() if items}
    # Apply the namespace prefix before fusing, to avoid memories/wiki id
    # spaces colliding
    prefixed = {ch: [_ns(ch, i) for i in ids] for ch, ids in ranked.items()}
    fused = rrf_fuse_ranked(prefixed, k=k_rrf)

    # Backfill item content (reverse lookup via the prefixed key)
    lookup = {}
    for ch, items in raw.items():
        for item in items:
            lookup[_ns(ch, item["id"])] = (ch, item)

    out_items = []
    for key, score, chans in fuse_within_topk(fused, top_k):
        ch, item = lookup.get(key, (None, None))
        if item is None:
            continue
        out_items.append({"id": item["id"], "kind": ch, "score": score,
                          "channels": chans, "item": item})

    return {"query": query, "fused": out_items, "channels": raw,
            "meta": {"candidate_k": candidate_k, "k_rrf": k_rrf, "top_k": top_k}}


# ── Card refinement (reading room: title/summary/tags generation) ──
def refine_prompt(content: str) -> str:
    return f"""你是记忆宫殿的图书管理员。给一条记忆生成著录卡片。
要求:
1. 题名: 10字以内, 概括主题 (如 "xray部署")
2. 摘要: 30字以内, 一句话要点
3. 标签: 2-4个, 用中括号逗号分隔 (如 [xray][部署])

记忆内容:
{content[:500]}

输出严格JSON: {{"title":"...","summary":"...","tags":["a","b"]}}"""


async def refine_cards(pool, limit: int = 20) -> dict:
    """Batch-refine tome cards (LLM generates title/summary/tags); only processes rows with created_by='backfill'"""
    import json
    from core.llm import call_llm_json
    done, failed = 0, 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT memory_id, title, summary, content FROM tome_cards c "
            "JOIN memories m ON m.id = c.memory_id "
            "WHERE c.created_by='backfill' AND m.user_id='default' AND m.is_deleted=FALSE "
            "ORDER BY m.heat_score DESC NULLS LAST LIMIT $1", limit)
        for r in rows:
            try:
                res = call_llm_json(refine_prompt(r["content"] or ""))
                # In json_mode, content is already the extracted JSON string
                raw = res.get("content", "") if isinstance(res, dict) else ""
                if not raw:
                    failed += 1
                    continue
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(parsed, dict) or not parsed.get("title"):
                    failed += 1
                    continue
                await conn.execute(
                    "UPDATE tome_cards SET title=$1, summary=$2, tags=$3::text[], created_by='refined' "
                    "WHERE memory_id=$4",
                    str(parsed["title"])[:50], str(parsed.get("summary", ""))[:200],
                    [str(t)[:20] for t in parsed.get("tags", [])[:4]], r["memory_id"])
                done += 1
            except Exception:
                failed += 1
    return {"refined": done, "failed": failed}


# ── Reading room: fact-extraction pipeline (conversation → facts → catalogued) ──
async def extract_facts_pipeline(pool, batch: int = 20) -> dict:
    """Extract facts from session/worklog → write to memories
    (preference/knowledge) → auto-catalogue
    Reuses tmt/factextract.py logic; idempotent (marked via metadata
    fact_extracted)
    """
    from tmt import factextract
    from tmt.distill import load_env  # noqa: F401
    processed, facts_created = 0, 0
    try:
        # Explicit public schema, to remove search_path ambiguity (memories
        # can resolve incorrectly when ag_catalog takes priority)
        candidates = await factextract.find_candidates(pool, batch)
        for c in candidates:
            if factextract.is_skip(c["content"] or ""):
                # Content too short to be worth extracting, but mark it
                # processed anyway (avoid it staying pending forever)
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE mnemosyne.memories SET metadata = COALESCE(metadata,'{}'::jsonb) || '{\"fact_extracted\":true}'::jsonb "
                        "WHERE id=$1", c["id"])
                processed += 1
                continue
            facts, status = await factextract.extract_facts(c["content"] or "")
            if facts:
                for f in facts:
                    ftype = factextract.classify_fact(f)
                    nid = await factextract.insert_fact(pool, c["id"], c["category"] or "", f, ftype)
                    if nid:
                        facts_created += 1
                        # Auto-catalogue the new fact (classify + archive
                        # number + card) — uses the id returned by the
                        # insert directly, no race condition
                        cls = classify(f, ftype)
                        archive_no = f"F·{cls['room'].upper()}·{datetime.now().year}-{nid % 100000:05d}"
                        try:
                            async with pool.acquire() as conn:
                                await conn.execute(
                                    "UPDATE mnemosyne.memories SET archive_no=$1 WHERE id=$2 AND (archive_no IS NULL OR archive_no='')",
                                    archive_no, nid)
                                await conn.execute(
                                    "INSERT INTO tome_cards (memory_id, title, summary, archive_no, wing, room, shelf, tags, retention, source_session, created_by) "
                                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'long','', 'fact') ON CONFLICT (memory_id) DO NOTHING",
                                    nid, f[:30], f[:120], archive_no, cls["wing"], cls["room"], cls["shelf"], [ftype])
                        except Exception:
                            pass
            # Mark as extracted (regardless of whether any facts were found)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE mnemosyne.memories SET metadata = COALESCE(metadata,'{}'::jsonb) || '{\"fact_extracted\":true}'::jsonb "
                    "WHERE id=$1", c["id"])
            processed += 1
    except Exception as e:
        return {"processed": processed, "facts_created": facts_created, "error": str(e)[:200]}
    return {"processed": processed, "facts_created": facts_created}


# ── Eternal-tier lifecycle ──
RETENTION_RULES = {
    "permanent": {"decay": 0.0, "keep_days": None},   # Permanent: no decay, never purged
    "long":      {"decay": 0.999, "keep_days": None},  # Long: very slow decay, not auto-purged
    "short":     {"decay": 0.98, "keep_days": 90},     # Short: fast decay, auto-retired after 90 days
}

async def apply_lifecycle(pool, user_id: str = "default") -> dict:
    """Eternal tiering: adjust heat by retention tier + auto-soft-delete
    expired short-tier items
    Returns: {expired: N, degraded: N}
    """
    import json
    expired, degraded = 0, 0
    async with pool.acquire() as conn:
        # 1. Short: auto-soft-delete (retire) once past keep_days
        rows = await conn.fetch(
            "SELECT c.memory_id, m.created_at FROM tome_cards c "
            "JOIN memories m ON m.id = c.memory_id "
            "WHERE c.retention='short' AND m.user_id=$1 AND m.is_deleted=FALSE",
            user_id)
        for r in rows:
            if r["created_at"] and (datetime.now() - r["created_at"]).days > RETENTION_RULES["short"]["keep_days"]:
                await conn.execute(
                    "UPDATE mnemosyne.memories SET is_deleted=TRUE, forgotten_at=NOW() WHERE id=$1", r["memory_id"])
                expired += 1
        # 2. Permanent/long: heat protection (permanent already has no decay,
        # expressed via decay=0; this just gives permanent items a heat floor)
        await conn.execute(
            "UPDATE mnemosyne.memories SET heat_score = GREATEST(heat_score, 0.8) "
            "WHERE id IN (SELECT memory_id FROM tome_cards WHERE retention='permanent') "
            "AND user_id=$1 AND is_deleted=FALSE", user_id)
        degraded = await conn.fetchval(
            "SELECT count(*) FROM tome_cards WHERE retention='permanent'")
    return {"expired": expired, "permanent_protected": degraded}


if __name__ == "__main__":
    # Self-test
    tests = [
        ("在生产服务器部署xray代理，2081端口，systemd服务", "ops"),
        ("用户喜欢红果短剧风格的AI美女图", "preference"),
        ("密钥在保险柜GITHUB/KEY.txt，line3细粒度PAT", "ops"),
        ("mnemosyne蒸馏链修复，双底座DeepSeek", "worklog"),
    ]
    print("=== Classification self-test ===")
    for content, cat in tests:
        r = classify(content, cat)
        print(f"  [{cat}] {content[:30]}... → {r}")
