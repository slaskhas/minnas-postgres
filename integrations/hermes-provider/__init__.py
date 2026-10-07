"""
Mnemosyne Memory Provider — replaces OpenViking, uses the in-house memory palace.

Provider v1.1.0 (2026-07-29)
  - v1.0.0: initial version, full sync_turn + hot memories + prefetch
  - v1.1.0: write filtering (skip low-value messages) + first-turn cold start + formatting improvements (cat_emoji)
  - Mnemosyne dependency: v5.5.1+ (production server)
  - Compatible with: Hermes v0.19.0+

Connects over an SSH tunnel to the Mnemosyne REST API on the production server
(localhost:18010). Provides full memory storage, semantic retrieval, TMT-tier
distillation, and heat management.

Configuration (profile-scoped .env):
  MNEMOSYNE_ENDPOINT — API address (default: http://127.0.0.1:18010)
  MNEMOSYNE_USER_ID — user ID (default: default)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://127.0.0.1:18010"
_DEFAULT_USER_ID = "default"
_API_TIMEOUT = 15.0
# recall includes server-side LLM distillation (measured at 13s+); given its own
# relaxed timeout so an occasional slow response isn't mistaken for an empty result
_RECALL_TIMEOUT = 60.0
# Idempotent retrieval POST paths: safe to retry on connection/timeout errors
# (write paths are never retried, to avoid duplicate writes)
_RETRYABLE_POST = {
    "/memories/search",
    "/memories/search-chunks",
    "/memories/tree",
    "/memories/stats",
    "/memories/chunks/stats",
    "/memories/heat-top",
    "/memories/conflicts",
    "/tmt/recall",
    "/tmt/recall/simple",
    "/tmt/tree",
    "/dialectic",
    "/graph/search",
    "/beliefs/search",
    "/halls/stats",
    "/projects/",
}
_HEADER_API_KEY = "X-API-Key"

# ── Tool schemas ─────────────────────────────────────────

SEARCH_SCHEMA = {
    "name": "mnemosyne_search",
    "description": "Four-dimensional search of the Mnemosyne memory palace (semantic + keyword + heat + graph). Returns the best-matching historical memories.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords"},
            "user_id": {"type": "string", "description": "User ID (defaults to the current user)"},
            "limit": {"type": "integer", "description": "Number of results to return", "default": 5},
            "category": {"type": "string", "description": "Filter by category"},
        },
        "required": ["query"],
    },
}

REMEMBER_SCHEMA = {
    "name": "mnemosyne_remember",
    "description": "Actively store an important memory in the Mnemosyne memory palace. Automatically added to the semantic index and heat system.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Memory content"},
            "category": {
                "type": "string",
                "enum": ["fact", "experience", "belief", "chat", "work", "note", "test"],
                "description": "Memory category",
                "default": "fact",
            },
            "importance": {"type": "number", "description": "Importance, 0-1", "default": 0.5},
        },
        "required": ["content"],
    },
}

RECALL_SCHEMA = {
    "name": "mnemosyne_recall",
    "description": "TMT smart recall: cross-tier (L1→L3) comprehensive retrieval from the memory palace. Deeper than search — includes session distillation and daily summaries.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Content to retrieve"},
            "max_results": {"type": "integer", "description": "Maximum number of results", "default": 5},
        },
        "required": ["query"],
    },
}

TREE_SCHEMA = {
    "name": "mnemosyne_tree",
    "description": "Browse the TMT memory tree structure: L1 fragments / L2 sessions / L3 daily / L4 weekly / L5 profile — a quick overview of the whole memory system.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

HOT_SCHEMA = {
    "name": "mnemosyne_hot_memories",
    "description": "Get the currently hottest memories. A quick way to see the most important recent information.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of results to return", "default": 5},
            "min_heat": {"type": "number", "description": "Minimum heat threshold", "default": 0.3},
        },
    },
}

PALACE_SUMMON_SCHEMA = {
    "name": "mnemosyne_palace_summon",
    "description": "Three-channel summon for the magic memory palace: ① name (exact hit on accession number/title/tag) ② guide (narrow via the category tree) ③ resonance (vector-semantic fallback). More accurate and faster than semantic search for precisely looking up configs/keys/paths/projects.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Query content (accession number/tag/title/keyword)"},
            "user_id": {"type": "string", "description": "User ID (defaults to the current user)"},
            "top_k": {"type": "integer", "description": "Number of results to return", "default": 5},
        },
        "required": ["query"],
    },
}

DIALECTIC_SCHEMA = {
    "name": "mnemosyne_dialectic",
    "description": "Dialectical reasoning: searches memories with attached L2/L3 session context, returning a structured memory tree for the LLM to synthesize. Deeper than search — includes temporal relationships.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords"},
            "limit": {"type": "integer", "description": "Number of results to return", "default": 3},
        },
        "required": ["query"],
    },
}

TIERED_READ_SCHEMA = {
    "name": "mnemosyne_tiered_read",
    "description": "Three-tier memory read: L5 summary (200 chars) / L3 overview (800 chars + session summary) / L1 full text (linked fragments + daily summary). Smarter tiering than a direct lookup.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "integer", "description": "Memory ID"},
            "level": {"type": "string", "description": "Read level: L5/L3/L1", "default": "L3"},
        },
        "required": ["memory_id"],
    },
}

CONFLICT_SCHEMA = {
    "name": "mnemosyne_conflicts",
    "description": "View the list of memories with detected contradictions/conflicts. When a conflict is detected, the older memory is automatically marked as expired and evidence is kept.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of results to return", "default": 10},
        },
    },
}

WIKI_SCHEMA = {
    "name": "mnemosyne_wiki",
    "description": "Query knowledge-base (Wiki) pages — a full-text snapshot archive of papers/articles/proposals. Supports: search (semantic full-text search), get (view full text by ID), by_source (verify by source path), list (listing). Use it when looking up paper/proposal details — it's there to be used.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "search (semantic search) / get (view by ID) / by_source (verify by source) / list (listing)", "default": "search"},
            "query": {"type": "string", "description": "search mode: natural-language query"},
            "page_id": {"type": "integer", "description": "get mode: page ID"},
            "source_path": {"type": "string", "description": "by_source mode: source file path"},
            "source_url": {"type": "string", "description": "by_source mode: source URL"},
            "top_k": {"type": "integer", "description": "search mode: number of results", "default": 5},
            "limit": {"type": "integer", "description": "list mode: number of results", "default": 10},
        },
    },
}

MEDIA_SCHEMA = {
    "name": "mnemosyne_media",
    "description": "Manage media memories (files/images/links). Supports list, get, create.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list/get/create", "default": "list"},
            "media_id": {"type": "integer", "description": "get mode: media ID"},
            "content": {"type": "string", "description": "create mode: content description"},
            "media_type": {"type": "string", "description": "create mode: file/image/link", "default": "file"},
            "media_url": {"type": "string", "description": "create mode: file path/URL"},
            "limit": {"type": "integer", "description": "list mode: number of results", "default": 10},
        },
    },
}


# ── HTTP client ──────────────────────────────────────────

def _get_httpx():
    try:
        import httpx
        return httpx
    except ImportError:
        return None


class _MnemosyneClient:
    """Thin HTTP client wrapping the Mnemosyne REST API."""

    def __init__(self, endpoint: str, user_id: str):
        self._endpoint = endpoint.rstrip("/")
        self._api_base = f"{self._endpoint}/api/v1"
        self._user_id = user_id
        self._httpx = _get_httpx()
        if self._httpx is None:
            raise ImportError("httpx is required: pip install httpx")
        headers = {}
        api_key = os.environ.get("MNEMOSYNE_API_KEY", "")
        if api_key:
            headers[_HEADER_API_KEY] = api_key
        self._http = self._httpx.Client(timeout=_API_TIMEOUT, headers=headers)

    def _call(self, method: str, path: str, retry: bool = True, **kwargs) -> dict:
        """Unified API call. Connection/timeout errors are retried once automatically
        for idempotent retrieval requests.

        retry=False, or non-retrieval paths (write operations), are never retried,
        to avoid duplicate writes.
        """
        url = f"{self._api_base}{path}"
        can_retry = retry and (
            method.upper() == "GET"
            or (method.upper() == "POST" and path in _RETRYABLE_POST)
        )
        last_error: Optional[Exception] = None
        for attempt in range(2 if can_retry else 1):
            try:
                r = self._http.request(method, url, **kwargs)
                r.raise_for_status()
                return r.json() if r.text else {}
            except Exception as e:
                last_error = e
                if attempt == 0 and can_retry:
                    logger.debug("mnemosyne %s %s failed (%s), retrying…", method, path, e)
                    time.sleep(0.5)
                    continue
        detail = ""
        if last_error is not None:
            resp = getattr(last_error, "response", None)
            if resp is not None:
                detail = getattr(resp, "text", "")[:300]
        return {"error": str(last_error) if last_error else "unknown error", "detail": detail}

    def health(self) -> bool:
        """Health check with retry: gives a slow first response a second chance,
        to avoid wrongly declaring the service down."""
        for attempt in range(2):
            try:
                r = self._http.get(f"{self._endpoint}/api/v1/echo", timeout=8.0)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            if attempt == 0:
                time.sleep(0.5)
        return False

    def search_memories(self, query: str, limit: int = 5, category: str = "") -> list:
        payload = {"query": query, "user_id": self._user_id, "top_k": limit}
        if category:
            payload["category"] = category
        result = self._call("POST", "/memories/search", json=payload)
        if "error" in result:
            return []
        # Tolerate different response shapes
        if isinstance(result, dict):
            return result.get("memories", result.get("results", result.get("items", [])))
        return result if isinstance(result, list) else []

    def dialectic_search(self, query: str, limit: int = 3) -> dict:
        """Dialectical reasoning: search + session context, returns a structured memory tree."""
        payload = {"query": query, "user_id": self._user_id, "max_memories": limit}
        result = self._call("POST", "/dialectic", json=payload)
        if "error" in result:
            return {"memories": [], "context": [], "error": result["error"]}
        return result

    def tiered_read(self, memory_id: int, level: str = "L3") -> dict:
        """Three-tier read: L5 summary / L3 overview / L1 full text + context."""
        result = self._call("GET", f"/memories/{memory_id}/tiered",
                           params={"level": level, "user_id": self._user_id})
        if "error" in result:
            return {"error": result["error"]}
        return result

    def get_conflicts(self, limit: int = 10) -> dict:
        """View contradicting/conflicting memories."""
        return self._call("GET", "/memories/conflicts",
                         params={"user_id": self._user_id, "limit": limit})

    def list_wiki(self, limit: int = 10) -> list:
        return self._call("GET", "/wiki", params={"user_id": self._user_id, "limit": limit})

    def get_wiki_page(self, page_id: int) -> dict:
        return self._call("GET", f"/wiki/{page_id}")

    def search_wiki(self, query: str, top_k: int = 5) -> list:
        """Semantic search over the Wiki knowledge base (v7.4: queries wiki_pages.embedding HNSW directly)."""
        return self._call("POST", "/wiki/search",
                          json={"query": query, "user_id": self._user_id, "top_k": top_k})

    def get_wiki_by_source(self, source_path: str = "", source_url: str = "") -> dict:
        """Verify a snapshot by exact source (v7.4 tamper-proof archive)."""
        params = {"user_id": self._user_id}
        if source_path:
            params["source_path"] = source_path
        if source_url:
            params["source_url"] = source_url
        return self._call("GET", "/wiki/by-source", params=params)

    def list_media(self, limit: int = 20, media_type: str = "") -> list:
        params = {"user_id": self._user_id, "limit": limit}
        if media_type:
            params["media_type"] = media_type
        return self._call("GET", "/media", params=params)

    def get_media(self, media_id: int) -> dict:
        return self._call("GET", f"/media/{media_id}")

    def create_media(self, content: str, media_type: str = "file",
                     media_url: str = "", media_hash: str = "") -> dict:
        return self._call("POST", "/media", params={
            "content": content, "media_type": media_type, "media_url": media_url,
            "media_hash": media_hash, "user_id": self._user_id
        })

    def store_memory(self, content: str, category: str = "fact",
                     importance: float = 0.5, source: str = "hermes") -> dict:
        payload = {
            "user_id": self._user_id,
            "content": content,
            "category": category,
            "importance": importance,
            "source": source,
        }
        return self._call("POST", "/memories", json=payload)

    def list_memories(self, limit: int = 10) -> list:
        result = self._call("GET", f"/memories", params={"user_id": self._user_id, "limit": limit})
        if "error" in result:
            return []
        if isinstance(result, dict):
            return result.get("items", result.get("results", []))
        return result if isinstance(result, list) else []

    def get_hot_memories(self, limit: int = 5, min_heat: float = 0.0) -> list:
        params = {"user_id": self._user_id, "limit": limit, "min_heat": min_heat}
        result = self._call("GET", "/memories/heat-top", params=params)
        if "error" in result:
            return []
        if isinstance(result, dict):
            return result.get("memories", result.get("results", result.get("items", [])))
        return result if isinstance(result, list) else []

    def get_tmt_tree(self) -> dict:
        return self._call("GET", f"/tmt/tree/{self._user_id}")

    def consolidate_session(self) -> dict:
        return self._call("POST", "/tmt/consolidate/session",
                          json={"user_id": self._user_id})

    def consolidate_daily(self) -> dict:
        return self._call("POST", "/tmt/consolidate/daily",
                          json={"user_id": self._user_id})

    def recall_simple(self, query: str) -> list:
        """Fast recall (no LLM needed)."""
        result = self._call("GET", f"/tmt/recall/simple",
                            params={"user_id": self._user_id, "q": query})
        if "error" in result:
            return self.search_memories(query)  # fallback
        if isinstance(result, dict):
            return result.get("results", result.get("memories", []))
        return result if isinstance(result, list) else []

    def recall(self, query: str, max_results: int = 5) -> dict:
        """3-stage smart recall (includes LLM distillation). Server-side distillation is slow, so it uses its own long timeout."""
        payload = {
            "user_id": self._user_id,
            "query": query,
            "max_results": max_results,
        }
        result = self._call("POST", "/tmt/recall", json=payload, timeout=_RECALL_TIMEOUT)
        return result


# ── MemoryProvider implementation ──────────────────────────────────

class MnemosyneMemoryProvider(MemoryProvider):

    def __init__(self):
        self._client: Optional[_MnemosyneClient] = None
        self._endpoint = ""
        self._user_id = ""
        self._session_id = ""
        self._turn_count = 0
        self._sync_thread: Optional[threading.Thread] = None
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        return bool(os.environ.get("MNEMOSYNE_ENDPOINT", _DEFAULT_ENDPOINT))

    def get_config_schema(self):
        return [
            {
                "key": "endpoint",
                "description": "Mnemosyne API address",
                "required": True,
                "default": _DEFAULT_ENDPOINT,
                "env_var": "MNEMOSYNE_ENDPOINT",
            },
            {
                "key": "user_id",
                "description": "Mnemosyne user ID",
                "default": _DEFAULT_USER_ID,
                "env_var": "MNEMOSYNE_USER_ID",
            },
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        self._endpoint = os.environ.get("MNEMOSYNE_ENDPOINT", _DEFAULT_ENDPOINT)
        self._user_id = os.environ.get("MNEMOSYNE_USER_ID", _DEFAULT_USER_ID)
        self._session_id = session_id
        self._turn_count = 0
        self._hot_cache = []  # preheat cache

        try:
            self._client = _MnemosyneClient(self._endpoint, self._user_id)
            if not self._client.health():
                logger.warning("Mnemosyne at %s not reachable", self._endpoint)
                self._client = None
            else:
                # P3-T2a: startup preheat — preload popular memories
                self._preheat_memories()
        except ImportError:
            logger.warning("httpx not installed — Mnemosyne plugin disabled")
            self._client = None

    def _preheat_memories(self) -> None:
        """Preload the hottest memories into the cache at startup."""
        try:
            hot = self._client.get_hot_memories(limit=5, min_heat=0.5)
            if hot:
                self._hot_cache = [
                    {"content": m.get("content", "")[:200], "heat": m.get("heat_score", 0)}
                    for m in hot if isinstance(m, dict)
                ]
                logger.info("preheat: %d hot memories loaded", len(self._hot_cache))
        except Exception as e:
            logger.debug("preheat failed: %s", e)

    def _fetch_hot_memories(self) -> str:
        """Fetch the hottest non-test memories, formatted as inline text. Applies time decay."""
        try:
            from datetime import datetime, timezone
            client = _MnemosyneClient(self._endpoint, self._user_id)
            hot = client.get_hot_memories(limit=12, min_heat=0.1)
            if not hot:
                return ""
            now = datetime.now(timezone.utc)
            scored = []
            for m in hot:
                if not isinstance(m, dict):
                    continue
                content = (m.get("content", "") or "")[:150].replace("\n", " ")
                heat = m.get("heat_score", 0)
                tier = m.get("tier", "L1")
                cat = m.get("category", "?")
                m_id = m.get("id", "")
                # Skip test/meaningless memories (keep the Chinese keyword below — it
                # matches against actual Chinese memory content, not a comment)
                if "测试" in content[:20] or heat < 0.1:
                    continue
                # ── Time decay ──
                effective_heat = heat
                if m.get("created"):
                    try:
                        created_str = m["created"]
                        if created_str.endswith("Z"):
                            created_str = created_str[:-1] + "+00:00"
                        created = datetime.fromisoformat(created_str)
                        age_days = (now - created).total_seconds() / 86400
                        # Exponential decay: 7-day half-life, drops to 12.5% at 21 days
                        if age_days > 0:
                            effective_heat = heat * (0.5 ** (age_days / 7))
                    except Exception:
                        pass
                if effective_heat < 0.15:
                    continue
                scored.append((effective_heat, heat, tier, cat, m_id, content))
            # Sort by effective heat
            scored.sort(key=lambda x: x[0], reverse=True)
            lines = []
            for eff_heat, raw_heat, tier, cat, m_id, content in scored[:3]:
                age_note = ""
                if eff_heat < raw_heat * 0.5:
                    age_note = " [decayed]"
                lines.append(f"- [{tier}|{cat}|{eff_heat:.2f}{age_note}] {content}")
            return "\n".join(lines)
        except Exception:
            return ""

    def system_prompt_block(self) -> str:
        if not self._client:
            return ""
        try:
            # v7.0 palace status (replaces the old TMT L1-L5 stats; time-based distillation is retired)
            palace_stats = {}
            try:
                palace_stats = self._client._call("GET", f"/palace/status?user_id={self._user_id}")
            except Exception:
                pass

            # Fetch hot memories (with decay + semantic-relevance filtering)
            # Note: no context_filter is set on the first turn, since there's no user message yet
            hot_block = self._fetch_hot_memories()

            # Stats overview
            try:
                stats = self._client._call("GET", f"/memories/stats?user_id={self._user_id}")
                total_mem = stats.get("total", "?")
                by_cat = stats.get("by_category", {})
                top_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:3]
                cat_str = " | ".join(f"{k}:{v}" for k, v in top_cats)
            except Exception:
                total_mem = "?"
                cat_str = "?"

            archive_coverage = palace_stats.get("archive_coverage", "?")
            tome_cards = palace_stats.get("tome_cards", "?")
            tax_count = len(palace_stats.get("taxonomy", [])) if isinstance(palace_stats.get("taxonomy"), list) else "?"

            parts = [
                f"Mnemosyne memory palace (Endpoint: {self._endpoint})",
                f"User: {self._user_id} | Total memories: {total_mem} | Categories: {cat_str}",
                f"🏰 Palace: archive coverage {archive_coverage}% | catalog cards {tome_cards} | category tree {tax_count} nodes",
            ]
            if hot_block:
                parts.append(
                    "🔥 Hot memories (aggregated across sessions, sorted after time decay. For session continuation prefer session_search):\n"
                    f"{hot_block}"
                )
            parts.append(
                "📌 Use mnemosyne_search/mnemosyne_recall to retrieve relevant memories.\n"
                "📌 When the user brings up a known topic (tech/project/preference/etc.), proactively search memory and cite it."
            )
            return "\n\n".join(parts)
        except Exception:
            return (
                "# Mnemosyne memory palace\n"
                f"Endpoint: {self._endpoint}\n"
                "📌 Use mnemosyne_search/mnemosyne_recall to retrieve memories.\n"
                "📌 When the user brings up a known topic, proactively search and cite it."
            )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return the background-prefetched result (for context injection).

        Read optimization v1.1: when there's no prefetch result on the first turn,
        fill in with hot memories instead."""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            # First turn: use hot memories as cold-start context
            try:
                hot = self._fetch_hot_memories()
                if hot:
                    return f"## Mnemosyne hot memories (cold start)\n{hot}"
            except Exception:
                pass
            return ""
        return f"## Mnemosyne related memories\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Search for relevant memories in the background, inject context on the next turn.

        Comparable to Honcho dialectic: appends a dialectical search (with L2/L3
        session context) for complex queries.
        """
        if not self._client or not query:
            return

        def _run():
            try:
                client = _MnemosyneClient(self._endpoint, self._user_id)
                memories = client.search_memories(query, limit=5)
                if not memories:
                    memories = client.recall_simple(query)

                # For complex queries (>15 chars), append a dialectical search
                dialectic_context = ""
                if len(query) > 15:
                    try:
                        dialectic = client.dialectic_search(query, limit=1)
                        if dialectic and isinstance(dialectic, dict):
                            ctx = dialectic.get("context", [])
                            if ctx:
                                dialectic_parts = []
                                for c in ctx[:1]:
                                    if isinstance(c, dict):
                                        dialectic_parts.append(
                                            f"[{c.get('tier','?')}] {c.get('summary', str(c))[:120]}"
                                        )
                                if dialectic_parts:
                                    dialectic_context = " | Session context: " + "; ".join(dialectic_parts)
                    except Exception:
                        pass

                if not memories:
                    return

                parts = []
                for i, mem in enumerate(memories[:8]):
                    if isinstance(mem, dict):
                        content = mem.get("content", str(mem))
                        heat = mem.get("heat_score", mem.get("score", 0))
                        tier = mem.get("tier", "L1")
                        cat = mem.get("category", "?")
                        # Two-factor: skip injecting low-heat (<0.3) items to save tokens
                        try:
                            if float(heat) < 0.3:
                                continue
                        except Exception:
                            pass
                        stars = "⭐" if heat > 0.6 else ("✦" if heat > 0.3 else "·")
                        cat_emoji = {"fact":"📌","experience":"💡","belief":"🧠","chat":"💬","work":"🔧","note":"📝"}.get(cat, "📎")
                        parts.append(f"- {stars} [{tier}|{cat_emoji}|{heat:.2f}] {content[:160]}")
                    elif isinstance(mem, str):
                        parts.append(f"- · {mem[:160]}")
                    if len(parts) >= 3:  # cap of 3 prefetched items, to limit injection size
                        break

                if parts:
                    result_text = "\n".join(parts)
                    if dialectic_context:
                        result_text += f"\n{dialectic_context}"
                    with self._prefetch_lock:
                        self._prefetch_result = result_text
            except Exception as e:
                logger.debug("Mnemosyne prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="mnemosyne-prefetch"
        )
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: Optional[List[Dict[str, Any]]] = None) -> None:
        """Automatically store to Mnemosyne after every conversation turn (via the
        persistent write queue, crash-safe).

        Write optimization v1.1: filters out low-value short messages (hmm/okay/ok/
        continue/etc.) to reduce L1 noise.
        """
        if not self._client:
            return

        self._turn_count += 1

        # Clean the message (strip injected tags + filter trivial messages)
        from .message_cleaner import prepare_for_storage
        clean_user = prepare_for_storage(user_content) if user_content else ""
        clean_asst = prepare_for_storage(assistant_content) if assistant_content else ""

        # ── Write filter v1.1: skip low-value short messages ──
        # (keep the Chinese entries below — they're matched against actual chat
        # content, not comments)
        _LOW_VALUE_PATTERNS = [
            "嗯", "好的", "ok", "OK", "继续", "go on", "是的",
            "知道了", "明白", "试试", "来吧", "看看", "搞起",
        ]
        def _is_low_value(text: str) -> bool:
            stripped = text.strip().lower()
            if len(stripped) < 8:  # shorter than 8 characters
                for pat in _LOW_VALUE_PATTERNS:
                    if pat.lower() in stripped:
                        return True
            return False

        if _is_low_value(clean_user) and _is_low_value(clean_asst):
            return  # both sides are low-value, skip the whole turn

        # Write to the persistent queue (lands in SQLite immediately, no data loss)
        from .write_queue import get_queue
        q = get_queue()

        if clean_user and not _is_low_value(clean_user):
            q.enqueue(user_content=clean_user[:2000], assistant_content="",
                      category="session", source="hermes-sync")

        if clean_asst and not _is_low_value(clean_asst):
            q.enqueue(user_content="", assistant_content=clean_asst[:3000],
                      category="session" if len(clean_asst) > 100 else "chat",
                      source="hermes-sync")

        # Background sender thread (consumes from the queue)
        def _sync():
            from .write_queue import get_queue
            q = get_queue()

            if q.is_circuit_open():
                logger.debug("Mnemosyne circuit breaker OPEN, skipping this round's send")
                return

            try:
                client = _MnemosyneClient(self._endpoint, self._user_id)
                items = q.dequeue(batch_size=3)
                for item in items:
                    try:
                        if item["user_content"]:
                            client.store_memory(
                                content=f"User asked: {item['user_content'][:2000]}",
                                category="session", importance=0.4, source=item["source"]
                            )
                        if item["assistant_content"]:
                            client.store_memory(
                                content=item["assistant_content"][:3000],
                                category="session" if len(item["assistant_content"]) > 100 else "chat",
                                importance=0.3, source=item["source"]
                            )
                        q.mark_done(item["id"])
                        q.record_success()
                    except Exception as e:
                        q.mark_failed(item["id"], str(e))
                        q.record_failure()
                        logger.debug("Mnemosyne send failed (id=%d): %s", item["id"], e)
            except Exception as e:
                q.record_failure()
                logger.debug("Mnemosyne sync_turn client failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="mnemosyne-sync"
        )
        self._sync_thread.start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """On session end: trigger TMT L2 distillation.
        (v7.8: removed full-fidelity conversation_messages sync — Hermes's state.db is
         already the authoritative session store, so Mnemosyne no longer keeps a second
         copy; use session_search for the raw session text if needed.)"""
        if not self._client:
            return

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)

        if self._turn_count == 0:
            return

        # ② TMT L2 distillation
        try:
            result = self._client.consolidate_session()
            logger.info("Mnemosyne L2 consolidation triggered (%d turns): %s",
                        self._turn_count, result.get("skipped", False))
        except Exception as e:
            logger.warning("Mnemosyne session consolidation failed: %s", e)

        # ③ Archive fact extraction (v7.0): this session's newly written session
        # fragments → facts → filed into the archive
        try:
            ex = self._client._call("POST", "/palace/extract?batch=30", json={"batch": 30})
            logger.info("Mnemosyne palace extract: processed=%s facts=%s",
                        ex.get("processed", "?"), ex.get("facts_created", "?"))
        except Exception as e:
            logger.warning("Mnemosyne palace extract failed: %s", e)

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[Dict[str, Any]] = None) -> None:
        """Mirror built-in memory-tool writes to Mnemosyne."""
        if not self._client or action != "add" or not content:
            return

        category_map = {
            "user": "preference",
            "memory": "pattern",
        }
        category = category_map.get(target, "fact")

        def _write():
            try:
                client = _MnemosyneClient(self._endpoint, self._user_id)
                client.store_memory(
                    content=content,
                    category=category,
                    importance=0.6,
                    source="hermes-memorytool"
                )
            except Exception as e:
                logger.debug("Mnemosyne memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="mnemosyne-memwrite")
        t.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, REMEMBER_SCHEMA, RECALL_SCHEMA, TREE_SCHEMA, HOT_SCHEMA,
                DIALECTIC_SCHEMA, TIERED_READ_SCHEMA, CONFLICT_SCHEMA, WIKI_SCHEMA, MEDIA_SCHEMA,
                PALACE_SUMMON_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if not self._client:
            return tool_error("Mnemosyne not connected")

        try:
            if tool_name == "mnemosyne_search":
                return self._tool_search(args)
            elif tool_name == "mnemosyne_remember":
                return self._tool_remember(args)
            elif tool_name == "mnemosyne_recall":
                return self._tool_recall(args)
            elif tool_name == "mnemosyne_tree":
                return self._tool_tree(args)
            elif tool_name == "mnemosyne_hot_memories":
                return self._tool_hot(args)
            elif tool_name == "mnemosyne_palace_summon":
                return self._tool_palace_summon(args)
            elif tool_name == "mnemosyne_dialectic":
                return self._tool_dialectic(args)
            elif tool_name == "mnemosyne_tiered_read":
                return self._tool_tiered(args)
            elif tool_name == "mnemosyne_conflicts":
                return self._tool_conflicts(args)
            elif tool_name == "mnemosyne_wiki":
                return self._tool_wiki(args)
            elif tool_name == "mnemosyne_media":
                return self._tool_media(args)
            return tool_error(f"Unknown tool: {tool_name}")
        except Exception as e:
            return tool_error(str(e))

    def shutdown(self) -> None:
        for t in (self._sync_thread, self._prefetch_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Before context compression: extract key insights, store them to Mnemosyne,
        and return a compression summary.

        Comparable to Honcho dialectic + Mem0 fact extraction.
        `messages` is the list of messages about to be compressed/discarded.
        The returned text gets injected into the compression prompt so the
        compressor retains these insights.
        """
        if not self._client or not messages:
            return ""

        try:
            # Extract user questions and key AI decisions
            user_questions = []
            ai_decisions = []

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if not content or len(content) < 10:
                    continue

                if role == "user":
                    # Extract questions (ends with a question mark or contains a keyword;
                    # keep the Chinese keyword list below — it's matched against actual
                    # chat content, not a comment)
                    if "?" in content or "?" in content or "？" in content:
                        user_questions.append(content[:300])
                    elif any(kw in content[:50] for kw in ["检查", "修复", "部署", "发布", "审计"]):
                        user_questions.append(content[:200])

                elif role == "assistant":
                    # Extract key decisions/conclusions (contains ✅/❌ markers or a
                    # concluding statement; keep the Chinese keyword list below — it's
                    # matched against actual chat content, not a comment)
                    if any(kw in content[:200] for kw in ["✅", "完成", "已修复", "已部署", "结论"]):
                        ai_decisions.append(content[:400])

            if not user_questions and not ai_decisions:
                return ""

            # Build the compression insight
            blocks = []
            if user_questions:
                blocks.append(f"- User focus: {'; '.join(q[:100] for q in user_questions[:5])}")
            if ai_decisions:
                blocks.append(f"- AI decisions: {'; '.join(d[:150] for d in ai_decisions[:3])}")

            insight_text = "\n".join(blocks)

            # Sync-archive to Mnemosyne and get back the entity ID (loss-prevention
            # loop: details dropped by compression can still be recalled)
            mid = None
            try:
                resp = self._client.store_memory(
                    content=f"[Compression insight] {insight_text[:500]}",
                    category="note",
                    importance=0.5,
                    source="hermes-precompress"
                )
                mid = resp.get("id") or (resp.get("memory") or {}).get("id")
            except Exception as e:
                logger.debug("pre_compress store failed: %s", e)

            if mid:
                return f"[Compression insight archived as Mnemosyne#{mid}, full detail recallable] {insight_text}"
            return insight_text
        except Exception as e:
            logger.debug("on_pre_compress failed: %s", e)
            return ""

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """When a subagent finishes: store the task + result in the memory palace.

        Comparable to Zep temporal fact tracking — records "who did what, when".
        """
        if not self._client or not task:
            return

        try:
            # Extract a result summary (first 200 chars)
            result_summary = (result or "")[:200].replace("\n", " ")
            task_summary = task[:200].replace("\n", " ")

            content = (
                f"[Subagent] Task: {task_summary}"
            )
            if result_summary:
                content += f" | Result: {result_summary}"
            if child_session_id:
                content += f" | session={child_session_id[:20]}"

            def _store():
                try:
                    client = _MnemosyneClient(self._endpoint, self._user_id)
                    client.store_memory(
                        content=content,
                        category="work",
                        importance=0.5,
                        source="hermes-delegation"
                    )
                except Exception as e:
                    logger.debug("delegation store failed: %s", e)

            threading.Thread(target=_store, daemon=True,
                            name="mnemosyne-delegation").start()
        except Exception as e:
            logger.debug("on_delegation failed: %s", e)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Auto-refresh on session switch: check the queue + update the session ID."""
        try:
            from .write_queue import get_queue
            q = get_queue()
            pending = q.pending_count()
            if pending > 0:
                logger.info("session_switch: %d pending (consumer active)", pending)
                q.replay_pending()  # ensure the consumer keeps processing
        except Exception as e:
            logger.debug("session_switch queue check: %s", e)
        logger.debug("session_switch → %s (parent=%s, reset=%s)", new_session_id, parent_session_id, reset)

    # ── Tool implementations ──────────────────────────────────────────

    def _tool_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")
        user_id = args.get("user_id") or self._user_id
        limit = args.get("limit", 5)
        category = args.get("category", "")

        # Create a temporary client scoped to the given user_id
        client = _MnemosyneClient(self._endpoint, user_id)
        memories = client.search_memories(query, limit, category)

        if not memories:
            return json.dumps({"results": [], "total": 0})

        formatted = []
        for mem in memories:
            if isinstance(mem, dict):
                formatted.append({
                    "content": mem.get("content", "")[:300],
                    "heat": mem.get("heat_score", mem.get("score", 0)),
                    "tier": mem.get("tier", "L1"),
                    "category": mem.get("category", ""),
                    "id": mem.get("id", ""),
                    "created": mem.get("created_at", ""),
                })
            elif isinstance(mem, str):
                formatted.append({"content": mem[:300]})

        return json.dumps({"results": formatted, "total": len(formatted)}, ensure_ascii=False)

    def _tool_remember(self, args: dict) -> str:
        content = args.get("content", "")
        if not content:
            return tool_error("content is required")

        # Clean the message before storing
        from .message_cleaner import prepare_for_storage
        clean = prepare_for_storage(content)
        if not clean:
            return tool_error("content is empty after cleaning, skipping storage")

        category = args.get("category", "fact")
        importance = args.get("importance", 0.5)

        result = self._client.store_memory(clean, category, importance)
        if "error" in result:
            return tool_error(f"Store failed: {result['error']}")
        return json.dumps({
            "status": "stored",
            "id": result.get("id", "?"),
            "message": "Memory stored in the palace and added to the index",
        }, ensure_ascii=False)

    def _tool_recall(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")
        max_results = args.get("max_results", 5)

        result = self._client.recall(query, max_results)
        if "error" in result:
            # fall back to simple search
            memories = self._client.search_memories(query, max_results)
            return json.dumps({"recall": memories, "fallback": True}, ensure_ascii=False)

        return json.dumps(result, ensure_ascii=False)

    def _tool_tree(self, args: dict) -> str:
        tree = self._client.get_tmt_tree()
        return json.dumps(tree, ensure_ascii=False)

    def _tool_hot(self, args: dict) -> str:
        limit = args.get("limit", 5)
        min_heat = args.get("min_heat", 0.3)
        memories = self._client.get_hot_memories(limit, min_heat)

        formatted = []
        for mem in memories:
            if isinstance(mem, dict):
                formatted.append({
                    "content": mem.get("content", "")[:200],
                    "heat": mem.get("heat_score", 0),
                    "tier": mem.get("tier", "L1"),
                    "access_count": mem.get("access_count", 0),
                    "id": mem.get("id", ""),
                })

        return json.dumps({"hot_memories": formatted, "total": len(formatted)}, ensure_ascii=False)

    def _tool_palace_summon(self, args: dict) -> str:
        """Three-channel summon for the magic memory palace (name/guide/resonance)."""
        query = args.get("query", "")
        user_id = args.get("user_id", self._user_id)
        top_k = args.get("top_k", 5)
        if not query:
            return tool_error("query is required")
        try:
            result = self._client._call("GET", f"/palace/summon?q={quote(query)}&user_id={quote(user_id)}&top_k={top_k}")
            if isinstance(result, dict) and result.get("error"):
                # fall back to semantic search
                memories = self._client.search_memories(query, top_k)
                return json.dumps({"recall": memories, "fallback": True}, ensure_ascii=False)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return tool_error(f"palace_summon failed: {e}")

    def _tool_dialectic(self, args: dict) -> str:
        query = args.get("query", "")
        limit = args.get("limit", 3)
        if not query:
            return tool_error("query required")
        result = self._client.dialectic_search(query, limit)
        if isinstance(result, dict) and result.get("error"):
            return tool_error(result["error"])
        return json.dumps(result, ensure_ascii=False)

    def _tool_tiered(self, args: dict) -> str:
        memory_id = args.get("memory_id", 0)
        level = args.get("level", "L3")
        if not memory_id:
            return tool_error("memory_id required")
        result = self._client.tiered_read(memory_id, level)
        if isinstance(result, dict) and result.get("error"):
            return tool_error(result["error"])
        return json.dumps(result, ensure_ascii=False)

    def _tool_conflicts(self, args: dict) -> str:
        limit = args.get("limit", 10)
        result = self._client.get_conflicts(limit)
        if isinstance(result, dict) and result.get("error"):
            return tool_error(result["error"])
        return json.dumps(result, ensure_ascii=False)

    def _tool_wiki(self, args: dict) -> str:
        action = args.get("action", "search")
        if action == "search":
            query = args.get("query", "")
            if not query:
                return tool_error("query required for search action")
            top_k = args.get("top_k", 5)
            data = self._client.search_wiki(query, top_k)
            result = {"hits": data, "total": len(data)} if isinstance(data, list) else data
        elif action == "by_source":
            sp = args.get("source_path", "")
            su = args.get("source_url", "")
            if not sp and not su:
                return tool_error("source_path or source_url required for by_source action")
            result = self._client.get_wiki_by_source(source_path=sp, source_url=su)
        elif action == "get":
            page_id = args.get("page_id", 0)
            if not page_id:
                return tool_error("page_id required for get action")
            result = self._client.get_wiki_page(page_id)
        else:
            limit = args.get("limit", 10)
            data = self._client.list_wiki(limit)
            result = {"pages": data, "total": len(data)} if isinstance(data, list) else data
        if isinstance(result, dict) and result.get("error"):
            return tool_error(result["error"])
        return json.dumps(result, ensure_ascii=False)

    def _tool_media(self, args: dict) -> str:
        action = args.get("action", "list")
        if action == "get":
            mid = args.get("media_id", 0)
            if not mid:
                return tool_error("media_id required")
            result = self._client.get_media(mid)
        elif action == "create":
            content = args.get("content", "")
            if not content:
                return tool_error("content required for create")
            result = self._client.create_media(
                content, args.get("media_type", "file"),
                args.get("media_url", ""),
            )
        else:
            limit = args.get("limit", 10)
            data = self._client.list_media(limit)
            result = {"media": data, "total": len(data)} if isinstance(data, list) else data
        if isinstance(result, dict) and result.get("error"):
            return tool_error(result["error"])
        return json.dumps(result, ensure_ascii=False)


# ── Plugin entry point ────────────────────────────────────────────

def register(ctx) -> None:
    """Register Mnemosyne as a Hermes memory provider."""
    ctx.register_memory_provider(MnemosyneMemoryProvider())
