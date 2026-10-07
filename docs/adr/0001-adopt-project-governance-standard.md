# 1. Adopt the G-CAT Project Governance Standard

- **Status**: Accepted
- **Date**: 2026-09-24
- **Decision-makers**: G-CAT (user) + Noah (AI steward)

## Background (Context)

The repo has gone through multiple major v6/v7 iterations, exposing systemic problems:

1. **High onboarding cost**: a new session (human or AI) doesn't know what the project is, how far it's gotten, or where it's headed next; every time, you have to dig through code and chat history archaeology.
2. **No single source of truth for capabilities**: what the system actually does, what's changed, what's deprecated — can only be reverse-engineered by reading code; what the README claims doesn't match reality (e.g. RRF is only implemented in wiki, the main search is linear weighting).
3. **Only additive, never subtractive**: features pile up with no "deprecated" vocabulary; dead code and historical leftovers accumulate.
4. **Missing standards**: `AGENTS.md` reached 8,466 characters (loaded on every request, and past the reasonable range under Hermes's 20,000-character ceiling); no CHANGELOG governance, no decision records, no spec layer.
5. **Inconsistent practice across repos**: the same classes of problems get hit repeatedly (version-number drift, missing tags, `.gitignore` missing sensitive files).

## Decision

Adopt **G-CAT Project Governance Standard v1.0** (`~/gcat-std/STANDARD.md`), executed at **Tier L** for this repo:

- **Charter layer**: `PROJECT.md` (what this is / why / where it's headed) + `AGENTS.md` (**slimmed to ≤30–60 lines**) + `CHANGELOG.md` + `README.md`
- **Spec layer**: `openspec/specs/` (ground truth for capabilities) + `openspec/changes/` (change proposals, including ADDED/MODIFIED/**REMOVED**) + `docs/adr/` (decision records)
- **Process layer**: Spec → Plan → Tasks → Implement → **Validate**
- **Gate layer**: the CI trio (test / version-scan / privacy-audit) + `repo-doctor` inspection + a four-stage release gate

Benchmarked against: the Linux Foundation **AGENTS.md** standard (folded into the Agentic AI Foundation, 2025-12), and 2026's mainstream **Spec-Driven Development** (OpenSpec / GitHub Spec Kit).

## Consequences

- ✅ **3-minute handoff**: read `PROJECT.md` → `AGENTS.md` → `openspec/changes/` to get the full picture
- ✅ **Capabilities have a source of truth**: `specs/` is the sole authority, no more reverse-engineering from code
- ✅ **Prevents a tangled mess**: `REMOVED Requirements` forces deprecated capabilities to be recorded by name, so no one re-adds them three months later
- ✅ **Decisions are traceable**: every architectural trade-off lands in an ADR, instead of living only in chat history
- ✅ **Lower token cost**: the per-request injection size of AGENTS.md drops by about 74%
- ⚠️ **Cost**: every behavior change now requires writing an extra `proposal.md` (the lightweight path can be three lines); requires building the habit of closing the loop
- 🔄 **Given up**: the practice of "write all docs in one place and call it done"; also gave up backfilling full spec baselines for existing code (per the brownfield principle, specs grow around real changes)

## Alternatives

| Option | Why not chosen |
|---|---|
| Keep the status quo (no governance layer) | Already proven to produce repeated pitfalls and archaeology costs |
| Adopt an external tool (OpenSpec CLI / Spec Kit) | Conflicts with the self-built-ecosystem-first principle; also depends on an external npm/pip toolchain, unusable offline. **Copy the structure, build the tooling ourselves** |
| Backfill full specs and docs in one pass | A big, all-at-once baseline that no one validates starts rotting from day one (this is also OpenSpec's own official position) |
| Only slim down AGENTS.md | Treats the symptom, not the cause: doesn't solve the three root problems of "capability truth / decision records / change articulation" |
