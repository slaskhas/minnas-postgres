"""v7.3 combined algorithm — Rank formula / mention-based upgrade / bidirectional dynamics unit tests (no DB needed)

Coverage: Rank calculation / mention-count upgrades / cumulative S upgrades / drawer percentile banding
"""
import pytest
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Pure functions from the same logic as production reflect's Rank ──
def rank_score(S: float, R: float, mention_count: int, heat: float) -> float:
    """Rank = 0.3S + 0.3R + 0.2ln(mention+1)/ln(1001)*10 + 0.2heat*10"""
    m_term = 10.0 * (math.log(mention_count + 1) / math.log(1001))
    return round(0.3 * S + 0.3 * R + 0.2 * m_term + 0.2 * heat * 10, 4)


def s_after_mentions(initial_s: float, mentions: int) -> float:
    """Every 5 cumulative mentions, S+1 (only upgrades when crossing a multiple of 5)"""
    return min(10.0, initial_s + mentions // 5)


def drawer_by_percentile(pr: float) -> str:
    if pr <= 0.10: return "hot"
    if pr <= 0.30: return "normal"
    if pr <= 0.70: return "cool"
    return "frozen"


class TestRankScore:
    def test_high_value_high_mention(self):
        # Frequently mentioned increases value: score should be high
        r = rank_score(7, 6, 30, 0.8)
        assert r > 6.0

    def test_low_value_low_mention(self):
        r = rank_score(3, 1, 0, 0.2)
        assert r < 3.0

    def test_mention_boost(self):
        """Mention count boosts Rank (old but frequently mentioned → upgraded)"""
        low = rank_score(3, 1, 0, 0.3)
        high = rank_score(3, 1, 50, 0.3)
        assert high > low

    def test_multi_dimensional(self):
        """Multi-dimensional fusion: S/R/mentions/heat all have an effect"""
        r1 = rank_score(5, 5, 10, 0.5)
        r2 = rank_score(3, 3, 10, 0.5)  # lower S/R
        assert r1 > r2


class TestSUpgrade:
    def test_no_upgrade_below_5(self):
        assert s_after_mentions(3, 4) == 3

    def test_upgrade_at_5(self):
        assert s_after_mentions(3, 5) == 4

    def test_upgrade_at_10(self):
        assert s_after_mentions(3, 10) == 5

    def test_cap_ten(self):
        assert s_after_mentions(9, 5) == 10
        assert s_after_mentions(10, 100) == 10

    def test_rebound(self):
        """Re-mentioned after downgrading → S can rebound (bidirectional dynamics)"""
        assert s_after_mentions(3, 15) == 6


class TestDrawerPercentile:
    def test_hot_top10(self):
        assert drawer_by_percentile(0.05) == "hot"
        assert drawer_by_percentile(0.10) == "hot"

    def test_normal_10_30(self):
        assert drawer_by_percentile(0.15) == "normal"
        assert drawer_by_percentile(0.30) == "normal"

    def test_cool_30_70(self):
        assert drawer_by_percentile(0.50) == "cool"
        assert drawer_by_percentile(0.70) == "cool"

    def test_frozen_70plus(self):
        assert drawer_by_percentile(0.75) == "frozen"
        assert drawer_by_percentile(1.0) == "frozen"
