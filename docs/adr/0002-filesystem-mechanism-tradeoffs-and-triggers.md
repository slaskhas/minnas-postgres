# ADR-0002 · Filesystem Mechanism Trade-offs and Triggers

- **Status**: Accepted
- **Date**: 2026-09-25
- **Decision-makers**: G-CAT (authority) + EN-Noah (proposal)
- **Related**: Proposal [P-20260925-01](../../openspec/changes/2026-09-25-v8-memory-os/proposal.md) · prior research round `~/memfs-8.0-team/` · original report "Memory Filesystem (Memory FS) — Unified Architecture Research Report"

## Background

On 2026-09-24, a 193KB "Memory Filesystem Research Report" proposed migrating 40 years of filesystem engineering principles (20 dimensions: inode / extent / dentry / B+tree / journal / COW / checksum / snapshot / hard links / page cache / delalloc / defrag / GC / quota / ACL / LFS / RAID / mount / barrier / back-references) into Mnemosyne-OS.

A prior specialist group ran three cross-validation passes, and the conclusion **sharply narrowed the scope**:

1. **Code-anchor spot check**: 10/10 sampled claims in the report checked out against the code → the report's **diagnosis of the current state is accurate** and worth trusting.
2. **Red-team review, 6 objections**: most of the mechanisms are a **scale mismatch or duplicated effort** at the current size (15,000 records); and PostgreSQL **already provides** most of them (WAL / MVCC / shared buffer / btree).
3. **FS feasibility ruling, dimension by dimension**: of the 20 dimensions, **8 are portable, 11 need reshaping, and 1 doesn't apply (RAID)**; what's genuinely missing is only three things — **lifecycle reclamation, referential integrity, row-level permissions**.

Notably, the report **itself** marked boundary conditions and counterexamples for every dimension, and set its own thresholds (e.g. "only split the table once rows exceed 5 million or body text exceeds 4KB").

## Decision

### 1. Adopted (goes into v8.0 implementation)

| Mechanism | Why adopted |
|---|---|
| **Write atomicity** (journal's semantics, not a journal table) | The only real defect with a "data can be wrong" consequence: non-atomic writes + no idempotency → a crash leaves half-finished records, retries double-insert |
| **Lifecycle reclamation: GC + compaction** | The whole repo has no DELETE / no VACUUM; a soft delete is a dead end. This is the one FS mechanism that is **genuinely missing** and **already hurts at the current scale** |
| **Referential integrity** (reclaim only once references hit zero) | A prerequisite safety condition for reclamation; without it, GC would delete memories that are still referenced |
| **Recall fusion via RRF** (the **beneficial part** of directory lookup + layered content indexing) | Not fusing the four-channel results is the real ceiling on recall quality, and there's **already a validated implementation** (the WIKI path) |
| **End-to-end verification** (content hash / embedding version stamp) | Read-only scrub reporting only, no auto-repair — low risk, high payoff |

### 2. Explicitly rejected (the core value of this ADR)

| Mechanism | Why rejected |
|---|---|
| Splitting `memory_inode` metadata from content into separate tables | Not needed at the current 15,000 rows; and a table split is an **irreversible** structural change — the payoff only arrives at scale |
| An application-layer `memory_wal` write log | **Reinvents PostgreSQL transactions.** The report itself acknowledges PG already has WAL, yet still wants an application-layer copy = write amplification with no payoff |
| An `archive_directory` accession-number directory tree / B+tree | Nine wings × ~20 rooms ≈ 180 leaves; a btree has no discriminating power here — the real problem is just a single modulo-collision case, fixable on its own |
| Block-level snapshot rollback / point-in-time recovery | Single-user, no strong isolation requirement; COW's space-accounting complexity far exceeds the payoff |
| Quota system / multi-tenant isolation refactor | A concept substitution: there is currently only one real tenant, no strong isolation requirement |
| Physical separation of content/embedding (extent) | Over-engineering before 5 million rows |
| Page-cache tuning / defrag / vector clocks | The DB already has its own shared buffer — don't double-cache; traditional defrag is moot on SSD; single-user LWW is sufficient |
| RAID / erasure coding | A block-layer mechanism; cross-machine consistency relies on LWW + idempotency keys, not directly transferable |

### 3. Triggers (the **enforcement clause** of this ADR)

> **None of the rejected mechanisms above may be added until triggered.** When triggered, return to this ADR first, re-evaluate, and record a new ADR — "just adding it in passing" is not allowed.

| Trigger | Threshold | Unlocks |
|---|---|---|
| T-1 Table scale | the `memories` table **exceeds 5 million rows** | splitting out `memory_inode` · physically separating `memory_extent` |
| T-2 Body size | average body text **exceeds 4KB** | physical separation of content/vector + compression |
| T-3 Real multi-tenancy | a **second genuine tenant** appears (not just a same-name convergence) | quota system · tenant-isolation refactor |
| T-4 Accession-number collision | an actual `archive_no` collision occurs ≥ 1 time (not just a theoretical risk) | accession-number directory tree |
| T-5 Deletion incident | 1 occurrence of an **unrecoverable accidental deletion** | block-level snapshot / point-in-time rollback |

**Current measurement (2026-09-25)**: table ≈ 16,000 rows · average body <1KB · single tenant · no recorded collisions · no deletion incidents → **none of the five triggers have fired**.

## Consequences

**Positive**:
- Converges "research → one giant report" down to "**4 genuine defects + 1 measurable optimization**," avoiding v8.0 turning into a quagmire of 20 parallel dimension-by-dimension rewrites.
- The original report's value is **institutionally preserved**: its boundary conditions became our gates — neither wasting that research nor being dragged along by it.
- When scale changes in the future, there's a clear **re-evaluation entry point** (the triggers) instead of relying on someone remembering.

**Negative / risks**:
- If scale grows far faster than expected, performance pressure might be felt before a trigger fires (mitigation: the `S1-4` instrumentation provides measured p95s, so pressure is visible).
- Trigger thresholds are empirical and may need adjusting based on measurement (mitigation: the thresholds themselves are an amendable clause of this ADR).

**Rejected alternative**: wholesale adoption of all 20 dimensions (judged by the red team to be a scale mismatch plus duplicated effort); also rejected "do nothing" (S1-1/S1-2/S1-3 are genuine defects).
