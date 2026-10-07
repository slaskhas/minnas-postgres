-- Mnemosyne v7.1 drawer migration (2026-08-09)
-- Production DB ag_catalog? No — memories is in the public schema (verified via \d public.memories)
-- But note: palace tables (tome_cards/tmt_*) are in ag_catalog; memories is in public ✓

-- 1. Add dual-drawer fields + dedup fingerprint (idempotent: IF NOT EXISTS)
ALTER TABLE public.memories
  ADD COLUMN IF NOT EXISTS temp_drawer varchar(10) DEFAULT 'normal',
  ADD COLUMN IF NOT EXISTS time_drawer varchar(10) DEFAULT 'recent',
  ADD COLUMN IF NOT EXISTS dedup_fingerprint varchar(64),
  ADD COLUMN IF NOT EXISTS full_content_archived text;

-- 2. Constraints (drop old ones first, then recreate — idempotent)
ALTER TABLE public.memories DROP CONSTRAINT IF EXISTS chk_temp_drawer;
ALTER TABLE public.memories ADD CONSTRAINT chk_temp_drawer
  CHECK (temp_drawer IN ('hot','normal','cool','frozen'));
ALTER TABLE public.memories DROP CONSTRAINT IF EXISTS chk_time_drawer;
ALTER TABLE public.memories ADD CONSTRAINT chk_time_drawer
  CHECK (time_drawer IN ('recent','mid','long'));

-- 3. Indexes (for querying drawer distribution / forget candidates)
CREATE INDEX IF NOT EXISTS idx_memories_temp_drawer ON public.memories (temp_drawer) WHERE is_deleted = FALSE;
CREATE INDEX IF NOT EXISTS idx_memories_time_drawer ON public.memories (time_drawer) WHERE is_deleted = FALSE;
CREATE INDEX IF NOT EXISTS idx_memories_forget_candidate ON public.memories ((metadata->>'forget_candidate')) WHERE is_deleted = FALSE;
-- v7.1 review addendum: composite index (high-frequency drawer queries: status/forget-candidate stats)
CREATE INDEX IF NOT EXISTS idx_memories_drawers_join ON public.memories (temp_drawer, time_drawer) WHERE is_deleted = FALSE;

-- 4. Backfill existing rows: derive drawers from current heat_score + last_accessed (run once; reflect maintains it after)
UPDATE public.memories SET temp_drawer = CASE
    WHEN heat_score >= 0.7 THEN 'hot'
    WHEN heat_score >= 0.3 THEN 'normal'
    WHEN heat_score >= 0.1 THEN 'cool'
    ELSE 'frozen'
  END
WHERE is_deleted = FALSE;

UPDATE public.memories SET time_drawer = CASE
    WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '30 days' THEN 'recent'
    WHEN COALESCE(last_accessed, created_at) > NOW() - INTERVAL '90 days' THEN 'mid'
    ELSE 'long'
  END
WHERE is_deleted = FALSE;

-- 5. Initial forget-candidate marking (same rule as reflect)
UPDATE public.memories SET metadata = COALESCE(metadata,'{}'::jsonb) || '{"forget_candidate":true}'::jsonb
WHERE is_deleted = FALSE
  AND temp_drawer = 'frozen' AND time_drawer = 'long'
  AND COALESCE(metadata->>'pinned','false') != 'true'
  AND category != 'preference';

-- 6. Verification
SELECT 'drawer distribution' AS check_name;
SELECT temp_drawer, COUNT(*) FROM public.memories WHERE is_deleted = FALSE GROUP BY 1 ORDER BY 1;
SELECT 'time drawer distribution' AS check_name;
SELECT time_drawer, COUNT(*) FROM public.memories WHERE is_deleted = FALSE GROUP BY 1 ORDER BY 1;
SELECT 'forget candidates' AS check_name;
SELECT COUNT(*) FROM public.memories
WHERE is_deleted = FALSE AND COALESCE(metadata->>'forget_candidate','false') = 'true';
