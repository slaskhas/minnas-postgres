#!/usr/bin/env python3
"""
core/rrf.py — Reciprocal Rank Fusion, v8.0 S2-1
================================================

Why we need it
--------------
Before v8.0, the primary retrieval path fused signals via **a single SQL linear
weighting**:
    0.45*vector + 0.15*BM25 + 0.15*time + 0.15*reliability + 0.10*heat
Problem: the five scores have **completely different units** (cosine distance
∈[0,2] / BM25 unbounded / time ∈{0,0.08,0.15}) → the weighting coefficients have
no physical meaning and can only be tuned by gut feeling.

RRF's approach is to **only look at rank, never at the raw score**:
    score(d) = Σ_over_channels  1 / (k + rank_channel(d) + 1)
It's unit-independent, and has been validated for a long time in the search-engine
industry (k=60 is the universal default).

Relationship to the existing implementation
--------------------------------------------
`wiki/wiki_bm25.py` already has an `rrf_fuse(vec_ranked, bm25_scores, graph_scores, k=60)`
aimed at the specific combination of **vector distance + score-based channels + graph
boost**, already validated as effective on the WIKI retrieval path. This module is
its **generalized, rank-list version**: the input is N "already-ranked id lists,"
and the output is a unified fusion score. Both use the same formula
(1/(k+rank+1), k=60); this module doesn't depend on asyncpg or any other project
module — it's a **pure function, unit-testable**.

Namespace caveat (hit in practice)
-----------------------------------
`memories.id` and `wiki_pages.id` are **two independent id spaces** whose values
can collide. So callers must prefix the id **before** fusing (e.g. `m:123` / `w:45`),
otherwise two unrelated entries would be treated as the same one and boost each
other's score. This module doesn't do that for the caller — it only does the pure
rank fusion; prefixing is `palace.summon_fused`'s responsibility.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


def rrf_fuse_ranked(
    ranked_lists: Mapping[str, Sequence],
    k: int = 60,
    weights: Mapping[str, float] | None = None,
) -> List[Tuple]:
    """Rank fusion.

    ranked_lists : {channel_name: [id, id, ...]} — each list is **already sorted
                   best-to-worst for that channel**
    k            : RRF smoothing constant, default 60 (larger → the top-rank
                   advantage flattens out more)
    weights      : optional {channel_name: weight}, defaults to 1.0 for all
                   (equal weight)

    Returns: [(id, score, {channel: rank within that channel})] sorted by score
             descending. The second element, score, is the fused score; the third,
             channels, is for **explainability** — answering "why did this item
             rank where it did" — a single high-scoring channel, or multiple
             channels hitting it together.

    Design trade-offs (written into the code, not left as a verbal agreement):
      - **Only rank matters**, not the raw score → unit-independent, no
        normalization needed
      - **Multi-channel hits automatically win out**: an item hit by 3 channels
        at once will necessarily score higher than one that's merely first in a
        single channel (when k is large enough). This is exactly the "consensus
        wins" behavior we want, and it's a **mathematical consequence**, not a
        tuning outcome.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    weights = weights or {}
    scores: Dict = {}
    channels: Dict = {}

    for ch, ids in ranked_lists.items():
        w = float(weights.get(ch, 1.0))
        if w == 0:
            continue
        for rank, item_id in enumerate(ids):
            scores[item_id] = scores.get(item_id, 0.0) + w * (1.0 / (k + rank + 1))
            channels.setdefault(item_id, {})[ch] = rank

    return sorted(
        ((i, round(s, 10), channels[i]) for i, s in scores.items()),
        key=lambda t: (-t[1], str(t[0])),
    )


def fuse_within_topk(fused: Iterable[Tuple], top_k: int) -> List[Tuple]:
    """Truncate to top_k (kept as its own function for testing and reuse)."""
    if top_k < 0:
        raise ValueError("top_k cannot be negative")
    out = []
    for i, row in enumerate(fused):
        if i >= top_k:
            break
        out.append(row)
    return out
