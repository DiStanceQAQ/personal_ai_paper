"""Optional passage embedding providers and serialization helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import importlib
import json
import sqlite3
from typing import Any, Protocol, cast

import httpx

from db import get_connection

DEFAULT_PROVIDER = "none"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_SENTENCE_TRANSFORMER_MODEL = "all-MiniLM-L6-v2"

_CONFIG_KEYS = (
    "embedding_provider",
    "embedding_api_key",
    "embedding_base_url",
    "embedding_model",
    "embedding_dimension",
)


class EmbeddingProviderError(RuntimeError):
    """Base error for embedding provider failures."""


class EmbeddingProviderUnavailable(EmbeddingProviderError):
    """Raised when a configured embedding provider cannot be used."""


class EmbeddingProvider(Protocol):
    """Common interface for optional embedding backends."""

    provider: str
    model: str

    def is_configured(self) -> bool:
        """Return whether the provider can generate embeddings."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate one embedding vector per input text."""


@dataclass(frozen=True)
class EmbeddingConfig:
    """Stored embedding provider settings."""

    provider: str = DEFAULT_PROVIDER
    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    model: str = ""
    dimension: int | None = None


@dataclass(frozen=True)
class SerializedEmbedding:
    """JSON payload and dimension for a single embedding vector."""

    embedding_json: str
    dimension: int


class NoEmbeddingProvider:
    """Disabled provider used by default so FTS-only search remains unchanged."""

    provider = DEFAULT_PROVIDER
    model = ""

    def is_configured(self) -> bool:
        return False

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return []


class OpenAICompatibleEmbeddingProvider:
    """Embedding provider for OpenAI-compatible `/embeddings` APIs."""

    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        model: str = DEFAULT_OPENAI_MODEL,
        dimension: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL
        self.model = model.strip() or DEFAULT_OPENAI_MODEL
        self.dimension = dimension
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=60.0)

    def is_configured(self) -> bool:
        return bool(self.model and self.base_url and (self.api_key or _is_local_url(self.base_url)))

    def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        clean_texts = [str(text) for text in texts]
        if not clean_texts:
            return []
        if not self.is_configured():
            raise EmbeddingProviderUnavailable(
                "Embedding API key is required for remote OpenAI-compatible providers."
            )

        payload: dict[str, Any] = {"model": self.model, "input": clean_texts}
        if self.dimension is not None:
            payload["dimensions"] = self.dimension

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = self._client.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise EmbeddingProviderError("Embedding response must be a JSON object.")
        return _vectors_from_openai_payload(data, expected_count=len(clean_texts))


class SentenceTransformerEmbeddingProvider:
    """Local sentence-transformer provider loaded only when available."""

    provider = "sentence_transformer"

    def __init__(self, *, model_name: str = DEFAULT_SENTENCE_TRANSFORMER_MODEL, model: Any | None = None) -> None:
        self.model = model_name.strip() or DEFAULT_SENTENCE_TRANSFORMER_MODEL
        self._model = model if model is not None else _load_sentence_transformer(self.model)

    def is_configured(self) -> bool:
        return self._model is not None

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        clean_texts = [str(text) for text in texts]
        if not clean_texts:
            return []
        raw_vectors = self._model.encode(clean_texts)
        return [_coerce_vector(vector) for vector in raw_vectors]


