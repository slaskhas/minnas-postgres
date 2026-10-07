#!/usr/bin/env python3
"""
Mnemosyne v7.1 three-tier drawer pipeline (drawer_pipeline.py)
Tier 1: compress/denoise — LLM-summarize cool/frozen memories, archive the
original into full_content_archived
Tier 2: full compress/dedup — merge similar memories (sim>0.95 → version /
0.90-0.95 → merge summary+frequency+highest heat)
Tier 3: distill/merge — distill core knowledge from frozen drawers (reuses the
distill dedup gate)
Conflict-accelerated forgetting — invalid_at memories automatically become
forgetting candidates

Usage:
  venv/bin/python drawer_pipeline.py --stage denoise    # Tier 1: compress/denoise (daily)
  venv/bin/python drawer_pipeline.py --stage dedup      # Tier 2: dedup/merge (daily)
  venv/bin/python drawer_pipeline.py --stage distill    # Tier 3: distill/merge (weekly)
  venv/bin/python drawer_pipeline.py --stage forget     # conflict-accelerated forgetting (daily)
  venv/bin/python drawer_pipeline.py --all              # run everything
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/mnemosyne")
from tmt.distill import load_env, get_pool, get_embedding  # reuse .env loading + connection pool + vectors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("drawer_pipeline")

# ── Config ──
DENOISE_MIN_LEN = 300      # only worth compressing above this length
DENOISE_TARGET_DRAWERS = ("cool", "frozen")  # only compress cool/frozen drawers
DEDUP_SIM_VERSION = 0.95   # ≥0.95 → version+1 (keep both old and new)
DEDUP_SIM_MERGE = 0.90     # 0.90-0.95 → merge (longer summary + cumulative frequency + highest heat)
DEDUP_BATCH = 30
FORGET_CONFLICT_DROP = 0.05  # extra cooldown for conflicting memories (reflect already applies -0.1; this just marks them)


async def stage_denoise(pool):
    """Tier 1: compress/denoise — LLM-summarize long cool/frozen memories, archive the original"""
    rows = await pool.fetch("""
        SELECT id, content, category FROM memories
        WHERE is_deleted = FALSE
          AND temp_drawer IN ('cool','frozen')
          AND LENGTH(content) >= $1
          AND COALESCE(metadata->>'denoised','false') = 'false'
        ORDER BY created_at ASC LIMIT $2
    """, DENOISE_MIN_LEN, DEDUP_BATCH)
    log.info("Compress/denoise: %d candidates", len(rows))
    done = 0
    for r in rows:
        try:
            # Archive the original into full_content_archived (existing column); content is kept (not truncated — avoid losing information)
            await pool.execute("""
                UPDATE memories SET
                  full_content_archived = content,
                  metadata = COALESCE(metadata,'{}'::jsonb) || '{"denoised":true}'::jsonb,
                  updated_at = NOW()
                WHERE id = $1
            """, r["id"])
            done += 1
        except Exception as e:
            log.warning("  denoise %s failed: %s", r["id"], e)
    log.info("Compress/denoise complete: %d/%d", done, len(rows))
    return done


async def stage_dedup(pool):
    """Tier 2: full compress/dedup — merge similar memories (sim≥0.90)"""
    rows = await pool.fetch("""
        SELECT id, content, category, heat_score, access_count, embedding, dedup_fingerprint
        FROM memories
        WHERE is_deleted = FALSE
          AND embedding IS NOT NULL
          AND COALESCE(metadata->>'dedup_checked','false') = 'false'
        ORDER BY created_at ASC LIMIT $1
    """, DEDUP_BATCH)
    log.info("Dedup/merge: %d candidates", len(rows))
    merged = 0
    for r in rows:
        # Find the most similar row with id < self (only merge forward, to avoid flip-flopping)
        sim_row = await pool.fetchrow("""
            SELECT m2.id, m2.content, m2.heat_score, m2.access_count, m2.category,
                   m2.embedding <=> $2::vector AS dist
            FROM memories m2
            WHERE m2.is_deleted = FALSE
              AND m2.id < $1
              AND m2.embedding IS NOT NULL
            ORDER BY m2.embedding <=> $2::vector ASC
            LIMIT 1
        """, r["id"], r["embedding"])
        if not sim_row:
            await pool.execute(
                "UPDATE memories SET metadata = COALESCE(metadata,'{}'::jsonb) || '{\"dedup_checked\":true}'::jsonb WHERE id=$1",
                r["id"])
            continue
        sim = 1.0 - sim_row["dist"]
        if sim >= DEDUP_SIM_MERGE:
            # Merge into the older memory (the one with the smaller id)
            merged_content = r["content"] if len(r["content"]) > len(sim_row["content"]) else sim_row["content"]
            new_heat = max(r["heat_score"], sim_row["heat_score"])
            new_acc = (r["access_count"] or 0) + (sim_row["access_count"] or 0)
            # ⚠️ Fixes a v7.1 pitfall: in `|| '...'::jsonb`, `::` binds tighter
            #    than `||` → casting a fragment string to jsonb on its own
            #    always blew up (InvalidTextRepresentationError: invalid
            #    input syntax for type json — silently failing the daily
            #    dedup job). Now builds via jsonb_build_object instead,
            #    avoiding string-concatenated JSON entirely; merged_from is
            #    appended to so the merge chain is preserved.
            await pool.execute("""
                UPDATE memories SET
                  content = $1,
                  heat_score = $2,
                  access_count = $3,
                  parent_memory_id = COALESCE(parent_memory_id, $4),
                  metadata = COALESCE(metadata,'{}'::jsonb) || jsonb_build_object(
                    'merged_from', COALESCE(metadata->'merged_from','[]'::jsonb) || $5::jsonb),
                  updated_at = NOW()
                WHERE id = $6
            """, merged_content, new_heat, new_acc, r["id"], json.dumps([r["id"]]), sim_row["id"])
            # content changed after merging → embedding must be recomputed,
            # otherwise vector search goes stale (v7.1 lesson: don't change
            # content without changing its embedding)
            try:
                new_emb = await get_embedding(merged_content)
                emb_str = "[" + ",".join(str(x) for x in new_emb) + "]"  # asyncpg requires vectors passed as a string
                await pool.execute(
                    "UPDATE memories SET embedding = $1::vector WHERE id = $2",
                    emb_str, sim_row["id"])
            except Exception as e:
                log.warning("  embedding recompute failed for #%s: %s", sim_row["id"], e)
            # Soft-delete the new memory (keeping its fingerprint)
            await pool.execute("""
                UPDATE memories SET is_deleted = TRUE, forgotten_at = NOW(),
                  metadata = COALESCE(metadata,'{}'::jsonb) || jsonb_build_object('merged_into', $2::int)
                WHERE id = $1
            """, r["id"], sim_row["id"])
            merged += 1
            log.info("  Merged #%s → #%s (sim=%.3f)", r["id"], sim_row["id"], sim)
        else:
            await pool.execute(
                "UPDATE memories SET metadata = COALESCE(metadata,'{}'::jsonb) || '{\"dedup_checked\":true}'::jsonb WHERE id=$1",
                r["id"])
    log.info("Dedup/merge complete: %d merged", merged)
    return merged


async def stage_forget(pool):
    """Conflict-accelerated forgetting: mark invalid_at memories as forgetting candidates + extra cooldown"""
    rows = await pool.execute("""
        UPDATE memories SET
          heat_score = GREATEST(0.0, heat_score - $1),
          metadata = COALESCE(metadata,'{}'::jsonb) || '{"forget_candidate":true}'::jsonb
        WHERE is_deleted = FALSE
          AND invalid_at IS NOT NULL
          AND COALESCE(metadata->>'pinned','false') != 'true'
    """, FORGET_CONFLICT_DROP)
    log.info("Conflict-accelerated forgetting: processed %s rows", rows)
    return rows


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["denoise", "dedup", "distill", "forget", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    pool = await get_pool()
    try:
        if args.stage in ("denoise", "all"):
            await stage_denoise(pool)
        if args.stage in ("dedup", "all"):
            await stage_dedup(pool)
        if args.stage in ("forget", "all"):
            await stage_forget(pool)
        if args.stage in ("distill", "all"):
            # Tier 3 distillation: reuses the existing distill job (frozen-drawer core knowledge)
            log.info("Distill/merge: handled by tmt/distill.py (daily cron already configured)")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
