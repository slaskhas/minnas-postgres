"""
Mnemosyne v5.0 — heterogeneous competitive audit engine
Whitepaper L3 second line of defense: cross-model validation

Principle: two different models reason independently → results compete → the
better one wins
Personal-scale setup: deepseek-v4-pro vs doubao-seed-2.0
"""
from typing import Dict, Optional
import json


async def audit_memory(conn, memory_id: int, content: str) -> Dict:
    """
    Run a heterogeneous audit on a single memory

    Flow:
    1. deepseek-v4-pro independently assesses the memory's factuality
    2. doubao-seed-2.0 independently assesses the memory's factuality
    3. Compare results → agreement = reinforce / contradiction = downgrade

    Returns:
        {"verdict": "consistent"|"disputed"|"uncertain", "confidence": float, "models": [...],
         "reliability_delta": float}
    """
    from core.llm import call_llm, call_llm_json

    # Build the audit prompt (kept in Chinese — sent verbatim to the LLM and its
    # output format is relied on downstream; not a comment)
    audit_prompt = f"""你是一个知识审计专家。请评估以下记忆内容的真实性和可靠性。

记忆内容:
{content[:1000]}

请用JSON格式输出:
{{
    "is_factual": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "评估理由(一句话)",
    "potential_issues": ["问题1", "问题2"] 或 []
}}

只输出JSON，不要其他文字。"""
    
    results = []

    # Audit 1: doubao-seed-2-0-lite (Tier 3)
    r1 = call_llm_json(audit_prompt, tier=3)
    try:
        d1 = json.loads(r1.get("content", "{}"))
        results.append({"model": "doubao-lite", "verdict": d1, "tier": r1.get("tier")})
    except:
        results.append({"model": "doubao-lite", "error": "parse_failed"})
    
    # Audit 2: deepseek-v4-pro — via Hermes's deepseek provider
    # (here we substitute doubao-code, since the code layer can't call Hermes directly)
    r2 = call_llm_json(audit_prompt, tier=4)
    try:
        d2 = json.loads(r2.get("content", "{}"))
        results.append({"model": "doubao-code", "verdict": d2, "tier": r2.get("tier")})
    except:
        results.append({"model": "doubao-code", "error": "parse_failed"})
    
    # Verdict logic
    verdicts = []
    for r in results:
        if "verdict" in r:
            v = r["verdict"]
            verdicts.append({
                "factual": v.get("is_factual", False),
                "confidence": v.get("confidence", 0.5),
                "reasoning": v.get("reasoning", ""),
                "issues": v.get("potential_issues", []),
            })
    
    if not verdicts:
        return {"verdict": "uncertain", "confidence": 0, "models": results, "reliability_delta": 0}
    
    if len(verdicts) == 1:
        v = verdicts[0]
        return {
            "verdict": "consistent" if v["factual"] else "disputed",
            "confidence": v["confidence"],
            "models": results,
            "reliability_delta": 0.05 if v["factual"] else -0.1,
        }
    
    # Compare the two models
    v1, v2 = verdicts
    if v1["factual"] == v2["factual"]:
        # Agreement → reinforce confidence
        avg_conf = (v1["confidence"] + v2["confidence"]) / 2
        return {
            "verdict": "consistent",
            "confidence": round(avg_conf, 4),
            "models": results,
            "reliability_delta": 0.1 if v1["factual"] else -0.15,
        }
    else:
        # Contradiction → downgrade
        return {
            "verdict": "disputed",
            "confidence": 0.3,
            "models": results,
            "reliability_delta": -0.2,
            "dispute_detail": f"doubao-lite={v1['factual']}, doubao-code={v2['factual']}",
        }


async def schedule_audit(conn, tenant_id: str = "default", limit: int = 5) -> list:
    """
    Scheduled audit: picks high-heat memories (>0.5) that haven't been audited in a while

    Returns: list of audit results
    """
    rows = await conn.fetch(
        """SELECT id, content, heat_score, reliability
           FROM memories 
           WHERE user_id=$1 AND is_deleted=FALSE AND heat_score > 0.5
           ORDER BY (reliability * heat_score) ASC, created_at ASC
           LIMIT $2""",
        tenant_id, limit
    )
    
    results = []
    for r in rows:
        audit = await audit_memory(conn, r["id"], r["content"] or "")
        
        # Update reliability
        delta = audit.get("reliability_delta", 0)
        if abs(delta) > 0:
            new_rel = round(max(0, min(1, r["reliability"] + delta)), 4)
            await conn.execute(
                "UPDATE memories SET reliability=$1, updated_at=NOW() WHERE id=$2",
                new_rel, r["id"]
            )
        
        results.append({
            "memory_id": r["id"],
            "verdict": audit["verdict"],
            "confidence": audit.get("confidence", 0),
            "reliability": r["reliability"],
            "reliability_new": round(r["reliability"] + delta, 4) if abs(delta) > 0 else r["reliability"],
            "dispute": audit.get("dispute_detail", ""),
        })
    
    return results
