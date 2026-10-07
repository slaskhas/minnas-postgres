#!/usr/bin/env python3
"""
v8.0 S2-1 · RRF rank-fusion pure function tests (no database needed)

Coverage:
  R1 basic fusion and descending order
  R2 multi-channel consensus wins (this is RRF's core value, must be a **mathematical
     result**, not a tuning knob)
  R3 k parameter effect
  R4 weights
  R5 top_k truncation
  R6 invalid arguments rejected (regression guard)
  R7 empty input doesn't blow up
  R8 namespace isolation (two separate id spaces: memories/wiki)
"""
import pytest

from core.rrf import rrf_fuse_ranked, fuse_within_topk


def test_r1_basic_desc_order():
    fused = rrf_fuse_ranked({"a": ["x", "y", "z"]})
    assert [i for i, _s, _c in fused] == ["x", "y", "z"]
    assert fused[0][1] > fused[1][1] > fused[2][1]


def test_r2_multichannel_consensus_wins():
    """An item ranked 1st in only one channel should **lose** to one that ranks near the
    top across all three channels.

    This is the whole reason RRF exists — if this test fails, fusion has no point.
    """
    fused = rrf_fuse_ranked({
        "vec":    ["solo_first", "shared"],
        "bm25":   ["shared", "other"],
        "time":   ["shared", "another"],
    })
    order = [i for i, _s, _c in fused]
    assert order[0] == "shared", f"the consensus item should rank first, got: {order}"
    shared = next(t for t in fused if t[0] == "shared")
    assert set(shared[2].keys()) == {"vec", "bm25", "time"}, "hits from all three channels should be recorded"


def test_r3_k_controls_flatness():
    """Larger k flattens the top-of-list advantage: the gap between rank 1 and rank 2 should monotonically narrow as k grows."""
    lists = {"a": ["p", "q"]}
    gaps = []
    for k in (1, 10, 60, 1000):
        f = rrf_fuse_ranked(lists, k=k)
        gaps.append(round(f[0][1] - f[1][1], 12))
    assert gaps == sorted(gaps, reverse=True), f"the gap should shrink as k grows: {gaps}"
    assert gaps[0] > gaps[-1], "k's effect must actually exist"


def test_r4_weights_shift_ranking():
    lists = {"a": ["x"], "b": ["y"]}
    equal = [i for i, _s, _c in rrf_fuse_ranked(lists)]
    assert equal == sorted(equal) or equal in (["x", "y"], ["y", "x"])
    biased = [i for i, _s, _c in rrf_fuse_ranked(lists, weights={"b": 5.0, "a": 1.0})]
    assert biased[0] == "y", "weighting should change the ranking"
    zeroed = [i for i, _s, _c in rrf_fuse_ranked(lists, weights={"b": 0.0})]
    assert zeroed == ["x"], "a channel with weight 0 should not participate at all"


def test_r5_topk_truncation():
    fused = rrf_fuse_ranked({"a": list("abcdefghij")})
    assert len(fuse_within_topk(fused, 3)) == 3
    assert len(fuse_within_topk(fused, 0)) == 0
    assert len(fuse_within_topk(fused, 100)) == 10


def test_r6_invalid_args_rejected():
    """Regression guard: deliberately passing an invalid value must be rejected — not silently produce a wrong result."""
    with pytest.raises(ValueError):
        rrf_fuse_ranked({"a": ["x"]}, k=0)
    with pytest.raises(ValueError):
        rrf_fuse_ranked({"a": ["x"]}, k=-5)
    with pytest.raises(ValueError):
        fuse_within_topk([], top_k=-1)


def test_r7_empty_input_is_safe():
    assert rrf_fuse_ranked({}) == []
    assert rrf_fuse_ranked({"a": []}) == []
    assert fuse_within_topk([], 5) == []


def test_r8_namespace_isolation():
    """Two separate id spaces: without a prefix, memories#1 and wiki#1 would boost each
    other's score (an incorrect merge).

    This test locks in the reason palace._ns exists — if someone removes the prefix for
    convenience, this will fail.
    """
    from palace import _ns
    assert _ns("wiki", 1) == "w:1"
    assert _ns("resonate", 1) == "m:1"
    assert _ns("summon", 1) == "m:1"
    # The same numeric value is **a different item** in the memory vs. wiki channel
    fused = rrf_fuse_ranked({"resonate": [_ns("resonate", 1)], "wiki": [_ns("wiki", 1)]})
    assert len(fused) == 2, "same value but different namespace must count as two items"
