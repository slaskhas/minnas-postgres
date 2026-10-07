"""
Mnemosyne v5.0 — hash purification and fossil nodes
Whitepaper L3 third line of defense + compliance/fidelity

Principle:
- Deletion = original content replaced by a SHA-256 hash (irreversible)
- Metadata + topology are retained (the link chain stays intact)
- Shown as a gray fossil node in the DAG

Corresponds to whitepaper §5.6, balancing compliance and fidelity
"""
import hashlib
from datetime import datetime, timezone


def purify_content(content: str) -> str:
    """
    Hash purification: replace the original content with a SHA-256 hash

    Args:
        content: the original content

    Returns:
        sha256:hash_prefix (irreversible)
    """
    h = hashlib.sha256(content.encode()).hexdigest()
    return f"sha256:{h}"


async def soft_delete_memory(conn, memory_id: int, reason: str = "user_request") -> dict:
    """
    Soft delete + hash purification

    Flow:
    1. Original content → SHA-256 hash
    2. content is replaced with the purified value (unreadable)
    3. is_deleted = TRUE
    4. metadata records the deletion reason
    5. All relationships are kept (topology stays intact)

    Returns:
        {"memory_id": int, "status": "purified", "fossil": bool}
    """
    row = await conn.fetchrow(
        "SELECT content FROM memories WHERE id=$1 AND is_deleted=FALSE",
        memory_id
    )
    if not row:
        return {"error": "Memory not found or already deleted"}
    
    purified = purify_content(row["content"] or "")
    
    await conn.execute(
        """UPDATE memories SET 
           content=$1, is_deleted=TRUE, 
           metadata = COALESCE(metadata,'{}')::jsonb || $2::jsonb,
           updated_at=$3
           WHERE id=$4""",
        purified,
        f'{{"purified_at": "{datetime.now(timezone.utc).isoformat()}", "reason": "{reason}"}}',
        datetime.now(timezone.utc),
        memory_id
    )
    
    return {
        "memory_id": memory_id,
        "status": "purified",
        "fossil": True,
        "purified_hash": purified[:20] + "...",
        "reason": reason,
        "note": "内容已哈希净化，拓扑关系完整保留",
    }


def verify_purified(content: str) -> bool:
    """Check whether content has already been purified"""
    return content.startswith("sha256:")


async def get_fossil_nodes(conn, tenant_id: str = "default", limit: int = 20) -> list:
    """
    Query the list of fossil nodes (memories that are purified but whose topology is retained)

    Whitepaper: "a purified node is shown as a gray fossil in the DAG"
    """
    rows = await conn.fetch(
        """SELECT id, content, category, hall, metadata, 
           created_at, updated_at, reliability, heat_score
           FROM memories
           WHERE user_id=$1 AND is_deleted=TRUE AND content LIKE 'sha256:%'
           ORDER BY updated_at DESC LIMIT $2""",
        tenant_id, limit
    )
    
    import json as _json
    fossils = []
    for r in rows:
        meta = r["metadata"] or {}
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except:
                meta = {}
        
        fossils.append({
            "id": r["id"],
            "status": "fossilized",
            "hash_snippet": (r["content"] or "")[:30] + "...",
            "category": r["category"],
            "hall": r["hall"],
            "purified_at": meta.get("purified_at", ""),
            "reason": meta.get("reason", "unknown"),
            "created": str(r["created_at"])[:19],
            "reliability": r["reliability"],
            "heat": r["heat_score"],
        })
    
    return fossils
