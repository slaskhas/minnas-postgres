-- ══════════════════════════════════════════════════════════════════════════════
-- Mnemosyne OS v8.0 · Migration
--   Write-atomicity support / idempotency-key unique index / memory garbage-collection (GC) support
--
-- Date     : 2026-09-25
-- Proposal : P-20260925-01  (openspec/changes/2026-09-25-v8-memory-os/proposal.md)
-- ADR      : docs/adr/0002-filesystem-mechanism-tradeoffs-and-triggers.md
-- Idempotent: everything uses IF NOT EXISTS / conditional checks — safe to re-run without error
-- Rollback : see the "Rollback section" at the end of this file
-- ══════════════════════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────────────────────────────────────
-- S1-2 · Idempotency key: unique index on dedup_fingerprint
-- ─────────────────────────────────────────────────────────────────────────────
-- Current state [verified 2026-09-25 against production]: memories has 16,595 rows, **0** rows
--   have a non-null dedup_fingerprint → this column has never been populated since it was added
--   in v7.1, so it provides no idempotency today.
--
-- Decision: **do not backfill historical fingerprints**. Three reasons:
--   (a) PostgreSQL unique indexes **ignore NULL** → creating the index directly won't fail due to
--       historical duplicates;
--   (b) backfilling would expose historical duplicate rows (132 groups with identical content,
--       verified) to the unique index → index creation **could fail**;
--   (c) deduplication of historical semantics is already the job of detect_conflict()'s
--       merge/conflict path, which doesn't overlap with this key's responsibility.
--
-- Effect: every new write now carries a fingerprint → crash retries / client-server disconnect
-- resends / duplicate POSTs **no longer create new rows**; the original id is returned instead.
CREATE UNIQUE INDEX IF NOT EXISTS dedup_fingerprint_key
    ON memories (dedup_fingerprint)
    WHERE dedup_fingerprint IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- S1-3 · Cold archive table: a **recoverable record** taken before physical deletion
-- ─────────────────────────────────────────────────────────────────────────────
-- Design: structurally identical to memories (LIKE) plus three bookkeeping columns.
--   _archived_at   the moment of archiving
--   _archive_batch batch id (matches gc_log.batch, enabling whole-batch restore)
--   _traces        a jsonb snapshot of this memory's memory_traces rows — because CASCADE would
--                  otherwise wipe out traces along with the parent row
-- ⚠️ v8.0.1 correction: the CASCADE child tables **must be snapshotted too**, otherwise a restore
--   produces a "zombie memory".
--   Verified (2026-09-25, reproduced after red-team flagged it): before deletion keywords=1,
--   tome_cards=1 → after deletion both are 0 → after --restore, the memories row comes back, but
--   keywords/tome_cards are still 0.
--   → The memory exists in the DB, but BM25 can't find it and it has no catalog card.
--   Root cause: the original implementation only archived memories + traces, while
--   memory_entities / memory_keywords / tome_cards are all ON DELETE CASCADE off memories, so they
--   get **silently deleted along with it**.
CREATE TABLE IF NOT EXISTS memories_archive (LIKE memories INCLUDING DEFAULTS);
ALTER TABLE memories_archive ADD COLUMN IF NOT EXISTS _archived_at   timestamptz DEFAULT NOW();
ALTER TABLE memories_archive ADD COLUMN IF NOT EXISTS _archive_batch text;
ALTER TABLE memories_archive ADD COLUMN IF NOT EXISTS _traces        jsonb;
ALTER TABLE memories_archive ADD COLUMN IF NOT EXISTS _entities      jsonb;  -- memory_entities snapshot
ALTER TABLE memories_archive ADD COLUMN IF NOT EXISTS _keywords      jsonb;  -- memory_keywords snapshot (BM25)
ALTER TABLE memories_archive ADD COLUMN IF NOT EXISTS _tome_cards    jsonb;  -- tome_cards snapshot (catalog card)


-- ─────────────────────────────────────────────────────────────────────────────
-- S1-3 · GC ledger: every reclamation run is auditable and reviewable
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gc_log (
    id           bigserial   PRIMARY KEY,
    run_at       timestamptz NOT NULL DEFAULT NOW(),
    batch        text        NOT NULL,
    dry_run      boolean     NOT NULL,
    candidates   integer     NOT NULL DEFAULT 0,   -- number of candidates that entered the window
    purged       integer     NOT NULL DEFAULT 0,   -- number actually physically deleted
    refused_ref  integer     NOT NULL DEFAULT 0,   -- number **refused** reclamation due to still being referenced
    traces_kept  integer     NOT NULL DEFAULT 0,   -- number of trace rows archived alongside
    rollback_csv text                              -- path to the rollback evidence file
);

CREATE INDEX IF NOT EXISTS idx_gc_log_run_at ON gc_log (run_at DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- S1-3 · memory_traces foreign key changed to ON DELETE CASCADE
-- ─────────────────────────────────────────────────────────────────────────────
-- Current state [verified]: memory_traces_memory_id_fkey = FOREIGN KEY (memory_id) REFERENCES memories(id)
--                 i.e. **NO ACTION** → physically deleting a memories row would be blocked by
--                 traces, so reclamation could never complete.
-- Fix: change to CASCADE. Safety is guaranteed by the compaction job — it snapshots traces into
--       memories_archive._traces before deletion, so what CASCADE clears is the **already-archived
--       copy**.
DO $$
DECLARE orphans bigint;
BEGIN
    -- Pre-check: if orphans exist, the constraint cannot be added (same lesson as project
    -- governance: check for orphans before adding a constraint)
    SELECT count(*) INTO orphans
    FROM memory_traces t LEFT JOIN memories m ON m.id = t.memory_id
    WHERE m.id IS NULL;
    IF orphans > 0 THEN
        RAISE EXCEPTION 'Found % orphaned memory_traces rows — clean these up before adding the CASCADE constraint', orphans;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conname = 'memory_traces_memory_id_fkey' AND confdeltype <> 'c') THEN
        ALTER TABLE memory_traces DROP CONSTRAINT memory_traces_memory_id_fkey;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'memory_traces_memory_id_fkey') THEN
        ALTER TABLE memory_traces
            ADD CONSTRAINT memory_traces_memory_id_fkey
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE;
    END IF;
END $$;


-- ─────────────────────────────────────────────────────────────────────────────
-- Rollback section (run manually; not executed automatically as part of this migration)
-- ─────────────────────────────────────────────────────────────────────────────
-- DROP INDEX IF EXISTS dedup_fingerprint_key;
-- ALTER TABLE memory_traces DROP CONSTRAINT memory_traces_memory_id_fkey;
-- ALTER TABLE memory_traces ADD CONSTRAINT memory_traces_memory_id_fkey
--     FOREIGN KEY (memory_id) REFERENCES memories(id);          -- back to NO ACTION
-- The cold archive table and ledger are **not recommended to drop** (they carry the deletion evidence):
-- DROP TABLE IF EXISTS gc_log;  DROP TABLE IF EXISTS memories_archive;