def get_embedding_config(conn: sqlite3.Connection | None = None) -> EmbeddingConfig:
    """Return stored embedding configuration, defaulting to disabled."""
    owns_connection = conn is None
    connection = conn or get_connection()
    try:
        placeholders = ", ".join("?" for _ in _CONFIG_KEYS)
        rows = connection.execute(
            f"SELECT key, value FROM app_state WHERE key IN ({placeholders})",
            _CONFIG_KEYS,
        ).fetchall()
    finally:
        if owns_connection:
            connection.close()

    values = {str(row["key"]): str(row["value"]).strip() for row in rows}
    return EmbeddingConfig(
        provider=values.get("embedding_provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER,
        api_key=values.get("embedding_api_key", ""),
        base_url=values.get("embedding_base_url", DEFAULT_OPENAI_BASE_URL)
        or DEFAULT_OPENAI_BASE_URL,
        model=values.get("embedding_model", ""),
        dimension=_parse_dimension(values.get("embedding_dimension", "")),
    )


def get_embedding_provider(
    config: EmbeddingConfig | None = None,
    *,
    http_client: httpx.Client | None = None,
    sentence_transformer_model: Any | None = None,
) -> EmbeddingProvider:
    """Build an embedding provider from stored or supplied configuration."""
    resolved = config or get_embedding_config()
    provider_name = _normalize_provider_name(resolved.provider)

    if provider_name in {"", "none", "disabled", "off"}:
        return NoEmbeddingProvider()
    if provider_name in {"openai", "openai_compatible", "openai_compatible_embeddings"}:
        return OpenAICompatibleEmbeddingProvider(
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            model=resolved.model or DEFAULT_OPENAI_MODEL,
            dimension=resolved.dimension,
            http_client=http_client,
        )
    if provider_name in {"sentence_transformer", "sentence_transformers", "local"}:
        return SentenceTransformerEmbeddingProvider(
            model_name=resolved.model or DEFAULT_SENTENCE_TRANSFORMER_MODEL,
            model=sentence_transformer_model,
        )

    raise EmbeddingProviderUnavailable(f"Unsupported embedding provider: {resolved.provider}")


def serialize_embedding_vector(vector: Sequence[float]) -> SerializedEmbedding:
    """Serialize an embedding vector for SQLite JSON storage."""
    coerced = _coerce_vector(vector)
    if not coerced:
        raise ValueError("Cannot serialize an empty embedding vector.")
    return SerializedEmbedding(
        embedding_json=json.dumps(coerced, separators=(",", ":")),
        dimension=len(coerced),
    )


def _parse_dimension(value: str) -> int | None:
    if not value:
        return None
    try:
        dimension = int(value)
    except ValueError:
        return None
    if dimension <= 0:
        return None
    return dimension


def _normalize_provider_name(provider: str) -> str:
    return provider.strip().lower().replace("-", "_")


def _is_local_url(base_url: str) -> bool:
    lowered = base_url.lower()
    return any(host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


def _vectors_from_openai_payload(data: dict[str, Any], *, expected_count: int) -> list[list[float]]:
    raw_items = data.get("data")
    if not isinstance(raw_items, list):
        raise EmbeddingProviderError("Embedding response missing data list.")

    indexed_items: list[tuple[int, Any]] = []
    for fallback_index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise EmbeddingProviderError("Embedding response data items must be objects.")
        index = item.get("index", fallback_index)
        if not isinstance(index, int):
            raise EmbeddingProviderError("Embedding response index must be an integer.")
        indexed_items.append((index, item.get("embedding")))

    indexed_items.sort(key=lambda item: item[0])
    vectors = [_coerce_vector(raw_vector) for _, raw_vector in indexed_items]
    if len(vectors) != expected_count:
        raise EmbeddingProviderError(
            f"Embedding response returned {len(vectors)} vectors for {expected_count} inputs."
        )
    return vectors


def _coerce_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)):
        raise EmbeddingProviderError("Embedding vector must be a numeric sequence.")
    try:
        return [float(value) for value in vector]
    except (TypeError, ValueError) as exc:
        raise EmbeddingProviderError("Embedding vector contains non-numeric values.") from exc


def _load_sentence_transformer(model_name: str) -> Any:
    try:
        module = importlib.import_module("sentence_transformers")
    except ImportError as exc:
        raise EmbeddingProviderUnavailable(
            "sentence-transformers is not installed; install the embeddings extra to use local embeddings."
        ) from exc

    sentence_transformer = getattr(module, "SentenceTransformer", None)
    if sentence_transformer is None:
        raise EmbeddingProviderUnavailable(
            "sentence_transformers.SentenceTransformer is unavailable."
        )
    return cast(Any, sentence_transformer)(model_name)
