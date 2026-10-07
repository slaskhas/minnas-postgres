#!/usr/bin/env python3
"""tmt/factextract.py — Fact extraction pipeline v6.4
Design origin: LongMemEval attribution findings (#6293) — fills in the missing
"personal-info facts" dimension
Extracts user facts (personal info/preferences/events/schedule/skills) from
session/worklog memories → preference/knowledge

Lessons absorbed:
- extract line-by-line from short text (Doubao lite misses deep answers in long sessions)
- non-JSON mode (Doubao json_mode returns empty for prompts >2500 chars)
- tier3 lite is sufficient (extraction is a simple task)
- only extract, never fabricate; ANN dedup gate; heat 0.65; metadata provenance; retry on failure

Usage: venv/bin/python tmt/factextract.py [--batch N] [--dry-run]
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before importing core (keys are read at import time)
from tmt.distill import load_env, get_pool, dedup_check, get_embedding, utcnow, EXCLUDE_WORDS

load_env()

from core.llm import call_llm  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("factextract")

MIN_LEN = 80          # minimum candidate length (extracting from short text)
MAX_FACTS = 5         # max facts per memory
HEAT_INIT = 0.65      # initial heat for functional memories
DEDUP_THRESHOLD = 0.92

# preference/personal info → preference; other facts → knowledge
# (kept in Chinese — matched against Chinese memory content; do not translate)
PREF_KEYWORDS = ("喜欢", "爱好", "想要", "希望", "居住", "住在", "工作", "职业",
                 "毕业", "家人", "宠物", "生日", "习惯", "不爱", "讨厌", "最近在",
                 "计划", "打算", "预约", "每周", "每天")


# NOTE: the prompt text below is sent to the LLM and instructs it to respond in
# Chinese (matching the memory content's language) — left untranslated
# intentionally, this is functional prompt data, not a comment.
def extract_prompt(content: str) -> str:
    return f"""从下面的内容中提取关于用户的事实。
事实类型: 个人信息(毕业/职业/居住/家人/宠物/年龄)、偏好(喜欢/不喜欢/习惯)、事件(做过的具体事/经历)、安排(计划/预约/任务)、能力(技能)。

内容:
{content}

规则:
- 只提取明确提到的, 不猜测不编造
- 每条一行, 以 "- " 开头
- 中文表述
- 最多{MAX_FACTS}条
- 没有事实就输出 "无"
- 严禁输出 "事件:" "能力:" "安排:" 等前缀标签, 直接写事实内容本身 """


def parse_facts(text: str) -> list[str]:
    """Parse "- fact" line format (non-JSON). NOTE: the literal strings matched
    below ("无"/"没有"/etc., and the "从"/"规则" prefixes) are the actual tokens the
    Chinese-language prompt produces — left as-is, this is functional data."""
    facts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line in ("无", "没有", "暂无"):
            continue
        line = re.sub(r"^[-•*]?\s*\d*[\.、\)]?\s*", "", line).strip()
        if len(line) > 4 and not line.startswith("从") and not line.startswith("规则"):
            facts.append(line[:200])
        if len(facts) >= MAX_FACTS:
            break
    return facts


def classify_fact(fact: str) -> str:
    """preference/personal info → preference, everything else → knowledge"""
    if any(k in fact for k in PREF_KEYWORDS):
        return "preference"
    return "knowledge"


def is_skip(content: str) -> bool:
    """Skip content that's too short or pure small talk (EXCLUDE_WORDS is the small-talk exclusion list)"""
    if len(content) < MIN_LEN:
        return True
    if any(s in content for s in EXCLUDE_WORDS):
        return True
    return False


async def extract_facts(content: str) -> tuple[list[str], str]:
    """Non-JSON extraction (tier3 lite). Returns (facts, status): status in ('ok','no_facts','fail')"""
    r = call_llm(extract_prompt(content), tier=3, temperature=0.1)
    txt = (r.get("content") or "").strip()
    if not txt:
        # timeout/empty → retry once
        r = call_llm(extract_prompt(content), tier=3, temperature=0.1)
        txt = (r.get("content") or "").strip()
    if not txt:
        return [], "fail"
    if txt in ("无", "没有", "暂无", "没有事实"):
        return [], "no_facts"
    return parse_facts(txt), "ok"


