"""
Minnas v5.0 — multi-backend model configuration
Supports: Doubao (ARK, chat only) / OpenAI-compatible (embedding + chat) / local models
"""
import os
from typing import Optional

# ── Model backend definitions ──
MODEL_BACKENDS = {
    "ark": {
        "name": "Volcengine ARK (Doubao)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "auth": lambda: f"Bearer {os.getenv('ARK_API_KEY', '')}",
        "models": {
            "chat_mini": "doubao-seed-2-0-mini-260215",
            "chat_lite": "doubao-seed-2-0-lite-260215",
            "chat_code": "doubao-seed-2-0-code-preview-260215",
            "image": "doubao-seedream-5-0-260128",
        },
        # Note: ARK is no longer used for embedding (embedding goes through the
        # OpenAI-compatible backend below)
        "dimensions": [],
    },
    "openai": {
        "name": "OpenAI-compatible (OpenAI / DeepSeek / local)",
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "auth": lambda: f"Bearer {os.getenv('OPENAI_API_KEY', '')}",
        "models": {
            "embedding": os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
            "chat_mini": os.getenv("OPENAI_CHAT_MINI", "gpt-4o-mini"),
            "chat_lite": os.getenv("OPENAI_CHAT_LITE", "gpt-4o"),
            "chat_code": os.getenv("OPENAI_CHAT_CODE", "gpt-4o"),
        },
        "dimensions": [512, 1536],
        "default_dim": 1536,
    },
}

# ── Currently active backend ──
ACTIVE_BACKEND = os.getenv("MODEL_BACKEND", "ark")


def get_backend(name: Optional[str] = None) -> dict:
    """Get a model backend's configuration"""
    backend = MODEL_BACKENDS.get(name or ACTIVE_BACKEND)
    if not backend:
        raise ValueError(f"Unknown model backend: {name or ACTIVE_BACKEND}. Available: {list(MODEL_BACKENDS.keys())}")
    return backend


def get_model(model_type: str, backend_name: Optional[str] = None) -> str:
    """
    Get the model name for a given type

    model_type: embedding / chat_mini / chat_lite / chat_code / image
    """
    backend = get_backend(backend_name)
    model = backend["models"].get(model_type)
    if not model:
        raise ValueError(f"Backend {backend['name']} does not support {model_type}")
    return model


def list_backends() -> list:
    """List all available backends"""
    return [
        {"id": k, "name": v["name"], "dimensions": v["dimensions"]}
        for k, v in MODEL_BACKENDS.items()
    ]


# ── Reranker (Tier 1.5) — based on embedding similarity ──
# The whitepaper's triple-recall design calls for a reranker. Doubao has no
# standalone reranker API, so we use embedding cosine similarity as a lightweight
# substitute.
def rerank_by_similarity(query_embedding: list, documents: list,
                         doc_embeddings: list, top_k: int = 5) -> list:
    """
    Lightweight reranker based on cosine similarity

    Args:
        query_embedding: query vector
        documents: list of documents
        doc_embeddings: list of document vectors (same order as documents)
        top_k: return the top N results

    Returns:
        the reranked list of documents
    """
    import numpy as np

    q = np.array(query_embedding)
    scores = []
    for i, emb in enumerate(doc_embeddings):
        d = np.array(emb)
        sim = np.dot(q, d) / (np.linalg.norm(q) * np.linalg.norm(d) + 1e-8)
        scores.append((i, float(sim)))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [documents[i] for i, _ in scores[:top_k]]
