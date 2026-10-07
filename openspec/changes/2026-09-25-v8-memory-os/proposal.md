---
Proposal ID: P-20260925-01
Proposer: EN-Noah
Date: 2026-09-25T06:30+08:00
Target: mnemosyne/v8.0.0
Action: ADDED
Basis: [Original "Memory Filesystem (Memory FS) — Unified Architecture Research Report", Proposal P-20260924-04 agenda pool A1~A10, box docs "Memory Layering Model v1" / "Memory System 8.0 Expert Panel Assessment", 2026-09-25 three-way status measurement, gcat-std docs/adr/0004]
Status: in-progress
Conflicts: []
Previous version: v7.8.4
---

# Memory Palace OS v8.0 · Change Proposal (PRD-level)

> **One line**: upgrade the memory substrate from "**can be stored**" to "**written correctly · retrievable · findable accurately · fully traceable**."
> **This is not** a wholesale port of filesystem engineering principles — it's **only the handful of things PostgreSQL doesn't already give us, that we genuinely lack**.

## 0. Why now

1. On 2026-09-25 the user explicitly authorized the project and asked for "local, production, and the repo all pushed through today."
2. The previous round (09-23/24) already completed **fact-finding**: the FS report's 10/10 code-anchor spot checks hit, three-way version reconciliation done, red-team's 6 objections addressed — the research phase has converged, moving into implementation.
3. Measurement found **exactly one real drift**: production is running v7.8.3 while the repo is already at v7.8.4 → this gets reconciled as part of this round.

## 1. Basis for judgment (three design principles, non-negotiable)

| # | Principle | Meaning |
|---|---|---|
| R1 | **Effectiveness over scale** | Anything PostgreSQL already provides (WAL/MVCC/shared buffer/btree) is **never rebuilt**. Of the filesystem's 20 dimensions, only the ones PG doesn't cover are in scope |
| R2 | **No new mechanism without a trigger** | The report's self-imposed thresholds (e.g. only split tables past 5M rows) are elevated to **ADR triggers**; nothing moves until triggered |
| R3 | **Every change must have a falsifiable criterion** | It either fixes a real defect ("data would be wrong"), or brings a **measurable** quality improvement (A/B numbers) — otherwise it doesn't happen |

> R2/R3 institutionalize the previous round's red-team prescription. **A "deferred" that isn't written into an ADR isn't actually deferred.**

## 2. What changes (What)

### S1 · Reliability layer — fixing real defects (defects with "data could be wrong" consequences)

| # | Change | Current-state evidence [verified] | Acceptance criterion (how we know it's done) |
|---|---|---|---|
| **S1-1** | Wrap `create_memory` writes in a **single PG transaction** (including entity sync + tokenization + cataloging) | `main.py:778-836` runs sequential `execute` calls under `pool.acquire()`, with no `conn.transaction()` anywhere → asyncpg auto-commits, **non-atomic**; a crash can leave a half-finished state with "memory but no entity / no tokens" | After an injected exception interrupts the flow, **zero leftover rows** across the `memories`/`entities`/`memory_keywords` tables; 183+ test cases all green |
| **S1-2** | Activate the `dedup_fingerprint` **idempotency key** = `sha256(content + category + user_id)`, add a unique index + `ON CONFLICT DO NOTHING` | This column was added back in v7.1 (`schema.sql:333`), **but the code has never written to it** → no idempotency, retries/reconnects duplicate rows | Reposting identical content **returns the same id**; only one row exists in the DB; existing duplicates are pre-deduplicated |
| **S1-3** | **Memory reclamation (GC / compaction)**: soft-deleted rows past the retention window with zero references → cold archive table → physical delete → `VACUUM (ANALYZE)`; add `memory_traces` FK CASCADE | The whole repo has **no `DELETE FROM memories`, no `VACUUM`** anywhere → soft delete is the end of the line, tombstones accumulate forever, tables only ever grow | Tombstones have an expiry exit; **a rollback receipt (CSV) exists before deletion**; orphan audit reports 0; mistaken deletes can be restored within the window |
| **S1-4** | **Observability**: write/recall latency instrumentation (p50/p95) exposed on the health endpoint; backups upgraded from "did it run" to "**ran AND was verified, with failure alerts**" | Past incident: backups **silently broke for 36 days**; `perf_alert.py` has a 2000ms threshold but no real instrumentation | A single request can read real measured p95; backups get a **restorability spot-check** (actually decompress, actually read the table), not just a file-exists check |

### S2 · Retrieval quality layer — measurable improvements

| # | Change | Current-state evidence [verified] | Criterion |
|---|---|---|---|
| **S2-0** | **Build the evaluation baseline first** (≤100 gold-label queries + precision@k / MRR); **must exist before touching recall** | Red team's own words: "'RRF is better' has no evaluation backing it" | Baseline numbers committed; any future ranking change gets an A/B comparison against it |
| **S2-1** | Four-channel **RRF fusion**: `summon/guide/resonate/wiki` merged into one RRF pool (reusing the already-verified `wiki/wiki_bm25.py: rrf_fuse`, k=60), unified top_k | `palace.py:180-250` currently returns the four channels **independently, unfused, with no unified cutoff** | The same query returns a **single fused ranking**; gold-label precision@3 is **no worse than** baseline (with numbers) |
| **S2-2** | Two-stage candidate-then-rerank: each channel takes top-50 candidates → RRF fusion → top-20; rerank **off by default** (toggleable) | Currently there's no candidate-pool concept at all | Candidate pool is configurable; rerank toggle defaults to off, no unverified behavior introduced |

