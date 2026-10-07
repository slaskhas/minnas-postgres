#!/usr/bin/env python3
"""
distill.py — Knowledge distillation pipeline v0.1 (2026-08-05)

Design origin: legacy-system archaeology — NCP-008's seven-step knowledge absorption
          (identify→analyze→semanticize→index→verify→archive→cite)
          + the cognitive-AI foundation's TEL/MAIL protocol (architecture-level
          hallucination prevention: FACTS multi-source verification + MAIL
          structured return)

Goal: fix category imbalance (session+worklog at 88%, knowledge only 9%)
    by distilling reusable knowledge/pitfall entries out of raw session/worklog memories.

Usage:
  python3 distill.py --batch 30            # process the 30 most recent undistilled candidates
  python3 distill.py --batch 30 --dry-run  # preview candidates + LLM output only, no DB writes
  python3 distill.py --stats               # view distillation stats

Pipeline (mapped to the seven steps):
  1. Identify    — candidate screening: session/worklog + undistilled + length/signal-word filter
  2. Analyze     — TEL assembly: CONTRACT + FACTS + REQUIREMENTS
  3. Semanticize — LLM condensation: Doubao Lite (tier3), JSON mode
  4. Index       — write to DB: category=knowledge/pitfall, hall=archive/engineering
  5. Verify      — dedup gate: pgvector ANN, skip if sim>0.92
  6. Archive     — move between the three halls: knowledge→archive, pitfall→engineering
  7. Cite        — metadata.source_memory_id provenance
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg

async def get_embedding(text: str) -> list[float]:
    """Synchronous embedding wrapper (same pattern as consolidate.py)"""
    return _embedding_sync([text])[0]

PG_DSN = os.environ.get("MNEMOSYNE_PG_DSN", "postgresql://postgres@127.0.0.1:5432/mnemosyne")


def load_env(path: str = "/opt/mnemosyne/.env") -> None:
    """Load .env (standalone script doesn't rely on systemd EnvironmentFile)"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


load_env()

# import only once the key is ready (core.config reads env vars at import time)
from core.embedding import get_embedding as _embedding_sync
from core.llm import call_llm_json

# Knowledge signal words: a candidate memory matching any of these enters the distillation pool
# (kept in Chinese — matched against Chinese memory content; do not translate)
SIGNAL_WORDS = (
    "学到", "解决", "发现", "坑", "教训", "经验", "方案", "架构", "踩坑",
    "修复", "优化", "结论", "要点", "注意", "规则", "流程", "设计", "原理",
    "配置", "部署", "故障", "报错", "错误", "成功", "失败", "踩过", "总结",
)
# Exclusion words: pure small talk/emotion/irrelevant content
# (kept in Chinese — matched against Chinese memory content; do not translate)
EXCLUDE_WORDS = ("撒娇", "可爱", "晚安", "早安", "哈哈", "嘿嘿", "呜呜", "喵~", "欺负", "亲亲")

MIN_LEN = 120          # minimum candidate length
DEDUP_THRESHOLD = 0.92  # similarity to an existing knowledge entry above this → skip
MAX_RETRY = 2          # LLM failure retries

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("distill")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)


