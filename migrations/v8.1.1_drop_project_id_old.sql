-- ══════════════════════════════════════════════════════════════════════════════
-- Minnas v8.1.1 · Migration
--   Drop mnemosyne.memories.project_id_old — legacy string-typed column, superseded
--   by the bigint project_id column since the v7.6.2 str→int type contract fix.
--
-- Date     : 2026-10-09
-- Idempotent: IF EXISTS guard — safe to re-run without error
-- Rollback : ALTER TABLE mnemosyne.memories ADD COLUMN project_id_old text;
--            (data is not recoverable once dropped — confirmed zero non-null
--            rows before this migration was applied; nothing to re-populate)
-- ══════════════════════════════════════════════════════════════════════════════

ALTER TABLE mnemosyne.memories DROP COLUMN IF EXISTS project_id_old;
