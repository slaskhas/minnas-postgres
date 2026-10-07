#!/usr/bin/env python3
"""wiki_tokenize — WIKI page jieba tokenization → wiki_keywords table (v7.5 P0a)

Builds the index for the BM25 keyword channel: tokenizes each page, strips stop
words, stores token+freq.
- Title weighted ×3, body ×1 (title words are more important)
- Filters out: single characters/pure numbers/overly long tokens/stop words

Usage (production server):
    cd /opt/mnemosyne && venv/bin/python wiki_tokenize.py --batch 20
    venv/bin/python wiki_tokenize.py --all
"""
import asyncio
import os
import sys
import time
from collections import Counter

# add repo root to path (needed for tmt/core when run from the wiki/ subdirectory)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmt.distill import load_env, PG_DSN
load_env()

import asyncpg  # noqa: E402
import jieba  # noqa: E402

# v7.5 expert review P1: load the domain dictionary (for terminology tokenization)
import os as _os
_DICT_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "wiki_dict.txt")
if _os.path.exists(_DICT_PATH):
    jieba.load_userdict(_DICT_PATH)

USER_ID = "default"

STOPWORDS = set()  # kept minimal, delegated to jieba's default dictionary


def clean_token(tok: str) -> bool:
    """Filters out: single characters, pure numbers/punctuation, overly long tokens"""
    if len(tok) < 2 or len(tok) > 20:
        return False
    if tok.isdigit() or tok.isspace():
        return False
    if not any(c.isalnum() for c in tok):
        return False
    return True


def tokenize_content(title: str, content: str) -> list:
    """Returns [(token, freq), ...], with title words weighted higher"""
    title_toks = [t.strip() for t in jieba.cut(title) if clean_token(t.strip())]
    body_toks = [t.strip() for t in jieba.cut(content[:20000]) if clean_token(t.strip())]
    cnt = Counter()
    for t in title_toks:
        cnt[t] += 3
    for t in body_toks:
        cnt[t] += 1
    return list(cnt.items())


async def tokenize_page(conn, page) -> dict:
    pid = page["id"]
    title = page["title"] or ""
    content = page["content"] or ""
    if len(content) < 50:
        return {"page_id": pid, "status": "skipped-small", "title": title}

    toks = tokenize_content(title, content)
    if not toks:
        return {"page_id": pid, "status": "no-tokens", "title": title}

    # clear old + write new (idempotent rebuild)
    await conn.execute("DELETE FROM wiki_keywords WHERE page_id=$1", pid)
    await conn.executemany(
        "INSERT INTO wiki_keywords (page_id, token, freq) VALUES ($1,$2,$3) "
        "ON CONFLICT (page_id, token) DO UPDATE SET freq=EXCLUDED.freq",
        [(pid, t, f) for t, f in toks]
    )
    return {"page_id": pid, "status": "ok", "title": title, "tokens": len(toks)}


async def main(batch: int, all_pages: bool):
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            if all_pages:
                rows = await conn.fetch(
                    "SELECT id, title, content FROM wiki_pages WHERE user_id=$1 AND content IS NOT NULL "
                    "AND length(content)>50 ORDER BY id",
                    USER_ID
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, title, content FROM wiki_pages WHERE user_id=$1 AND content IS NOT NULL "
                    "AND length(content)>50 AND id NOT IN (SELECT DISTINCT page_id FROM wiki_keywords) "
                    "ORDER BY id LIMIT $2",
                    USER_ID, batch
                )
            if not rows:
                print("No pages pending tokenization")
                return
            print(f"Tokenizing {len(rows)} pages ...")
            total_tokens = 0
            for page in rows:
                start = time.time()
                r = await tokenize_page(conn, page)
                r["sec"] = round(time.time() - start, 2)
                total_tokens += r.get("tokens", 0)
                if r["status"] == "ok":
                    print(f"  #{r['page_id']} {r['title'][:30]} tokens={r['tokens']}")
            print(f"Done: {len(rows)} pages, {total_tokens} tokens")
    finally:
        await pool.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--all", action="store_true", help="full rebuild")
    args = ap.parse_args()
    asyncio.run(main(args.batch, args.all))
