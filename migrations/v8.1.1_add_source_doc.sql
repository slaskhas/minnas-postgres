-- ══════════════════════════════════════════════════════════════════════════════
-- Minnas v8.1.1 · Migration
--   Add mnemosyne.memories.source_doc — free-form string (URL or file path)
--   identifying the source document a memory's content was derived from.
--
-- Date     : 2026-10-09
-- Idempotent: IF NOT EXISTS guard — safe to re-run without error
-- Rollback : ALTER TABLE mnemosyne.memories DROP COLUMN IF EXISTS source_doc;
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE mnemosyne.memories ADD COLUMN IF NOT EXISTS source_doc text;
