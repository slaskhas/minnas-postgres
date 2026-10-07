#!/usr/bin/env python3
"""
Reflector — Mnemosyne scheduled reflection engine v1.0

Usage:
  python3 reflector.py --mode light    # run hourly: heat decay + redundancy merge
  python3 reflector.py --mode deep     # run nightly: same as above + entity extraction

Design doc: document 14 §6 Reflector reflection engine
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

import asyncpg
import httpx

# ── Config ──
API_BASE = "http://127.0.0.1:8010"
PG_DSN = "postgresql://postgres@127.0.0.1:5432/mnemosyne"
# v5.1 — migrated to Doubao doubao-embedding-vision-251215
from core.embedding import get_embedding as _get_embedding
SIM_THRESHOLD = 0.85  # v6.0: 0.92→0.85, fixed dedup not merging similar phrasings
BATCH_SIZE = 200       # memories processed per batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reflector")


# ── Utility functions ──

async def get_embedding(text: str) -> list[float]:
    """v5.1 — Doubao multimodal embedding (via core.embedding)"""
    return _get_embedding([text])[0]


def cosine_sim(a_raw, b_raw) -> float:
    """pgvector returns its type as a JSON array string "[0.014, -0.042, …]", needs deserializing"""
    a = json.loads(a_raw) if isinstance(a_raw, (str, bytes)) else a_raw
    b = json.loads(b_raw) if isinstance(b_raw, (str, bytes)) else b_raw
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na * nb > 0 else 0.0


def parse_embedding(raw) -> list[float]:
    """Normalize pgvector's return value into a float list"""
    if isinstance(raw, (str, bytes)):
        return json.loads(raw)
    return raw


async def get_all_users(pool) -> list[str]:
    rows = await pool.fetch(
        "SELECT DISTINCT user_id FROM memories WHERE is_deleted = FALSE"
    )
    return [r["user_id"] for r in rows]


# ── Redundancy detection & merging ──

async def detect_redundancy(pool, user_id: str) -> int:
    """
    v6.0: replaced O(n²) Python pairwise comparison with a pgvector ANN index.
    Runs one nearest-neighbor query (LIMIT 3) per candidate memory, merging
    pairs with sim > SIM_THRESHOLD.
    Keeps whichever has the higher heat_score, transfers its entities, and
    soft-deletes the other.
    """
    merged = 0
    checked: set[int] = set()
    # Candidates: top BATCH_SIZE by descending heat (prioritize merging
    # redundancy among high-value memories)
    rows = await pool.fetch(
        "SELECT id, content, heat_score FROM memories "
        "WHERE user_id=$1 AND is_deleted=FALSE AND embedding IS NOT NULL "
        "ORDER BY heat_score DESC, access_count DESC LIMIT $2",
        user_id, BATCH_SIZE,
    )
    if len(rows) < 2:
        return 0

    for i in range(len(rows)):
        if merged >= BATCH_SIZE:
            break
        if rows[i]["id"] in checked:
            continue

        ei_raw = await pool.fetchval(
            "SELECT embedding::text FROM memories WHERE id = $1", rows[i]["id"]
        )
        if ei_raw is None:
            continue
        ei = parse_embedding(ei_raw)
        vec_str = "[" + ",".join(str(x) for x in ei) + "]"

        # ANN: nearest-neighbor query (via ivfflat index, millisecond-scale)
        neighbors = await pool.fetch(
            "SELECT id, heat_score, 1 - (embedding <=> $1::vector) AS sim "
            "FROM memories WHERE user_id=$2 AND is_deleted=FALSE "
            "AND embedding IS NOT NULL AND id != $3 "
            "AND 1 - (embedding <=> $1::vector) > $4 "
            "ORDER BY embedding <=> $1::vector LIMIT 3",
            vec_str, user_id, rows[i]["id"], SIM_THRESHOLD,
        )
        for nb in neighbors:
            if merged >= BATCH_SIZE:
                break
            if nb["id"] in checked:
                continue

            keep_id = rows[i]["id"]
            del_id = nb["id"]
            if nb["heat_score"] > rows[i]["heat_score"]:
                keep_id, del_id = del_id, keep_id

            # Transfer entities (avoiding duplicates)
            await pool.execute(
                """UPDATE memory_entities
                   SET memory_id = $1
                   WHERE memory_id = $2
                     AND entity_id NOT IN (
                       SELECT entity_id FROM memory_entities WHERE memory_id = $1
                     )""",
                keep_id, del_id,
            )
            # Soft-delete the redundant memory
            await pool.execute(
                "UPDATE memories SET is_deleted = TRUE WHERE id = $1", del_id
            )
            # The surviving record absorbs the access count
            await pool.execute(
                "UPDATE memories SET access_count = access_count + 1 WHERE id = $1",
                keep_id,
            )

            checked.add(del_id)
            merged += 1
            log.info("  └─ Merged #%d → #%d  (sim=%.3f, keep_heat=%.1f)",
                      del_id, keep_id, nb["sim"],
                      max(rows[i]["heat_score"], nb["heat_score"]))

    return merged


# ── Run modes ──

async def run_light(pool, users: list[str]):
    """Light mode: heat decay + redundancy merge"""
    for uid in users:
        log.info("[light] Processing user=%s", uid)
        # 1. Call the reflect API (heat decay + tier migration)
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    f"{API_BASE}/api/v1/reflect",
                    params={"user_id": uid, "mode": "light"},
                )
                if r.status_code == 200:
                    log.info("  ├─ reflect(light) API OK")
                else:
                    log.warning("  ├─ reflect API returned %s", r.status_code)
        except Exception as e:
            log.error("  ├─ reflect API error: %s", e)

        # 2. Redundancy detection
        n = await detect_redundancy(pool, uid)
        if n:
            log.info("  └─ Merged %d redundant memories", n)
        else:
            log.info("  └─ No redundancy found")


async def run_deep(pool, users: list[str]):
    """Deep mode: heat decay + entity extraction + redundancy merge"""
    for uid in users:
        log.info("[deep] Processing user=%s", uid)
        # 1. Call the reflect(deep) API (heat decay + entity extraction)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{API_BASE}/api/v1/reflect",
                    params={"user_id": uid, "mode": "deep"},
                )
                if r.status_code == 200:
                    log.info("  ├─ reflect(deep) API OK")
                else:
                    log.warning("  ├─ reflect API returned %s", r.status_code)
        except Exception as e:
            log.error("  ├─ reflect API error: %s", e)

        # 2. Redundancy detection
        n = await detect_redundancy(pool, uid)
        if n:
            log.info("  └─ Merged %d redundant memories", n)
        else:
            log.info("  └─ No redundancy found")


# ── Entry point ──

async def main():
    parser = argparse.ArgumentParser(description="Mnemosyne Reflector — scheduled reflection engine")
    parser.add_argument(
        "--mode",
        choices=["light", "deep"],
        default="light",
        help="light=hourly (heat+redundancy), deep=daily (includes entity extraction)",
    )
    args = parser.parse_args()

    log.info("Reflector starting — mode=%s", args.mode)
    start = datetime.now(timezone.utc)

    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
    try:
        # v6.0: converged to a single user — only process "default" (legacy
        # user_ids were consolidated by a data migration)
        users = await get_all_users(pool)
        users = [u for u in users if u == "default"]
        if not users:
            log.info("No users found, nothing to do.")
            return

        log.info("Found %d active user(s)", len(users))

        if args.mode == "light":
            await run_light(pool, users)
        else:
            await run_deep(pool, users)

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        log.info("Reflector completed in %.1fs", elapsed)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
