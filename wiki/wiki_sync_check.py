#!/usr/bin/env python3
"""wiki_sync_check — periodic self-check of the live snapshot vs. the local source (v7.4)

Design: the WSL local source is the source of truth; the production wiki is an
archive snapshot.
WSL gets shut down, so the periodic self-check lives in production instead:
compare the "live snapshot" against the "hash list from the last sync".
- If the live hashes match the list → the snapshot is healthy
- If they don't match → a local update sync happened or something went wrong; log it
- Also checks: live pages with content but an empty hash (stale data) / orphan
  pages with empty content

Usage (production crontab):
    0 5 * * * cd /opt/mnemosyne && venv/bin/python wiki_sync_check.py >> /tmp/wiki_sync_check.log 2>&1
"""
import asyncio
import json
import os
import sys
import time

# add repo root to path (needed for tmt/core when run from the wiki/ subdirectory)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmt.distill import load_env, PG_DSN
load_env()

import asyncpg  # noqa: E402

USER_ID = "default"
SNAPSHOT_FILE = "/opt/mnemosyne/wiki_hash_snapshot.json"


async def main():
    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, title, source_path, content_hash, source_lost, "
                "length(content) AS content_len "
                "FROM wiki_pages WHERE user_id=$1 ORDER BY id",
                USER_ID
            )
    finally:
        await pool.close()

    total = len(rows)
    with_hash = [r for r in rows if r["content_hash"]]
    no_hash = [r for r in rows if not r["content_hash"]]
    empty = [r for r in rows if not r["content_len"]]
    lost = [r for r in rows if r["source_lost"]]

    print(f"[{time.strftime('%Y-%m-%d %H:%M')}] wiki self-check: {total} pages total")
    print(f"  ✅ has fingerprint: {len(with_hash)} | ⚠️ no fingerprint (stale data): {len(no_hash)} | ⚠️ empty content: {len(empty)} | 🏳️ source-lost flag: {len(lost)}")

    issues = []
    for r in no_hash[:10]:
        issues.append(f"no fingerprint: #{r['id']} {r['title']} ({r['content_len']}ch)")
    for r in empty[:10]:
        issues.append(f"empty content: #{r['id']} {r['title']}")
    for r in lost[:10]:
        issues.append(f"source lost: #{r['id']} {r['title']} ({r['source_path']})")
    for i in issues:
        print(f"  ⚠️ {i}")

    # compare against the list from the previous run
    prev = {}
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            pass

    changed = []
    for r in with_hash:
        pid = str(r["id"])
        cur = r["content_hash"]
        if pid in prev and prev[pid] != cur:
            changed.append(f"#{pid} {r['title']}")
    if changed:
        print(f"  🔄 {len(changed)} pages changed vs. the previous list: {'; '.join(changed[:8])}")
    else:
        print("  🔄 No change vs. the previous list")

    # update the list
    snap = {str(r["id"]): r["content_hash"] for r in with_hash}
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)

    # exit code: return 1 if there are issues (so a watchdog can detect it)
    if no_hash or empty:
        sys.exit(1)
    print("  ✅ Self-check complete")


if __name__ == "__main__":
    asyncio.run(main())
