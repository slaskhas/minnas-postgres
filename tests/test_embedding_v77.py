"""v7.7.0 embedding deep optimization — pure logic tests (mocked API, no real calls)

Coverage: cache hits / batch chunking / retry backoff / concurrency / LRU eviction / empty input / dimension validation
"""
import sys
sys.path.insert(0, ".")
import json
import urllib.error
from unittest.mock import patch, MagicMock
import core.embedding as emb


class TestCache:
    def setup_method(self):
        emb._cache.clear()
        emb._cache_loaded = True  # skip disk loading

    def test_cache_hit_no_api_call(self):
        """Calling the same text twice → no second API call"""
        fake = MagicMock(return_value=[0.1] * 3)
        with patch.object(emb, "_call_openai_single", fake):
            emb.get_embedding(["hello"])
            emb.get_embedding(["hello"])
        assert fake.call_count == 1  # called only once

    def test_partial_cache_miss(self):
        fake = MagicMock(return_value=[0.1] * 3)
        with patch.object(emb, "_call_openai_single", fake):
            emb.get_embedding(["a", "b"])
            emb.get_embedding(["b", "c"])  # b hits the cache, c is new
        assert fake.call_count == 3  # a, b, c each called once (concurrent, one at a time)

    def test_many_texts_concurrent(self):
        """Multiple texts → fetched concurrently, result order matches the original order"""
        n = 30
        texts = [f"t{i}" for i in range(n)]
        fake = MagicMock(side_effect=lambda t: [float(t[1:])] * 3)  # returns based on the text's number
        with patch.object(emb, "_call_openai_single", fake):
            out = emb.get_embedding(texts)
        assert len(out) == n
        # Order is preserved: the i-th vector's first element = i
        for i, vec in enumerate(out):
            assert vec[0] == float(i)

    def test_empty_input(self):
        assert emb.get_embedding([]) == []

    def test_lru_cutoff(self):
        """Automatically evicted once over CACHE_SIZE"""
        fake = MagicMock(return_value=[0.1] * 3)
        with patch.object(emb, "_call_openai_single", fake):
            for i in range(emb.CACHE_SIZE + 50):
                emb.get_embedding([f"unique{i}"])
        assert len(emb._cache) <= emb.CACHE_SIZE


class TestRetry:
    def setup_method(self):
        emb._cache.clear()
        emb._cache_loaded = True

    class FakeResp:
        """A real context-manager response (MagicMock's __enter__ returns a new object each time, which is unreliable)"""
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_retry_then_success(self):
        """_call_openai_single: the first two HTTP calls fail, the third succeeds → returns a result"""
        calls = {"n": 0}

        def flaky_urlopen(req, timeout=0):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)
            payload = json.dumps({"data": [{"embedding": [0.5] * 3}]}).encode()
            return self.FakeResp(payload)

        with patch.object(emb.urllib.request, "urlopen", flaky_urlopen), \
             patch.object(emb, "RETRY_BASE", 0.01), \
             patch.object(emb, "EMBED_DIM", 3):
            out = emb._call_openai_single("x")
        assert len(out) == 3
        assert calls["n"] == 3

    def test_total_failure_raises(self):
        def always_fail(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)

        with patch.object(emb.urllib.request, "urlopen", always_fail), \
             patch.object(emb, "RETRY_BASE", 0.01):
            try:
                emb._call_openai_single("x")
                assert False, "should raise"
            except RuntimeError:
                pass

    def test_dimension_mismatch_raises_without_retry(self):
        """Returned dimension doesn't match EMBED_DIM (e.g. Ollama silently downgrading
        `dimensions` beyond the model's native size) → immediately raise
        EmbeddingDimensionError, no retry (retrying makes no sense for a config error)"""
        calls = {"n": 0}

        def wrong_dim_urlopen(req, timeout=0):
            calls["n"] += 1
            payload = json.dumps({"data": [{"embedding": [0.1] * 1024}]}).encode()
            return self.FakeResp(payload)

        with patch.object(emb.urllib.request, "urlopen", wrong_dim_urlopen), \
             patch.object(emb, "RETRY_BASE", 0.01), \
             patch.object(emb, "EMBED_DIM", 1536):
            try:
                emb._call_openai_single("x")
                assert False, "should raise EmbeddingDimensionError"
            except emb.EmbeddingDimensionError:
                pass
        assert calls["n"] == 1  # non-retryable config error, should only be called once
