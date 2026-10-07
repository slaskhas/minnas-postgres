#!/usr/bin/env python3
"""v8.0.1 · idempotency-key tiering tests (pure functions)

Background (flagged by the red team → confirmed on review): the initial version used a
content fingerprint for all categories, which directly contradicts the L0 contract of
"append-only, contradictions allowed" — the same text appearing at different times would
be wrongly deduped.
"""
import pytest

from main import compute_write_fingerprint as fp
from main import should_run_conflict_detection as det


def test_l0_same_session_retry_dedupes():
    """A retry (same session, same content) must dedupe — that's the whole point of idempotency."""
    a = fp("好的，收到", "session", "default", session_id="s1", layer="L0")
    b = fp("好的，收到", "session", "default", session_id="s1", layer="L0")
    assert a == b


def test_l0_different_sessions_are_preserved():
    """**Regression guard**: the same line said in different sessions must **each be stored
    separately** (L0 allows contradiction). The initial behavior (without context) would merge
    them into one — that's the wrongful dedup."""
    a = fp("好的，收到", "session", "default", session_id="s1", layer="L0")
    b = fp("好的，收到", "session", "default", session_id="s2", layer="L0")
    assert a != b, "L0 merged the same text from different sessions into one — violates 'append-only, contradictions allowed'"


def test_l0_source_also_distinguishes():
    a = fp("heartbeat ok", "session", "default", source="cron-A", layer="L0")
    b = fp("heartbeat ok", "session", "default", source="cron-B", layer="L0")
    assert a != b


def test_l0_without_context_uses_hour_bucket():
    """Bare L0 (no session/source): retries within the same hour dedupe, across hours are preserved."""
    same = fp("TODO: 检查备份", "temp", "default", layer="L0", now_ts=1_700_000_000)
    same2 = fp("TODO: 检查备份", "temp", "default", layer="L0", now_ts=1_700_000_059)
    later = fp("TODO: 检查备份", "temp", "default", layer="L0", now_ts=1_700_000_000 + 7200)
    assert same == same2
    assert same != later, "crossing an hour boundary should count as a different event (L0 allows contradiction)"


def test_non_l0_is_content_fingerprint_and_idempotent():
    """L1~L4: versioned families → content fingerprint, session-independent (same content must be idempotent)."""
    for cat, layer in (("knowledge", "L1"), ("pitfall", "L2"),
                       ("reference", "L4"), ("worklog", "L4")):
        a = fp("同一个事实", cat, "default", session_id="s1", layer=layer)
        b = fp("同一个事实", cat, "default", session_id="s2", layer=layer)
        assert a == b, f"{layer}({cat}) should be content-idempotent"


def test_fingerprint_is_64_hex():
    for layer in ("L0", "L1", "L4"):
        v = fp("x", "knowledge", "default", layer=layer)
        assert len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def test_l0_skips_conflict_detection():
    """**A half-fix caught in P4 production testing**: changing only the fingerprint without also
    routing conflict detection meant L0 entries would still get merge-compressed.

    Contract basis: L0 = append-only, contradictions allowed → semantic merging on L0 is
    equivalent to compressing a log.
    """
    assert det("L0") is False, "L0 must not run semantic merging — would violate 'append-only'"
    for layer in ("L1", "L2", "L3", "L4"):
        assert det(layer) is True, f"{layer} should keep semantic merge/overwrite"


def test_two_fixes_are_both_required():
    """Regression guard: what happens with only a half-fix — different fingerprints but
    near-duplicate content still get eaten by merge."""
    a = fp("同一句话", "temp", "default", source="A", layer="L0")
    b = fp("同一句话", "temp", "default", source="B", layer="L0")
    assert a != b, "the fingerprint is already tiered"
    assert det("L0") is False, "but if conflict detection isn't routed too, these two would still get merged — so both places need the fix"
