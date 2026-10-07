"""v7.7.0 injection scheduling — pure function tests (no DB dependency)

Coverage: (1) RRF pid space consistency (vector channel uses id, not index, to prevent out-of-range)
(2) state-weight ranking (3) limits hard validation
Copied from the same logic as production (see api/skills.py / api/injection.py)
"""
import sys
sys.path.insert(0, ".")
from wiki.wiki_bm25 import rrf_fuse


def rank_skills(rows, bm25_scores, top_k=3, state_weight=None):
    """Replicates api/skills.py's RRF+state-weight ranking logic (same as production)"""
    state_weight = state_weight or {"active": 1.0, "stale": 0.85, "archived": 0.7}
    vec_ranked = [(r["id"], r["dist"]) for r in rows]
    fused = rrf_fuse(vec_ranked, bm25_scores) if (bm25_scores or vec_ranked) else []
    id2row = {r["id"]: r for r in rows}
    scored = []
    for pid, rrf in fused:
        r = id2row.get(pid)
        if not r:
            continue
        w = state_weight.get(r["state"], 1.0)
        scored.append((rrf * w, r))
    scored.sort(key=lambda x: -x[0])
    return [r["skill_name"] for _s, r in scored[:top_k]]


class TestRRFIdSpace:
    """P0 regression: BM25 channel's pid is a DB id, so the vector channel must also use DB ids (not an index)"""

    def test_no_index_out_of_range(self):
        """A BM25-matched skill_id larger than the candidate list length must not go out of range (old bug: using index caused IndexError)"""
        rows = [
            {"id": 1, "skill_name": "skill-a", "state": "active", "dist": 0.1},
            {"id": 2, "skill_name": "skill-b", "state": "active", "dist": 0.2},
        ]
        # BM25 matched id=18 (not in the vector channel's top 2, but a valid DB id)
        bm25 = {18: 20.0, 1: 10.0}
        names = rank_skills(rows, bm25, top_k=3)
        # 18 has no matching row → skipped; 1 does → appears
        assert "skill-a" in names
        assert len(names) >= 1

    def test_pid_alignment(self):
        """Same-id fusion across both channels: the higher BM25-scored id ranks first"""
        rows = [
            {"id": 5, "skill_name": "hot", "state": "active", "dist": 0.05},
            {"id": 9, "skill_name": "cold", "state": "active", "dist": 0.3},
        ]
        bm25 = {5: 50.0, 9: 1.0}
        names = rank_skills(rows, bm25, top_k=2)
        assert names[0] == "hot"


class TestStateWeight:
    def test_active_beats_archived(self):
        """Same rrf score: active ranks before archived (weight 1.0 vs 0.7)"""
        rows = [
            {"id": 1, "skill_name": "archived-skill", "state": "archived", "dist": 0.1},
            {"id": 2, "skill_name": "active-skill", "state": "active", "dist": 0.11},
        ]
        names = rank_skills(rows, {}, top_k=2)
        # active has a slightly farther distance but higher weight → ranks first
        assert names.index("active-skill") < names.index("archived-skill")

    def test_archived_still_visible(self):
        """archived is only down-weighted, not hidden (optimization ≠ forgetting)"""
        rows = [
            {"id": 1, "skill_name": "only-archived", "state": "archived", "dist": 0.1},
        ]
        names = rank_skills(rows, {}, top_k=5)
        assert "only-archived" in names


class TestHardCaps:
    def test_cap_clamp(self):
        """limits over the ceiling get clamped to HARD_CAPS"""
        def clamp(limits, hard=None):
            hard = hard or {"skills": 8, "memories": 10, "hooks": 10}
            return {k: max(1, min(int(limits.get(k, 3)), hard[k])) for k in hard}
        caps = clamp({"skills": 999, "memories": 999, "hooks": 999})
        assert caps == {"skills": 8, "memories": 10, "hooks": 10}
        caps2 = clamp({"skills": 0})
        assert caps2["skills"] == 1  # floor of 1
