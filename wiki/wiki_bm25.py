#!/usr/bin/env python3
"""wiki_bm25 — WIKI keyword-channel BM25 scoring (v7.5 P0a)

Computes BM25 scores from the wiki_keywords table (simplified BM25: token
frequency + IDF). Called by main.py's search_wiki, fused with the vector
channel via RRF.
"""
import math


def _idf_from_stats(total_pages: int, pages_with_token: int) -> float:
    """IDF = ln(1 + (N - n + 0.5) / (n + 0.5)), guards against division by zero"""
    n = max(pages_with_token, 1)
    return math.log(1 + (total_pages - n + 0.5) / (n + 0.5))


def compute_bm25_scores(rows, query_tokens: list, total_pages: int) -> dict:
    """rows: [{'page_id', 'token', 'freq', 'pages_with_token'}]
    query_tokens: query terms after jieba tokenization
    returns {page_id: bm25_score}
    BM25 = Σ IDF * (freq*(k1+1)) / (freq + k1*(1-b+b*dl/avgdl))
    simplified: k1=1.5, b=0.75, dl≈sum of freq, avgdl is the average token count per page
    """
    k1 = 1.5
    b = 0.75

    # total word count per page (dl) and the global average (avgdl)
    page_dl = {}
    for r in rows:
        page_dl[r["page_id"]] = page_dl.get(r["page_id"], 0) + r["freq"]
    avgdl = sum(page_dl.values()) / max(len(page_dl), 1)

    scores = {}
    for tok in query_tokens:
        # each row computes IDF from its own pages_with_token (rarer terms get higher IDF)
        for r in rows:
            if r["token"] != tok:
                continue
            pid = r["page_id"]
            freq = r["freq"]
            idf = _idf_from_stats(total_pages, r["pages_with_token"])
            dl = page_dl.get(pid, 1)
            denom = freq + k1 * (1 - b + b * dl / max(avgdl, 1))
            score = idf * (freq * (k1 + 1)) / max(denom, 0.001)
            scores[pid] = scores.get(pid, 0) + score
    return scores


def rrf_fuse(vec_ranked: list, bm25_scores: dict = None, graph_scores: dict = None, k: int = 60) -> list:
    """Reciprocal Rank Fusion: fuses multiple channels (vector + BM25 + graph boost).
    vec_ranked: [(page_id, distance), ...] ascending by distance (closer is better)
    bm25_scores: {page_id: score} (optional) — independent channel, contributes an RRF rank score
    graph_scores: {page_id: score} (optional) — boost channel, only raises the rank of
    pages already present (prevents noise)
    returns [(page_id, rrf_score), ...] descending
    """
    bm25_scores = bm25_scores or {}
    graph_scores = graph_scores or {}
    rrf = {}
    for rank, (pid, _dist) in enumerate(vec_ranked):
        rrf[pid] = rrf.get(pid, 0) + 1.0 / (k + rank + 1)

    # BM25 channel: rank score assigned in descending score order (independent channel)
    if bm25_scores:
        sorted_bm = sorted(bm25_scores.items(), key=lambda x: -x[1])
        for rank, (pid, _) in enumerate(sorted_bm):
            rrf[pid] = rrf.get(pid, 0) + 1.0 / (k + rank + 1)

    # Graph channel: a boost, not an independent channel — only raises pages already
    # matched, never introduces new pages (prevents noise)
    if graph_scores:
        for pid, score in graph_scores.items():
            if pid in rrf and score > 0:
                rrf[pid] = rrf[pid] + 0.02 * min(score, 1.0)

    return sorted(rrf.items(), key=lambda x: -x[1])
