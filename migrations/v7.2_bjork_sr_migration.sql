-- Mnemosyne v7.2 Bjork S/R separation migration (2026-08-09)
-- Storage strength S (1-10, non-decaying) / Retrieval strength R (1-10, exponential decay, 30-day half-life)
-- Coexists with v7.1 heat_score/temp_drawer: S/R is the new-generation drawer basis; heat_score kept for compatibility

-- 1. Add S/R fields (idempotent)
ALTER TABLE public.memories
  ADD COLUMN IF NOT EXISTS storage_strength double precision DEFAULT 3,
  ADD COLUMN IF NOT EXISTS retrieval_strength double precision DEFAULT 3;

-- 2. Backfill existing rows: derive S from heat_score + category + access_count
--    Manually pinned / important categories (preference/pin): S=7; knowledge categories: S=5; everything else: S=3
--    R initialized = S (decayed afterward by reflect based on access)
UPDATE public.memories SET
  storage_strength = CASE
    WHEN COALESCE(metadata->>'pinned','false') = 'true' OR category = 'preference' THEN 7
    WHEN category IN ('knowledge','pitfall','reference') THEN 5
    ELSE 3
  END,
  retrieval_strength = CASE
    WHEN COALESCE(metadata->>'pinned','false') = 'true' OR category = 'preference' THEN 7
    WHEN category IN ('knowledge','pitfall','reference') THEN 5
    ELSE 3
  END
WHERE is_deleted = FALSE;

-- 3. Index (for S/R queries)
CREATE INDEX IF NOT EXISTS idx_memories_sr ON public.memories (storage_strength, retrieval_strength) WHERE is_deleted = FALSE;

-- 4. Verification
SELECT 'S/R initialization distribution' AS check_name;
SELECT storage_strength, COUNT(*) FROM public.memories WHERE is_deleted = FALSE GROUP BY 1 ORDER BY 1;
