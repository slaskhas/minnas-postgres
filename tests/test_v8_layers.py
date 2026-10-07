#!/usr/bin/env python3
"""
v8.0 S3-1 · memory tiering model (executable spec) tests

The **existence of this test group is itself the criterion** — it proves the tiering
isn't just documentation: if the spec only lives in an md file, nobody can disprove
"the system doesn't actually follow it"; once it's written as runnable assertions,
any drift in the wordlist/tier definitions will fail here.
"""
import pytest

from core import layers as L


def test_l1_self_check_passes():
    r = L.self_check()
    assert r["ok"], f"spec self-check failed: {r['problems']}"
    assert r["problems"] == []
    assert set(r["layers"]) == {"L0", "L1", "L2", "L3", "L4"}


def test_l2_every_controlled_category_maps_to_a_layer():
    for cat in L.KNOWN_CATEGORIES:
        info = L.classify_layer(cat)
        assert info["layer"] in L.LAYERS, f"{cat} has no assigned tier"
        assert info["why"]


def test_l3_unknown_category_normalizes_like_the_api():
    """On the API side, an illegal category normalizes to knowledge — the classifier must agree."""
    info = L.classify_layer("totally-made-up")
    assert info["category"] == "knowledge"
    assert info["layer"] == "L1"
    assert "不在受控词表" in info["why"]


def test_l4_l3_constraint_layer_is_not_in_the_database():
    """Honest assertion: the constraint tier's carrier lives on the Hermes side
    (SOUL/MEMORY/config); there's no corresponding category in the database.

    If someone stuffs a category into L3 for the sake of "tidiness", this test fails —
    that would conflate the "tiering model" with the "storage carrier", causing a
    constraint to be governed like an ordinary memory.
    """
    assert L.LAYERS["L3"]["categories"] == ()
    assert "L3" not in set(L.CATEGORY_TO_LAYER.values())


def test_l5_log_layer_allows_contradiction():
    for cat in ("session", "temp"):
        info = L.classify_layer(cat)
        assert info["layer"] == "L0"
        assert info["family"] == "只增不改"
        assert "允许矛盾" in info["conflict_policy"]


def test_l6_only_cognition_layer_requires_source():
    assert L.classify_layer("knowledge")["requires_source"] is True
    assert L.classify_layer("knowledge", source="session:abc")["source_provided"] is True
    for cat in ("session", "reference", "pitfall"):
        assert L.classify_layer(cat)["requires_source"] is False


def test_l7_artifact_pointer_only_when_artifact_present():
    with_art = L.classify_layer("worklog", has_artifact=True)
    assert with_art["artifact_pointer"] is True
    assert with_art["artifact_rule"], "a pointer rule must be given when an artifact is present"
    without = L.classify_layer("worklog", has_artifact=False)
    assert without["artifact_pointer"] is False
    assert without["artifact_rule"] is None


def test_l8_reference_layer_points_not_copies():
    """Reference tier's key rule: memory only stores a pointer + fingerprint, never the entity itself."""
    info = L.classify_layer("reference")
    assert info["layer"] == "L4"
    assert "指针" in info["write_policy"]
    assert set(L.ARTIFACT_INDEX["entity_home"]) == {
        "交付/双击打开/给外部看", "要被检索/被反复引用", "代码/仓库产物"}


def test_l9_self_check_detects_drift(monkeypatch):
    """Regression guard: deliberately drop one category's tier assignment — the self-check must report it (not silently pass)."""
    broken = dict(L.CATEGORY_TO_LAYER)
    broken.pop("worklog")
    monkeypatch.setattr(L, "CATEGORY_TO_LAYER", broken)
    r = L.self_check()
    assert not r["ok"]
    assert any("worklog" in p for p in r["problems"]), r["problems"]


def test_l10_conflict_policies_are_distinct_by_family():
    """The three families' conflict policies must genuinely differ — otherwise the family split is meaningless."""
    l0 = L.LAYERS["L0"]["conflict_policy"]
    l1 = L.LAYERS["L1"]["conflict_policy"]
    l3 = L.LAYERS["L3"]["conflict_policy"]
    assert l0 != l1 != l3
    assert "禁止并存" in l3, "the constraint tier must explicitly forbid coexistence"