# ── 1. Identify: candidate screening ──
async def find_candidates(pool, batch: int) -> list[dict]:
    """Undistilled session/worklog memories, newest-first, up to `batch` rows"""
    rows = await pool.fetch(
        """
        SELECT id, content, category, created_at
        FROM memories
        WHERE user_id = 'default'
          AND is_deleted = FALSE
          AND category IN ('session', 'worklog')
          AND (metadata->>'distilled_at') IS NULL
          AND char_length(content) >= $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        MIN_LEN, batch * 3,  # over-fetch 3x so enough remain after signal-word filtering
    )
    # signal-word + exclusion-word filtering
    cands = []
    for r in rows:
        c = r["content"]
        if any(w in c for w in EXCLUDE_WORDS) and not any(w in c for w in SIGNAL_WORDS):
            continue
        if any(w in c for w in SIGNAL_WORDS):
            cands.append(r)
        if len(cands) >= batch:
            break
    # if signal-word filtering left too few, top up with anything long enough and not pure small talk
    if len(cands) < batch:
        for r in rows:
            if r not in cands and len(cands) < batch:
                cands.append(r)
    return cands[:batch]


# ── 2+3. Analyze + Semanticize: TEL assembly → LLM condensation ──
# NOTE: the TEL prompt text below is sent to the LLM and instructs it to respond in
# Chinese (per the memory content's language) — left untranslated intentionally,
# this is functional prompt data, not a comment.
def build_tel(content: str) -> str:
    return f"""<TEL>
<CONTRACT>你是记忆宫殿的知识蒸馏器。把一段原始记忆凝练成结构化知识条目。铁律:
1. 只提炼可复用的知识/教训/经验/方案, 剔除寒暄、情绪、过程流水、对话噪音
2. type 三选一: knowledge(可复用知识/方案/经验) / pitfall(坑/教训/踩坑) / skip(无提炼价值)
3. summary 用中文, 一句话讲清核心, 60字内
4. keywords 给 3-5 个检索关键词(中英皆可)
5. importance 0-1, 越通用越可复用越高
6. confidence 三选一: high(事实明确可复用) / medium(经验性) / low(存疑)
7. 必须输出合法 JSON, 不要多余文字</CONTRACT>
<TASK>蒸馏以下原始记忆为知识条目</TASK>
<FACTS>{content[:3000]}</FACTS>
<REQUIREMENTS>{{"type": "knowledge|pitfall|skip", "summary": "...", "keywords": [...], "importance": 0.0-1.0, "confidence": "high|medium|low"}}</REQUIREMENTS>
</TEL>"""


async def distill_one(content: str) -> dict | None:
    for attempt in range(MAX_RETRY):
        try:
            res = call_llm_json(build_tel(content), tier=3)
            raw = res.get("content", "")
            m = json.loads(raw) if raw else {}
            if not m or "type" not in m:
                raise ValueError(f"LLM response missing type: {raw[:100]}")
            return m
        except Exception as e:
            log.warning("  LLM failed (attempt %d): %s", attempt + 1, e)
    return None


# ── 5. Verify: dedup gate (ANN) ──
async def dedup_check(pool, summary: str, in_batch: list[str] | None = None) -> bool:
    """Compares against existing knowledge/pitfall memories plus this batch; returns True (duplicate) if sim > threshold"""
    # In-batch dedup (v6.2): skip entries similar to ones already distilled in this same batch
    if in_batch:
        for prev in in_batch:
            if prev and len(prev) > 5 and (summary[:40] in prev or prev[:40] in summary):
                return True
    emb = await get_embedding(summary)
    if not emb:
        return False
    vec_str = "[" + ",".join(str(x) for x in emb) + "]"
    row = await pool.fetchrow(
        """
        SELECT 1 - (embedding <=> $1::vector) AS sim
        FROM memories
        WHERE user_id = 'default' AND is_deleted = FALSE
          AND category IN ('knowledge', 'pitfall')
          AND embedding IS NOT NULL
          AND 1 - (embedding <=> $1::vector) > $2
        ORDER BY embedding <=> $1::vector
        LIMIT 1
        """,
        vec_str, DEDUP_THRESHOLD,
    )
    return row is not None


# ── 4+6+7. Index + archive + cite: write to DB ──
async def insert_knowledge(pool, src_id: int, src_category: str, distilled: dict) -> bool:
    mtype = distilled["type"]
    if mtype == "skip":
        # Only mark the source, produce no new memory
        await pool.execute(
            "UPDATE memories SET metadata = metadata || $2::jsonb WHERE id = $1",
            src_id, json.dumps({"distilled_at": utcnow(), "distill_result": "skip"}),
        )
        return False

    hall = "archive" if mtype == "knowledge" else "engineering"
    # Re-confirm the type is valid
    if mtype not in ("knowledge", "pitfall"):
        return False

    summary = (distilled.get("summary") or "").strip()
    if len(summary) < 10:
        # Summary too short, treat as invalid, just mark it
        await pool.execute(
            "UPDATE memories SET metadata = metadata || $2::jsonb WHERE id = $1",
            src_id, json.dumps({"distilled_at": utcnow(), "distill_result": "invalid"}),
        )
        return False

    importance = min(max(float(distilled.get("importance", 0.4)), 0.1), 0.95)
    # v6.2: distilled knowledge starts with heat signal 0.65 (default 0.5 → 0.65, marks "newly distilled")
    # v6.3: pitfalls are inherently important → 0.70
    heat_init = 0.70 if mtype == "pitfall" else 0.65
    metadata = {
        "distilled_from": src_id,
        "distilled_at": utcnow(),
        "source_category": src_category,
        "keywords": distilled.get("keywords", []),
        "confidence": distilled.get("confidence", "medium"),
    }
    await pool.execute(
        """
        INSERT INTO memories
          (user_id, content, category, hall, importance, tier, heat_score, metadata, tmt_level)
        VALUES ('default', $1, $2, $3, $4, 'L3', $5, $6::jsonb, 1)
        """,
        summary, mtype, hall, importance, heat_init, json.dumps(metadata, ensure_ascii=False),
    )
    # Mark the source as distilled
    await pool.execute(
        "UPDATE memories SET metadata = metadata || $2::jsonb WHERE id = $1",
        src_id, json.dumps({"distilled_at": utcnow(), "distill_result": mtype}),
    )
    return True


# ── Main flow ──
async def run(batch: int, dry_run: bool) -> None:
    pool = await get_pool()
    try:
        cands = await find_candidates(pool, batch)
        log.info("%d candidates (dry_run=%s)", len(cands), dry_run)

        stats = {"knowledge": 0, "pitfall": 0, "skip": 0, "dedup": 0, "fail": 0}
        in_batch: list[str] = []  # v6.2: in-batch dedup
        for i, c in enumerate(cands, 1):
            log.info("[%d/%d] Distilling #%d (%s): %.50s...", i, len(cands), c["id"], c["category"], c["content"])
            d = await distill_one(c["content"])
            if d is None:
                stats["fail"] += 1
                continue
            t = d.get("type", "skip")
            if t == "skip":
                stats["skip"] += 1
                if not dry_run:
                    await insert_knowledge(pool, c["id"], c["category"], d)
                log.info("  → skip: %.60s", d.get("summary", ""))
                continue
            if t not in ("knowledge", "pitfall"):
                stats["skip"] += 1
                continue

            dup = await dedup_check(pool, d.get("summary", ""), in_batch if not dry_run else None)
            if dup:
                stats["dedup"] += 1
                log.info("  → skipped as duplicate (sim>%.2f): %.60s", DEDUP_THRESHOLD, d.get("summary", ""))
                continue

            stats[t] += 1
            in_batch.append(d.get("summary", ""))
            if not dry_run:
                await insert_knowledge(pool, c["id"], c["category"], d)
            log.info("  → %s [%s]: %.70s (imp=%.2f)", t, d.get("confidence", "?"), d.get("summary", ""), float(d.get("importance", 0)))

        log.info("═══ Distillation complete: %s ═══", json.dumps(stats, ensure_ascii=False))
    finally:
        await pool.close()


async def show_stats(pool) -> None:
    rows = await pool.fetch(
        """
        SELECT category, COUNT(*) AS n,
               COUNT(*) FILTER (WHERE metadata ? 'distilled_at') AS distilled
        FROM memories
        WHERE user_id='default' AND is_deleted=FALSE
        GROUP BY category ORDER BY n DESC
        """
    )
    print("\n=== Category distribution (before/after distillation) ===")
    print(f"{'category':<12}{'total':>6}{'distilled':>8}")
    for r in rows:
        print(f"{r['category']:<12}{r['n']:>6}{r['distilled']:>8}")


async def main():
    parser = argparse.ArgumentParser(description="Mnemosyne knowledge distillation pipeline")
    parser.add_argument("--batch", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    pool = await get_pool()
    try:
        if args.stats:
            await show_stats(pool)
            return
        await run(args.batch, args.dry_run)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
