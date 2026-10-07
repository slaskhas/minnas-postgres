# openspec · Spec Layer

> Two layers: `specs/` = **what the system does now** (truth); `changes/` = **what's currently being changed** (in flight).

```
openspec/
├── specs/                    # Current truth (authoritative)
│   └── <domain>/spec.md
├── changes/                  # In-flight changes, one folder per change
│   ├── <change-name>/
│   │   ├── proposal.md       # Why change / what changes / what doesn't / acceptance
│   │   ├── design.md         # How it's technically implemented
│   │   ├── tasks.md          # Checkable tasks
│   │   └── specs/delta.md    # Delta: ADDED / MODIFIED / REMOVED
│   └── archive/              # Completed (date-prefixed)
└── config.yaml
```

## How to use it

1. **Before changing behavior**: `mkdir -p openspec/changes/2026-09-24-<change-name>`, copy the template and write `proposal.md` (even three lines is fine)
2. **After the change**: check off `tasks.md` → merge the delta into `specs/` → move the whole change directory into `archive/`
3. **Never**: write a "full baseline" doc for code that already exists (unverified documentation starts rotting from day one)

## Domain index (current capability map, see [specs/](specs/) for details)

| Domain | Contents | Spec |
|---|---|---|
| memory | Memory write/recall/lifecycle | TBD (to be established at next change) |
| palace | Palace classification/accession numbers/catalog cards/three-channel recall | TBD |
| distill | TMT distillation/fact extraction/merging | TBD |
| wiki | Knowledge base retrieval (BM25 + vector RRF) | TBD |
| sync | Edge-cloud sync (SQLite↔PG) | TBD |
| integrations | REST / SDK / MCP / Hermes Memory Provider | TBD |
| ops | Scheduled jobs/backup/health monitoring | TBD |

> ⚠️ The table above is a **capability map**, not a spec. Specs are established incrementally, around real changes, following brownfield principles.
