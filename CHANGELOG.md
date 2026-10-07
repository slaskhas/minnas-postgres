## unreleased · Memory GC job fix (2026-09-26) — Oversized field in the voucher caused whole-batch compaction to fail

> Rationale: **production-measured** (a real instance, batch `GC-20260926`, 549 items pending) — a genuine defect surfaced by production use.

### Fixes

- `jobs/compaction.py`: explicitly raised `csv.field_size_limit` (→ 2^31-1).
  The longest `content` in production memory is **270448 characters** > the CSV default limit **131072** ⇒ when the voucher
  **re-counts the row lines after writing** it throws
  `Error: field larger than field limit (131072)`, the whole `--apply` batch `exit=3`, **nothing deleted at all**
  (the transaction rolls back cleanly, but the CSV is already on disk ⇒ a "the voucher exists, but the data was not deleted" liar voucher emerged).
- Voucher row count now **counts while writing**, no longer reads the whole CSV back (second trigger of the same-day cap).
- Regression test `tests/test_v8_compaction.py::test_c10_voucher_survives_oversized_field`
  —— **red first, then green**: before the fix `rc=3` (reproduces the same error as in production), after the fix full suite **268 green**.
- Lesson: all fixtures used short text ⇒ even a locally 267-all-green run still can't exercise this path (another instance of "local all-green ≠ path correct").
- **Second fix in the same batch: voucher "promotion" (liar voucher)** — the voucher now first writes to `*.part`, and only
  **after the transaction commits** does it `os.replace` to make it official; the failure path clears the `.part`. The old writing wrote the official
  voucher directly inside the transaction ⇒ after the transaction rolled back, the disk was left with a file that "claimed to have deleted, but actually deleted nothing"
  (the production batch `GC-20260926` really left behind an 8.4MB orphan). Regression tests `test_c11` (inject a failure ⇒ no voucher file allowed) /
  `test_c12` (success ⇒ promoted and no `.part` left over); full suite increased to **270 green**.

## unreleased · Repo refresh (2026-09-25) — Front-page poster redesign + data-caliber correction + install script

> Rationale: user instruction "the repo on GitHub needs a re-decorate… the poster needs a redesign, **don't just carry over the old design**".
> The design standard was taken from the existing on-site "Little Xiao Ju news" series language (rice-paper background + warm orange + dark brown + ink black).

### Poster redesign (`docs/poster.*`)

- **Design-language change**: from "extra-long infographic" to **newspaper front-page style** — masthead / header / lede (drop-cap two-column lead) /
  news numbers / staff column (with image) / three sections / two-column explainer / investigative feature / colophon. **Number of info sections did not decrease** (13 sections).
- New companion image: `docs/poster-mascot-8.0.png` (solid background → cut-out → composite; zero text inside the image)
- Finished size **1800×8281** (was 1800×7400); 100% of text goes through HTML layout (the AI image does not write text)
- Source `docs/poster.html` keeps **referencing external images** (small diff, easier to re-render on revision)

### Data-caliber correction (`README.md` / `README_CN.md`)

- Total memories `12,427+` → **`16,500+`** (caliber: `GET /api/v1/metrics` + memory stats)
- Archive rate `99.9%` → **`99.4%`**; added traceable line "data rows 17,320 (incl. 743 tombstones) · 297 MB"
- **Removed the number lines with no real-time caliber** (structured facts / Tome cards) — the status table only holds traceable numbers

### Install-piece completion

- Added **`setup.sh`**: `--check` (read-only self-test) / default (venv + deps + config template + DB init) / `--start` (start and health-check).
  Idempotent; **does not overwrite an existing `.env`**; no hardcoded paths or credentials.
- `INSTALL.md` version `v7.8.3 → v8.0.0` (3 drift fixes, command examples synced)
- Python floor `3.12+ → 3.11+`: measured on this machine 3.11.15 all green, no 3.12-specific syntax in the code
  (the original declaration was an **unnecessary external floor**)

### Corrections

- `docs/poster.html` main-title typo "Memory Power Supply" (`记忆供电`) → "Memory Palace" (`记忆宫殿`) (fixed in the same batch as the previous commit)
- **Poster footer verification numbers corrected** (cross-repo confusion + stale snapshot):
  - "Contract 45 passed" (`契约 45 通过`) — that **45 belongs to the `gcat-std` repo** (its governance-foundation suite), and was mistakenly brought into this repo's poster →
    changed to this repo's measured value **`MCP bridge contract 6 passed`** (`tests/test_mcp_bridge_contract.py`)
  - "Unit 267 passed" (`单元 267 通过`) (a local snapshot at v8.0.0 completion, per `PROGRESS.md`) → **`273`** (full `pytest` measured, reproducible by anyone)
