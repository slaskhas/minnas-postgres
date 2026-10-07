"""MCP bridge contract test — inbound parameter shape (user_id / feedback must go via query, missing → 422)

Background (observed in production, 2026-09-12): the feedback_memory / delete_memory / restore_memory
tools **must report 422** — the bridge sent feedback as a JSON body with no user_id at all, while
the server's REST API requires both as query parameters. From the user's side it just looks like
"the tool is broken"; whereas the same class of mismatch on a **response field** (e.g. reading `heat`
when the server returns `heat_score`) **doesn't even error** — it silently falls back to a default.
Hence both directions are locked down by contract tests.

This test suite locks down the outbound request shape; no DB/network dependency (monkeypatches `_call`).
The mcp SDK is only installed where the stdio-to-HTTP bridge runs, so this auto-skips on a minimal
install without affecting the server-side test suite.
"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

BRIDGE = (Path(__file__).resolve().parents[1]
          / "integrations" / "hermes-mcp" / "mnemosyne_mcp.py")


@pytest.fixture(scope="module")
def bridge():
    pytest.importorskip("mcp")      # skipped on a minimal install (no mcp SDK)
    pytest.importorskip("httpx")
    spec = importlib.util.spec_from_file_location("mnes_bridge", BRIDGE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def calls(bridge, monkeypatch):
    """Intercepts _call and records (method, path, kwargs)."""
    seen = []

    async def fake_call(method, path, **kwargs):
        seen.append({"method": method, "path": path, "kwargs": kwargs})
        return {"status": "ok"}

    monkeypatch.setattr(bridge, "_call", fake_call)
    return seen


def _dispatch(bridge, name, arguments=None):
    return asyncio.run(bridge._dispatch(name, dict(arguments or {})))


class TestQueryParamContract:
    """The three handlers with "required query params" — omitting one gives 422."""

    def test_feedback_uses_query_params_not_body(self, bridge, calls):
        _dispatch(bridge, "feedback_memory", {"memory_id": "42", "feedback": "positive"})
        req = calls[-1]
        assert (req["method"], req["path"]) == ("POST", "/memories/42/feedback")
        assert req["kwargs"]["params"]["user_id"] == "default"
        assert req["kwargs"]["params"]["feedback"] == "positive"
        assert "json" not in req["kwargs"], "sending feedback as a JSON body would get a 422 from the server"

    def test_feedback_honours_caller_user_id(self, bridge, calls):
        _dispatch(bridge, "feedback_memory",
                  {"memory_id": "7", "feedback": "negative", "user_id": "alice"})
        assert calls[-1]["kwargs"]["params"]["user_id"] == "alice"

    def test_delete_requires_user_id(self, bridge, calls):
        _dispatch(bridge, "delete_memory", {"memory_id": "99"})
        req = calls[-1]
        assert (req["method"], req["path"]) == ("DELETE", "/memories/99")
        assert req["kwargs"]["params"] == {"user_id": "default"}

    def test_restore_requires_user_id(self, bridge, calls):
        _dispatch(bridge, "restore_memory", {"memory_id": "99", "user_id": "bob"})
        req = calls[-1]
        assert (req["method"], req["path"]) == ("POST", "/memories/99/restore")
        assert req["kwargs"]["params"] == {"user_id": "bob"}


class TestBodyContractRegression:
    """Regression: don't over-correct toward query params — endpoints that need a body still use json."""

    def test_store_memory_uses_json_body(self, bridge, calls):
        _dispatch(bridge, "store_memory", {"content": "契约测试", "category": "knowledge"})
        req = calls[-1]
        assert (req["method"], req["path"]) == ("POST", "/memories")
        assert req["kwargs"]["json"]["user_id"] == "default"
        assert req["kwargs"]["json"]["content"] == "契约测试"
        assert "params" not in req["kwargs"]

    def test_search_memory_uses_json_body(self, bridge, calls):
        _dispatch(bridge, "search_memories", {"query": "契约"})
        req = calls[-1]
        assert req["path"] == "/memories/search"
        assert req["kwargs"]["json"]["query"] == "契约"


class TestSearchGraphContract:
    """search_graph's REST handler (main.py graph_search) takes bare query params
    (query/user_id/max_hops), not a JSON body — json= 422s."""

    def test_search_graph_uses_query_params(self, bridge, calls):
        _dispatch(bridge, "search_graph", {"query": "widgets", "limit": 5})
        req = calls[-1]
        assert (req["method"], req["path"]) == ("POST", "/graph/search")
        assert req["kwargs"]["params"] == {"user_id": "default", "query": "widgets", "max_hops": 5}
        assert "json" not in req["kwargs"]

    def test_search_graph_default_max_hops(self, bridge, calls):
        _dispatch(bridge, "search_graph", {"query": "widgets"})
        assert calls[-1]["kwargs"]["params"]["max_hops"] == 2


class TestExtractEntitiesContract:
    """extract_entities's REST handler (main.py extract_entities) takes query params
    (user_id/max_memories) and scans the user's own unlinked stored memories — it
    does not accept free text."""

    def test_extract_entities_uses_query_params(self, bridge, calls):
        _dispatch(bridge, "extract_entities", {"max_memories": 25})
        req = calls[-1]
        assert (req["method"], req["path"]) == ("POST", "/extract-entities")
        assert req["kwargs"]["params"] == {"user_id": "default", "max_memories": 25}
        assert "json" not in req["kwargs"]

    def test_extract_entities_ignores_stale_text_arg(self, bridge, calls):
        """Old callers still sending `text` (pre-fix schema) must not break."""
        _dispatch(bridge, "extract_entities", {"text": "ignored now"})
        req = calls[-1]
        assert "text" not in req["kwargs"].get("params", {})
        assert "json" not in req["kwargs"]
