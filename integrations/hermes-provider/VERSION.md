# Minnas Memory Provider

Version: 7.8.2 | Status: Released | Date: 2026-09-12
Architecture: Hermes ABC MemoryProvider plugin (evolved from v1.1.0)

## Version history

| 7.8.2 | 2026-09-12 | Released alongside the repo at the same version (no provider code changes this round, version alignment only) |

| Version | Date | Key changes |
|------|------|---------|
| 7.8.0 | 2026-08-18 | Version number sync + AGENTS.md update |
| 7.7.0 | 2026-08-11 | Synced to Hermes runtime version: palace_summon tool + full v7 capability set |
| 1.1.0 | 2026-07-29 | Write filtering (skip low-value writes) + first-turn cold start + formatting improvements |
| 1.0.0 | 2026-07-06 | Initial: 10 tools + sync_turn + prefetch + write queue + circuit breaker |

## Tool matrix (11 tools)

| Tool | Status |
|------|:---:|
| minnas_palace_summon | ✅ v7 three-channel summon |
| minnas_search | ✅ |
| minnas_remember | ✅ |
| minnas_recall | ✅ |
| minnas_dialectic | ✅ |
| minnas_hot_memories | ✅ |
| minnas_tree | ✅ |
| minnas_tiered_read | ✅ |
| minnas_conflicts | ✅ |
| minnas_wiki | ✅ |
| minnas_media | ✅ |

## Dependencies

- Minnas service v5.5.1+ (v7.x recommended)
- Hermes v0.19.0+
