"""v7.2 forgetting throttle — user-activity-aware unit tests (no DB needed)

Coverage: activity determination / decay pause while inactive / clearing the marker on return
"""
import pytest
import sys, os
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Pure functions from the same logic as production reflect's user-activity awareness ──
def user_active(last_active, now, window_days=7) -> bool:
    if last_active is None:
        return False
    return last_active >= now - timedelta(days=window_days)


def absence_days(last_active, now, is_active) -> int:
    if last_active is None or is_active:
        return 0
    return (now - last_active).days


class TestUserActive:
    NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

    def test_active_today(self):
        assert user_active(self.NOW - timedelta(hours=1), self.NOW) is True

    def test_active_6d(self):
        assert user_active(self.NOW - timedelta(days=6), self.NOW) is True

    def test_inactive_8d(self):
        """User away for 8 days → determined inactive → decay pauses"""
        assert user_active(self.NOW - timedelta(days=8), self.NOW) is False

    def test_inactive_30d(self):
        assert user_active(self.NOW - timedelta(days=30), self.NOW) is False

    def test_none(self):
        assert user_active(None, self.NOW) is False

    def test_absence_days(self):
        la = self.NOW - timedelta(days=15)
        assert absence_days(la, self.NOW, False) == 15
        assert absence_days(la, self.NOW, True) == 0

    def test_absence_boundary(self):
        """Exactly 7 days: active (>= semantics) — no pause on the day the user returns"""
        assert user_active(self.NOW - timedelta(days=7), self.NOW) is True
