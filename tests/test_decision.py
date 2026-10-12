#!/usr/bin/env python3
"""
v8.2.0 · `core/decision.py` decision-model tests

No network / DB dependency: monkeypatches `urllib.request.urlopen` with a
canned response, so `decide()` exercises the real contract-check logic
(a valid response must carry a non-empty `answers` object — otherwise
`DecisionFormatError`) and the retry path without hitting a live Ollama
instance.

Import idiom (mirrors tests/test_source_doc_v811.py): put the project root
on sys.path first so `import core.decision` succeeds whether the test is run
as part of `pytest tests/` (root auto-added) or on its own.
"""
import asyncio
import json
import os
import sys
import urllib.error
import unittest.mock as _mock

import pytest  # noqa: F401 — pytest.raises used below

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import decision  # noqa: E402 — imported after sys.path fix-up
from core.decision import decide, decide_async, DecisionFormatError  # noqa: E402


# ── fixtures shared by the tests ──────────────────────────────────────────────

GOOD = {
    "model": "tev1:0.8b",
    "answers": {
        "team": {"type": "choice", "choice": "billing",
                 "probabilities": {"billing": 0.9, "technical": 0.05},
                 "confidence": 0.9},
        "noul_answer": {"type": "noul", "noul": "Please contact billing."},
        "urgency": {"type": "score", "score": "Urgent",
                    "legend": ["Routine", "Soon", "Urgent"],
                    "probabilities": {"Routine": 0.1, "Soon": 0.2, "Urgent": 0.7},
                    "confidence": 0.8},
    },
    "usage": {"input_tokens": 120, "output_tokens": 30},
}

STATE = {"ticket": "I was charged twice. Please refund the extra payment."}
QUESTIONS = {
    "team": {"type": "choice",
             "instructions": "Which team should handle this?",
             "criteria": {"billing": "Payments and refunds",
                          "technical": "Bugs and integrations"}},
    "noul_answer": {"type": "noul", "instructions": "Draft a reply to the customer."},
    "urgency": {"type": "score", "instructions": "How urgent is this?",
                "criteria": ["Routine", "Soon", "Urgent"]},
}


def _resp(body):
    """A MagicMock usable as a context manager (`as resp:`) whose .read()
    returns the canned JSON payload. `__enter__` is wired to return `self`
    so the `as resp` binding is the very mock we configured (a bare
    MagicMock's `__enter__` would otherwise yield an unconfigured child)."""
    resp = _mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _return_urlopen(body):
    """Patch urllib.request.urlopen to return a single canned response."""
    return _mock.patch("urllib.request.urlopen",
                       side_effect=lambda *a, **kw: _resp(body))


def _raise_urlopen(code):
    """Patch urllib.request.urlopen to always raise an HTTPError(code)."""
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("http://x", code, "err", {}, None)
    return _mock.patch("urllib.request.urlopen", side_effect=_raise)


def _sleep_noop():
    """No-op the sleep so the retry tests don't actually wait."""
    return _mock.patch("core.decision.time.sleep", lambda *_: None)


class TestDecisionFormatCheck:
    """The raw-response contract: non-empty `answers` is required; anything
    else is a DecisionFormatError (a config/contract problem, not retriable)."""

    def test_decide_returns_raw_answers_and_usage(self):
        with _return_urlopen(GOOD):
            out = decide(STATE, QUESTIONS)
        assert out["model"] == "tev1:0.8b"
        assert out["usage"]["input_tokens"] == 120
        assert out["answers"]["team"]["choice"] == "billing"
        assert out["answers"]["team"]["confidence"] == 0.9
        assert out["answers"]["noul_answer"]["noul"] == "Please contact billing."

    def test_decide_raises_when_answers_missing(self):
        bad = {"model": "m", "usage": {"input_tokens": 1}}  # no `answers`
        with _return_urlopen(bad):
            with pytest.raises(DecisionFormatError):
                decide(STATE, QUESTIONS)

    def test_decide_raises_when_answers_empty(self):
        bad = {"model": "m", "answers": {}}
        with _return_urlopen(bad):
            with pytest.raises(DecisionFormatError):
                decide(STATE, QUESTIONS)

    def test_format_error_is_not_retried(self):
        """DecisionFormatError escapes the retry loop on the first attempt —
        a malformed contract shouldn't burn the whole retry budget."""
        hits = []
        bad = {"model": "m"}  # no answers
        def fake_urlopen(req, timeout=None):
            hits.append(req)
            return _resp(bad)
        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(DecisionFormatError):
                decide(STATE, QUESTIONS)
        assert len(hits) == 1


class TestDecisionRequestShape:
    """The outgoing request body must match the Jev contract exactly."""

    def test_default_model_and_state_questions_sent(self):
        seen = []
        def fake_urlopen(req, timeout=None):
            seen.append(req)
            return _resp(GOOD)
        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            decide(STATE, QUESTIONS)
        body = json.loads(seen[0].data.decode("utf-8"))
        assert body["model"] == decision.DECISION_MODEL  # default when model=None
        assert body["state"] == STATE
        assert body["questions"] == QUESTIONS

    def test_explicit_model_override_wins(self):
        seen = []
        def fake_urlopen(req, timeout=None):
            seen.append(req)
            return _resp(GOOD)
        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            decide(STATE, QUESTIONS, model="nimble")
        assert json.loads(seen[0].data.decode("utf-8"))["model"] == "nimble"

    def test_bearer_header_and_content_type(self):
        seen = []
        def fake_urlopen(req, timeout=None):
            seen.append(req)
            return _resp(GOOD)
        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            decide(STATE, QUESTIONS)
        # urllib.request.Request normalizes header keys via add_header()
        # (capitalize(): first char upper, rest lower) → "Content-Type"→"Content-type".
        assert seen[0].headers["Authorization"] == \
               f"Bearer {decision.DECISION_API_KEY}"
        assert seen[0].headers["Content-type"] == "application/json"


class TestDecisionRetry:
    """Exponential-backoff retry on 429/5xx, non-retriable on 4xx."""

    def test_retries_on_500_then_succeeds(self):
        hits = []
        def fake_urlopen(req, timeout=None):
            hits.append(req)
            if len(hits) == 1:
                raise urllib.error.HTTPError("http://x", 500, "Internal", {}, None)
            return _resp(GOOD)
        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             _sleep_noop():
            out = decide(STATE, QUESTIONS)
        assert out["answers"]["team"]["choice"] == "billing"
        assert len(hits) == 2  # first call 500 → retry → second call succeeds

    def test_exhausts_retries_then_raises_runtime_error(self):
        with _raise_urlopen(503), _sleep_noop():
            with pytest.raises(RuntimeError, match="decision API failed"):
                decide(STATE, QUESTIONS)

    def test_4xx_is_not_retried(self):
        """A 4xx (bad config / 404 on the route) should fail fast — no retry."""
        hits = []
        def fake_urlopen(req, timeout=None):
            hits.append(req)
            raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
        with _mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with _sleep_noop():
                with pytest.raises(RuntimeError, match="decision API failed"):
                    decide(STATE, QUESTIONS)
        assert len(hits) == 1


class TestDecisionAsync:
    def test_decide_async_runs_and_returns(self):
        with _return_urlopen(GOOD):
            out = asyncio.run(decide_async(STATE, QUESTIONS))
        assert out["answers"]["team"]["choice"] == "billing"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
