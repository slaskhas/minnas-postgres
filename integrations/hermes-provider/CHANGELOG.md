# Mnemosyne Memory Provider — CHANGELOG

## v7.7.0 (2026-08-11)

### Sync
- Synced to Hermes's actual running version (plugins/memory/mnemosyne)
- Added `mnemosyne_palace_summon` tool (three-channel summon, v7 core)
- Enhanced wiki methods (search_wiki / get_wiki_by_source)
- Write filtering (skip low-value messages) + first-turn cold start
- Compatible with Hermes v0.19.0+

### Tool count
- 10 → 11

---

## v1.1.0 (2026-07-29)

### Added
- Write filtering: skip low-value messages (keep noise out of the palace)
- First-turn cold start: inject relevant memories on a new session's first turn
- Formatting improvements: cat_emoji summary style

---

## v1.0.0 (2026-07-06)

### Initial release
- 10 tools (search/remember/recall/tree/hot/dialectic/tiered/conflicts/wiki/media)
- sync_turn auto-stores every conversation turn (write queue + circuit breaker)
- prefetch/queue_prefetch background prefetch + injection
- system_prompt_block hot-memory injection (time decay)
- on_session_end triggers TMT L2 distillation
- on_memory_write built-in memory mirroring
- on_session_switch queue flush
- Crash-safe write queue + circuit-breaker protection (unique in the field)
