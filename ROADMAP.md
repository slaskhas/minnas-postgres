# Mnemosyne OS · Roadmap

> v7.8.3 | 2026-09-12 🔧 Service port / host env vars take effect (MNEMOSYNE_PORT / MNEMOSYNE_HOST) + doc fixes
> v7.8.0 | 2026-08-18 🧹 Precise defusing + architecture slimming
> v7.7.0 | 2026-08-09 🧠 Combined algorithm + efficient retrieval
> v7.2.0 | 2026-08-09 🧠 Bjork S/R separation + GZ tuning
> v7.1.0 | 2026-08-09 🗄️ Drawer-based memory (dual-track + spatial-aware forgetting)
> v7.0.0 | 2026-08-06 🏰 Magic Memory Palace
> v6.4.0 | 2026-08-05
> v6.3.0 | 2026-08-05
> v6.2.0 | 2026-08-05
> v6.0.1 | 2026-08-02
> v6.0.0 | 2026-08-02
> v5.5.1 | 2026-07-23

## Version Line

v5.4 (three halls activated) ✅ → v5.5 (temporal validity) ✅ → v6.0 (cognitive architecture) ✅ → v7.0 (Magic Memory Palace) 🚀 → v7.6 (combined retrieval) ✅

## v7.x Released (2026-08-09/10) ✅

| Version | Content |
|------|------|
| v7.1 | 🗄️ Drawer-based memory (temp × time dual-track + forget candidates) |
| v7.2 | 🧠 Bjork S/R separation (storage strength never decays, retrieval strength recoverable) + production tuning |
| v7.3 | 📊 Rank combined score + drawer tiering |
| v7.4 | 🧩 WIKI knowledge base + knowledge graph (Apache AGE) |
| v7.5 | 🔍 WIKI retrieval optimization (BM25+vector RRF, precision@3 100%) |
| v7.6 | 🔬 Memory isolation & source tracking + episodic/semantic/procedural three-way classification |

## v8.0 (Planned) — External-Sharing Milestone

> Positioning: the first step for the product to go to the open-source community. Features are mature; the focus shifts to "getting friends to use it".

- [x] INSTALL.md per-environment install guide (Linux/macOS/WSL)
- [x] AGENTS.md rewrite (AI integration manual: API quick-reference / env vars / Hermes onboarding)
- [x] hermes-provider synced running version (11 tools including palace_summon)
- [x] README restructuring + repo refresh (desensitization / templates / schema fixes)
- [ ] setup.sh bare-metal one-click install script
- [ ] Real-user grayed install verification (find 1-2 friends to test in the field)
- [ ] LongMemEval retest (target ≥50%, baseline 16.7%)

## v7.0.0 — Magic Memory Palace ✅

- Palace architecture: classification tree (7 wings × 20 rooms) + accession numbers + catalog cards + three-channel summoning
- Reading Room: full fact extraction (2319 conversations → 6231 facts)
- Permanence tiers: permanent / long-term / short-term lifecycles
- Hermes adaptation: palace_summon tool + auto-extraction + palace injection
- Data: 8647 memories / 100% archived / 8614 cards

### Follow-up Planning (feature deepening, non-priority)
- [ ] Full card refinement (backfill 2346 cards awaiting refinement)
- [ ] Classification-tree visualization (palace tour API)


## v5.4.0 — Three Halls Activated ✅

- Gate integrated with heterogeneous audit (dual-model cross-verification)
- Suggestion list endpoint (GET /halls/suggestions)
- Tool archive path fix
- 18 pytest cases

## v5.5.0 — Temporal Validity ✅

- Search / list / stats SQL filters out expired memories
- 39 benchmark cases (temporal_validity + recall + conflict + weighting)

## v6.0 — Cognitive Architecture

- Metacognitive layer
- Belief system rework
- Three-stage intelligent recall
- Desktop app (end-to-end encrypted memory access)

## v6.4.0 — Fact Extraction Pipeline ✅ (2026-08-05)

- [x] tmt/factextract.py: conversation → user facts (personal info / preferences / events / schedules / capabilities) → preference/knowledge
- [x] Per-line short-text extraction + non-JSON mode (evaluation lessons)
- [x] ANN dedup gate + heat 0.65 + provenance metadata
- [x] cron daily 02:00

## v6.3.0 — Cognitive Write Signals ✅ (2026-08-05)

- [x] compute_write_heat write signals (todo +0.15 / correction +0.10 / pitfall +0.10 / decision +0.08 / path +0.05 / important +0.05, category bonus)
- [x] Protected decay: pinned/preference only -0.005 (reflect heat v2)
- [x] Distilled pitfall stored at 0.70

## v6.2.0 — Cognitive Heat Engine ✅ (2026-08-05)

- [x] Search / recall hits are heated (access+1, heat+0.05)
- [x] Differential decay (recently active memories within 48h decay slowly)
- [x] Distillation enhancement: stored heat signal 0.65 + intra-batch dedup
- [x] Health-monitoring backup-freshness check (prevents silent memory loss)

## v6.1 — Knowledge Distillation (2026-08-05)

- [x] tmt/distill.py knowledge distillation pipeline v0.1 — NCP-008 seven steps + TEL/MAIL concept mapping
  (session/worklog → knowledge/pitfall, keyword screening + LLM distillation + ANN dedup gate + provenance)
- [x] GZ cron batch-distills 60 items daily 1:10 (first run 30 items: +22 knowledge, +1 pitfall)
- [ ] Intra-batch dedup (merge similar items in the same batch)
- [ ] Distillation-quality feedback loop (high/medium/low confidence affects heat)

## Iteration Discipline

1. PLAN→code→test→privacy scan→docs→tag
2. Local development→GZ verification→repo release
3. Safety first: do not expose sensitive data on the public network
