#!/usr/bin/env python3
"""
core/layers.py — the **executable spec** for the memory layering model (v8.0 S3-1)
====================================================================================

Why this code exists
---------------------
On 2026-09-24 the user dictated a memory taxonomy (session / cognition / skill /
constraint / fact / reference / work), which was organized into a 5-layer + 1
cross-cutting model. But **a spec written only in docs = nobody enforces it** —
the red team's own challenge was: "won't this just become a case of 'the docs say
so, but the system hasn't changed a line'?"

So this module turns the layering into a **runnable judgment**: given a memory
about to be written, it answers "which layer does it belong to / what's the write
rule / how are conflicts handled / where does it live." Falsifiable criterion: any
new write must get a unique answer from `classify_layer()` — if some category
doesn't fit into any layer, or a rule contradicts that layer's declaration, the
tests go red.

The model (user dictated 2026-09-24 → normalized)
---------------------------------------------------
There is only one axis: **whether contradictions are allowed to coexist**.

  Family "append-only, never edited"       L0 log layer         — append-only, contradictions allowed, dedup only, never compressed
  Family "versioned, latest wins"          L1 cognition layer   — updatable, requires a source, conflict → update + keep a changelog
                                            L2 skill layer       — file-versioned, old versions kept in .archive
                                            L3 constraint layer  — human-finalized, **no coexistence allowed**, changes are recorded
                                            L4 reference layer   — versioned + back-linking pointer
  Cross-cutting (not a layer)              artifact index        — one copy per entity, pointers can be many

Key facts (stated honestly)
-----------------------------
- **The L3 constraint layer's carrier is not inside the Mnemosyne store** — it's
  `SOUL.md` / `MEMORY.md` / `config.yaml` (on the Hermes side, human-finalized).
  None of the store's 10 `category` values map to it. This isn't a gap in the
  model — it's evidence that the layering model and the storage carrier are
  deliberately separate.
- Per the user's own words ("things like skills, constraints"), "factual memory"
  is **folded into L2/L3** rather than given its own layer.
- The cognition layer currently belongs to the "latest wins" family and allows a
  confidence score; **whether to retain a "cognitive evolution history" is still
  undecided** (see proposal P-20260925-01 §7) — if decided to keep it, it would
  become a third family, requiring this module to be extended accordingly.
"""
from __future__ import annotations

from typing import Optional

# ── Layer definitions ──────────────────────────────────────────────────────────
# NOTE: the Chinese string values below (name/family/carrier/write_policy/
# conflict_policy, and ARTIFACT_INDEX) are functional data asserted on by exact
# substring in tests/test_v8_layers.py and returned verbatim via the API — do not
# translate them.
LAYERS: dict[str, dict] = {
    "L0": {
        "name": "日志层",
        "family": "只增不改",
        "carrier": "state.db（全量原文+tool_calls）+ Mnemosyne session 归档",
        "write_policy": "append-only；只去噪，不压缩正文",
        "conflict_policy": "允许矛盾，作参考（不覆盖、不判定谁对）",
        "categories": ("session", "temp"),
    },
    "L1": {
        "name": "认知层",
        "family": "版本化·只认最新",
        "carrier": "Mnemosyne knowledge / beliefs + MEMORY.md / USER.md",
        "write_policy": "可更新，须带来源；更新时留变更日志",
        "conflict_policy": "冲突 → 更新为新值 + 记录『从什么变成什么』",
        "categories": ("knowledge", "preference"),
    },
    "L2": {
        "name": "技能层",
        "family": "版本化·只认最新",
        "carrier": "skills/ 文件 + 触发式注入",
        "write_policy": "文件版本化；旧版进 .archive（不删，留源）",
        "conflict_policy": "只认最新；旧版可召回但不再注入",
        "categories": ("pitfall", "ops", "deploy"),
    },
    "L3": {
        "name": "约束层",
        "family": "人工定稿",
        "carrier": "SOUL.md / MEMORY.md / config.yaml（⚠️ 不在 Mnemosyne 库内）",
        "write_policy": "人工定稿；变更有记录",
        "conflict_policy": "**禁止并存** —— 不允许两条约束同时有效",
        "categories": (),  # no corresponding category in the store — this is a fact, not an oversight
    },
    "L4": {
        "name": "参考层",
        "family": "版本化·只认最新",
        "carrier": "wiki_pages + 箱子文件 + 项目文档（N+EN / ADR）",
        "write_policy": "版本化；**记忆里只放指针 + 指纹，不放实体**",
        "conflict_policy": "只认最新 + 指针回链",
        "categories": ("reference", "project", "worklog"),
    },
}

