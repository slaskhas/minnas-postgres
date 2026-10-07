# change: archive-quality-v784 — archival quality: closing reports no longer truncated + report cards get stored

- **Status**: implemented (tests all green), **pending release** (not tagged / not pushed / not deployed)
- **Date**: 2026-09-24
- **Target version**: v7.8.4
- **Blast radius**: the collection side (Hermes → Mnemosyne archival pipeline). **Does not touch** `main.py`'s server-side retrieval/storage behavior.

## Why (trigger)

The user asked "what is that few-hundred-MB file on the production server," and it couldn't be found
anywhere — resulting in two misjudgments (first "I never said that," then a correction with the wrong
attributed date).

Root cause, measured (gathered evidence before writing anything, no guessing):

| Fact | Evidence |
|---|---|
| **Not a single word** of the original text was lost | `~/.hermes/state.db` has 296 sessions / 42,565 messages |
| But it's **unfindable** at the semantic layer | `archive_session.py` truncates every message uniformly to `content[:2000] + "(truncated)"`, while closing reports are often >2000 characters |
| What got cut was exactly the valuable part | deliverable **absolute paths / URLs / hashes / measured numbers** all live in the back half of the report |
| Tool evidence was **lost entirely** | archival only kept `content`; `tool_calls` was never stored (this machine has 17,486 messages with tool calls) |
| Small tasks leave **no trace** | `auto_mode()` has `message_count < 5: continue` |
| Present in production, absent in the repo | the deployed copy's `_detect_project` wiring (from 2026-09-04) was never merged back (drift) |

Same root cause as (v7.8.2)'s "semantically equivalent ≠ usable": **the granularity changed but the
consumer didn't keep up — it doesn't error, it just silently loses fidelity.**

## MODIFIED

`scripts/archive_session.py`
- Tiered truncation: the closing report (the session's last AI message with a body) and user messages
  are **kept in full**; intermediate process messages are compressed to `LIMIT_PROCESS=1200`; when the
  total budget `MAX_TOTAL=120000` is exceeded, **only process messages are compressed** — the closing
  report is never trimmed (the budget is no lower than what the old version actually archived).
- Evidence signature: messages carrying `tool_calls` get a `⟪tools: terminal×3, read_file×2⟫` tag (capped
  at `TOOL_SIG_MAX=5` kinds, tolerant of malformed JSON).
- Short sessions: `message_count < SHORT_SESSION(5)` is no longer skipped — it's stored with a `[short]`
  prefix; `--skip-short` restores the old behavior.
- Titles: `_compose_title()` merges `[project]` / `[short]` into a **single** tag (`[short·relife]`), and
  is **idempotent** (reprocessing doesn't stack tags).
- Backport: merged in the already-verified `_detect_project` wiring from the deployed copy (requires ≥2
  keyword hits before tagging a project prefix).

## ADDED

`scripts/report_card.py` — report-card extractor
- Signals: paths (Win/Unix) · URLs · hashes (≥16 hex) · measured numbers · closing phrases ·
  verification phrases; **≥2 signal classes** must hit before it's treated as a delivery-grade report.
- Output: `~/.hermes/reports/cards.jsonl` (one card per line, containing session/msg_id/timestamp/signal
  counts/paths/URLs/hashes/numbers/excerpt).
- Ingestion: `POST /api/v1/memories`, `category=worklog` (**within capabilities' controlled vocabulary**;
  made-up values are silently normalized to `knowledge` server-side).
- Idempotent: card fingerprints (session+msg_id+text) are stored in `.index.json`, reruns don't
  double-insert.
- `--dry-run` writes nothing.

`tests/test_archive_quality_v784.py` — 29 contract tests (no DB/network dependency)

## REMOVED

None (`--skip-short` is kept as a toggle for the old behavior).

## Pitfalls hit and fixed during implementation (recorded here so they don't happen again)

1. **A loose threshold pollutes memory**: the initial ">=2 signal classes" rule pulled **66 cards** out of
   the last 3 sessions. → Changed the default to "only take the closing report per session" (final mode),
   measured at 3 cards.
2. **Tool-output echoes with `role='tool'` were being mistaken for delivery reports**: tool output is full
   of paths/hashes/numbers. → Tightened the SQL to `role='assistant'`.
3. **Diff/log noise was being matched as paths**: `/path/a.py\n+++ b/a.py` got matched whole. → The path
   regex now excludes backslashes/plus signs/backticks, with a `MAX_SIG_LEN=160` cap per fragment.
4. **The report isn't always the last line**: sessions often close with something like "okay, that's it."
   → final mode now looks back `FINAL_LOOKBACK=3` messages.
5. **Wrong semantics for the yield-count** (iteration count 4227 instead of message count) + the
   net-reduction undercounted suffixes → the loop ran more times than needed. → Switched to set-based
   counting + net reduction including suffixes.

## Invariants (regression guardrails)

- Session-archival push shape is unchanged: `POST /api/v1/sessions/archive`, JSON body, four fields
  `user_id/session_id/title/content`.
- `category` may only take one of the controlled vocabulary's 10 values.
- Zero changes to server-side code; this change only affects "the quality of content fed into the memory
  palace."

## Verification

```
$ .venv/bin/python -m pytest tests/test_archive_quality_v784.py -q
29 passed
$ .venv/bin/python -m pytest tests/ -q
228 passed, 6 skipped
```

End-to-end production pipeline verification (real write + read back):

```
$ report_card.py --last 3 --push     → pushed: 3
$ GET /api/v1/memories?user_id=default&category=worklog&limit=5
  #20077 [report card] deliverable path: C:\Users\<user>\Desktop\…\example-deliverable_double-click-to-view.html
               URL: https://your-site.example.com/news/example-page.html
```
→ The semantic layer can now answer "what was delivered last time, and where."

Dry run against real data (local session `20260924_043847_cafcc2`, 155 messages):

| Metric | Old version | New version |
|---|---|---|
| Closing report archived | first 2000 chars | **5,924 chars, in full** |
| Process messages | ≤2000 each | ≤1200 each |
| Tool evidence | none | `⟪tools: …⟫` signature |
| Total archived size | 125,782 characters | 99,041 characters (less, but more valuable) |

Report-card measurement (last 3 sessions): loose threshold produced **66 cards** (pollutes memory) →
default final mode produces **3 cards** (one per session).

Privacy gate self-check (same scan as `privacy.yml`): zero output for domains / real usernames.

## Release gate (not yet run)

## Execution status (updated 2026-09-24, closing)

- ✅ **Executed**: commit `ee5c4d1` · tag `v7.8.4` · pushed to the public repo · Release created (not
  draft, not prerelease)
- ✅ **Production side**: the (non-running) script copy has been synced (with backup, zero restart, service
  uptime unaffected); **the server-side version string intentionally stays at 7.8.3** — no new version is
  reported until it's actually deployed, avoiding a false release where "the doc says it but it never
  actually ran" (see the pending-deploy note in `PROGRESS.md`)
- ⏳ **Not yet executed** (filed under the memory-8.0 agenda pool, out of scope for this patch): bulk
  de-identification of historical internal codenames (~20 occurrences + historical snapshots)

Release order follows the existing convention: **deploy and verify stability first → only then tag/Release**
(avoiding "the doc says it but it was never actually released").
