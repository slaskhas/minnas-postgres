"""
Mnemosyne v5.0 — model routing engine v2.0
Full implementation of the whitepaper's L3 compute scheduling layer

Tier 1: doubao-embedding-vision   → vectorization (128 tokens/item, ¥0.0001)
Tier 2: doubao-seed-2-0-mini      → fast classification/summarization (¥0.001/1K tokens)
Tier 3: doubao-seed-2-0-lite      → primary distillation, JSON mode (¥0.003/1K tokens)
Tier 4: deepseek-v4-pro           → heterogeneous audit/conflict detection (¥0.015/1K tokens)
Tier 5: doubao-seedream           → visualization assets (billed per image)
"""
import urllib.request
import urllib.error
import json
import re
import sys, os
import time
from typing import Dict, Optional, List

# Compatibility import
try:
    from .config import (ARK_API_KEY, ARK_BASE, DOUBAO_MINI, DOUBAO_LITE, DOUBAO_CODE,
                         DEEPSEEK_PRO, DEEPSEEK_FLASH, TMT_MAX_RETRIES)
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import (ARK_API_KEY, ARK_BASE, DOUBAO_MINI, DOUBAO_LITE, DOUBAO_CODE,
                        DEEPSEEK_PRO, DEEPSEEK_FLASH, TMT_MAX_RETRIES)

# v6.5 dual-backbone: DeepSeek primary (distillation/audit), Doubao secondary (mini fast classification)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

# ── Model tier definitions ──
TIERS = {
    1: {"model": "doubao-embedding-vision-251215", "type": "embedding", "cost_per_1k": 0.0001},
    2: {"model": DOUBAO_MINI, "type": "chat", "cost_per_1k": 0.001, "max_tokens": 500},
    # v6.5: Tier 3's primary distillation model switched to DeepSeek (Doubao's
    # json_object mode 400s on long prompts; DeepSeek handles 60K fine and fast)
    3: {"model": DEEPSEEK_FLASH, "type": "chat", "cost_per_1k": 0.003, "max_tokens": 2000, "provider": "deepseek"},
    4: {"model": DEEPSEEK_PRO, "type": "chat", "cost_per_1k": 0.015, "max_tokens": 2048, "provider": "deepseek"},
    5: {"model": "doubao-seedream-5-0-260128", "type": "image", "cost_per_image": 0.02},
}

# ── Cost stats ──
_cost_stats: Dict[str, dict] = {}  # {tier: {calls, tokens, cost}}

# ── Two-tier semantic cache ──
_cache: Dict[str, dict] = {}
_embed_cache: Dict[str, List[float]] = {}
MAX_CACHE = 500


def _call_ark(messages: list, model: str, max_tokens: int = 500,
              response_format: Optional[dict] = None, temperature: float = 0.3,
              provider: Optional[str] = None) -> dict:
    # v6.5 dual-backbone routing: provider=deepseek → DeepSeek API, default → Doubao ARK
    if provider == "deepseek":
        api_key = DEEPSEEK_API_KEY
        base = DEEPSEEK_BASE
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured")
    else:
        api_key = ARK_API_KEY
        base = ARK_BASE
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format
    if provider == "deepseek":
        # Disable DeepSeek V4's thinking chain (not needed for distillation), speeds up response
        payload["thinking"] = {"type": "disabled"}

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
        return {
            "content": data['choices'][0]['message']['content'],
            "tokens": data['usage']['total_tokens'],
            "model": model,
        }


