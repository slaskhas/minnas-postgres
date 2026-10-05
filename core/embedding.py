"""
Mnemosyne v7.7.0 — Embedding 抽象层 (深度优化版)
主后端: OpenAI 兼容 embeddings API (OpenAI / Azure OpenAI / 本地 vLLM/Ollama)
维度: 1536 (用户确认)

v7.7.0 优化:
  1. 并发调用: 逐条并发请求 (简单可靠, 不依赖 provider 的批量 input 支持)
  2. LRU 缓存(OrderedDict 标准实现): 同内容不重算 (prefetch/搜索/同步复用)
  3. 指数退避重试: 429/5xx 自动重试 (3 次, 1s→2s→4s)
  4. 并发可配置: MAX_CONCURRENT 支持环境变量覆盖 (适配不同 API 配额)
  5. 缓存持久化: 重启后热内容仍命中 (JSON 落盘, 文件 600 权限, 可开关)
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

# ── 配置 (环境变量可覆盖) ──
MAX_CONCURRENT = int(os.environ.get("EMBED_MAX_CONCURRENT", "8"))
MAX_RETRIES = int(os.environ.get("EMBED_MAX_RETRIES", "3"))
RETRY_BASE = float(os.environ.get("EMBED_RETRY_BASE", "1.0"))
CACHE_SIZE = int(os.environ.get("EMBED_CACHE_SIZE", "2048"))
CACHE_FILE = os.environ.get(
    "EMBED_CACHE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "embedding_cache.json"),
)
USE_DISK_CACHE = os.environ.get("EMBED_DISK_CACHE", "1") == "1"

# ── 内存 LRU 缓存 (OrderedDict 标准实现: 命中即 move_to_end, 超限淘汰最久未用) ──
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
                # 超限则保留最近 CACHE_SIZE 条
                while len(_cache) > CACHE_SIZE:
                    _cache.popitem(last=False)
    except Exception:
        # 缓存损坏 → 自愈: 清空重建 (防错误向量长期被用)
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
        # 敏感数据防护: 文件 600 权限
        try:
            os.chmod(CACHE_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    except Exception:
        pass


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class EmbeddingDimensionError(RuntimeError):
    """Provider 实际返回的向量维度与 EMBED_DIM 不符 — 配置问题, 不可通过重试解决
    (例如 Ollama 对超出模型原生维度的 `dimensions` 请求会静默忽略, 返回原生长度)"""


def _call_openai_single(text: str) -> List[float]:
    """单条调用 OpenAI 兼容 embeddings API (带重试)"""
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
                        f"embedding 维度不匹配: 期望 {EMBED_DIM}, 实际 {len(emb_val)} "
                        f"(model={EMBED_MODEL}, url={EMBED_URL}) — 请检查 EMBED_DIM/"
                        f"OPENAI_EMBED_MODEL 是否与该 provider 实际输出维度一致"
                    )
                return emb_val
            last_err = f"empty response: {str(data)[:200]}"
        except EmbeddingDimensionError:
            raise
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.reason}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(RETRY_BASE * (2 ** attempt))
                continue
            break
        except Exception as e:
            last_err = str(e)
            time.sleep(RETRY_BASE * (2 ** attempt))
    raise RuntimeError(f"embedding API 失败: {last_err}")


def get_embedding(texts: List[str]) -> List[List[float]]:
    """同步版本 — LRU 缓存 + 并发调用 + 重试 (用于 run_in_executor)
    逐条并发请求, MAX_CONCURRENT=8 并行"""
    _load_disk_cache()
    if not texts:
        return []
    # 1. 查缓存
    results: List[Optional[List[float]]] = [None] * len(texts)
    to_fetch: List[Tuple[int, str]] = []
    with _cache_lock:
        for i, t in enumerate(texts):
            k = _cache_key(t)
            if k in _cache:
                results[i] = _cache[k]
                _cache.move_to_end(k)  # LRU: 命中即置最近
            else:
                to_fetch.append((i, t))
    # 2. 全部命中 → 直接返回
    if not to_fetch:
        return [r for r in results]  # type: ignore
    # 3. 并发取未命中的
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
            # LRU 淘汰: 超限移除最久未用
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
        raise RuntimeError(f"embedding 部分失败: {len(missing)}/{len(texts)} 条无结果")
    return [r for r in results]  # type: ignore


async def get_embedding_async(texts: List[str]) -> List[List[float]]:
    """异步版本 — 通过 run_in_executor 避免阻塞 uvicorn 事件循环"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(get_embedding, texts))


def get_embedding_single(text: str) -> List[float]:
    """单个文本的便捷包装"""
    return get_embedding([text])[0]


def clear_cache() -> None:
    """清空缓存 (维护用)"""
    with _cache_lock:
        _cache.clear()
    _save_disk_cache()
