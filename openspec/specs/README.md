# specs/ · Current Truth

Holds the specs for **what the system does now**, split by domain.

Format (one Requirement = one verifiable behavior):

```markdown
## Requirement: <Capability name>
The system SHALL <specific behavior>.
**Acceptance**: <how to tell it's done — testable, observable>
```

When to establish: **the first time this domain is touched**. After the change, merge
the ADDED/MODIFIED items from `changes/<name>/specs/delta.md` in, and delete the REMOVED ones.
