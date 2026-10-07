"""v6.0 regression test — controlled category wordlist normalization + reflect L4 protection semantics"""

import pytest
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Extract normalize_category (without triggering main.py's full import side effects)
SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")).read()
_NS = {}
exec(SRC[SRC.index("CATEGORY_WHITELIST"):SRC.index("# ── v5.0: modular imports")], _NS)
normalize_category = _NS["normalize_category"]
CATEGORY_WHITELIST = _NS["CATEGORY_WHITELIST"]


@pytest.mark.parametrize("cat,expected", [
    # Controlled primary keys pass through unchanged
    ("knowledge", "knowledge"), ("pitfall", "pitfall"), ("reference", "reference"),
    ("project", "project"), ("ops", "ops"), ("deploy", "deploy"),
    ("preference", "preference"), ("session", "session"), ("worklog", "worklog"),
    ("temp", "temp"),
    # Legacy English category mappings
    ("monitoring", "ops"), ("healthcheck", "ops"), ("chat", "session"),
    ("note", "worklog"), ("work", "worklog"), ("fact", "knowledge"),
    ("pattern", "knowledge"), ("belief", "knowledge"), ("experience", "pitfall"),
    ("architecture", "knowledge"), ("design-pattern", "knowledge"), ("research", "reference"),
    # Chinese categories
    ("架构", "knowledge"), ("架构设计", "knowledge"), ("论文研究", "reference"),
    ("踩坑记录", "pitfall"), ("运维日报", "ops"), ("部署文档", "deploy"),
    ("项目计划", "project"), ("用户偏好", "preference"), ("会话记录", "session"),
    ("工作日志", "worklog"), ("临时提醒", "temp"), ("知识图谱", "knowledge"),
    # Fallback for unknown categories
    ("", "knowledge"), (None, "knowledge"), ("xyz", "knowledge"),
])
def test_normalize_category(cat, expected):
    assert normalize_category(cat) == expected


def test_whitelist_has_exactly_10_categories():
    assert set(CATEGORY_WHITELIST.keys()) == {
        "knowledge", "pitfall", "reference", "project", "ops",
        "deploy", "preference", "session", "worklog", "temp",
    }


def test_reflect_l4_preserves_memory_not_deletes():
    """v6.0: reflect L4 only marks forgotten_at, never is_deleted=TRUE"""
    src = SRC
    # The L4 migration statement should not contain is_deleted=TRUE
    l4_lines = [l for l in src.splitlines() if "tier = 'L4'" in l]
    assert l4_lines, "reflect should have an L4 migration statement"
    for line in l4_lines:
        assert "is_deleted = TRUE" not in line, f"L4 should not directly delete: {line}"
        assert "forgotten_at = NOW()" in line, f"L4 should mark forgotten_at: {line}"
