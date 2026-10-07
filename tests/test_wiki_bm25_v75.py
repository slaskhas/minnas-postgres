"""v7.5 WIKI hybrid retrieval — BM25 + RRF pure function tests (no DB dependency)

Coverage: BM25 scoring (IDF/term frequency/length normalization) + RRF fusion (dual-channel ranking).
Copied from the same logic as production (see wiki_bm25.py).
"""
import sys
sys.path.insert(0, ".")
from wiki.wiki_bm25 import compute_bm25_scores, rrf_fuse


class TestBM25:
    def test_high_freq_scores_higher(self):
        rows = [
            {"page_id": 1, "token": "记忆", "freq": 5, "pages_with_token": 2},
            {"page_id": 2, "token": "记忆", "freq": 1, "pages_with_token": 2},
        ]
        s = compute_bm25_scores(rows, ["记忆"], 10)
        assert s.get(1, 0) > s.get(2, 0)

    def test_rare_token_gets_higher_idf(self):
        rows = [
            {"page_id": 1, "token": "浑天芯算", "freq": 3, "pages_with_token": 1},
            {"page_id": 2, "token": "浑天芯算", "freq": 3, "pages_with_token": 5},
        ]
        s = compute_bm25_scores(rows, ["浑天芯算"], 10)
        assert s.get(1, 0) > s.get(2, 0)  # rarer token gets a higher IDF

    def test_empty_query(self):
        assert compute_bm25_scores([], [], 10) == {}

    def test_multi_token_accumulates(self):
        rows = [
            {"page_id": 1, "token": "记忆", "freq": 5, "pages_with_token": 2},
            {"page_id": 1, "token": "宫殿", "freq": 3, "pages_with_token": 1},
        ]
        s = compute_bm25_scores(rows, ["记忆", "宫殿"], 10)
        assert s.get(1, 0) > 0


class TestRRF:
    def test_fusion_combines_channels(self):
        vec = [(1, 0.1), (2, 0.3), (3, 0.5)]  # vector channel: 1 is first
        bm = {2: 10.0, 4: 20.0}  # BM25 channel: 4 scores highest, 2 scores high
        fused = rrf_fuse(vec, bm, k=60)
        ids = [pid for pid, _ in fused]
        # 2, which scores high on BM25, should be in the top two (2nd in vector + 2nd in BM25)
        assert ids[0] == 2 or ids[1] == 2
        # 4 only appears in BM25, should rank ahead of pure-vector 3 (1st in BM25)
        assert ids.index(4) < ids.index(3)

    def test_graph_boost_only_existing(self):
        """The graph is a boost channel: it only lifts pages that already exist, never introduces new ones"""
        vec = [(1, 0.1), (2, 0.2), (3, 0.3)]
        bm = {}
        graph = {1: 1.0, 99: 1.0}  # 99 isn't in the vector results, should not be introduced
        fused = rrf_fuse(vec, bm, graph, k=60)
        ids = [pid for pid, _ in fused]
        assert 99 not in ids  # noise guard: a graph-only page must not enter the results
        assert ids[0] == 1  # 1 still ranks first after the graph boost (the boost doesn't change the ranking's nature)

    def test_graph_boost_reorders_within_existing(self):
        """A graph boost can raise the relative ranking of an existing page"""
        vec = [(1, 0.1), (2, 0.11), (3, 0.12)]  # 1 slightly beats 2, 2 slightly beats 3
        bm = {}
        graph = {2: 1.0}  # the graph gives 2 a strong boost
        fused = rrf_fuse(vec, bm, graph, k=60)
        ids = [pid for pid, _ in fused]
        assert ids[0] == 2  # after the boost, 2 overtakes 1

    def test_k_parameter_smoothing(self):
        vec = [(1, 0.1), (2, 0.2)]
        bm = {2: 100.0}
        fused_small = rrf_fuse(vec, bm, k=1)
        fused_big = rrf_fuse(vec, bm, k=60)
        assert fused_small[0][0] == 2  # small k = steeper rank weighting, BM25's #1 wins comfortably
        assert fused_big[0][0] == 2

    def test_empty_bm25_falls_back_to_vec(self):
        vec = [(1, 0.1), (2, 0.2)]
        fused = rrf_fuse(vec, {}, k=60)
        assert [pid for pid, _ in fused] == [1, 2]  # preserves the original vector order

    def test_tie_handling(self):
        vec = [(1, 0.1)]
        bm = {1: 5.0}
        fused = rrf_fuse(vec, bm)
        assert fused[0][0] == 1  # hit in both channels, no error and no duplicate
