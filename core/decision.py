"""
Mnemosyne — decision-model abstraction layer (Jev-style decision models)
Primary backend: Ollama's dedicated decision route, POST <base>/systemone
(OpenAI-compatible base URL; the code appends "/systemone"). Independent of the
chat/LLM backend — the decision model is its own service (see DECISION_* vars in
config.py).

Supported question types (per Ollama "Jev-style decision models"):
  * "choice" — { type, instructions, criteria: {label -> description} }
  * "noul"   — { type, instructions }
  * "score"  — { type, instructions, criteria: [labels] }

Optimizations (mirroring core/embedding.py):
  1. Exponential-backoff retry on 429/5xx / connection errors
  2. DecisionFormatError on a response we can't interpret (a config/contract
     problem, not retryable) — analogous to EmbeddingDimensionError
"""
import urllib.request
import urllib.error
import json
import asyncio
import os
import sys
import time
from typing import Any, Dict, Optional

try:
    from .config import DECISION_URL, DECISION_MODEL, DECISION_API_KEY
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import DECISION_URL, DECISION_MODEL, DECISION_API_KEY

# ── Config (overridable via env vars) ──
MAX_RETRIES = int(os.environ.get("DECISION_MAX_RETRIES", "3"))
RETRY_BASE = float(os.environ.get("DECISION_RETRY_BASE", "1.0"))
TIMEOUT = float(os.environ.get("DECISION_TIMEOUT", "120.0"))


class DecisionFormatError(RuntimeError):
    """The decision endpoint responded, but not in the expected shape (e.g. no
    'answers'). A configuration/contract problem that retrying won't fix —
    analogous to core.embedding.EmbeddingDimensionError."""


def decide(state: Dict[str, Any], questions: Dict[str, Dict[str, Any]],
           model: Optional[str] = None) -> Dict[str, Any]:
    """Call the Jev-style decision model and return the raw response dict.

    Returns ``{"model": ..., "answers": {...}, "usage": {...}}``.
    Raises :class:`DecisionFormatError` if the response lacks a non-empty
    ``answers``; raises ``RuntimeError`` after retries are exhausted.
    """
    payload = {
        "model": model or DECISION_MODEL,
        "state": state,
        "questions": questions,
    }
    req = urllib.request.Request(
        DECISION_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DECISION_API_KEY}",
        },
    )
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read())
            # Contract check: a valid decision response always carries a non-empty
            # per-question 'answers' object.
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict) \
                    or not data["answers"]:
                raise DecisionFormatError(
                    f"decision response missing 'answers' (model={model or DECISION_MODEL}, "
                    f"url={DECISION_URL}) — check DECISION_MODEL / DECISION_BASE_URL"
                )
            return data
        except DecisionFormatError:
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
    raise RuntimeError(f"decision API failed: {last_err}")


async def decide_async(state: Dict[str, Any], questions: Dict[str, Dict[str, Any]],
                       model: Optional[str] = None) -> Dict[str, Any]:
    """Async version — runs :func:`decide` in a thread pool executor so it never
    blocks the uvicorn event loop (mirrors core.embedding.get_embedding_async)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, decide, state, questions, model)
