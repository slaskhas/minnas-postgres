# Memory Palace OS 8.0.1 · Task Breakdown

> Basis: G-CAT delivery process standard (`~/gcat-std/RELEASE-FLOW.md`) §4 "Task breakdown rules."
> Source: broken down line-by-line from the user's 2026-09-25 session (three rounds of criticism + process restatement).
> Rule: one task = one independently acceptable deliverable; the criterion must be **falsifiable**; blocking-type changes **must have a falsification test**.

## A. User requests → task mapping (the source of the breakdown)

| # | User's original words (paraphrased) | Task derived | Owner stage |
|---|---|---|---|
| 1 | "Once local dev is done, it needs testing" | T-1 local test suite + falsification | P2 |
| 2 | "Only release to production after testing is complete" | T-2 pre-deploy acceptance gate | P2→P3 |
| 3 | "Test it again once it's in production" | T-3 real end-to-end live test | P4 |
| 4 | "Confirm again once that's also fine" | T-4 pre-public confirmation gate | P4→P5 |
| 5 | "Only then release to the GitHub repo" | T-5 go public (incl. open-source readiness) | P5 |
| 6 | "The repo version also needs special optimization, since it's shared with others" | T-6 open-source readiness items (one-click install/placeholders/outsider-perspective docs) | P5 |
| 7 | "Every stage has its own standard" | T-7 per-environment standards (workspace/test DB/production/public) | P0 standard |
| 8 | "GitHub privacy needs a separate review" | T-8 two-layer privacy scan + independent clone re-scan | P5 |
| 9 | "Needs to be broken into several pieces, work breakdown and task orchestration" | T-9 task breakdown table + gate executor | P0 |
| 10 | "Accept it layer by layer" | T-10 seven stages + three gates (no self-approval) | P0 tooling |
| 11 | "The process needs to be made permanent" | T-11 standard document + executor + regression tests | P0 tooling |
| 12 | "There's an issue communicating with DSH, find the root cause" | T-12 DSH orchestration-interface fix (task spec written to a file) | P1 (not started) |
| 13 | "Found another lurking bug, which means testing wasn't thorough" | T-13 GC restore integrity (fixed + tests) | P2 (done) |
| 14 | "Call this fix release 8.0.1" | T-14 version number 8.0.1 + CHANGELOG | P1 (done) |

## B. Per-task acceptance criteria (falsifiable)

| # | Task | Criterion (how we know it's done) | Evidence (what proves it) | Status |
|---|---|---|---|---|
| T-13 | GC restore integrity | Per-subtable counts before deletion == counts after restore; any mismatch is red | `test_c9`; falsification: revert the fix → C9 goes red | ✅ Done (P2 pending sign-off) |
| T-10 | Seven stages, three gates | Every gate's `pass` must be rejectable (not just tested for one gate) | `test_g3` (iterates all gates); forged credentials rejected by `test_g4` | ✅ Done |
| T-11 | Process made permanent | Standard document and executor exist as a pair; 41-case suite green | `RELEASE-FLOW.md` existence assertion `test_g11`; unittest 41 OK | ✅ Done |
| T-5/T-8 | Public release standard | Privacy scan **zero output**; independent clone re-scan zero hits | `release_checks.py privacy`; independent clone re-scan | ⏸ Pending P5 |
| T-6 | Open-source readiness | A stranger can install and use it: one-click install script exists and runs; docs contain no real values | TBD (`setup.sh` + INSTALL update) | ⏸ Not started |
| T-12 | DSH orchestration interface | Task specs **bypass the shell** (written to a file, pass the path); failed dispatch can auto-retry | TBD (5 consecutive classes of escaping failures reduced to zero) | ⏸ Not started |
| T-1 | Local testing | Full suite green + falsification passed | mnemosyne-dev 259 passed / gcat-std 41 OK | ✅ Done |
| T-3 | Real end-to-end test | Production end-to-end (not mocked) + test artifacts cleaned up | TBD | ⏸ P4 |
| T-4 | Pre-public confirmation | Documented confirmation from the user on production behavior | TBD | ⏸ P4→P5 |

## C. Stage orchestration (what comes first, where it's blocked)

```
P0 Plan           ✅ passed (this file + proposal + ADR + standard)
 └─🔒 Plan confirmation ── waiting on your go-ahead
P1 Workspace dev   ⏸ ← currently blocked here
P2 Local testing   (T-1/T-13 done, pending formal sign-off)
 └─🔒 Pre-deploy acceptance
P3 Deploy
P4 Production testing
 └─🔒 Pre-public confirmation
P5 Release         (includes T-6 open-source readiness)
P6 Retrospective
```

## D. Open decisions (need the user's call, not to be decided unilaterally)

1. **Idempotency key vs. L0 contract conflict** — does it belong in 8.0.1 or 8.1?
2. **RRF tie-break** — document that "memory wins" ties, or switch to a neutral rule?
3. **Evaluation baseline** — run it now (E3 already has 150 cases designed) or keep it on the backlog?
4. **DSH orchestrator** — build it (root fix = task spec written to a file; fallback = intake validation + auto-retry)?
