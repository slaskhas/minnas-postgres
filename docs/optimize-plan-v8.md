# Mnemosyne v7.x → v8.0 Optimization Plan (expert-panel review output)

> ⚠️ **Outdated notice (2026-08-18)**: some recommendations in this document have been superseded by what actually shipped in v7.8.0 —
> - **Apache AGE has been removed** (v7.8): graph-related recommendations (edge_fp fingerprint dedup, etc.) are obsolete; entity association now goes through the entities/memory_entities/wiki_entities tables
> - **The Docker plan was rejected** (2026-08-11 user decision): do not bring up Docker again
> - Directions still valid: the P0 memory-layer retrieval eval set (drift prevention), institutionalizing complexity convergence, the LongMemEval initiative (trimmed subset)
> Kept only as a historical review record; for v8.0 execution, AGENTS.md / ROADMAP.md are authoritative.

Date: 2026-08-14
Source: rigorous self-review (8.1/10) → 5 parallel Doubao expert reviews (architect / retrieval-eval / graph-quality / cognitive-science / product-engineering)
User red lines: complexity should be reasonable — neither over-inflated nor dismissive of real possibilities; automation only flags, never auto-executes; key actions require a human decision; no GUI; Hermes takes priority.

---

## I. Summary of Expert Consensus

| Direction | Avg. importance | Consensus points |
|------|-----------|---------|
| P0 memory-layer retrieval eval set | 9.7 | Highest priority; reuse the wiki eval pipeline, just add an `--eval-set` parameter |
| P0 institutionalize complexity convergence | 9.0 | Flag only, never delete; require upfront admission declarations; dormant ≠ unclaimed |
| P1 denoise the graph at the source | 7.5 | edge_fp fingerprint dedup at the source + confidence filtering + lightweight epistemic tagging |
| P1 LongMemEval initiative | 7.3 | Run only a trimmed subset (20-100 items), not the full set; get a bare baseline score |
| P2 deployment and cadence | 5.5 | Single custom image + single main branch + stable tag; lowest priority |

All 5 experts scored the eval set 9+, unanimously judging that "being able to find it" drift-prevention is the single most critical need for a personal-use memory OS.

---

## II. Phase 1 (this week): P0 memory-layer retrieval eval set ← highest priority

**Goal**: give the palace's main retrieval path (summon's three channels / rank) a regular eval at the same spec as the wiki eval, to prevent drift.

### Eval-set construction (25 items, stratified to avoid bias)
- 70% (18 items): real Hermes query logs from the last 3 months (test queries filtered out)
- 20% (5 items): manually constructed ambiguous edge-case queries (e.g. "that AI startup idea I had last year")
- 10% (2 items): cross-chunk relational queries (e.g. "all the prompt records related to Doubao")
- Access-frequency stratification: high-frequency (≥3 hits in the last 30 days) 7 items / mid-frequency (1-2 hits in the last 90 days) 11 items / low-frequency (not accessed in the last 180 days) 7 items — to avoid "teaching to the test" by only optimizing for high-frequency queries
- Expected-hit labeling: a script auto-extracts entity/keyword candidates → 10 minutes of manual review to label them, 3-8 expected hits per query

### Data structures (two lightweight tables)
```sql
CREATE TABLE memory_eval_set (
  eval_id SERIAL PRIMARY KEY,
  query_text TEXT NOT NULL,
  ground_truth_mem_ids INT[] NOT NULL,
  freq_bin INT,            -- 1 high, 2 mid, 3 low
  category VARCHAR(20),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE memory_eval_log (
  log_id SERIAL PRIMARY KEY,
  eval_date DATE NOT NULL,
  precision_at3 FLOAT NOT NULL,
  drift_flag BOOL DEFAULT FALSE,
  commit_hash TEXT
);
```

### Cron and alerting
- Every Sunday 02:00: run the three-channel rank, take the top 3 → precision@3 = hits/3 → store in the log
- Alert threshold: two consecutive weekly drops of ≥10%, or a single week ≥5% below the 4-week average → notify a human via Hermes for investigation
- Quarterly refresh: replace queries that haven't actually been searched in six months (manual review)

### Reuse and cuts
- Reuse the wiki eval pipeline: add an `--eval-set [wiki|main]` parameter, a ~10-line change
- ✅ Cut: online A/B traffic splitting (not needed for personal use), a visualization dashboard (just log + alert)
- Incremental rollout: run 10 items for 2 weeks first to confirm metrics match the actual experience → expand to 25 → then put it on cron

---

## III. Phase 2 (this week–next week): P0 institutionalize complexity convergence

**Goal**: turn "anything with no consumer gets cut" from a habit into a rule, without locking down future evolution.

