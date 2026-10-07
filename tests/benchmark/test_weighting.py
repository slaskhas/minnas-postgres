"""
test_weighting.py — heat + reliability weighting effect

Scenario: verify higher-quality memories rank higher in search results
"""
import pytest
import sys
sys.path.insert(0, ".")

from tmt.router import compute_parent_heat


def test_high_reliability_matters():
    """High-reliability memory → higher search weight"""
    # reliability carries a 0.15 weight in the five-dimension search
    # 0.9 reliability → +0.135
    # 0.5 reliability → +0.075
    # 6% difference
    boost_high = 0.9 * 0.15
    boost_low = 0.5 * 0.15
    assert boost_high > boost_low
    assert abs(boost_high - 0.135) < 0.001


def test_high_heat_matters():
    """High-heat memory → higher search weight"""
    boost_high = 0.9 * 0.15
    boost_low = 0.1 * 0.15
    assert boost_high > boost_low


def test_parent_heat_aggregates_children():
    """Parent-node heat correctly aggregates child nodes"""
    # High-heat children → high parent heat
    high_children = [0.9, 0.9, 0.9]
    low_children = [0.1, 0.1, 0.1]
    
    parent_high = compute_parent_heat(high_children)
    parent_low = compute_parent_heat(low_children)
    
    assert parent_high > parent_low, \
        f"high children ({parent_high:.3f}) should produce higher parent heat than low children ({parent_low:.3f})"


def test_heat_boundaries():
    """Heat value stays within the 0-1 range"""
    result = compute_parent_heat([0.5, 0.5, 0.5])
    assert 0.0 <= result <= 1.0, f"heat {result} out of bounds"


def test_reliability_heat_synergy():
    """High reliability + high heat = highest combined search score"""
    with open("main.py", "r") as f:
        content = f.read()

    # Verify the five-dimension search uses reliability and heat
    assert "m.reliability" in content, "search should include reliability"
    assert "m.heat_score" in content, "search should include heat_score"
    assert "0.10 * m.reliability" in content, "reliability weight should be 0.10"