async def find_candidates(pool, batch: int) -> list[dict]:
    rows = await pool.fetch(
        "SELECT id, content, category FROM mnemosyne.memories "
        "WHERE user_id='default' AND category IN ('session','worklog') AND is_deleted=FALSE "
        "AND metadata->>'fact_extracted' IS NULL "
        "AND length(content) >= 80 "   # v7.0.1: only take long content worth extracting from
        "ORDER BY created_at ASC LIMIT $1", batch)
    return [dict(r) for r in rows]


async def insert_fact(pool, src_id: int, src_category: str, fact: str, ftype: str) -> int:
    """Write a fact to the DB (preference/knowledge, heat 0.65, provenance). Returns the new memory id, or None on failure"""
    vec = await get_embedding(fact)
    vec_str = "[" + ",".join(str(x) for x in vec) + "]"
    metadata = json.dumps({
        "fact_extracted_from": src_id,
        "fact_extracted_at": utcnow(),
        "source_category": src_category,
        "fact_type": ftype,
    }, ensure_ascii=False)
    r = await pool.fetchrow(
        "INSERT INTO memories (user_id, project_id, content, category, embedding, metadata, heat_score, tmt_level, tier, hall) "
        "VALUES ('default', NULL, $1, $2, $3, $4::jsonb, $5, 1, 'L1', 'engineering') "
        "RETURNING id",
        fact, ftype, vec_str, metadata, HEAT_INIT)
    return r["id"] if r else None


async def run(batch: int, dry_run: bool) -> None:
    pool = await get_pool()
    cands = await find_candidates(pool, batch)
    log.info("%d candidates (dry_run=%s)", len(cands), dry_run)

    stats = {"extracted": 0, "facts": 0, "dedup": 0, "fail": 0, "skip": 0, "no_facts": 0}
    in_batch: list[str] = []

    for i, c in enumerate(cands, 1):
        if is_skip(c["content"]):
            stats["skip"] += 1
            continue
        log.info("[%d/%d] Extracting #%d (%s): %.40s...", i, len(cands), c["id"], c["category"], c["content"])
        facts, status = await extract_facts(c["content"])
        if status == "fail":
            stats["fail"] += 1
            continue
        if status == "no_facts" or (status == "ok" and not facts):
            stats["no_facts"] += 1
            if not dry_run:
                await pool.execute(
                    "UPDATE memories SET metadata = metadata || $2::jsonb WHERE id = $1",
                    c["id"], json.dumps({"fact_extracted": True, "fact_extracted_at": utcnow(),
                                          "fact_result": "no_facts"}, ensure_ascii=False))
            continue
        if not dry_run:
            for f in facts:
                if await dedup_check(pool, f, in_batch):
                    stats["dedup"] += 1
                    continue
                ftype = classify_fact(f)
                ok = await insert_fact(pool, c["id"], c["category"], f, ftype)
                if ok:
                    stats["facts"] += 1
                    in_batch.append(f)
                    log.info("  +%s [%s]: %.50s", ftype, ftype, f)
            await pool.execute(
                "UPDATE memories SET metadata = metadata || $2::jsonb WHERE id = $1",
                c["id"], json.dumps({"fact_extracted": True, "fact_extracted_at": utcnow()},
                                    ensure_ascii=False))
        stats["extracted"] += 1

    log.info("═══ Fact extraction result: processed %d → %d facts / %d dedup / %d skipped / %d no_facts / %d failed ═══",
             stats["extracted"], stats["facts"], stats["dedup"], stats["skip"], stats["no_facts"], stats["fail"])
    await pool.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.batch, args.dry_run))


if __name__ == "__main__":
    main()
