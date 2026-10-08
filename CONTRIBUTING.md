# Contributing Guide

## Commit Conventions
Conventional Commits: `feat:` `fix:` `docs:` `style:` `refactor:` `perf:` `test:` `chore:` `release:`
Titles are imperative, short English sentences; descriptions explain **why**, not "what".

## Branching & Merging
1. `git fetch origin`, confirm local `main` matches `origin/main`
2. Create a feature branch: `git worktree add ../wt/minnas-<topic> -b <type>/<topic> origin/main`
3. Change → `pytest tests/` → commit → push → open PR → **watch CI until all green**

## Before Making Changes
- **Behavior** change → first write `proposal.md` in `openspec/changes/<name>/`; after completion, fold into `openspec/specs/` and archive
- **Outward-facing interface / field name / parameter position** change → must add contract tests + counter-evidence (must go red after `git stash`ing the fix)
- **Architecture trade-off** → add `docs/adr/NNNN-*.md`

## Releasing
Four-stage gate: A develop → B tests all green → C real-path acceptance → D tag/push/Release.
Version numbers must be **consistent in three places**: `VERSION` / bilingual README badge / `CHANGELOG.md`. Run `scripts/version-scan.sh` before release.

## Red Lines
- ❌ Never hardcode API Keys / real IPs / domains / passwords
- ❌ Privacy scan is mandatory before push (criterion: **zero output**)
- ❌ Do not hand `AGENTS.md` / `CLAUDE.md` to AI for silent rewriting (prompt-injection attack surface; changes go through PR review)
- ❌ Do not advance to C without passing B, nor release at D without passing C
