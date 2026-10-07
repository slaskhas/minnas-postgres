#!/usr/bin/env python3
"""wiki_graph — WIKI graph-expansion retrieval channel (v7.5 P1)

Design: entity anchoring + 1-hop RELATED_TO
1. Query → jieba tokenization → match against entities table (name ILIKE) → candidate entity ids
2. Follow AGE RELATED_TO edges 1 hop → related entity ids
3. Related entities → wiki_entities table → which wiki pages they appear on (score boost)
4. Returns {page_id: graph_score, related_entities: [...]}

Fused with the vector/BM25 channels via RRF (called from search_wiki).
"""
import asyncio
import math


async def graph_expand(conn, query: str, user_id: str = "default", top_k: int = 10) -> dict:
    """Graph expansion: returns {page_scores: {page_id: score}, entities: [name,...]}"""
    try:
        import jieba
        # Load the domain dictionary (v7.5 expert review P1: terminology tokenization)
        # — jieba is a global singleton, only loaded once
        import os as _os
        _dict_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "wiki_dict.txt")
        if _os.path.exists(_dict_path):
            jieba.load_userdict(_dict_path)
    except ImportError:
        return {"page_scores": {}, "entities": []}

    # 1. Tokenize the query → match against the entities table (prefer entities with a wiki association)
    tokens = [t.strip() for t in jieba.cut(query) if len(t.strip()) >= 2]
    if not tokens:
        return {"page_scores": {}, "entities": []}

    anchor_entities = []
    seen_ids = set()
    for tok in tokens[:6]:
        # Only take entity names 2-12 characters long (filters out overly long full
        # names), preferring those with a wiki association
        rows = await conn.fetch(
            "SELECT DISTINCT e.id, e.name FROM entities e "
            "JOIN wiki_entities we ON we.entity_id = e.id "
            "WHERE e.user_id=$1 AND length(e.name) BETWEEN 2 AND 15 "
            "AND e.name ILIKE '%'||$2||'%' LIMIT 5",
            user_id, tok
        )
        for r in rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                anchor_entities.append({"id": r["id"], "name": r["name"]})

    if not anchor_entities:
        return {"page_scores": {}, "entities": []}

    anchor_ids = [e["id"] for e in anchor_entities]
    entities_found = [e["name"] for e in anchor_entities]

    # 2. (v7.8: the AGE RELATED_TO 1-hop was removed along with AGE — anchor entities only now)

    # 3. Related entities → look up pages via wiki_entities
    all_ids = list(anchor_ids)
    page_scores = {}
    if all_ids:
        rows = await conn.fetch(
            "SELECT we.wiki_page_id, count(*) AS n FROM wiki_entities we "
            "WHERE we.entity_id = ANY($1::bigint[]) GROUP BY we.wiki_page_id ORDER BY n DESC LIMIT $2",
            all_ids, top_k
        )
        total = sum(r["n"] for r in rows) or 1
        for r in rows:
            # normalize: page's associated-entity count / total associations
            page_scores[r["wiki_page_id"]] = round(r["n"] / total, 4)

    return {"page_scores": page_scores, "entities": entities_found[:10]}
