-- v7.4 WIKI knowledge graph MD memory
-- 2026-08-10
-- 1) Add source/fingerprint columns to wiki_pages (full-text snapshot archive semantics)
-- 2) Clean up empty paper-#N placeholder shells

-- Add columns (idempotent: skip if already present)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wiki_pages' AND column_name='source_path') THEN
    ALTER TABLE public.wiki_pages ADD COLUMN source_path text;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wiki_pages' AND column_name='source_url') THEN
    ALTER TABLE public.wiki_pages ADD COLUMN source_url text;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wiki_pages' AND column_name='content_hash') THEN
    ALTER TABLE public.wiki_pages ADD COLUMN content_hash text;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wiki_pages' AND column_name='source_type') THEN
    ALTER TABLE public.wiki_pages ADD COLUMN source_type text DEFAULT 'memo';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wiki_pages' AND column_name='source_lost') THEN
    ALTER TABLE public.wiki_pages ADD COLUMN source_lost boolean DEFAULT FALSE;
  END IF;
END $$;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_wiki_source_path ON public.wiki_pages (source_path);
CREATE INDEX IF NOT EXISTS idx_wiki_source_type ON public.wiki_pages (source_type);

-- Clean up empty placeholder shells (paper-#N, 0 bytes + early test pages, no source, no content)
DELETE FROM public.wiki_pages
WHERE source_path IS NULL AND source_url IS NULL
  AND (content IS NULL OR length(content) = 0)
  AND title LIKE 'paper-%';

-- 2) wiki entity extraction upgrade (v7.4): association table + extraction marker
CREATE TABLE IF NOT EXISTS public.wiki_entities (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    wiki_page_id bigint NOT NULL REFERENCES public.wiki_pages(id) ON DELETE CASCADE,
    entity_id bigint NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    relation text,
    created_at timestamp without time zone DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_we_page ON public.wiki_entities (wiki_page_id);
CREATE INDEX IF NOT EXISTS idx_we_entity ON public.wiki_entities (entity_id);

ALTER TABLE public.wiki_pages ADD COLUMN IF NOT EXISTS extracted_at timestamp without time zone;
