-- Mnemosyne v7.3 composite-ranking migration (2026-08-09)
-- Shifting from forgetting toward organization/optimization + efficient retrieval
-- Adds: mention count / composite rank / fast pointer table

-- 1. New fields on memories (idempotent)
ALTER TABLE public.memories
  ADD COLUMN IF NOT EXISTS mention_count integer DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_mention timestamptz,
  ADD COLUMN IF NOT EXISTS rank_score numeric DEFAULT 0;

-- 2. Fast full-scan pointer table (idempotent)
CREATE TABLE IF NOT EXISTS public.memory_pointer (
  memory_id     bigint PRIMARY KEY REFERENCES public.memories(id) ON DELETE CASCADE,
  rank_score    numeric DEFAULT 0,
  palace_path   text,
  archive_no    text,
  mention_count integer DEFAULT 0,
  last_mention  timestamptz,
  created_at    timestamptz DEFAULT now(),
  updated_at    timestamptz DEFAULT now()
);

-- 3. Pointer table indexes (idempotent)
CREATE INDEX IF NOT EXISTS idx_pointer_rank ON public.memory_pointer (rank_score DESC);
CREATE INDEX IF NOT EXISTS idx_pointer_palace ON public.memory_pointer (palace_path);
CREATE INDEX IF NOT EXISTS idx_pointer_archive ON public.memory_pointer (archive_no);

-- 4. Backfill existing rows into the pointer table (non-deleted memories)
INSERT INTO public.memory_pointer (memory_id, rank_score, palace_path, archive_no, mention_count, last_mention)
SELECT id,
       ROUND((0.3*storage_strength + 0.3*retrieval_strength + 0.2*heat_score*10 + 0.2*(LN(access_count+1)/LN(1001)) )::numeric, 4) AS rank_score,
       NULL AS palace_path,
       archive_no,
       access_count AS mention_count,
       COALESCE(last_accessed, created_at) AS last_mention
FROM public.memories
WHERE is_deleted = FALSE
ON CONFLICT (memory_id) DO NOTHING;

-- 5. Verification
SELECT 'pointer table backfill' AS check_name;
SELECT count(*) AS pointer_rows FROM public.memory_pointer;
SELECT 'rank distribution' AS check_name;
SELECT ROUND(rank_score) AS rank_bucket, count(*) FROM public.memory_pointer GROUP BY 1 ORDER BY 1;