- **CI privacy-gate misfire fix** (`.github/workflows/privacy.yml`): the credential-class pattern was originally a "fire on presence" writing
  `PGPASSWORD=[^y]`, which judged **legitimate variable expansion** in `setup.sh` as a leak → after push, main went red twice in a row (externally visible).
  - The criterion was changed to **only catch hardcoded values** (variable expansion begins with `$`, so it naturally doesn't match; genuinely hardcoded ones are still caught as before)
  - The pattern was moved from inside the step to the workflow-level `env`: **the scan and self-test share one definition**, so a single change takes effect on both sides
  - Added a "gate self-test · pattern two-way verification" step: legitimate form must be green + hardcoded must be red
    (a blocking rule also needs reverse verification; only testing "can catch bad samples" equals not testing "doesn't misfire on good samples")
  - Evidence: reproduced locally step-by-step per the YAML — old pattern hit `setup.sh:66/114`, new pattern all green;
    separately planted a genuine hardcoded sample, and both the credential-class and internal-domain scans **went red at the same time** (counter-evidence passed)
  - This discipline was also written into the `AGENTS.md` red line (pattern change: reproduce locally step-by-step + two-way self-test + clean counter-evidence)

## release · v8.1.0 (2026-10-06) — MCP bridge folded into core: 15 tools now serve /mcp over streamable HTTP (stdio kept)

> Basis: proposal [P-20261006-03](openspec/changes/2026-10-06-mcp-inprocess-http-merge/proposal.md) · ADR [0003](docs/adr/0003-mcp-bridge-inprocess-tradeoffs-and-triggers.md) · user instruction "do alternative A, implement"
> Net effect: the 15 Mnemosyne MCP tools — previously a separate stdio subprocess behind the 18010→8010 SSH tunnel —
> are now **mounted in-process** on the core uvicorn process at `/mcp`. The *same* contract-tested handlers
> (`_dispatch`/`_call`/`list_tools`/`call_tool`) serve **both** transports; stdio is preserved untouched for backwards compatibility.

### 🧠 In-process `/mcp` (streamable HTTP)

- `integrations/hermes-mcp/mnemosyne_mcp.py`
  - New guarded `mcp` import → `_MCP_AVAILABLE`; the module now imports cleanly without the MCP SDK (so the REST core is never taken down by a missing dependency).
  - New `set_base_url(url)`: re-computes `MNEMOSYNE_URL` / `API_BASE` and rebuilds the `httpx` client so handlers reach the core over **loopback REST**.
  - New `build_server()` (the low-level `Server` shared by stdio + HTTP), `build_http_app(streamable_http_path="/mcp", stateless_http=True)` (standalone Starlette app, kept for the in-process test), and — what `main.py` actually mounts — `build_mcp_mount()` -> `(asgi_endpoint, run_cm)`: the raw per-endpoint ASGI app (`session_manager.asgi_app`, path-agnostic, the JSON-RPC method lives in the body) + the `session_manager.run()` async context manager (initializes the session-manager task group; missing it -> 500).
  - New `__main__` guard (stdio entry unchanged); stubs raise a clear error when the SDK is absent.
  - **`_dispatch`/`_call`/`list_tools`/`call_tool` are byte-identical** (contract red line: never guess field names / param positions).
- `main.py`: loads the bridge **by file path** (`hermes-mcp` contains a hyphen → not a normal import path), checks `_MCP_AVAILABLE` + `set_base_url`/`build_mcp_mount`, then, inside a `lifespan` (`asynccontextmanager`; `app = FastAPI(..., lifespan=_mcp_lifespan)`): `set_base_url("http://<HOST>:<PORT>")` (`0.0.0.0`/empty → `127.0.0.1`) and `asgi_endpoint, run_cm = build_mcp_mount()` + `_app.router.add_route("/mcp", asgi_endpoint, methods=None, include_in_schema=False)`, driving `async with run_cm():` (initializes the session-manager task group). **Not** `app.mount` — a `Mount` never runs the sub-app lifespan (500 "Task group not initialized") and a sub-app whose own route is `/mcp` would nest to `/mcp/mcp`; a plain `Route(..., methods=None)` at exact `/mcp` mirrors the SDK's own standalone app.
  Wrapped in `try/except`: absent SDK / changed bridge → DEBUG-only skip, REST API unaffected.
  `stateless_http=True` (set inside `build_mcp_mount`) keeps each request independent → safe under `uvicorn --workers N`.
- `tests/test_mcp_http_mount.py` (new): in-process `mcp.Client(build_server())` lists all 15 tools; `build_http_app()` returns a Starlette app with a `/mcp` route;
  `set_base_url` re-points `API_BASE` + the client. Gated by `pytest.importorskip("mcp")` (auto-skip on minimal installs).
- `requirements.txt`: added `mcp>=2.0` (REST core still works without it — the mount self-skips).

### ✅ Verification (live uvicorn, single worker, 2026-10-06)

- [x] Syntax green: `py_compile` on the bridge, the new test file, and `main.py`.
- [x] Without the SDK: bridge imports cleanly, stubs raise `RuntimeError`, and the core's mount degrades to a DEBUG log (REST stays up).
- [x] With the SDK: `POST /mcp initialize` → **200**, SSE body contains `"mnemosyne"` (`serverInfo.name`); a real `mcp.Client("http://127.0.0.1:8010/mcp")` lists **all 15** tools and a `get_memory_stats` call round-trips; `GET /api/v1/echo` → 200 (REST intact); `POST /mcp/mcp` → 404; `POST /mcp/` → 307 → `/mcp`.
- [x] Existing `tests/test_mcp_bridge_contract.py` unaffected (handlers unchanged).
- [x] Full `pytest tests/` green: **261 passed, 19 skipped** (the 19 = no local PG test DB, pre-existing skips).
- [x] Version consistent in VERSION / README badges (EN+CN) / CHANGELOG (= **8.1.0**); local privacy reproduction (creds / domain / internal-file gates) **zero hits** on tracked files.

## release · v8.0.0 (2026-09-25) — Memory Palace OS 8.0: write right · recover back · find precisely · clarify

> Basis: proposal [P-20260925-01](openspec/changes/2026-09-25-v8-memory-os/proposal.md) · ADR [0002](docs/adr/0002-filesystem-mechanism-tradeoffs-and-triggers.md)
> Relationship with the prior "Memory Filesystem Research Report" (20-dimension FS mechanism mapping): **narrowed and institutionalized** ——
> only filled in what PostgreSQL did not provide and we truly lack; the other 17 dimensions were written into ADR triggers (no additions before they fire).
> The original report's value was preserved as a gate: its boundary conditions are our trigger thresholds.

### 🛡 S1 Reliability — real defect fixes (with "data can be wrong" consequences)

- **S1-1 Write atomicity** (`main.py: create_memory`): the original implementation did ordered `execute`
  three autocommit steps under `pool.acquire()`, so a crash could leave a half-finished state "memories row present, no entities / no memory_keywords".
  Now changed to a **single-transaction wrap** (conflict-detection read + primary write + entity sync + tokenize); any step's failure rolls back the whole.
- **S1-2 Idempotency key activated**: `dedup_fingerprint = sha256(content|category|user_id)`,
  builds a **partial unique index** `dedup_fingerprint_key` + `ON CONFLICT DO NOTHING`.
  Semantics: crash retry / client-cloud disconnect resend / duplicate POST **returns the original id, no longer creates a new row**.
  Design trade-off: **no history backfill** (history's 0 rows are NULL → the unique index ignores NULL; backfilling would expose 132 groups of historical duplicates and fail the index build).
- **S1-3 Memory GC / compaction** `jobs/compaction.py`: the original system had no `DELETE FROM memories`
  anywhere / no `VACUUM` → soft delete was the end point. Now fills in the `tombstone → purged` stage with **five safety gates**:
  window (default 30 days) → protected positions (permanent/pinned) → reference integrity (beliefs evidence / surviving child memories) →
  **cold archive `memories_archive` (incl. a traces snapshot)** → **CSV rollback voucher**.
  Default **dry-run**; only `--apply` truly deletes; `--restore <batch>` can restore a whole batch.
  `memory_traces` FK changed to `ON DELETE CASCADE` (was NO ACTION, which would block compaction; orphans were verified before changing).
- **S1-3 Integrity inspection** `jobs/scrub.py` (**read-only**): 6 classes of orphans/anomalies (orphan inode / orphan reference /
  dangling belief evidence / expired but un-compacted / missing fingerprint / living child hanging on a dead parent).
- **S1-4 Observability**: write/recall **latency instrumentation** (p50/p95/p99, in-process ring buffer, cost≈0)
  + `GET /api/v1/metrics` sees it all in one request (latency / DB scale / tombstone ratio / idempotency-index self-proof / last GC).
  Previously `perf_alert.py` had a 2000ms threshold but **no instrumentation at all** — the threshold was in name only.
- **S1-4 Backup re-verification** `jobs/backup_verify.py`: the original backup only answered "did it run or not" (there had been a **36-day silent failure**).
  Now adds a **recoverability** four-check: freshness / size / **structure (really reading the pg_restore TOC)** / `--deep` real recovery to a temp DB and comparing row counts.

### 🔍 S2 Retrieval quality

- **S2-0 Measure before changing**: the design is clear that "before changing recall ranking you must have an evaluation baseline" (red team's own words: "'RRF is better' has no evaluation backing").
- **S2-1 Four-channel RRF fusion** `core/rrf.py` + `palace.summon_fused()`: the original `summon()`'s four channels
  (naming / guided / resonance / library) **returned independently, without fusion, no unified truncation**. Now each takes candidate_k(50) candidates →
  RRF fusion (k=60) → unified top_k truncation. RRF fuses by **rank** only → unit-agnostic (the original single-SQL linear weighting
  directly added three units: cosine distance/BM25/time).
  **Namespace isolation**: `memories.id` and `wiki_pages.id` are two id spaces; prefix each with `m:`/`w:` before fusion
  (otherwise unrelated entries would boost each other). The `channels` field answers "why was this one ranked up" (explainability).
  **Disabled by default** (only enabled with `GET /api/v1/palace/summon?fused=true`) — zero behavioral change until the evaluation numbers come out.

### 🗂 S3 Governance

- **S3-1 Memory layering model → executable spec** `core/layers.py` + `openspec/specs/memory-layers.md`:
  5 layers (L0 log / L1 cognitive / L2 skill / L3 constraint / L4 reference) + cross-cutting artifact index, divided into **three families by "whether contradictions are allowed"**.
  **Not documentation**: `classify_layer()` actually runs on the write path, each memory lands in `metadata.layer`;
  external entry `GET /api/v1/layers` (with `self_check`) / `GET /api/v1/layers/classify`.
  Honest labeling: **the L3 constraint-layer carrier is on the Hermes side (SOUL/MEMORY/config), not in the DB** — assertions pin it down, preventing it from being forced into category.
- **S3-2 Release pipeline state machine** `gcat-std/scripts/release-gate.py` + this project's `release-gate.json`:
  A local → B real environment → C deploy → D public → E induction, **cannot go to the next stage without passing the previous one** (skipping a stage is hard-rejected).
  Accompanying `scripts/release_checks.py` (version consistency / privacy scan / service self-reported version number / changelog / artifact pointer).
  This v8.0 release is the first backfilled sample.
- **S3-3 Artifact pointer policy**: delivery = box / retrieval = Wiki / version = repo; **only put pointer + fingerprint in memory, not the entity itself**.

### ⚠️ Real incident during release (fixed, lesson kept)

**Symptom**: after deploying to production, the **first write was a 500**.
```
asyncpg.exceptions.InvalidColumnReferenceError:
there is no unique or exclusion constraint matching the ON CONFLICT specification
```
**Root cause**: `dedup_fingerprint_key` is a **partial unique index** (`WHERE dedup_fingerprint IS NOT NULL`),
but the write uses `ON CONFLICT (dedup_fingerprint)` —— PostgreSQL's partial-index inference **requires the predicate to match explicitly**,
a missing predicate errors directly, taking down the whole write path.
**Fix**: `ON CONFLICT (dedup_fingerprint) WHERE dedup_fingerprint IS NOT NULL DO NOTHING`
(keep the partial index: it only constrains rows with a fingerprint; the 16k historical NULL rows need no backfill)

**Why the existing unit tests didn't catch it (the most important lesson of this release)**
- Existing tests only verified "the index can block duplicate fingerprints" (bare `INSERT`),
  and **never ran the real `INSERT ... ON CONFLICT` statement in the product code**.
- **Testing the index ≠ testing the SQL that uses the index.**
  Wherever "the SQL assembled in the code" is coupled with "the object built in the DB", you must extract the real SQL and run it.
- Fix: added `tests/test_v8_write_path.py` — **extract the real SQL from the `main.py` source via regex**,
  bind placeholder values and execute it against the DB. As soon as source and DB objects mismatch, it goes red immediately.
  Counter-evidence verified: temporarily removed the predicate → `test_w2` failed immediately.
- This time it was caught by the **third layer of release (production function test)** — this is the evidence that "triple verification" isn't formalism.

### 🔧 Post-release corrections (**still inside v8.0.0**, not a new version)

> 2026-09-25 user set the tone: these defects happened in the **testing phase** and should have been **absorbed within the same version number** by correct rhythm,
> not given to "fix in the next version". So no new version number was added; corrections were folded directly into v8.0.0 and the release was cut again.

**🔴 Correction 1: memory GC "whole-batch restore" is a false safety (red team pointed out → I reproduced → fixed)**

Reproduced measurement:
```
before delete → keywords: 1  tome_cards: 1
after delete → keywords: 0  tome_cards: 0        ← CASCADE silently deleted along with it
after restore → memories: 1  traces: 2  keywords: 0  tome_cards: 0   ← didn't come back
```
The memory got back into the DB, but **BM25 can never search for it anymore, and the catalog card is gone too** (a zombie memory).
Root cause: the original implementation only archived `memories + memory_traces`, while the three tables `memory_entities` / `memory_keywords` /
`tome_cards` are all `ON DELETE CASCADE` —— silently deleted along with it, and not rebuilt on restore.
Fix: archive adds columns `_entities/_keywords/_tome_cards` — full snapshot of all four tables → restore rebuilds the four tables →
**integrity assertion** (if archive count ≠ restore count, roll back the whole thing; structurally a zombie memory can't appear).

**🔴 Correction 2: idempotency key contradicts the L0 contract (red team pointed out → re-verified → production P4 test exposed a "half-fix")**

The first version's `sha256(content|category|user_id)` was applied to **all** categories, directly contradicting our own layering model:
the L0 contract is "add only, no modify, contradictions allowed", but saying "OK, received" three times the same day gets merged into one.
The root cause confused "idempotency (a same-request retry takes effect only once)" with "deduplication (merging multiple of the same content into one)".

The fix **needs two places changed; changing only one is a half-fix** (caught by the production P4 measurement):
1. **Layered fingerprint** `compute_write_fingerprint()`:
   L1~L4 use the content fingerprint (versioned family, idempotent invariant); L0 uses **source context**
   (if there's a session_id/source, use it to distinguish; if neither, use an hour bucket — second-level retry dedup, across hours preserved)
2. **Layered conflict detection** `should_run_conflict_detection()`: L0 **skips** the
   semantic merge of `detect_conflict` —— otherwise near-duplicate content gets merged **before** the fingerprint decision, and the L0 log is still compressed

Measured comparison (production): old behavior across sessions = merged into one (false kill) → new behavior = each stored as one;
same-source retries still dedup; L1 same content across sessions is still idempotent.

**Lesson**: the unit test only tested the `compute_write_fingerprint` **pure function**, not the **whole write path** ——
same source as "only testing the half you can use". **This is exactly the point that production testing (P4) exists.**

### 🧪 Tests

- Added **26 cases**: `tests/test_v8_rrf.py` (8, pure functions) · `tests/test_v8_layers.py` (10, spec assertions) ·
  `tests/test_v8_compaction.py` (8, integration — needs the v8.0 migrated DB; if unavailable, skip the whole thing).
- Full: **228 → 267 passed / 6 skipped** (incl. write-path SQL contract, layered idempotency key, L0 conflict-skip, GC restore integrity C9, etc.)
- gcat-std governance foundation: **45 tests OK** (release-gate hard-constraint suite)
- **Counter-evidence tests** (build violating samples to prove it really can block): dry-run zero change · cross-stage rejected · unique index rejects duplicate fingerprint ·
  truncation / stale / empty backup blocked · layering spec drift detected.

### 📌 Three environments

| Environment | Status |
|---|---|
| Local (WSL) | v8.0.0 implemented, 254 cases green |
| Production (GZ) | After deploy, service self-reported version re-verified (**deploy and stabilize first, then go public**) |
| Repo (GitHub) | tag/Release v8.0.0, two-layer privacy scan zero output |

---

## release · v7.8.4 (2026-09-24) — Archive quality: wrap-up reports no longer truncated + report cards into the palace (+ governance foundation landed)

> Trigger: the user asked "what's that several-hundred-MB file on the production server", and it couldn't be found anywhere in the whole line — the original text is **not missing** in `state.db`, but can't be found at the semantic layer:
> session archiving did a uniform `content[:2000]` cut per **message**, and wrap-up reports are often >2000 chars, and what got cut is exactly the **absolute path / hash / measured numbers of the deliverable**.
> Same-root disease as (v7.8.2) "semantic equivalence ≠ usable": **changed the granularity but the consumer side didn't keep up, no error, only distortion**.

### 🩹 Fix — archive pipeline (`scripts/archive_session.py`)
- **Tiered truncation**: wrap-up reports (the last AI message in the session with body) and user messages **keep the full text**, process messages compressed to `LIMIT_PROCESS=1200`;
  over the total budget `MAX_TOTAL=120000` it **only yields from process messages**, reports are never trimmed (the old version cut 2000 chars uniformly per message)
- **Evidence signatures**: messages with `tool_calls` get a `⟪Tools: terminal×3, read_file×2⟫` appended — the semantic layer can now see "what this round did"
  (measured on this machine **17,486** messages with tool calls, the old version threw them all away)
- **Short sessions no longer abandoned**: `message_count < 5` no longer silently skipped, changed to stored into DB with a `[short]` prefix; keep `--skip-short` to restore the old behavior
- **Fix rolled back to the repo**: the `on_session_end` wiring of the deploy-copy 2026-09-04 `_detect_project` (project prefix into title) was previously **only in production, not rolled back to the repo**
- **Tag merge and idempotent**: `[project]/[short]` merged into a **single** tag `[short·relife]`, repeated processing doesn't stack (the old writing would nest square brackets)

### ➕ Added — report cards (`scripts/report_card.py`)
- **Default final mode: each conversation only takes the wrap-up report**; `scan`/`all` modes for analysis (measured: loose threshold, 3 conversations extracted 66 cards → would pollute memory; final mode 3 cards) → `~/.hermes/reports/cards.jsonl`
- Into the palace `POST /api/v1/memories` + `category=worklog` (**within the capabilities controlled vocabulary** — self-cooked values are silently **normalized by the server to knowledge**)
- Idempotent: dedup the card fingerprint (session+msg_id+original text), repeated runs don't pollute memory; `--dry-run` writes nothing

### 🧪 Added tests (`tests/test_archive_quality_v784.py`, 29 cases)
- Tiered truncation (report full text / user full text / process compression / total budget doesn't swallow the report) · evidence signature (with bad-JSON tolerance) · title composition and idempotency ·
  short-session storage and `--skip-short` restoration · report-card extraction / controlled category / request shape / idempotent / dry-run · session archive push-shape regression

### 🧪 Measured pitfalls, fixed (all locked in tests)
- Loose threshold, 3 conversations extracted **66 cards** → default changed to "each conversation only takes the wrap-up report", measured 3 cards
- `role='tool'` tool echoes were treated as a wrap-up report → SQL tightened to `role='assistant'`
- diff noise `…a.py\n+++ b/a.py` was treated as a path → path regex excludes backslash/plus/backtick + fragment cap 160
- Wrap-up often not in the last sentence → final mode looks back 3
- Yield-counting semantics wrong (iteration count 4227 ≠ message count) + net reduction missing suffix → fixed
- Production end-to-end: `--push` real write → `GET /api/v1/memories?category=worklog` read-back succeeded

### 📋 Change proposal
- `openspec/changes/archive-quality-v784/proposal.md` (incl. release gate and verification record)

### 🏗 Governance foundation landed (same day, project level)
> **This doesn't change runtime behavior**, only fills the "fact foundation": lets any handover session know in 3 minutes what this is, what capabilities it has, and where it stands.

### Added
- `PROJECT.md` project charter (what this is / why / where it stands / scope boundaries / key-decision index)
- `openspec/` spec layer: `specs/` (capability truth) + `changes/` (change proposals, with ADDED/MODIFIED/**REMOVED**) + `archive/`
- `docs/adr/` architecture decision records (first: ADR-0001 adopt project governance standard)
- `CONTRIBUTING.md` contributing guide (commit convention / branch / must-do before change / release gate / red line)
- `docs/` sink docs: `ARCHITECTURE.md` · `INTEGRATION.md` · `API.md` · `ENV.md` · `AGENT-USAGE.md`

### Changed
- `AGENTS.md` **slimmed 8,466 → 2,236 chars (-74%)**: root file keeps only "build / conventions / red lines / navigation", details sink to `docs/`

### Fixed
- `.gitignore` added `*.db` / `*.sqlite` / `*.dump` / `*.log` — previously `sync/local_cache.db` (local memory cache) wasn't ignored; a mis `git add -A` would commit real data into the public repo

## release · v7.8.3 (2026-09-12) — Service port / bind-address env vars actually take effect

> Trigger: `MNEMOSYNE_PORT` / `MNEMOSYNE_HOST` belong to the "docs say it, code doesn't read it" silent-failure kind — `config.py` has long read via `os.getenv`, but the service entry `main.py`'s `__main__` block has **hardcoded** `host="127.0.0.1", port=8010`, so env vars are silently ignored (the old `INSTALL.md` / `AGENTS.md` honestly labeled them "not yet effective"). When the doc-promised switch doesn't match the implementation, users configure per the doc and then are confused — same root as (v7.8.2) "semantic equivalence ≠ usable": **changed wording/position but the consumer side didn't keep up, no error, only distortion**.

### 🩹 Fix
- `main.py`'s `__main__` entry now uses the already-imported `config.HOST` / `config.PORT` (default unchanged: `127.0.0.1:8010`) → `MNEMOSYNE_HOST` / `MNEMOSYNE_PORT` now truly take effect from here on
- Docs synced: `INSTALL.md` (port description rewritten + example-response version), `AGENTS.md` (env var table removed the "not yet effective")

### 🧪 Added tests (`tests/test_server_port_env.py`, 2 cases)
- Contract: env vars not set → `config.PORT == 8010`; `MNEMOSYNE_PORT=9123` → `9123`. **Subprocess-verified**, doesn't pollute the module-level config of the current process

### 🔁 Compatibility
- Default behavior is completely identical to (v7.8.2) (default values unchanged); only deploys that explicitly set these two vars change the bind address —— and that is exactly the behavior the docs have been promising

## release · v7.8.2 (2026-09-12) — MCP bridge contract fixes rolled to repo + contract self-description alignment

> Trigger: production-measured, the MCP bridge's three management tools (feedback/delete/restore) always report 422 — the bridge sends feedback as a JSON body and omits user_id, while the server-side REST requires both to be query parameters; on the user side this shows as "the tool is broken". The same kind of risk lands on **response fields** and fails silently (read `heat` while the server returns `heat_score` → always falls back to the default and doesn't error), so the contract is locked in together.

### 🔌 Fixes rolled to repo (verified in production)
- `integrations/hermes-mcp/mnemosyne_mcp.py`: the `user_id` of the feedback / delete / restore three handlers (and the feedback value) changed to go through **query parameters**; production three-path measured pass (feedback→feedback recorded / delete→soft-deleted / restore→restored)
- Desensitize + contract iron-clad section: the bridge's docstring internal codename → "production server"; added three iron rules: "in-parameter position per server requirement / response field name per the self-description / changing a handler must run contract tests"

### 🧪 Added contract tests (tests/test_mcp_bridge_contract.py, 6 cases)
- Lock the outbound request shape: 3 query-parameter endpoints + 2 body-endpoint regressions (prevent "over-fix")
- **Counter-evidence passed**: under the old code 4 cases fail (proven red), after the fix 6/6 green
- The mcp SDK is only installed on the Hermes side → minimal install auto-skips, doesn't block the server-side test suite

### 📖 capabilities self-description alignment (API facade)
- `category` vocabulary: `fact|experience|belief` (3 classes) → controlled vocabulary **10 classes** + normalization note for illegal values (the old facade doc and the DB CHECK constraint were inconsistent; external users would write wrong per the doc)
- `DELETE /memories/{id}`, `POST /{id}/restore`, `/{id}/feedback` labeled `user_id (query, required)` —— exactly the pitfall point this time
- `GET /memories/heat-top` supplemented `returns: memories[].heat_score (field name is heat_score, not heat)`

### 🧰 Toolchain
- `scripts/version-scan.sh`: CHANGELOG / AGENTS matching alignment to real formatting (previously always judged MISSING, in name only)

### 🧹 Internal codename cleanup (active code / copy, same batch)
- **17 active files / 43 lines**: internal codenames in comments, docstrings, usage and log strings → neutral phrasing (`production server / server side / production`), covering `sync/*`, `wiki/*`, `integrations/hermes-provider/`, `tmt/router.py`, `scripts/version-scan.sh`, `cron/*`, `memory_tokenize.py`, `skill_sync.py`, `palace.py` examples
- **Runtime semantics unchanged**: pure copy; `scripts/version-scan.sh` local var `gz_ver`→`prd_ver` (for the script's own use); the `palace.classify` example and `tests/test_palace.py` are **paired changes**, tests 194 passed + 6 skipped (contract cases need the Hermes-side mcp SDK, minimal install auto-skips)
- **Intentionally preserved**: history in `CHANGELOG` / `ROADMAP` / `docs/**` —— these are release logs, don't rewrite history (same principle as "keep git history")
- **Criterion (this audit)**: full history + full tree **two-layer scan** — cloud-vendor access keys / code-hosting platform tokens / model-service-class keys / private-key header / collaboration-platform tokens / cloud-vendor SecretId / Bearer-JWT / Windows user path / SSH key name / local home dir / email all **0 hits** in the current tree; the only non-loopback IP is `16.14` in a `pg_dump` version comment (false positive); history SSH-tunnel docs use `your-server-ip` + `/path/to/your-key.pem` placeholders, the port scheme is **recon-level**; therefore keep the whole git history, no force-push to scrub
- **History residue (accepted with knowledge)**: the following non-credential info still persists in pre-cleanup history snapshots, assessed as **kept** (same principle as "keep git history as a release log"). ⚠️ **Concrete values are not re-stated in this file** (a desensitization list itself must not contain real values), see internal audit record `Mnemosyne #18926`:
  1. **Personal site domain** — only 1 snapshot's asset-index doc (`docs/github-assets-index.md` @ `2de398f`, the file was already deleted at that time); the same doc also included personal research project names and DOIs (portfolio disclosure); the current tree is clean
  2. The `pg_dump` `\restrict <random-string>` session marker — 3 snapshot schema dumps (`442f333`/`515e437`/`63e8869`); **not a real credential** (only pg_dump's session security marker), but it looks like a key in form; the current `docs/schema.sql` no longer has it
  3. Historical ops docs (deleted with the file) contained deploy path, service user, loopback address and port —— recon-level
  4. Basis of judgment: **no usable credential appears in any history snapshot** (access key / password / token / private key / real public IP). The cost of full scrubbing = `git-filter-repo` + force push + rebuilding 39 tag/Releases —— a destructive action, needs explicit authorization

## release · v7.8.1 (2026-08-24) — Same-day blindness fix rolled to repo + MCP 2.0 adaptation

> Closed the loop of production-ahead-of-repo drift: the v7.8.1 fix on GZ (new-memory same-day blindness P0) that had been running for two weeks is now officially rolled into the repo and released, and MCP bridge mcp 2.0 adaptation + test-assertion corrections are synced.

### 🩹 Fixes rolled to repo (verified on GZ production)
- **New-memory same-day blindness P0 (v7.8.1 core)**: tokenize on write `_tokenize_on_write` (both the main write and the session archive), eliminating BM25's max 24h blindness window; BM25 smoothing `LEAST(1.0, 0.5+SUM/8)` no longer a neutral 0.5 score for no keyword; vector weight 0.40→0.50, rel/heat 0.15→0.10 (three-formula unification: dialectic / main search / fallback)
- **Test assertion synced**: test_weighting.py reliability weight 0.15→0.10 (test went stale after the v7.8.1 weight change, 194/194 all green)

### 🔌 MCP bridge mcp 2.0 adaptation (Hermes side, 2026-08-24 measured)
- `integrations/hermes-mcp/mnemosyne_mcp.py`: mcp SDK 2.0 removed the `list_tools/call_tool` decorators → changed to construct-callback registration `Server(name, on_list_tools=..., on_call_tool=...)`; 15 tools fully preserved; stdio smoke + end-to-end search measured pass (Hermes 0.20.5 upstream pins mcp==2.0.0)

### 🧹 Production repo alignment
- tmt/router.py comment desensitize (GZ server → production server)
- integrations/hermes-provider version synced v7.8.1

---

## release · v7.8.0 (2026-08-18) — Precise defusing + architecture slimming

> 🔧 **Post-release refresh (same day)**: ⑤ removed the disabled Web memory browser (browser.html, plaintext API Key risk, abandoned) ⑥ added GitHub Actions CI (pytest dual Python versions) + README CI badge ⑦ poster replacement v7.8.0 (data snapshot 12,427/8,299/12,646, v7.7 skill-asset wing + v7.8 real BM25/defusing slimming, 15 tools)

> 🔧 **Post-release patch (same day)**: ① tome_links residue re-cut — palace.py still rebuilds that table (local+GZ), production DB had a leftover empty table; deleted code+tests (195→194) + production DROP, 21 tables = aligned to schema.sql ② 9-place version-number consistency fix (README_CN/PROGRESS/ROADMAP/INSTALL/provider VERSION/requirements) ③ data snapshot refresh (12,427 memories / 8,299 facts / 12,646 cards) ④ outdated marking of optimize-plan-v8.md

### 🧹 Sore-spot excision (three-expert audit + real fixes)
- **drawer_pipeline dedup daily-crash fix**: PostgreSQL `::` binds tighter than `||`, causing an incomplete JSON type-conversion error; changed to jsonb_build_object, merged_from append keeps the merge chain; recompute embedding after merge (list→str passed as vector)
- **entities noise cleanup**: 43,256→21,113 rows (pure-word rule + wiki exemption), 570MB→278MB
- **Main search real BM25**: jieba tokenize + memory_keywords TF weighting (replaces ILIKE fake BM25), precise number queries qualitatively improved; daily 4am incremental tokenize
- **perf_alert fix**: pg_stat_statements cumulative-average false positive → 30min window + alert dedup
- **589 NULL embeddings filled** + **facts template-prefix stripping 7,528 rows** (stored in metadata.fact_type)
- **Real SQL integration tests 5 cases** (local mnemosyne_itest DB), 195 all green; pre-fix-must-error regression tests

### 🗡 Apache AGE excision (PG upgrade freedom)
- 11 business tables ag_catalog→public; cypher multi-hop / graph write all deleted; wiki_extract only extracts entities (saves LLM tokens); wiki_dedupe deleted
- PG shared_preload_libraries drops age; DROP EXTENSION age; schema.sql fully publicized, 28 tables
- graph/search downgraded to pure SQL (entities→memory_entities adjacency)

### ✂️ Slimming second cut (no consumer-side assets, 28→21 tables)
- Removed: memory_chunks (231MB) + chunk pipeline / memory_pointer / conversation_messages (35MB, double storage) / tome_links / api-halls+gates / api-tools+tool_archives / api-projects / api-response (v5 leftover)
- provider on_session_end removed the full-fidelity session sync (Hermes state.db is the authority)

---

## release · v7.7.0 (2026-08-18) — Scheduling hall: inject the intelligence kernel

### 🆕 Procedural memory wing (skill assets)
- New tables `skill_assets` (skill name / description / category / status / usage stats / embedding) + `skill_keywords` (BM25 index, tenant isolation)
- Skill = executable memory: on par with declarative memory; state machine aligned to Hermes curator (active/stale/archived, never DELETE, only transition)
- `POST /api/v1/skills/sync` idempotent batch sync (empty description falls back to name+category embedding, single-item failure tolerates and keeps existing)
- `POST /api/v1/skills/search` semantic summoning (vector+BM25+RRF+status weight, active priority, dormant can be woken)
- `PATCH /skills/{name}` state transition (wake) / `POST /skills/{name}/touch` usage count
- Accompanying `skill_sync.py` (WSL skills → GZ) + `skill_tokenize.py` (jieba tokenize → skill_keywords)

### 🆕 Injection scheduling hall
- `POST /api/v1/injection/plan`: per scenario (query) returns an injection flow {related skills + related memories + hot hooks}
- Injection = cognitive scheduling: dynamically compiled by scenario × value, no longer fixed-heat topN
- Server-side hard-verifies limits (skills≤8 / memories≤10 / hooks≤10), empty context returns directly, single-channel failure silently degrades

### 🆕 Embedding deep optimization
- Measured ARK doubao-embedding-vision **doesn't support batch input** (multiple inputs return only the first) → replaced with concurrent-per-item (MAX_CONCURRENT=8, configurable via env)
- LRU cache (OrderedDict standard impl: hit move_to_end + over-cap evicts least-recently-used) + disk persistence (600 perms, self-heals on corruption)
- Exponential-backoff retry (429/5xx, 3 times) + cold-cache 50 items 1.9s (old serial 14.7s, **7.7× speedup**)

### 🔴 Fix: RRF fusion pid-space inconsistency (real bug)
- BM25 channel's pid is the DB skill_id, vector channel misused list index → after fusion an out-of-bounds IndexError was silently swallowed → injection plan returned empty
- Fix: both channels use skill_id uniformly; added test_injection_plan_v77.py to lock in regression

### ✅ Acceptance
- pytest **190 passed** (167 existing + 11 skill_sync + 7 embedding + 5 injection_plan)
- hermes verify ok=true, readiness 200
- Doubao 4-role expert acceptance (architecture/vector/ops/product): 2 P0 + 8 P1 all fixed then passed

## release · v7.6.2 (2026-08-15)

### 🔴 Fix: project_id type contract (str→int)
- Root cause: `MemoryCreate.project_id` / `MemorySearch.project_id` model defined `Optional[str]`, but the DB column is bigint (int8). Writing a project-id string (e.g. 'proj_xxx') → asyncpg 500 crash
- Fix: both models changed to `Optional[int]` — pydantic parses automatically; passing a project-id string → friendly 422 error, no longer 500
- Measured: reproduced scenario 422 ✓ / numeric id write 200 ✓

### 🔴 Fix: knowledge class wrongly frozen (high-value memory invisibility)
- Root cause: v7.3 Rank percentile tiering (after PERCENT_RANK, bottom 30% → frozen) squeezed knowledge/pitfall/reference/preference high-value memories into the frozen zone; while search defaults to `include_frozen=false`, excluding frozen → can't find what should be found
- Fix: two Rank-tiering CASEs in main.py (regular reflect + Sunday forced full) give knowledge classes a floor `cool` (never frozen, can cool but not invisible)
- Backfill fix: 224 wrongly-frozen knowledge classes already unfrozen back to cool
- Measured: frozen 2531→2307 (only session/worklog cold fragments left), knowledge classes hit on default search ✓

## fix · Missed cleanup (2026-08-11)

### Version-number fix (external API self-description)
- main.py: root endpoint / FastAPI title / capabilities service name — v6.x → v7.6.1
- GZ already deployed and verified: capabilities returns Mnemosyne OS v7.6.1

### Internal codename desensitize (code comments)
- main.py: GZ → production server; Qwen3-Embed (GZ :11436) → local fallback; noah example → default
- palace.py / tmt/router.py: example and timezone comments GZ → generic

### Deprecation cleanup
- cron/cron-hermes-kanban.sh deleted (kanban dispatch mode already deprecated)
- GZ crontab removed kanban dispatch residue (every 2min)
- docs/requirements.txt deleted (historical pip freeze snapshot, containing llama_cpp_python misleading)
- docs/schema.sql cleaned 4 tmt_*_old leftover tables + 6 indexes + 1 broken FK (measured: 34→30 tables, 0 residue)

### Data sync
- README/README_CN/WHITEPAPER: memory count 8,873/8,647 → 9,952+
- INSTALL.md: table count 32+ → 30
- PROGRESS.md: run state v7.0.0 → v7.6.1, next steps aligned to v8.0

## docs · Repo refresh round 2 (2026-08-11)

### Structure regularization
- wiki_bm25/wiki_graph/wiki_extract/wiki_tokenize/wiki_dedupe/wiki_eval/wiki_sync_check + wiki_dict.txt → `wiki/` subdirectory
- main.py: 3 reference fixes (wiki.wiki_bm25 / wiki.wiki_graph / wiki/wiki_dict.txt)
- 5 independent scripts in wiki/ have sys.path pointing to repo root (tmt/core importable)
- tests/test_wiki_bm25_v75.py: reference updated
- GZ production sync: rsync wiki/ + main.py + 4 crontab entries prefixed with wiki/ + restart verify ✅

### Meta info
- GitHub About description updated: v7.x capabilities + bilingual Chinese/English

### Rules solidified
- CONTRIBUTING.md: added "repo structure convention" + "edit/release rules" (version consistent in 3 places / bilingual sync / doc table / CHANGELOG/ROADMAP/GZ sync / tests / audit)

### Poster
- docs/poster.html + poster.png: v7.6.1 redo (warm hand-drawn style + character standing-card figure + 9 v7-series new capabilities + 860x1900)

## docs · External-sharing doc system (2026-08-11)

### Added
- `INSTALL.md`: per-environment install guide (Linux/macOS/WSL + DB init + model backend + FAQ)
- `integrations/hermes-provider/README.md`: Hermes adaptation doc (11 tools + auto hooks + config)

### Rewritten
- `AGENTS.md`: 76 → 236 lines. AI integration manual (API endpoint quick-ref / full env-var table / Hermes/MCP onboarding / best practices)
- `README.md` / `README_CN.md`: Quick Start fixed to an executable path (added DB step + INSTALL entry point) + Start-Here path routing table

### Removed
- Docker-related planning (docker-plan.md and README/ROADMAP/INSTALL references) — user decision not to do Docker for now

### Fixed
- `docs/schema.sql`: pg_dump FK-order bug (memory_pointer references the not-yet-built memories table) + palace section search_path + migration leftover table labeling
- `requirements.txt`: added `jieba` (v7.5 WIKI BM25 hard dependency; missing → ImportError)
- `integrations/hermes-provider`: synced Hermes running version (10 → 11 tools, added palace_summon)
- `deploy/mnemosyne.service`: generalized (was production-only, and was excluded by .gitignore)

### Desensitize
- CHANGELOG/README/.gitignore internal codename GZ → production env / production server

## v7.6.1 · Three-class memory tagging (2026-08-10)

### Added
- `metadata['memory_type']` three-class tagging: episodic (episodic: session/chat/worklog) / semantic (semantic: fact/preference/knowledge) / procedural (procedural: pitfall/ops/deploy/project)
- New writes auto-tag (category rule mapping, zero LLM cost)
- 9952 existing rows batch-tagged (semantic 7202 / episodic 2706 / procedural 45)

## v7.6.0 · Memory isolation and source tracing (2026-08-10)

### Added
- `MemoryCreate.source` field → writes to metadata['source'] (supports compression-archive / subagent batch recall)
- `GET /memories?source=` filter param (metadata->>'source' exact filter, used for OVERFLOW batch recall)
- Clone-memory isolation: mnemosyne-agent/website-agent no longer collapse to default (partition to prevent pollution)

### Accompanying (Hermes integration layer)
- Hook mechanism + on_pre_compress no-loss closed loop (archive-with-ID hook before compression)
- prefetch two-factor optimization (low-heat filter + cap 3)

### Fixed
- content clone's MNEMOSYNE_USER_ID wrongly set to catnest-agent → changed to content-agent

# Changelog

## v7.5.1 (2026-08-10) — Graph quality + evaluation expansion (expert-review acceptance)

### ✨ New features
- **eval evaluation expansion**: 20 → 30 queries (long-tail / cross-domain / boundary), metrics precision@3 + recall@3 + MRR
- **Graph edge periodic dedup**: wiki_dedupe.py (production cron Mon 8am), prevents AGE duplicate-edge accumulation

### 🔧 Fixes
- **Graph edge dedup**: 3,263 → 1,006 RELATED_TO edges (deleted 2,257 duplicates, cross-page common entity pairs); wiki_extract pre-edge-creation dedup check to prevent recurrence
- Graph regression verify: Huntian Xinsuan / Memory Palace / Noah all hit, multi-hop cross-page normal

### 📦 Others
- Expert team 4-role review: all conditionally passed, P1 fixes done (graph regression / eval expansion / dedup cron)
- Full acceptance 12/12 all green: function chain / data integrity / graph quality / search / quality
- Tests: 167 passed

## v7.5.0 (2026-08-10) — WIKI retrieval optimization (storing so it can be used)

### ✨ New features
- **BM25 keyword channel**: jieba tokenize → wiki_keywords table (71 pages 47,619 tokens) → BM25 scoring → RRF fusion (k=60), fills in the pure-vector keyword blind spot
- **Professional dictionary**: wiki_dict.txt 145 words (wiki high-frequency words + core terms), terms like Huntian Xinsuan / Jinshanlin protocol correctly segmented
- **Graph expansion channel P1**: entity anchoring + AGE 1-hop RELATED_TO → page scoring; additive mode to prevent noise, off by default (optional enhancement)
- **Palace summon 4th channel "Library"**: searching memories also brings out related papers/proposals
- **eval periodic evaluation**: 20 queries × 3 tiers (pure vector / vector+BM25 / all channels), production cron every Mon 7am, drift-alert prevention
- **Optional rerank**: reuses rerank_docs (Doubao embedding similarity re-ranking, off by default)

### 📈 Effect
- A/B evaluation 20 queries precision@3: pure vector 95% → vector+BM25 **100%** (avg rank 1.20→1.00)
- Term queries BM25 strong hits: Huntian Xinsuan 8.15 / Jinshanlin 15.25 / Concept Fission 14.75

### 🔧 Fixes
- create keyword-index threshold 50→20 (short content also needs BM25 index)
- search candidate pool 25→50 + BM25-only page backfill query (prevents new pages being pushed out by vector rank)
- BM25 IDF computed per-line (rare-word IDF correct)

### 📦 Others
- Expert team 4-role review: all conditionally passed, P1 fixes done (dictionary)
- Hermes adaptation tests 8/8 passed
- Tests: 167 passed (including BM25/RRF 10 cases)
- hermes verify ok=true

## v7.4.0 (2026-08-10) — WIKI knowledge graph MD memories

### ✨ New features
- **Full-text snapshot archive**: added source_path/source_url/content_hash/source_type/source_lost columns to wiki_pages, papers/articles full-text stored to prevent source damage; source URLs only as pointers
- **md_ingest import pipeline** (`scripts/md_ingest.py`): local authoritative one-way sync — `--sync` import/update (hash idempotent: same source same hash → exists, diff hash → updated + version history), `--verify` verify (match / drift / source lost); after source loss the online snapshot is still verifiable
- **LLM entity + relation extraction** (`wiki_extract.py`): replaces regex coarse extraction, each page extracts 8-20 entities + 5-15 relations → entities + wiki_entities + AGE graph (RELATED_TO / MENTIONS edges)
- **by-source quick-verify endpoint**: GET /api/v1/wiki/by-source exact query of snapshot by source path/URL
- **Semantic search upgrade**: POST /api/v1/wiki/search directly queries wiki_pages.embedding HNSW (originally queried the versions table, basically unsearchable)

### 🔧 Fixes
- Fixed the wiki endpoint duplicate-definition bug: main.py line 415 simple version vs line 1945 full version on the same path; unified to body model (WikiPageCreate/WikiSearchRequest), behavior consistent

### 📦 Others
- wiki_entities association table + wiki_pages.extracted_at marker
- Tests: tests/test_wiki_v74.py 9 cases (idempotent / fingerprint / extraction parsing), full 157 passed
- hermes verify ok=true

## v7.3.0 (2026-08-09) — 🧠 Combined algorithm + efficient retrieval (from forgetting toward organizing-optimization)

**Core idea**: memory should emphasize organizing-optimization, not forgetting. Mention = upgrade, retrieval is regional, pointer for fast locate. User turn: time-remote but always mentioned → upgrade; after demotion mentioned multiple times → upgrade again (two-way dynamic).

### 🧠 Combined memory strength Rank
- **Rank = 0.3S + 0.3R + 0.2ln(mention+1) + 0.2heat** (multi-dim fusion, weights configurable)
- **Two-way dynamic**: mention → mention+1 → R rebound → accumulated 5 times S+1 (demotion can rebound)
- Mention signals: explicit (search/summon hit) + implicit (conversation entity match, threshold >0.85)
- Drawer changed to Rank percentile tiering: hot top 10% / normal 10-30% / cool 30-70% / frozen 70%+

### ⚡ Quick whole-disk pointer
- **memory_pointer table**: whole DB 1/10 volume, B-tree index <10ms
- `GET /pointers/top` (Rank topN, optional partition) / `GET /pointers/search` (pointer-level search)
- `POST /pointers/trigger-mention` (conversation mention hook)

### 🗂 Regionalized retrieval
- Mixed search defaults to hot/normal/cool (frozen excluded), full-DB fallback only if insufficient
- Type A project-id hash O(1) / Type B partition pointer / Type C full-DB vector fallback

### ⚠️ Real incident during release (fixed, lesson kept)

**Symptom**: after deploying to production, the **first write was a 500**.
```
asyncpg.exceptions.InvalidColumnReferenceError:
there is no unique or exclusion constraint matching the ON CONFLICT specification
```
**Root cause**: `dedup_fingerprint_key` is a **partial unique index** (`WHERE dedup_fingerprint IS NOT NULL`),
but the write uses `ON CONFLICT (dedup_fingerprint)` —— PostgreSQL's partial-index inference **requires the predicate to match explicitly**,
a missing predicate errors directly, taking down the whole write path.
**Fix**: `ON CONFLICT (dedup_fingerprint) WHERE dedup_fingerprint IS NOT NULL DO NOTHING`
(keep the partial index: it only constrains rows with a fingerprint; the 16k historical NULL rows need no backfill)

**Why the existing unit tests didn't catch it (the most important lesson of this release)**
- Existing tests only verified "the index can block duplicate fingerprints" (bare `INSERT`),
  and **never ran the real `INSERT ... ON CONFLICT` statement in the product code**.
- **Testing the index ≠ testing the SQL that uses the index.**
  Wherever "the SQL assembled in the code" is coupled with "the object built in the DB", you must extract the real SQL and run it.
- Fix: added `tests/test_v8_write_path.py` — **extract the real SQL from the `main.py` source via regex**,
  bind placeholder values and execute it against the DB. As soon as source and DB objects mismatch, it goes red immediately.
  Counter-evidence verified: temporarily removed the predicate → `test_w2` failed immediately.
- This time it was caught by the **third layer of release (production function test)** — this is the evidence that "triple verification" isn't formalism.

### 🧪 Tests
- test_rank_v73.py 13 items (Rank formula / S upgrade / drawer tiering)
- Full pytest 148 passed

## v7.2.0 (2026-08-09) — 🧠 Bjork S/R separation + production tuning

**Core idea**: forgetting ≠ losing. Storage strength S does not decay (info always there), retrieval strength R decays (accessibility drops, recoverable). Access resets R=S + S small increase (spaced-repetition effect). Web research: Bjork New Theory of Disuse + FSRS/Anki spaced repetition.

### 🧠 S/R dual strength (Bjork landed)
- **storage_strength S** (1-10, no decay): manual/pin=7, knowledge=5, normal=3
- **retrieval_strength R** (1-10, exponential decay half-life 30 days): R*0.5^(days/30), floor 1
- **Access reset**: after hit R=GREATEST(R, S) (heat_hits)
- **Drawer division new rule**: hot (S≥7 and R≥5) / normal (S≥5 or R≥3) / cool (S≥3) / frozen (S<3 and R<2)
- **Pin floor**: R never below 5 (permanent volume)
- **Rollback switch**: metadata->>'use_sr'='false' restores pure heat mode
- Effect: high-value memories never frozen (deep roots), low-value long-unaccessed auto-settle

### 🔧 Production tuning
- Enabled pg_stat_statements slow-query monitoring (was missing, biggest hidden risk)
- uvicorn workers 2→4 (stress-tested 234 req/s, 100/100 OK)
- perf_alert.py water-level inspection every 30min (memory/disk/PG connections/slow queries, alert only over threshold)
- PG parameters confirmed reasonable (shared_buffers 4G / effective 10G)

### ⚠️ Real incident during release (fixed, lesson kept)

**Symptom**: after deploying to production, the **first write was a 500**.
```
asyncpg.exceptions.InvalidColumnReferenceError:
there is no unique or exclusion constraint matching the ON CONFLICT specification
```
**Root cause**: `dedup_fingerprint_key` is a **partial unique index** (`WHERE dedup_fingerprint IS NOT NULL`),
but the write uses `ON CONFLICT (dedup_fingerprint)` —— PostgreSQL's partial-index inference **requires the predicate to match explicitly**,
a missing predicate errors directly, taking down the whole write path.
**Fix**: `ON CONFLICT (dedup_fingerprint) WHERE dedup_fingerprint IS NOT NULL DO NOTHING`
(keep the partial index: it only constrains rows with a fingerprint; the 16k historical NULL rows need no backfill)

**Why the existing unit tests didn't catch it (the most important lesson of this release)**
- Existing tests only verified "the index can block duplicate fingerprints" (bare `INSERT`),
  and **never ran the real `INSERT ... ON CONFLICT` statement in the product code**.
- **Testing the index ≠ testing the SQL that uses the index.**
  Wherever "the SQL assembled in the code" is coupled with "the object built in the DB", you must extract the real SQL and run it.
- Fix: added `tests/test_v8_write_path.py` — **extract the real SQL from the `main.py` source via regex**,
  bind placeholder values and execute it against the DB. As soon as source and DB objects mismatch, it goes red immediately.
  Counter-evidence verified: temporarily removed the predicate → `test_w2` failed immediately.
- This time it was caught by the **third layer of release (production function test)** — this is the evidence that "triple verification" isn't formalism.

### 🧪 Tests
- New test_bjork_v72.py 14 items (decay / reset / drawer / pin floor)
- Full pytest 128 passed

## v7.1.0 (2026-08-09) — 🗄️ Drawer-based memory (dual-track)

**Core idea**: forgetting is a retrieval-quality intervention, not a storage problem. Temperature drawer (hot / normal / cool / frozen) × time drawer (recent / mid / long) dual-track, heat axis + time axis + special markers automate forgetting. User inspiration: drawer-based memory (heat/normal/refrigerated × recent/mid/long) + compression denoise/dedup/distill merge; theoretical base: Bjork dual-strength theory (storage strength doesn't decay, retrieval strength decays) + Mem0 four forgetting strategies + SCM sleep-consolidated memory.

### 🗄 Dual-drawer fields (migration v7.1)
- **temp_drawer**: hot (≥0.7) / normal (0.3-0.7) / cool (0.1-0.3) / frozen (<0.1)
- **time_drawer**: recent (<30d) / mid (30-90d) / long (≥90d), based on COALESCE(last_accessed, created_at)
- CHECK constraint + local index + existing backfill (hot 75 / normal 1984 / cool 5247 / frozen 2048)

### 🧠 reflect enhancement
- Dual drawers auto-transition with reflect (every 4h light / daily deep)
- **Forgetting candidate markers**: frozen+long+non-pin+non-preference → forget_candidate=true
- Forgetting candidates extra cool-down -0.03 per round (accelerates settling, per the Mem0 salience idea)

### ✏️ Update endpoint (fills in the memory-modify authority)
- `PUT /api/v1/memories/{id}`: modify content/category/importance/heat_score/metadata/pin
- `PATCH`: partial-update alias; content change auto-recomputes vector + re-classify + logs trace
- pin=true forces heat≥0.5 (a pinned volume at least back to normal)

### 📊 Drawer API
- `GET /drawers/status`: dual-drawer distribution + forgetting candidate count + pinned count
- `GET /drawers/forget-candidates`: list forgetting candidates
- `POST /drawers/forget`: manual forgetting (keeps a statistical fingerprint, soft-delete recoverable)

## v7.0.0 (2026-08-06) — 🏰 Magic Memory Palace

**Core idea**: memory-palace method spatial encoding (wings / rooms / shelves / volumes) + archival description (project ids) + three-room division (research room refine / archive store / library search / Chinese-medicine cabinet summon).
User inspiration: library classification + archival description + Chinese-medicine-cabinet drawer recipes — memory-logistics wisdom validated by humans for centuries.

### 🏰 Palace architecture (new module `palace.py`)
- **Classification tree**: 7 wings (K knowledge / N network / D development / O ops / A assets / P people / I inspiration) × 20 rooms, aligned to the Chinese Library Classification
- **Project-id system**: `K·NET·PROXY·2026-0007` — numbering is location, precise locate
- **Catalog cards** (`tome_cards`): project id / title / summary / tags / retention period / source, standardized description
- **Three-channel summon** (`/palace/summon`): ①naming (project id/title/tag exact) ②guided (classification-tree narrowing) ③resonance (vector semantic)
- **Research-room fact extraction** (`/palace/extract`): conversation → discrete facts → auto-filing (reuses factextract)
- **LLM card refinement** (`/palace/refine`): title/summary/tags auto-generated
- **Permanence tiers** (`/palace/lifecycle`): permanent (no decay) / long-term (slow decay) / short-term (auto-removed after 90 days)

### 📚 Data refresh
- Total memories 2775 → **8647** (100% archive rate)
- Structured facts **6231** (knowledge 5230 + preference 1001), conversation-fragment share 88% → 27%
- Catalog cards **8614** cards, classification tree 30 nodes
- Full fact extraction: 2319 conversations → 6231 facts (research-room pipeline)

### 🔌 Hermes adaptation
- `sync_turn`: conversation stored as session class (2000/3000 chars), no longer stores chat fragments
- `on_session_end`: auto-trigger research-room extraction at session end
- `mnemosyne_palace_summon` tool: three-channel summon direct to Hermes
- `system_prompt_block`: shows palace status (coverage / cards / classification tree)

### 🛠 Fixes
- find_candidates filters short content + skip marks processed (prevents dead loops)
- SQL explicit public schema (prevents search_path ambiguity)
- insert_fact returns id (eliminates SELECT race)
- Classification keywords 14→20 classes, security priority (unfiled halved)

### 📈 Performance
- Summon measured: xray / deploy / keys / Memory Palace all hit (0.1-0.4s)
- Card refinement: 380 cards, 0 failures

## v6.4.0 (2026-08-05)

### 🧬 Fact-extraction pipeline — fills in the "personal-info memory" dimension

**Motivation**: LongMemEval evaluation attribution (#6293) — the real gap = missing the "fact-extraction" layer (compared to Mem0 extract).
Conversation memory (session/worklog) only has knowledge distillation, no personal-info facts (graduation / commute / shift / preference).

**Changes**:
- `tmt/factextract.py` added: conversation → extract user facts (personal info / preference / events / schedules / capabilities) → preference/knowledge stored
  - Short-text per-item extraction (Doubao lite long-session misses deep answers — evaluation measured)
  - Non-json mode (Doubao json_mode long prompt >2500 chars returns empty — evaluation measured)
  - ANN dedup gate (>0.92) + heat 0.65 + provenance metadata
  - 1 failure retry, no-fact marked skip, failures re-recovered next round
- cron: daily 02:00 batch 60 (cost ~¥0.09/day)

**Verify**: real batch 30 → +14 facts stored (quality spot-check: user preference/plan/event accurate).
LongMemEval retest: the extraction layer still misses deep detail on super-long conversations (13k chars) — confirmed as the Doubao lite model's extraction capability boundary (official uses GPT-4 class); the pipeline is effective for real short conversation memory.

## v6.3.0 (2026-08-05)

### 🧠 Cognitive write signals — important memories are hot from birth

**Motivation**: v6.2 solved "hits don't heat up", but the write end has no cognitive signal — "to-do" and "greeting" have the same initial heat; important knowledge washed out over time.

**Design source**: Noah 3rd-gen "drawer cascade compression engine" temperature = hit count + importance bonus table (code-locked, not via LLM).

**Changes**:
- `main.py` compute_write_heat: on write, regex signal detection → initial heat bonus
  uncompleted task +0.15 / user correction +0.10 / pitfall lesson +0.10 / decision scheme +0.08 / path/API +0.05 / importance mark +0.05 / preference|knowledge|pitfall class +0.10 (cap 0.8)
- `main.py` reflect heat v2: protected decay — pinned/preference only -0.005 per hit (normal by time -0.01~-0.08)
- `tmt/distill.py`: pitfall distillation storage heat=0.70 (pitfalls are inherently important)

**Verify**: write signals (to-do 0.65 / greeting 0.5 / preference 0.6) + protected decay (preference -0.005 vs session -0.02) both measured pass.

### ⚠️ Key cognitive correction
- reflect actually runs main.py's "heat v2 multi-dim subtraction decay"; tmt/router.py's `tmt_decay` is an independent API path (multiplication) — to change heat you must look at main.py reflect first

## v6.2.0 (2026-08-05)

### 🔥 Cognitive heat engine — memory gets hotter with use

**Motivation**: measured avg_heat=0.10 / L1 hot points only 1. Root cause: heat has only initial value + pure decay, no usage signal (search hit doesn't heat up).

**Changes**:
- `main.py` search_memories: hit top5 → access_count+1 + heat+0.05 (LEAST 1.0)
- `tmt/router.py` tmt_recall: final returned memories source top3 heated
- `tmt/router.py` tmt_decay: differential decay — recently-accessed (48h) memories ×0.995, others ×0.98 (active keeps hot)
- Design source: Explosion Legacy Archaeology noah dual-weight heat formula (0.6 time decay + 0.4 frequency) practical simplified version

### 🧪 Distill enhancement (distill.py)
- Distillation storage heat_score=0.65 (new-knowledge heat signal, default 0.5)
- In-batch dedup: in-batch similar summaries cross-check (substring match), prevents in-batch duplicate refinement

### 🛡 Health-monitoring backup freshness
- `mnemosyne-health-monitor.sh` added backup freshness check: >10 days no new dump or 0 backups → WeChat alert (prevents silent memory loss)
- Fix: the production hardening version wasn't previously synced back to the repo (version consistency)

### 📌 Known issues (external deps, not fixed)
- TMT L3 distillation failed 3 days in a row (Doubao API 400/timeout fluctuation) — the model is effective (direct test 200), cron daily auto-retry + health monitor already alerted

## v6.1-dev (2026-08-05)

### 🧪 Knowledge distillation pipeline (P0-1, MVP)

**Motivation**: classification imbalance (session+worklog 88%, knowledge 9%) — distillation only did classification/organizing, not real knowledge refinement.

**New**: `tmt/distill.py` — knowledge distillation pipeline v0.1
- Design source: Explosion Legacy Archaeology (NCP-008 knowledge-absorption seven steps + cognitive AI base TEL/MAIL protocol)
- Flow: signal-word candidate screening → TEL assembly → Doubao Lite JSON condensation → ANN dedup gate (skip >0.92) → store (knowledge→archive / pitfall→engineering) → metadata provenance
- Usage: `python3 tmt/distill.py --batch N [--dry-run] [--stats]`

**Deploy**: production cron daily 1:10 batch 60 (first run 30: +22 knowledge, +1 pitfall, 5 Doubao occasional empty returns, re-recovered next round)

## v6.0.1 (2026-08-02)

### ⚡ Production performance & stability — dual workers + recall fault-tolerance

**Concurrency isolation**
- `main.py` uvicorn `workers=1 → 2`: 3 processes share 8010 (1 parent + 2 workers), slow requests (recall LLM distillation) no longer block search (measured search dropped from 13s+ queue to a stable 1.2-2.5s)

**Recall 3-layer fault tolerance** (`tmt/router.py` + `core/llm.py`)
- Complexity classification changed to **heuristic** (keyword/length judgment 0/1/2), no longer calls LLM: recall frequent queries 30s → 0.4s (embedding cache hit)
- Gate filter LLM failure **degrades to keep all candidates** (wrapped in try/except), no longer 502
- `_call_ark` Doubao timeout 60s → 15s; call_llm connection-class errors (URLError/TimeoutError/OSError) don't escalate tier, fast-fail directly (prevents 15s×3 retry amplification)
- Effect: recall new-query worst ~16s (Doubao embedding slow, external dep), no longer 60s timeout/502

**Notes**
- Release closed loop: upgrade-report review ✓ user acceptance ✓ GitHub Release + tag ✓
- Production backup: `main.py.bak.20260802` / `core/llm.py.bak.20260802` / `tmt/router.py.bak.20260802(.2/.predegrade)`

## v6.0.0 (2026-08-02)

### 🎯 Core — concept model restructure: classification controlled + pipeline fix + dedup speed-up

**Controlled classification system (fixes classification chaos)**
- New controlled vocabulary 10 classes: `knowledge` / `pitfall` / `reference` / `project` / `ops` / `deploy` / `preference` / `session` / `worklog` / `temp`
- Write-gateway auto-normalization: old Chinese/English categories (18 classes) → 10-class primary keys, unknown category → knowledge (substring + full-match double rule, 24/24 cases)
- DB added `chk_memories_category` CHECK constraint; category drift henceforth intercepted at the DB layer
- Existing migration: 2295 memories 18 classes → 10 classes, user_id all converge to `default` (single-user semantics)

**TMT distillation pipeline fix (pipeline previously spun empty)**
- Fixed L2 session distillation parameterless-query deadlock: 24h window + most recent 100 undistilled fragments fallback, pipeline no longer spins empty
- Clarified dual-level semantics: `tier` = value tier (reflect heat maintains), `tmt_level` = TMT time-tree level (write=1)
- reflect L4 no longer directly soft-deletes memory, changed to `forgotten_at` mark (keeps recoverable)

**Reflector performance + dedup fix**
- Redundancy detection O(n²) Python per-pair comparison → pgvector ANN neighbor query: **33 minutes → 5 seconds (400× speedup)**
- Similarity threshold 0.92 → 0.85: fixed the dedup-failure problem where similar phrasings weren't merged (first round merged 16 redundants)

**Cron fix**
- Monthly distillation JSON decode error root cause: `date +%m` leading-zero `08` is an illegal JSON number → `date +%-m`

**Ops cleanup**
- Removed historical leftover `tmt_*_old` tables (4)
- Local repo regularization: only keep `~/mnemosyne-dev` as the unique source of truth

## v5.5.2 (2026-07-29)

### Fix — semantic search NULL embedding filter

- **Fix**: added `AND m.embedding IS NOT NULL` to 5 search SQL, prevents NULL-vector old records ranking ahead of new ones
- **Root cause**: after migrating to Doubao embedding, the old records' vector column is NULL, SQL sorting puts old data before new data
- **Impact**: after fix, new memories 100% searchable, no longer drowned by historical noise
