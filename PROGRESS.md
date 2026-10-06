# Mnemosyne OS Status

## Status: v8.0.0 — 267 tests green locally; production deployed (with two corrections), P4 retest pending

## Current Version: 8.0.0 — "write right · recover back · find precisely · clarify"

> User directive 2026-09-25: defects found in the testing phase are **absorbed within the same version number**, no new version added (hence no 8.0.1).

## This Release (see CHANGELOG for details)
- [x] S1-1 Write atomicity (single-transaction wrapping)
- [x] S1-2 Idempotency key (**layered**: L0 uses source context / hour bucket; L1~L4 content fingerprint)
- [x] Conflict detection **routed by layer** (L0 skips semantic merge, keeping the "only-add-never-modify" contract)
- [x] S1-3 Memory GC + cold archive (**full snapshot of four tables**) + CSV vouchers + batch restore + integrity assertion
- [x] S1-3 Integrity inspection (read-only)
- [x] S1-4 Latency instrumentation + `/api/v1/metrics`
- [x] S1-4 Backup recoverability re-verification
- [x] S2-1 Four-channel RRF fusion (off by default, awaiting evaluation)
- [x] S3-1 Memory layering model (executable spec, write-path wired)
- [x] S3-2 Release pipeline state machine (7 stages + 3 gates, `publish` is the sole entry point)
- [x] S3-3 Artifact pointer policy

## Not Done (outstanding)
- [ ] Open-source readiness (setup.sh / INSTALL.md updates / external-perspective docs)
- [ ] Recall evaluation baseline (E3 delivered a 150-item design, not yet run)
- [ ] Layering-model decisioning (layer is currently only annotated, not yet feeding decisions)
- [ ] RRF tie-break direction

## Deployment & Rollback
- Deploy path: content-level reconciliation → backup (verify recoverability) → minimal coverage → three-way verification → service self-reported version re-check
- Rollback: restore the whole directory from `/opt/mnemosyne/.pre-deploy-<stamp>/` + `systemctl restart mnemosyne`