### S3 · Governance layer — the "layering + process" the user mandated

| # | Change | Criterion |
|---|---|---|
| **S3-1** | **Memory layering model L0-L4 + cross-cutting artifact index** written into `openspec/specs/` (living spec); write path gets a **layering decision** (executable, not just documentation) | Every layer has a carrier + write rule + conflict policy; **a new memory can be assigned a layer** (there's a runnable decision function) |
| **S3-2** | **Release process state machine**: A local → B test + falsification → C real-world verification → D deploy → E public → F retrospective; script + state file, **can't advance past a stage until the previous one is cleared** | One command answers "what stage are we at now"; every stage leaves auditable evidence; this release becomes the first sample |
| **S3-3** | **Artifact pointer policy**: deliverables = box / retrieval = Wiki / versioning = repo; memory only holds **pointer + fingerprint** | One hop from a memory entry to the entity (path / URL / sha256) |

## 3. Out of scope — written into ADR-0002 with triggers

`memory_inode` metadata table split · `memory_wal` application-layer journal · `archive_directory`
accession-number directory tree · block-level snapshot rollback · quota system · multi-tenancy isolation
refactor · content/embedding physical separation · page cache tuning · defrag · vector clocks

**Triggers (none of the above may be added until triggered)**:
1. The `memories` table exceeds **5 million rows**; or
2. Average body size exceeds **4KB**; or
3. A real second tenant appears (not just name convergence).

> Basis: the previous round's 6 red-team objections + the report's own self-imposed boundaries.
> **"Deferred" must be judgeable, otherwise it isn't actually deferred.**

Also explicitly not doing (historical decisions): GUI client · Docker approach (rejected 2026-08-11) ·
in-house vector index.

## 4. Acceptance criteria (three stages, all must pass)

| Stage | Criteria |
|---|---|
| **A Local** | ① Full test suite green (183 → ≥190 after additions) ② every new mechanism has a **falsification test** (construct a violating sample and prove it's actually blocked) ③ contract tests lock the MCP bridge / Provider's 14-tool shape ④ red-team review passed |
| **B Real environment** | ① backup runs first and restorability is verified ② minimal-footprint deploy ③ the service **self-reports its version** consistent with the repo ④ endpoints measured live (write/recall/GC dry-run) |
| **C Public** | ① version consistent in three places (VERSION / README badge / CHANGELOG) ② both privacy scan layers produce **zero output** ③ re-scanned on an independent clone ④ tag + non-draft Release |

**The release order cannot be reversed**: **deploy to GZ and verify stability first → only then tag/push/Release**.
(Reversing this is the root cause of "documentation says it but it never actually ran" 404 dead links — this has bitten us for real.)

## 5. Execution order (today)

```
① Kickoff (this proposal + ADR-0002)
② Expert panel parallel research (benchmarking / red-team / eval design / architecture review) → feed back into the plan
③ S1-1+S1-2 implementation (atomicity + idempotency) → falsification tests
④ S2-0 build the eval baseline (measure before changing) → ⑤ S2-1/S2-2 fusion (A/B comparison)
⑥ S1-3 GC + S1-4 observability
⑦ S3 governance trio (layering spec / release state machine / artifact pointers)
⑧ Stage B: deploy to GZ + re-verify → ⑨ Stage C: tag/Release v8.0
⑩ Stage F: retrospective (CHANGELOG / PROJECT / delivery trio / memory archival)
```

## 6. Basis (real pointers, can be re-checked)

- Original report: "Memory Filesystem · Unified Architecture Research Report" (193 KB, 20-dimension mapping + Appendix A code audit + Appendix B principles) — internal storage location is in the Mnemosyne accession #20077 session record (the public repo does not record local machine paths)
- Agenda pool: `~/gcat-std/openspec/changes/2026-09-24-memory-os-v8-agenda/proposal.md` (P-20260924-04)
- Box docs: `Memory-Layering-Model_v1_from-dictation_2026-09-24.html` · `Memory-System-8.0_Expert-Panel-Assessment_double-click-to-view_2026-09-24.html`
- Previous round's workspace: `~/memfs-8.0-team/` (charter + 12 deliverables)
- Code anchors: `main.py:778-836` · `palace.py:180-250` · `wiki/wiki_bm25.py` · `docs/schema.sql:301-344`
- Three-way measurement: 2026-09-25 06:14 (local `bf9ebb2`/7.8.4 · GitHub Release v7.8.4 · GZ running 7.8.3)

## 7. Pending user decisions (not blocking this implementation, but need to be known)

1. ~~Clean up lme_eval junk / freeze production .git / backfill v7.8.3 tag~~ — **already done on 09-23**
2. **S1-3 GC physically deletes data** — retention window set to **30 days** (adjustable), a CSV rollback receipt is exported before deletion, dry-run is on by default
3. **A8 bulk historical de-identification / history rewrite** (force push / rebuild tags) — **a destructive action, not done in this round**, still on the backlog
4. **A7 Hermes core compression write-back defect** — this round only does "fact-finding closure" (zero risk), the core itself is not modified