def call_llm(prompt: str, tier: int = 3, json_mode: bool = False, 
             temperature: float = 0.3, no_cache: bool = False) -> dict:
    """
    Tiered routing + automatic upgrade/downgrade + caching

    Args:
        prompt: the prompt
        tier: starting tier 2-4 (2=mini, 3=lite, 4=code)
        json_mode: structured JSON output
        temperature: temperature (Tier 2-4 only)
        no_cache: skip the cache

    Returns:
        {"content": str, "tokens": int, "model": str, "tier": int, "cost": float, "cache_hit": bool}
    """
    # Cache check
    cache_key = f"t{tier}:j{json_mode}:t{temperature}:{hash(prompt)}"
    if not no_cache and cache_key in _cache:
        result = _cache[cache_key].copy()
        result["cache_hit"] = True
        return result

    messages = [{"role": "user", "content": prompt}]
    # v6.5 fix for Doubao's json_object 400 on long prompts:
    # response_format=json_object + prompt >~4500 chars → Doubao returns HTTP 400
    # (threshold observed empirically). Workaround: above the threshold, drop
    # response_format automatically and rely on parse_json_response upstream to
    # extract the JSON.
    JSON_FMT_SAFE_LEN = 4000
    fmt = ({"type": "json_object"} if (json_mode and len(prompt) <= JSON_FMT_SAFE_LEN) else None)
    
    current_tier = tier
    last_error = None
    
    for attempt in range(TMT_MAX_RETRIES):
        tinfo = TIERS.get(current_tier, TIERS[3])
        
        try:
            t0 = time.time()
            result = _call_ark(messages, tinfo["model"], 
                              tinfo.get("max_tokens", 800), 
                              response_format=fmt, temperature=temperature,
                              provider=tinfo.get("provider"))
            elapsed = time.time() - t0
            
            # JSON validation
            if json_mode:
                try:
                    content = result["content"]
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        json.loads(json_match.group())
                        result["content"] = json_match.group()
                    else:
                        json.loads(content)
                except json.JSONDecodeError:
                    if current_tier < 4:
                        current_tier += 1
                        continue
                    result["content"] = "{}"
            
            # Cost accounting
            cost = (result["tokens"] / 1000) * tinfo["cost_per_1k"]
            
            final = {
                "content": result["content"],
                "tokens": result["tokens"],
                "model": tinfo["model"],
                "tier": current_tier,
                "cost": round(cost, 6),
                "cache_hit": False,
                "latency_ms": round(elapsed * 1000),
                "upgraded": current_tier > tier,
            }
            
            # Cost stats
            tk = str(current_tier)
            if tk not in _cost_stats:
                _cost_stats[tk] = {"calls": 0, "tokens": 0, "cost": 0.0}
            _cost_stats[tk]["calls"] += 1
            _cost_stats[tk]["tokens"] += result["tokens"]
            _cost_stats[tk]["cost"] += cost
            
            # Cache
            if len(_cache) >= MAX_CACHE:
                _cache.pop(next(iter(_cache)))
            _cache[cache_key] = final
            
            return final
            
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Connection/network errors: upgrading tier is pointless (service down
            # or network slow) — fail fast instead
            last_error = e
            break
        except Exception as e:
            last_error = e
            if current_tier < 4:
                current_tier += 1
            else:
                break
    
    return {
        "content": "",
        "tokens": 0,
        "model": "",
        "tier": 0,
        "cost": 0,
        "cache_hit": False,
        "latency_ms": 0,
        "upgraded": False,
        "error": str(last_error),
    }


def call_llm_json(prompt: str, tier: int = 3) -> dict:
    """Convenience wrapper for JSON mode"""
    return call_llm(prompt, tier=tier, json_mode=True)


def call_llm_fast(prompt: str) -> dict:
    """Tier 2 fast mode"""
    return call_llm(prompt, tier=2)


def get_cost_stats() -> dict:
    """Get cost stats"""
    total = sum(s["cost"] for s in _cost_stats.values())
    return {
        "by_tier": _cost_stats,
        "total_cost": round(total, 6),
        "currency": "CNY (estimated)",
    }


def get_cache_stats() -> dict:
    """Get cache stats"""
    return {
        "llm_cache_size": len(_cache),
        "llm_cache_max": MAX_CACHE,
        "embed_cache_size": len(_embed_cache),
    }


def get_embed_cached(text: str) -> Optional[List[float]]:
    """Get a cached vector"""
    return _embed_cache.get(text)


def set_embed_cached(text: str, embedding: List[float]):
    """Cache a vector"""
    if len(_embed_cache) >= MAX_CACHE:
        _embed_cache.pop(next(iter(_embed_cache)))
    _embed_cache[text] = embedding