### Three-layer mechanism
1. **Upfront admission (prevent bloat at the source)**: new modules must declare a header comment `// consumer: [consuming entry point]` or `// plan: [future evolution plan]`; a git hook checks this — commits without a declaration are blocked
2. **Periodic audit (1st of every other month, 02:00)**: a script tallies calls over the last 3 months → generates an audit list (module name / call count / last access)
   - Active (≥1 call) → keep
   - Dormant (0 calls but has a plan) → keep + mark as dormant
   - Unclaimed (0 calls, no declaration) → move to the git archive branch `archive/YYYYMM_unclaimed`, **not physically deleted**
   - Automation only tallies; deletion/archival requires human confirmation (per the user's decision-authority principle)
3. **Evolution safeguard**: a dormant module can return to the main branch simply by adding a declaration — no re-review needed

### Optional refactor (decide after sandbox validation, not part of this round)
- Architect's suggestion: fold the dual-drawer system into Rank (hot/cold = Rank tiers), fold the pointer table into graph edges — an aggressive merge
- ⚠️ Cognitive-science expert: drawer tiering matches how human memory categorization actually works, and was a user decision in the v7.1 design
- Conclusion: listed as an "optional exploration item," to be decided after sandbox-validated benefit, **not executed by default**

---

## IV. Phase 3: P1 denoise the graph at the source

**Goal**: fix the 69% duplicate-edge rate at the root, intercepting at the source rather than deduping after the fact.

### Core: globally unique edge fingerprints (source-side interception)
- Add properties to AGE edges: `edge_fp` (SHA-256 of subject-ID:type:relation:object-ID:type, unique index) + `confidence` + `epistemic_level` + `source_mem_id`
- Write flow: candidate triple → entity alignment → compute edge_fp → if it already exists, skip immediately (never written at the source) → only compute confidence if it doesn't exist
- Expected: duplicate rate 69% → <5%

### Confidence rules (lightweight, no training)
```
confidence = source score (0-0.5) + entity-alignment score (0-0.3) + model-confidence score (0-0.2)
Source score: manual user input = 0.5 / explicit LLM extraction from source text = 0.3 / implicit LLM inference = 0.1
Alignment score: both sides already resolved = 0.3 / one side = 0.1 / neither = 0
Thresholds: <0.3 reject (log only) / 0.3-0.7 pending-review tier / ≥0.7 write to main graph
```

### Lightweight epistemic tiers (product expert recommended dropping a fully automated model)
- L1 explicit user assertion (≥0.9): main graph, highest recall weight
- L2 explicit extraction from source text (0.7-0.9): main graph, second-highest weight
- L3 reasonable LLM inference (0.3-0.7): stored with an `is_candidate` flag, not recalled by default, reviewed weekly
- L4 low confidence (<0.3): log only, not stored
- Review cron: runs `review_l3_edges.py` together with the weekly eval on Sundays → markdown report → Hermes notification

### Incremental rollout
1. Ship edge_fp dedup only first, run for 2 weeks to observe the new duplicate rate
2. Add confidence scoring (without enabling filtering), observe the distribution and tune thresholds
3. Finally enable filtering + epistemic tiers

---

## V. Phase 4: P1 LongMemEval initiative

- Use only a trimmed subset of the public benchmark (20-100 items), not the full set (wasteful for personal use)
- Reuse the Phase 1 eval script for a bare run, report a baseline score (even 35% is better than "never measured")
- Store in eval_log; run alongside the weekly eval every time long-memory optimization work happens
- ✅ Cut: matching the public benchmark's output format (only record the personal score)

---

## VI. Phase 5 (can wait): P2 deployment and cadence

- Docker: official `postgres:15-alpine` with pgvector+AGE prebuilt → a single custom image `mnemosyne-pg:latest`; Compose with 2 services (custom PG + Hermes backend); mounted data volume; one-command up
- Cadence: develop on a single main branch (keep 6-day mini-iterations), tag `stable` (`v{major}.{minor}-stable`) every 2 features once tested, tag `dev` on the dev branch; don't maintain two long-lived branches
- ✅ Cut: enterprise-grade CI/CD auto-build (local testing + push is enough)

---

## VII. Priority and Effort Summary

| Order | Direction | Effort | Dependency |
|------|------|------|------|
| 1 | P0 memory-layer eval set | 2 person-days | none (do first) |
| 2 | P0 institutionalize complexity convergence | 1 person-day | none (all subsequent modules follow the admission rule) |
| 3 | P1 LongMemEval | 2 person-days | depends on #1's script |
| 4 | P1 denoise the graph at the source | 1.5 person-days | depends on #2's admission rule |
| 5 | P2 deployment and cadence | 1 person-day | last |

Execution principle: validate each item before moving to the next, no batch parallelism; every change is git + PG backed up, revert if something breaks; automation only flags, never auto-executes.

## VIII. One-Line Summary

The eval set is the dashboard for "being able to find it," the convergence rule is the guardrail against "rot," and graph denoising is the root fix for "less cleanup later" — all three are subtraction or instrumentation, not added weight; LongMemEval is an honest baseline, and Docker was just a convenient shortcut.
