# Memory Layering Model (Capability truth · Executable spec)

> Authoritative implementation: `core/layers.py` | External entry: `GET /api/v1/layers` · `GET /api/v1/layers/classify`
> Write path is wired up: `POST /api/v1/memories` sets `metadata.layer` / `metadata.layer_family` on every write
> Status: **active** (v8.0.0) | Origin: user dictation on 2026-09-24 → normalized → made executable

## 1. The judgment axis (there's only one)

**Whether contradiction is allowed to exist.**

This splits memory into three families and five layers. The split isn't by "content topic" but by
**governance style** — for two records on the same topic, if one must "only trust the latest" while
the other "allows contradiction," they don't belong in the same layer.

## 2. Five layers + one cross-cutting concern

| Layer | Name | Family | Carrier | Write rule | Conflict policy | Controlled category |
|---|---|---|---|---|---|---|
| **L0** | Log layer | Append-only | `state.db` (full raw text + tool_calls) + Mnemosyne `session` archive | Append-only; denoise only, **never compress the body** | **Contradiction allowed**, used as reference, no verdict on who's right | `session` `temp` |
| **L1** | Cognitive layer | Versioned · latest wins | Mnemosyne `knowledge` / `beliefs` + `MEMORY.md` / `USER.md` | Updatable, **must carry a source**; updates leave a changelog | Conflict → update to new value + record "changed from X to Y" | `knowledge` `preference` |
| **L2** | Skill layer | Versioned · latest wins | `skills/` files + trigger-based injection | File-versioned; old versions go to `.archive` (not deleted, source kept) | Latest wins; old versions can be recalled but no longer injected | `pitfall` `ops` `deploy` |
| **L3** | Constraint layer | **Manually finalized** | ⚠️ **Not in the database**: `SOUL.md` / `MEMORY.md` / `config.yaml` | Manually finalized; changes are recorded | **Coexistence forbidden** — two constraints cannot be in effect at the same time | (none) |
| **L4** | Reference layer | Versioned · latest wins | `wiki_pages` + box files + project docs (ZH+EN / ADR) | Versioned; **only pointers + fingerprints go in memory, never the entity itself** | Latest wins + pointer back-link | `reference` `project` `worklog` |
| Cross-cutting | **Artifact index** | — | Not a layer, referenceable from every layer | An entity exists exactly once, pointers can be in multiple places | See §4 | — |

### Why L3 has no in-database category (stated plainly)

The carrier for constraint memory (personality / rules / Hermes built-ins) is `SOUL.md` / `MEMORY.md` /
`config.yaml` — **manually finalized config files**, not database rows. This isn't a model shortcoming;
it's evidence that the "layering model" and the "storage carrier" are deliberately separate. `self_check()`
has an assertion locking this in — if anyone stuffs a category onto L3 for convenience, the test goes red.

### Two points of divergence from the user's original words (for review)

1. **"Factual memory" is folded into L2/L3** rather than kept as its own layer — in the user's original
   words, the content of "factual memory" was actually "skills, constraints."
2. **The cognitive layer is classified under the "latest wins" family** (with confidence scores allowed).
   If the "evolution history of cognition" should be preserved instead, it would become a **third family**,
   and `core/layers.py` would need to be extended accordingly. **This point is still pending a decision.**

## 3. Write-path layering (already wired up)

Every write through `POST /api/v1/memories` runs `classify_layer()`, writing `layer` /
`layer_family` into `metadata`; when L1 is written without a `source`, a `layer_note` hint is attached.

**Acceptance criterion (falsifiable)**: for any memory written after v8.0, `metadata->>'layer'` must be
non-null. If it's null, layering has degenerated into documentation — this is this spec's "acceptance anchor."

```sql
-- Production check: should return 0 rows (written after v8.0 but missing a layer tag)
SELECT count(*) FROM memories
WHERE created_at > TIMESTAMPTZ '2026-09-25' AND metadata->>'layer' IS NULL;
```

## 4. Where artifacts → entities go, and where pointers live

The distinction comes down to one question: **who is it for?**

| Entity type | Where it goes | Reason | What stays in memory |
|---|---|---|---|
| To be delivered / double-clicked open / shown externally | **Box** (inbox → archive) | Matches the user's existing habits, double-click to use | Absolute path (Windows format) + size + sha256 |
| To be retrieved / repeatedly referenced by AI | **Wiki** | Searchable, versionable, recallable across sessions | Wiki page name/ID + summary |
| Code / repo artifacts | **Repo + tag** | Versioning handled by git | tag/commit + live URL |

The common thread for all three: **only pointers + fingerprints go in memory, never the entity itself**.
An entity exists exactly once; pointers can be in multiple places.

## 5. Non-goals

- Not creating new storage tables to "implement layering" — the carriers for all five layers **already
  exist**; this layer's job is **classification and routing**.
- Not calling an LLM on the write path for classification (zero-token principle); layering is a pure
  rule-based mapping.
- Not letting L0's "contradiction allowed" leak into other layers (it would pollute cognition).

## 6. Change log

| Date | Change | Basis |
|---|---|---|
| 2026-09-25 | Established the executable spec (`core/layers.py` + write-path wiring + 10 spec tests) | Proposal P-20260925-01 §S3-1 |
