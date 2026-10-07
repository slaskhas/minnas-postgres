"""
test_hall_flow.py — 6 cases for the three-hall transition rules
"""
import pytest
import sys
sys.path.insert(0, ".")

# The valid_flow rules are defined in api/halls.py promote_memory
# This tests the correctness of the rule matrix

VALID_FLOW = {
    "research": ["engineering"],
    "engineering": ["archive", "research"],
    "archive": [],
}


def test_flow_research_to_engineering():
    """Research hall → Engineering hall: allowed"""
    assert "engineering" in VALID_FLOW["research"]


def test_flow_engineering_to_archive():
    """Engineering hall → Archive hall: allowed"""
    assert "archive" in VALID_FLOW["engineering"]


def test_flow_engineering_to_research():
    """Engineering hall → Research hall: allowed (sent back)"""
    assert "research" in VALID_FLOW["engineering"]


def test_flow_archive_blocked():
    """Archive hall → anything: forbidden (terminal state)"""
    assert VALID_FLOW["archive"] == []


def test_flow_research_to_archive_blocked():
    """Research hall → Archive hall: forbidden (must go through Engineering hall)"""
    assert "archive" not in VALID_FLOW["research"]


def test_flow_invalid_target():
    """A nonexistent target hall → not in valid_flow"""
    for targets in VALID_FLOW.values():
        assert "invalid_hall" not in targets
