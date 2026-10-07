---
Proposal ID: P-20260926-02
Proposer: HERMES-Noah(EN)
Date: 2026-09-26T16:10+08:00
Target: jobs/compaction
Action: MODIFIED
Basis: [production measurements (real run, batches GC-20260926 / GC-DRILL3), tests/test_v8_compaction.py C10-C12, CHANGELOG unreleased section]
Status: pending
Conflicts: []
Previous version: -
---

# Change proposal: hardening the memory reclamation job's (GC) receipt path

- **Date**: 2026-09-26
- **Tier**: S (single file + tests, narrows behavior surface, doesn't touch the contract)
- **Related**: `openspec/specs/` (reclamation/archival capability) · production incident (batch `GC-20260926`)

## Why (Why)

Both fixes came from **production measurement**, not desk analysis:

1. **Receipt row-count tally hits the csv field-size cap → the entire reclamation batch crashes.**
   The `csv` module's default `field_size_limit` is 131072 bytes, while production's longest memory
   `content` reaches **270448 characters**; when the receipt is **written and then read back to count
   rows**, it throws `Error: field larger than field limit (131072)`, and `--apply` fails the whole
   batch with `exit=3` (all 267 local test cases pass regardless — the fixtures are all short text).
2. **Lying receipts**: the receipt is written to disk under its final filename **inside the transaction**,
   so after a transaction rollback the disk is left with a file claiming "deleted, when actually nothing
   was deleted" (this actually happened, leaving an 8.4MB orphan file that had to be manually renamed
   and quarantined).

Point 2 is more dangerous than "no receipt at all": during an incident response it tells a human
"549 records have already been deleted" when they haven't been.

## What changes (What)

- `jobs/compaction.py`
  - Raise `csv.field_size_limit` at module level (→ 2^31-1), removing the oversized-field ceiling.
  - Tally the receipt row count **while writing**, instead of reading the whole CSV back afterward
    (which is what re-triggers the cap).
  - Change the receipt flow to "**write to `*.part` first → `os.replace` to its final name after the
    transaction commits**," deleting `.part` on the failure path; i.e. **side effects are moved outside
    the transaction**: the final receipt only appears on disk if the deletion actually committed.
- `tests/test_v8_compaction.py`
  - `test_c10`: an oversized-field (270KB) batch must be fully deleted and the receipt must parse.
  - `test_c11` (falsification): inject "throw after the receipt is written" ⇒ no receipt file may exist,
    and not a single row of data may be missing.
  - `test_c12` (positive): on the success path ⇒ the receipt is finalized and no `.part` remains.

## Out of scope

- ❌ Not touching the reclamation pipeline's **five gates** (window / protection flag / reference
  integrity / archival / receipt), and not touching `--restore` semantics.
- ❌ Not introducing a third-party dependency (red line: must run with bare `python3 xxx.py`).
- ❌ **Not doing `VACUUM FULL`**: `VACUUM (ANALYZE)` already achieves "the table no longer only grows +
  space is reusable"; actually returning space to the OS needs a brief exclusive lock on a production
  table, and the `memories` table at 308MB isn't a bottleneck ⇒ cost exceeds benefit (revisit if disk
  pressure becomes real in the future).

## Acceptance criteria

- [x] Full test suite green: **270 passed / 6 skipped** (267 before this change)
- [x] Falsification holds: `test_c11` was **red** before the fix (11 passed / 1 failed), green after
- [x] Production run verified: batch `GC-DRILL3-20260926` real single-row delete → final receipt present,
      no `.part`, `--restore` reports `integrity: OK`
- [x] Production batch `GC-20260926-2` really reclaimed 549 rows (tombstones 816→267), independently
      re-read and checked
- [ ] CI (test.yml + privacy.yml) fully green after push

## Risk and rollback

- Risk surface: only the timing of "when the receipt filename becomes final relative to the commit";
  the deletion and archival logic itself is untouched.
- Rollback: `git revert` this proposal's commit; production keeps two backups,
  `jobs/compaction.py.bak-20260926-*`.
