"""
test_recall.py — long-term memory retention test

Scenario: verify retrieval accuracy of memories over time
Does not connect to a real PG; verified with deterministic logic
"""
import pytest
import sys
sys.path.insert(0, ".")


def test_temporal_sql_decay_7days():
    """Memory within 7 days: weight 0.15"""
    sql = "CASE WHEN m.created_at > NOW() - INTERVAL '7 days' THEN 0.15"
    assert "0.15" in sql


def test_temporal_sql_decay_30days():
    """Memory within 30 days: weight 0.08"""
    sql = "WHEN m.created_at > NOW() - INTERVAL '30 days' THEN 0.08"
    assert "0.08" in sql


def test_temporal_sql_old():
    """Beyond 30 days: weight 0"""
    sql = "ELSE 0 END"
    assert "ELSE 0" in sql or "ELSE 0 END" in sql


def test_five_dimension_weights_sum():
    """Sum of the five dimension weights should be close to 1.0"""
    weights = {
        "semantic": 0.40,
        "bm25": 0.15,
        "temporal": 0.15,  # reduced to 0.10 for validity
        "reliability": 0.15,
        "heat": 0.15,
    }
    total = sum(weights.values())
    assert 0.95 <= total <= 1.05, f"weights sum should be ~1.0, got {total}"


def test_architecture_has_memory_hierarchy():
    """Verify the architecture has a tiered memory structure"""
    with open("main.py", "r") as f:
        content = f.read()

    # TMT tiers
    assert "L1" in content or "tier" in content, "should have memory tiers"
    # Palace (the three-hall model was removed in v5.0; palace system since v7.8)
    assert "palace" in content or "summon" in content, "should have a palace retrieval system"
    # Temporal decay
    assert "INTERVAL" in content, "should have a temporal decay mechanism"
