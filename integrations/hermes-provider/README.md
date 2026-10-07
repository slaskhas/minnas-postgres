# Mnemosyne Memory Provider

> The official Memory Provider that gives **Hermes Agent** access to the Mnemosyne OS memory palace.
> Version: v7.8.2 | Compatible with: Hermes Agent (ABC MemoryProvider protocol)

---

## What this is

A long-term memory plugin for Hermes Agent. Once enabled, your Agent automatically gets:

- 🪄 **11 memory tools** (including the three-channel summon `mnemosyne_palace_summon`)
- 🔄 **Automatic hooks**: per-turn sync, end-of-session distillation, pre-compression archiving, subtask logging
- 🧠 **Heat management**: important memories heat up, noise decays
- 🏰 **Palace organization**: category tree + accession numbers + catalog cards

Comparable to: Mem0 / Zep / Honcho. What's different: palace-style organization + 11 tools + crash-safe write queue + circuit-breaker protection.

---

## Quick setup

```bash
# 1. Prerequisite: the Mnemosyne OS service is already running (see the repo's INSTALL.md)
#    Local: http://127.0.0.1:8010  |  Remote: SSH tunnel mapped to 18010

# 2. Configure Hermes to use the mnemosyne provider
hermes config set memory.provider mnemosyne

# 3. Optional: specify the service address (defaults to http://127.0.0.1:18010)
#    In the profile's .env:
echo "MNEMOSYNE_ENDPOINT=http://127.0.0.1:8010" >> ~/.hermes/.env
echo "MNEMOSYNE_USER_ID=default" >> ~/.hermes/.env

# 4. Restart Hermes (or /reset) — the tools appear automatically
```

---

## Configuration

| Env var | Default | Description |
|---------|------|------|
| `MNEMOSYNE_ENDPOINT` | `http://127.0.0.1:18010` | Mnemosyne API address |
| `MNEMOSYNE_USER_ID` | `default` | User ID (for multi-user isolation) |
| `MNEMOSYNE_API_KEY` | none | If the service requires auth (`X-API-Key` header) |

---

## The 11 tools

| Tool | Purpose |
|------|------|
| `mnemosyne_palace_summon` | 🏰 Three-channel summon (name / guide / resonance) — the magic front-of-house |
| `mnemosyne_search` | Four-dimensional search (semantic + keyword + heat + graph) |
| `mnemosyne_recall` | Smart recall (cross-tier L1→L3) |
| `mnemosyne_remember` | Actively store a memory |
| `mnemosyne_dialectic` | Dialectical retrieval (with session context) |
| `mnemosyne_hot_memories` | Currently hot memories |
| `mnemosyne_tree` | Browse the memory tree (TMT tiers) |
| `mnemosyne_tiered_read` | Three-tier read (L5/L3/L1) |
| `mnemosyne_conflicts` | List detected conflicts |
| `mnemosyne_wiki` | Knowledge-base retrieval |
| `mnemosyne_media` | Media memories |

---

## Automatic hooks (ABC protocol)

| Hook | Trigger | Behavior |
|------|------|------|
| `sync_turn` | Every conversation turn | Writes the current turn's memory (write queue) |
| `on_session_end` | Session ends | TMT L2 distillation + fact extraction |
| `on_turn_start` | New turn | Prefetches relevant memories into context |
| `on_pre_compress` | Before context compression | Archives key insights to prevent loss |
| `on_delegation` | Subtask completes | Logs the task + result into the palace |
| `on_memory_write` | Built-in memory write | Mirrors it to Mnemosyne |
| `on_session_switch` | Session switch | Flushes the write queue |

> Crash-safe write queue + circuit-breaker protection: unique in the field — no memory is lost even if Mnemosyne is briefly unavailable.

---

## File overview

| File | Purpose |
|------|------|
| `__init__.py` | Provider body (ABC protocol implementation + 11 tools) |
| `write_queue.py` | Crash-safe write queue + circuit breaker |
| `message_cleaner.py` | Message cleaning (dedup / truncation / sensitive-content filtering) |
| `VERSION.md` | Version history |
| `CHANGELOG.md` | Changelog |

> MCP bridge (standard MCP protocol): see `../hermes-mcp/mnemosyne_mcp.py`, for use with non-Hermes frameworks.

---

## Compatibility notes

- Requires Mnemosyne OS service v5.3.0+ (v7.x recommended)
- Depends on Hermes's `agent.memory_provider.MemoryProvider` base class
- No local model dependency: all LLM/embedding calls go through the Mnemosyne service

---

*Mnemosyne OS · Memory isn't meant to be stored — it's meant to be lived.*
