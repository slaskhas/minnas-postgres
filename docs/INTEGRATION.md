# Integration Guide (3 Ways)

> Lifted out of `AGENTS.md`. 5-minute integration for any framework.
> Back to [AGENTS.md](../AGENTS.md)

## Quick Integration (3 Ways)

### Option 1: REST API (works in any framework)

```bash
# Health check
curl http://127.0.0.1:8010/api/v1/echo

# Store a memory
curl -X POST http://127.0.0.1:8010/api/v1/memories \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"default","content":"knowledge to remember","category":"knowledge"}'

# Search memories (five-dimensional: vector + BM25 + time + trust + heat)
curl -X POST http://127.0.0.1:8010/api/v1/memories/search \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"default","query":"keyword","top_k":5}'

# Three-channel summon (palace core)
curl "http://127.0.0.1:8010/api/v1/palace/summon?q=keyword&user_id=default&top_k=5"
```

### Option 2: Python SDK

```python
from integrations.sdk import MnemosyneHermesMemory

m = MnemosyneHermesMemory(endpoint="http://127.0.0.1:8010")
m.add("knowledge content", category="knowledge")
results = m.get_relevant("query")
m.search_by_hall("archive")       # verified knowledge
m.search_by_hall("engineering")   # pitfall notes
```

### Option 3: Hermes Agent (native Memory Provider)

```bash
hermes config set memory.provider mnemosyne
# You get 11 tools automatically:
#   mnemosyne_palace_summon · mnemosyne_search · mnemosyne_recall
#   mnemosyne_remember     · mnemosyne_dialectic · mnemosyne_hot_memories
#   mnemosyne_wiki         · mnemosyne_tree      · mnemosyne_media
#   mnemosyne_tiered_read  · mnemosyne_conflicts
```

---

---

## Hermes Integration (Memory Provider)

### Configuration

```yaml
# ~/.hermes/config.yaml
memory:
  provider: mnemosyne
  config:
    endpoint: "http://127.0.0.1:18010"   # or remote via SSH tunnel
```

### SSH Tunnel (remote deployment)

```bash
ssh -L 18010:127.0.0.1:8010 your-server
# Hermes's mnemosyne tools reach it automatically via 127.0.0.1:18010
```

### MCP Bridge (optional, standard MCP protocol)

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  mnemosyne:
    command: "python3"
    args: ["/path/to/mnemosyne_mcp.py"]
    enabled: true
```

> MCP server source: `integrations/hermes-mcp/mnemosyne_mcp.py`

### MCP in-process endpoint (/mcp, streamable HTTP · v8.1)

Since v8.1, the 15 Mnemosyne tools are also mounted directly in the core's uvicorn
process (`main.py` mounts them automatically) — **no stdio subprocess, no SSH tunnel**.
Any streamable-HTTP client can connect to `http://<host>:8010/mcp`.
The stdio bridge is retained (existing stdio client configs / `mcp_adapt_test.py` still work);
both share the same set of contract-tested handlers.

```bash
# Initialize (returns an SSE stream)
curl -i -N -X POST http://127.0.0.1:8010/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}'
# -> 200; SSE data contains {"server":{"name":"mnemosyne"}}
```

> `stateless_http`: each request is independent -> safe under `uvicorn --workers N`.
> If the `mcp` SDK is missing, the core skips this mount (DEBUG log); the REST API is unaffected.

### Automatic Hooks (built into the Memory Provider)

| Hook | Trigger | Effect |
|------|---------|--------|
| `sync_turn` | Each conversation turn | Memory sync |
| `on_session_end` | Session ends | Distillation + fact extraction |
| `on_turn_start` | New turn | Prefetch and inject relevant memories |
| `on_pre_compress` | Before compression | Archive to prevent loss |
| `on_delegation` | Subtask | Record subtask memories |
| `on_memory_write` | Memory write | Mirror to Mnemosyne |

---
