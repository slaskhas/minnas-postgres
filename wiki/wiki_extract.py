#!/usr/bin/env python3
"""wiki_extract — WIKI page LLM entity extraction (v7.4, v7.8 removed AGE)

For pages in wiki_pages that haven't been extracted yet:
1. LLM extracts {entity name, type, description}
2. upsert into the entities table (with embedding + type)
3. write the wiki_entities association (page→entity)
(v7.8: the AGE graph was removed, relation extraction was removed along with it —
no consumer left, so it doesn't spend LLM tokens for nothing)

Usage (production server):
    cd /opt/mnemosyne && venv/bin/python3 wiki_extract.py --batch 5
    venv/bin/python3 wiki_extract.py --batch 5 --dry-run
"""
import asyncio
import json
import os
import sys
import time

# add repo root to path (needed for tmt/core when run from the wiki/ subdirectory)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# load_env must run first (core.config reads env vars at import time)
from tmt.distill import load_env, PG_DSN
load_env()

import asyncpg  # noqa: E402
from core.llm import call_llm_json  # noqa: E402
from core.embedding import get_embedding_async  # noqa: E402

async def get_embedding(texts):
    return await get_embedding_async(texts)

USER_ID = "default"

# NOTE: the prompt text below is sent to the LLM and instructs it to respond in
# Chinese — left untranslated intentionally, this is functional prompt data, not
# a comment.
PROMPT = """你是知识图谱抽取引擎。从下面的文章/论文中抽取核心实体，输出严格 JSON。

要求:
- 只抽有实质意义的实体: 概念/理论/系统/人名/技术/架构/材料/地点 (不抽虚词)
- 每篇 8-20 个实体, 每个实体给: name(规范名), type(concept|theory|system|person|tech|architecture|material|place|other), description(一句话, ≤40字)
- 输出格式: {"entities": [{"name":"","type":"","description":""}]}
- 不要输出 JSON 以外的任何内容

文章内容:
{content}
"""


async def extract_page(conn, page):
    pid = page["id"]
    title = page["title"]
    content = (page["content"] or "")[:6000]  # truncate to prevent overlong input
    prompt = PROMPT.replace("{content}", content)
    try:
        res = call_llm_json(prompt, tier=3)
        raw = res.get("content") if isinstance(res, dict) else None
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return {"page_id": pid, "status": "empty", "title": title}
        entities = data.get("entities", []) or []
        if not entities:
            return {"page_id": pid, "status": "no-entities", "title": title}

        # name → entity_id mapping
        name2id = {}
        for ent in entities:
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            typ = ent.get("type") or "concept"
            desc = (ent.get("description") or "")[:200]
            row = await conn.fetchrow("SELECT id FROM entities WHERE user_id=$1 AND name=$2", USER_ID, name)
            if row:
                eid = row["id"]
                if desc:
                    await conn.execute("UPDATE entities SET type=$1, description=$2 WHERE id=$3", typ, desc, eid)
            else:
                try:
                    r_v = (await get_embedding([name]))[0]
                    v_str = "[" + ",".join(str(x) for x in r_v) + "]"
                    row = await conn.fetchrow(
                        "INSERT INTO entities (user_id, name, type, description, embedding) "
                        "VALUES ($1,$2,$3,$4,$5::vector) RETURNING id",
                        USER_ID, name, typ, desc, v_str
                    )
                    eid = row["id"]
                except Exception as e:
                    print(f"  [warn] entity {name} insert failed: {e}")
                    continue
            name2id[name] = eid
            # page association (v7.8: only writes the wiki_entities table now;
            # AGE RELATED_TO edges were removed along with AGE)
            await conn.execute(
                "INSERT INTO wiki_entities (wiki_page_id, entity_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                pid, eid
            )

        # mark as extracted
        await conn.execute("UPDATE wiki_pages SET extracted_at=now() WHERE id=$1", pid)
        return {"page_id": pid, "status": "ok", "title": title,
                "entities": len(name2id), "relations": 0}
    except Exception as e:
        return {"page_id": pid, "status": "error", "title": title, "error": str(e)[:200]}


async def main(batch: int, dry_run: bool):
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, title, content FROM wiki_pages "
                "WHERE user_id=$1 AND content IS NOT NULL AND length(content)>200 AND extracted_at IS NULL "
                "ORDER BY id LIMIT $2",
                USER_ID, batch
            )
            if not rows:
                print("No pages pending extraction")
                return
            if dry_run:
                print(f"[dry-run] {len(rows)} pages pending extraction:")
                for r in rows:
                    print(f"  #{r['id']} {r['title']} ({len(r['content'])}ch)")
                return
            print(f"Extracting {len(rows)} pages ...")
            for page in rows:
                start = time.time()
                r = await extract_page(conn, page)
                r["sec"] = round(time.time() - start, 1)
                print(json.dumps(r, ensure_ascii=False))
    finally:
        await pool.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.batch, args.dry_run))
