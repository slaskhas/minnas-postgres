"""
Mnemosyne v7.7.0 — embedding abstraction layer (deep-optimization edition)
Primary backend: OpenAI-compatible embeddings API (OpenAI / Azure OpenAI / local vLLM/Ollama)
Dimensions: 1536 (confirmed by user)

v7.7.0 optimizations:
  1. Concurrent calls: per-item concurrent requests (simple and reliable; doesn't
     depend on the provider supporting batched input)
  2. LRU cache (standard OrderedDict implementation): identical content isn't
     recomputed (reused across prefetch/search/sync)
  3. Exponential-backoff retry: auto-retries on 429/5xx (3 attempts, 1s→2s→4s)
  4. Configurable concurrency: MAX_CONCURRENT can be overridden via env var
     (to fit different API quotas)
  5. Cache persistence: hot content still hits after a restart (dumped to JSON,
     file mode 600, toggleable)
"""
import urllib.request
import urllib.error
import json
import asyncio
import functools
import hashlib
import os
import stat
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

try:
    from .config import OPENAI_API_KEY, EMBED_MODEL, EMBED_DIM, EMBED_URL
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import OPENAI_API_KEY, EMBED_MODEL, EMBED_DIM, EMBED_URL

# ── Config (overridable via env vars) ──
MAX_CONCURRENT = int(os.environ.get("EMBED_MAX_CONCURRENT", "8"))
MAX_RETRIES = int(os.environ.get("EMBED_MAX_RETRIES", "3"))
RETRY_BASE = float(os.environ.get("EMBED_RETRY_BASE", "1.0"))
CACHE_SIZE = int(os.environ.get("EMBED_CACHE_SIZE", "2048"))
CACHE_FILE = os.environ.get(
    "EMBED_CACHE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "embedding_cache.json"),
)
USE_DISK_CACHE = os.environ.get("EMBED_DISK_CACHE", "1") == "1"

# ── In-memory LRU cache (standard OrderedDict impl: hit → move_to_end, over limit → evict LRU) ──
_cache: "OrderedDict[str, List[float]]" = OrderedDict()
_cache_lock = threading.Lock()
_cache_loaded = False


def _load_disk_cache() -> None:
    global _cache_loaded
    if not USE_DISK_CACHE or _cache_loaded:
        return
    _cache_loaded = True
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            with _cache_lock:
                _cache.update(data)
                # over limit → keep only the most recent CACHE_SIZE entries
                while len(_cache) > CACHE_SIZE:
                    _cache.popitem(last=False)
    except Exception:
        # Corrupt cache → self-heal: clear and rebuild (avoid a bad vector being used long-term)
        with _cache_lock:
            _cache.clear()
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass


