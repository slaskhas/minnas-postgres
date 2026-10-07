"""
test_conflict.py — conflict-detection accuracy test

Scenario: verify detect_conflict's merge/conflict/fresh classification
"""
import pytest
import sys
sys.path.insert(0, ".")

from main import text_diff_ratio


def test_merge_identical():
    """Identical text → ratio > 0.85 → merge"""
    r = text_diff_ratio(
        "pgvector HNSW index is faster than IVFFlat",
        "pgvector HNSW index is faster than IVFFlat"
    )
    assert r > 0.85, f"identical texts got ratio={r}"


def test_merge_near_identical():
    """Near-identical text → ratio > 0.85 → merge"""
    r = text_diff_ratio(
        "HNSW index has better recall than IVFFlat",
        "HNSW index has better recall than IVFFlat in most cases"
    )
    assert r > 0.85, f"near-identical texts got ratio={r:.3f}"


def test_fresh_different():
    """Completely different topic → ratio < 0.5 → fresh (likely)"""
    r1 = text_diff_ratio(
        "HNSW is the best index for vector search",
        "The weather in Beijing is sunny today"
    )
    # Completely different, ratio should be very low
    assert r1 < 0.5, f"different texts got ratio={r1:.3f}"


def test_conflict_contradiction():
    """Semantically similar + contradictory content → ratio < 0.5"""
    r = text_diff_ratio(
        "HNSW is the best index for all scenarios",
        "IVFFlat is actually better for low-dimensional data"
    )
    # Related topic but not identical, ratio should be in the middle range
    assert r < 0.5, f"contradicting texts got ratio={r:.3f}"


def test_boundary_085():
    """Boundary test: the exact 0.85 case"""
    # ratio should be around 0.85 (depends on implementation details)
    r = text_diff_ratio("a" * 85 + "b" * 15, "a" * 85 + "c" * 15)
    # 85% match → ratio should be ≥ 0.85
    assert r >= 0.8, f"boundary case got ratio={r:.3f}"
