# Agent Usage Best Practices

> Lifted out of `AGENTS.md`.
> Back to [AGENTS.md](../AGENTS.md)

## Best Practices (for Agents)

1. **Store**: important decisions / pitfalls / user preferences → `POST /memories`, with a `category` (10-category list below)
2. **Retrieve**: everyday queries use `POST /memories/search`; palace capabilities use `palace/summon`; time-based questions use `GET /memories?sort=created_at` (**heat ≠ recency**)
3. **Category list** (10 categories, enforced by a database CHECK constraint):
   `knowledge` · `pitfall` · `reference` · `project` · `ops` · `deploy` · `preference` · `session` · `worklog` · `temp`
4. **Multi-user**: `user_id` provides natural isolation (`alice` / `bob` can't see each other's data)
5. **Distillation**: run `POST /reflect?mode=light` on a schedule (no LLM cost); use `mode=deep` for deep distillation
6. **Don't store**: code/scripts (put them in git); ephemeral state (keep it in the session); intermediate values that can be recomputed directly

---