def _save_disk_cache() -> None:
    if not USE_DISK_CACHE:
        return
    try:
        with _cache_lock:
            snapshot = dict(list(_cache.items())[-CACHE_SIZE:])
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        # Sensitive-data protection: file mode 600
        try:
            os.chmod(CACHE_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    except Exception:
        pass


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class EmbeddingError(RuntimeError):
    """Base for embedding failures with an operator-actionable message
    (surfaced by main.py as HTTP 503 with the message in `detail`)."""


class EmbeddingUnavailableError(EmbeddingError):
    """The embedding backend can't be used: unreachable, model not found,
    or credentials rejected."""


class EmbeddingDimensionError(EmbeddingError):
    """The vector dimension actually returned by the provider doesn't match EMBED_DIM —
    a configuration problem that retrying won't fix (e.g. Ollama silently ignores a
    `dimensions` request that exceeds the model's native dimension and returns the
    native length instead)"""


def _describe_http_error(e: "urllib.error.HTTPError") -> str:
    """Turn an HTTP error from the embeddings endpoint into a diagnosis."""
    try:
        body = e.read().decode("utf-8", "replace")[:300]
    except Exception:
        body = ""
    where = f"model={EMBED_MODEL!r}, url={EMBED_URL}"
    if e.code == 404 or (e.code == 400 and "not found" in body.lower()):
        return (f"embedding model not found ({where}): check EMBED_MODEL / OPENAI_EMBED_MODEL "
                f"for typos and that the model is pulled/deployed on the server. Server said: {body}")
    if e.code in (401, 403):
        return f"embedding API rejected credentials, HTTP {e.code} ({where}): check OPENAI_API_KEY. {body}"
    return f"embedding API returned HTTP {e.code} {e.reason} ({where}). {body}"


def _call_openai_single(text: str) -> List[float]:
    """Call the OpenAI-compatible embeddings API for a single item (with retry)"""
    payload = json.dumps({
        "model": EMBED_MODEL,
        "input": text,
        "dimensions": EMBED_DIM,
    }).encode()
    req = urllib.request.Request(
        EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {OPENAI_API_KEY}"}
    )
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            d = data.get("data")
            emb_val = d[0].get("embedding") if d else None
            if emb_val:
                if len(emb_val) != EMBED_DIM:
                    raise EmbeddingDimensionError(
                        f"embedding dimension mismatch: expected {EMBED_DIM}, got {len(emb_val)} "
                        f"(model={EMBED_MODEL}, url={EMBED_URL}) — set EMBED_DIM to the model's native "
                        f"output dimension (EMBED_MODEL / EMBED_BASE_URL, or their OPENAI_* fallbacks)"
                    )
                return emb_val
            last_err = f"empty response: {str(data)[:200]}"
        except EmbeddingDimensionError:
            raise
        except urllib.error.HTTPError as e:
            last_err = _describe_http_error(e)
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(RETRY_BASE * (2 ** attempt))
                continue
            raise EmbeddingUnavailableError(last_err)
        except urllib.error.URLError as e:
            last_err = (f"embedding backend unreachable (url={EMBED_URL}, model={EMBED_MODEL!r}): "
                        f"{e.reason} — check EMBED_URL / OPENAI_BASE_URL and that the service is running")
            time.sleep(RETRY_BASE * (2 ** attempt))
        except Exception as e:
            last_err = f"{type(e).__name__}: {e} (url={EMBED_URL}, model={EMBED_MODEL!r})"
            time.sleep(RETRY_BASE * (2 ** attempt))
    raise EmbeddingUnavailableError(f"embedding API failed after {MAX_RETRIES} attempts: {last_err}")


def get_embedding(texts: List[str]) -> List[List[float]]:
    """Sync version — LRU cache + concurrent calls + retry (for run_in_executor)
    Per-item concurrent requests, MAX_CONCURRENT=8 in parallel"""
    _load_disk_cache()
    if not texts:
        return []
    # 1. Check cache
    results: List[Optional[List[float]]] = [None] * len(texts)
    to_fetch: List[Tuple[int, str]] = []
    with _cache_lock:
        for i, t in enumerate(texts):
            k = _cache_key(t)
            if k in _cache:
                results[i] = _cache[k]
                _cache.move_to_end(k)  # LRU: a hit becomes most-recent
            else:
                to_fetch.append((i, t))
    # 2. All hit → return directly
    if not to_fetch:
        return [r for r in results]  # type: ignore
    # 3. Concurrently fetch the misses
    def fetch_one(item):
        idx, text = item
        vec = _call_openai_single(text)
        return idx, text, vec

    def _store(idx, text, vec):
        results[idx] = vec
        with _cache_lock:
            k = _cache_key(text)
            _cache[k] = vec
            _cache.move_to_end(k)
            # LRU eviction: over limit → remove the least-recently-used
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)

    if len(to_fetch) == 1:
        idx, text, vec = fetch_one(to_fetch[0])
        _store(idx, text, vec)
    else:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as ex:
            futures = [ex.submit(fetch_one, t) for t in to_fetch]
            for f in futures:
                idx, text, vec = f.result()
                _store(idx, text, vec)
    _save_disk_cache()
    missing = [i for i, r in enumerate(results) if r is None]
    if missing:
        raise RuntimeError(f"embedding partially failed: {len(missing)}/{len(texts)} items had no result")
    return [r for r in results]  # type: ignore


async def get_embedding_async(texts: List[str]) -> List[List[float]]:
    """Async version — uses run_in_executor to avoid blocking the uvicorn event loop"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(get_embedding, texts))


def get_embedding_single(text: str) -> List[float]:
    """Convenience wrapper for a single text"""
    return get_embedding([text])[0]


def clear_cache() -> None:
    """Clear the cache (for maintenance)"""
    with _cache_lock:
        _cache.clear()
    _save_disk_cache()
