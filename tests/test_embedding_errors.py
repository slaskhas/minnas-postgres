"""Embedding failures must name the cause (model / url / credentials), not a bare 500."""
import sys
sys.path.insert(0, ".")
import io
import urllib.error
from unittest.mock import patch
import pytest
import core.embedding as emb


def _http_error(code, body=b""):
    return urllib.error.HTTPError("http://x/v1/embeddings", code, "Err", {}, io.BytesIO(body))


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(emb, "RETRY_BASE", 0)


def test_model_not_found_is_diagnosed_and_not_retried():
    body = b'{"error":{"message":"model \\"foo\\" not found"}}'
    with patch.object(emb.urllib.request, "urlopen", side_effect=_http_error(404, body)) as m:
        with pytest.raises(emb.EmbeddingUnavailableError) as ei:
            emb._call_openai_single("hi")
    assert "model not found" in str(ei.value) and emb.EMBED_MODEL in str(ei.value)
    assert m.call_count == 1


def test_auth_failure_mentions_api_key():
    with patch.object(emb.urllib.request, "urlopen", side_effect=_http_error(401)):
        with pytest.raises(emb.EmbeddingUnavailableError, match="OPENAI_API_KEY"):
            emb._call_openai_single("hi")


def test_unreachable_backend_is_diagnosed():
    err = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    with patch.object(emb.urllib.request, "urlopen", side_effect=err):
        with pytest.raises(emb.EmbeddingUnavailableError, match="unreachable"):
            emb._call_openai_single("hi")


def test_dimension_error_is_an_embedding_error():
    assert issubclass(emb.EmbeddingDimensionError, emb.EmbeddingError)
