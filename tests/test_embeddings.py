"""Tests for optional passage embedding providers."""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx
import pytest

import embeddings


def create_app_state_connection() -> sqlite3.Connection:
    """Create an in-memory database with app_state available."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


def test_default_config_uses_disabled_noop_provider() -> None:
    """Embeddings are disabled by default and no-op without affecting FTS."""
    conn = create_app_state_connection()

    config = embeddings.get_embedding_config(conn)
    provider = embeddings.get_embedding_provider(config)

    assert config.provider == "none"
    assert provider.provider == "none"
    assert not provider.is_configured()
    assert provider.embed_texts(["alpha", "beta"]) == []

    conn.close()


def test_embedding_config_reads_app_state_values() -> None:
    """Embedding settings are read from app_state with stripped values."""
    conn = create_app_state_connection()
    for key, value in {
        "embedding_provider": " openai-compatible ",
        "embedding_api_key": " test-key ",
        "embedding_base_url": " http://embeddings.example/v1/ ",
        "embedding_model": " text-embedding-test ",
        "embedding_dimension": " 3 ",
    }.items():
        conn.execute("INSERT INTO app_state (key, value) VALUES (?, ?)", (key, value))

    config = embeddings.get_embedding_config(conn)

    assert config == embeddings.EmbeddingConfig(
        provider="openai-compatible",
        api_key="test-key",
        base_url="http://embeddings.example/v1/",
        model="text-embedding-test",
        dimension=3,
    )

    conn.close()


def test_openai_compatible_provider_posts_embedding_request() -> None:
    """OpenAI-compatible provider posts /embeddings and returns ordered vectors."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == "http://embeddings.example/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = request.read()
        assert b'"model":"text-embedding-test"' in payload
        assert b'"input":["alpha","beta"]' in payload
        assert b'"dimensions":3' in payload
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ]
            },
        )

    provider = embeddings.OpenAICompatibleEmbeddingProvider(
        api_key="test-key",
        base_url="http://embeddings.example/v1/",
        model="text-embedding-test",
        dimension=3,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    vectors = provider.embed_texts(["alpha", "beta"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert len(requests) == 1


def test_openai_compatible_provider_requires_remote_api_key() -> None:
    """Remote OpenAI-compatible providers require an API key."""
    provider = embeddings.OpenAICompatibleEmbeddingProvider(
        api_key="",
        base_url="https://api.openai.com/v1",
        model="text-embedding-3-small",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )

    assert not provider.is_configured()
    with pytest.raises(embeddings.EmbeddingProviderUnavailable, match="API key"):
        provider.embed_texts(["alpha"])


def test_sentence_transformer_provider_uses_injected_model() -> None:
    """Local provider uses an injected sentence-transformer compatible model."""

    class FakeSentenceTransformer:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def encode(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(texts)
            return [[1, 2, 3], [4, 5, 6]]

    model = FakeSentenceTransformer()
    provider = embeddings.SentenceTransformerEmbeddingProvider(
        model_name="local-test-model",
        model=model,
    )

    assert provider.provider == "sentence_transformer"
    assert provider.is_configured()
    assert provider.embed_texts(["alpha", "beta"]) == [
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
    ]
    assert model.calls == [["alpha", "beta"]]


def test_get_embedding_provider_rejects_unknown_provider() -> None:
    """Unknown embedding provider names fail clearly."""
    config = embeddings.EmbeddingConfig(provider="mystery")

    with pytest.raises(embeddings.EmbeddingProviderUnavailable, match="mystery"):
        embeddings.get_embedding_provider(config)


def test_serialize_embedding_vector_rejects_empty_vectors() -> None:
    """Embedding serialization records dimensions and rejects empty vectors."""
    with pytest.raises(ValueError, match="empty"):
        embeddings.serialize_embedding_vector([])

    serialized = embeddings.serialize_embedding_vector([1, 2.5, 3])

    assert serialized.dimension == 3
    assert serialized.embedding_json == "[1.0,2.5,3.0]"
