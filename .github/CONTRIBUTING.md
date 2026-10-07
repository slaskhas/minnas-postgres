# Contributing Guide

## Development workflow (iron rule)

```
Local development → Verify → Security audit → Docs update → git tag → push
```

## Red lines

1. **Never hardcode secrets** — API keys / tokens / passwords
2. **Never leak environment info** — real IPs / domains / local paths / usernames / internal codenames (e.g. production environment codenames)
3. **Privacy scan required before every push** (see the `git-privacy-audit` workflow)

## Repo layout conventions (new files must follow this)

```
Mnemosyne-OS/
  ├── main.py                 Service entrypoint + core routes (main logic only)
  ├── config.py               Unified config center (env vars)
  ├── palace.py                Palace core (categorization/archive numbers/cards/recall)
  ├── api/                    REST API modules
  ├── core/                   Core engine (LLM/Embedding/Chunker/backends)
  ├── tmt/                    Distillation engine (factextract/distill/router)
  ├── wiki/                   WIKI knowledge-base module (bm25/graph/extract/tokenize/dedupe/eval)
  ├── security/               Security audit & sanitization
  ├── integrations/           Hermes integration (Memory Provider + MCP + SDK)
  ├── sync/                   Edge-cloud sync (SQLite ↔ PG)
  ├── cron/                   Scheduled scripts
  ├── docs/                   Docs (whitepaper/design/schema)
  ├── deploy/                 systemd deployment templates
  ├── tests/                  pytest test cases
  └── scripts/                One-off ops scripts
```

**Placement rules:**
- Module code → its matching subdirectory (wiki-related code must live in `wiki/`, not scattered at the repo root)
- Standalone ops scripts (reflector/drawer/perf_alert) → repo root (production crontab binds to absolute paths)
- Docs → `docs/`, poster source file `docs/poster.html`
- New modules: create the directory first, then add files — don't pile things up at the repo root

## Editing & release rules (follow for every change)

1. **Version number consistent in three places**: `VERSION` / README badge / CHANGELOG (must be updated together when bumping)
2. **Keep docs in sync**: when adding new documentation, add it to the Documentation table in README
3. **CHANGELOG entries**: add a new entry at the top for every change (categorize as added/rewrite/fix/removed/redacted)
4. **ROADMAP alignment**: check off planned items when done and move them to the "Released" section
5. **Production sync**: changes to runtime files like main.py / wiki/ must be rsynced to the production server + restarted + verified with an echo check; changes to wiki_* paths must be synced to crontab
6. **Tests**: `pytest tests/` must be fully green (167+ cases)
7. **Security audit**: full scan before push, must be zero hits before pushing is allowed

## Commit conventions

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `chore:` misc
- `release:` version release
- Commit message should include a summary of changes + key details (follow the style of existing git log history)

## Version management

- Semantic versioning: MAJOR.MINOR.PATCH
- VERSION file + CHANGELOG.md + README badge updated together in all three places
- Tag: `git tag -a vX.Y.Z -m "description"`

## Testing

```bash
pytest tests/          # 167+ cases, must be fully green
```

## Security

- Run a privacy scan before every push
- GitHub Secret Scanning is enabled
- See AGENTS.md for more