ARTIFACT_INDEX = {
    "name": "产出物索引（横切，不是一层）",
    "rule": "实体只有一份，指针可多处；每条引用带 路径/URL + sha256 + 大小",
    "entity_home": {
        "交付/双击打开/给外部看": "箱子（收件箱 → 归档）",
        "要被检索/被反复引用": "Wiki（可版本化、可跨会话召回）",
        "代码/仓库产物": "仓库 + tag",
    },
}

# category → layer reverse lookup (generated from LAYERS, single source of truth)
CATEGORY_TO_LAYER: dict[str, str] = {
    cat: lk for lk, spec in LAYERS.items() for cat in spec["categories"]
}

# Project's controlled vocabulary (the 10 values in docs/schema.sql's chk_memories_category)
KNOWN_CATEGORIES = ("knowledge", "pitfall", "reference", "project", "ops",
                    "deploy", "preference", "session", "worklog", "temp")


def classify_layer(category: str, *, has_artifact: bool = False,
                   source: Optional[str] = None) -> dict:
    """Determine which layer a memory belongs to, and return that layer's write
    rule and conflict policy.

    category     : one of the 10 controlled-vocabulary values (unknown → normalized
                   to knowledge, consistent with the API)
    has_artifact : whether a deliverable/entity file accompanies it (decides whether
                   to attach an artifact-index pointer)
    source       : source marker (L1 requires a source)

    Returns: {layer, name, family, carrier, write_policy, conflict_policy,
           artifact_pointer, why}
    """
    cat = (category or "").strip().lower()
    normalized = cat in KNOWN_CATEGORIES
    cat_eff = cat if normalized else "knowledge"
    layer = CATEGORY_TO_LAYER.get(cat_eff, "L1")
    spec = LAYERS[layer]

    why = f"category={cat_eff} 属于 {layer} {spec['name']}（{spec['family']}）"
    if not normalized:
        why = f"category={cat!r} 不在受控词表 → 按 knowledge 归一化；" + why

    return {
        "category": cat_eff,
        "layer": layer,
        "name": spec["name"],
        "family": spec["family"],
        "carrier": spec["carrier"],
        "write_policy": spec["write_policy"],
        "conflict_policy": spec["conflict_policy"],
        "requires_source": layer == "L1",
        "source_provided": bool(source),
        "artifact_pointer": bool(has_artifact),
        "artifact_rule": ARTIFACT_INDEX["rule"] if has_artifact else None,
        "why": why,
    }


def self_check() -> dict:
    """Spec self-check: every controlled-vocabulary category must fall into some
    layer, and each layer's fields must be complete.

    This is what turns a "documented spec" into a "falsifiable assertion" — if
    someone changes the vocabulary but forgets to update the layering, this goes
    red.
    """
    problems = []
    for cat in KNOWN_CATEGORIES:
        if cat not in CATEGORY_TO_LAYER:
            problems.append(f"category={cat} 未归入任何层")
    for lk, spec in LAYERS.items():
        for f in ("name", "family", "carrier", "write_policy", "conflict_policy"):
            if not spec.get(f):
                problems.append(f"{lk} 缺字段 {f}")
    # L3 should have no in-store category — if someone forces one in, it means
    # the model and the carrier have been conflated
    if LAYERS["L3"]["categories"]:
        problems.append("L3 约束层不应映射库内 category（其载体在 Hermes 侧）")
    return {"ok": not problems, "problems": problems,
            "coverage": f"{len(CATEGORY_TO_LAYER)}/{len(KNOWN_CATEGORIES)} 类已归层",
            "layers": list(LAYERS.keys())}


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        r = self_check()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        raise SystemExit(0 if r["ok"] else 1)
    print(json.dumps({"known_categories": KNOWN_CATEGORIES,
                      "category_to_layer": CATEGORY_TO_LAYER,
                      "artifact_index": ARTIFACT_INDEX}, ensure_ascii=False, indent=2))
