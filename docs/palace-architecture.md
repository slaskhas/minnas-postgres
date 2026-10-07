# Magic Memory Palace · Architecture Design

> v7.0.0 | 2026-08-06
> Core idea: method-of-loci spatial encoding + archival-science cataloging + three-chamber division of labor

## I. Design Lineage (Human Wisdom)

| Source | Wisdom | Mapping |
|------|------|------|
| Library (Dewey Decimal) | Number IS position; new books auto-shelve | Archive-number system `K·NET·PROXY·2026-0007` |
| Archive (DA/T18) | Layered accession numbers + standardized cataloging + separation of raw material and description | `tome_cards` description cards |
| Chinese medicine cabinet | Position registry + labels + frequent items placed within reach | Category tree + three-channel summon |
| Method of loci (2500 years) | Spatial encoding; retrieval = walking the palace | Wing/Room/Shelf/Tome hierarchy |

## II. Palace Structure

```
LOBBY           → high-frequency memories (always-injected, grab-and-go)
WING            → top domain: K knowledge / N network / D dev / O ops / A assets / P people / I ideas
ROOM            → mid category (20 rooms)
SHELF           → sub-category/topic
TOME            → a single piece of knowledge (description card + archive-no + full-text pointer)
Underground archive → raw records, full fidelity (Hermes state.db)
```

## III. Three-Chamber Division of Labor

| Chamber | Function | Implementation |
|----|------|------|
| 🕵️ Research room | dialogue → fact distillation | `/palace/extract` (factextract pipeline) |
| 🏛️ Archive | categorize + catalog | `tome_cards` + archive-no + category tree |
| 📚 Library | retrieval/summon | `/palace/summon` three-channel |
| 🍵 Medicine cabinet | high-frequency fast access | category-tree guidance + archive-no lookup |

## IV. Three-Channel Summon

```
① Name (exact): archive-no/title/tag ILIKE direct hit → <100ms
② Guide (range): category-tree wing/room keyword matching → narrows scope
③ Resonate (fuzzy): vector search (pgvector HNSW) → semantic fallback
```

## V. Data Model

- `archive_taxonomy`: category tree (wing/room/shelf)
- `tome_cards`: description cards (memory_id, title, summary, archive_no, wing, room, shelf, tags, retention)
- `entities` / `memory_entities` / `wiki_entities`: entities and associations (replaces the lightweight `tome_links` graph since v7.8; AGE has been removed)
- `memories.archive_no`: archive-number column

## VI. Lifecycle (Permanence Tiers)

| Tier | Decay | Cleanup |
|------|------|------|
| permanent | 0.0 (never decays) | never cleaned up (rules/red-lines/identity) |
| long | 0.999 (very slow) | no auto-cleanup (knowledge/projects) |
| short | fast decay | auto-removed after 90 days (temporary notes) |

## VII. Hermes Integration

- `mnemosyne_palace_summon`: three-channel summon tool
- `sync_turn`: dialogue → session records (lossless, 2000/3000 chars)
- `on_session_end`: auto-triggers research-room extraction
- `system_prompt_block`: palace-state injection (coverage/cards/category tree)

## VIII. Key Metrics (v7.0.0)

- Total memories 8647 | archival rate 100% | cards 8614 | category tree 30 nodes
- facts 6231 (knowledge 5230 + preference 1001)
- Summon: xray/deployment/secrets/memory-palace all hit (0.1-0.4s)
