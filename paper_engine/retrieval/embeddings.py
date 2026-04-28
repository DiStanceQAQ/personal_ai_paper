"""Passage embedding providers and serialization helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Literal, Protocol, TypeAlias, cast

import httpx

from paper_engine.storage.database import get_connection

DEFAULT_PROVIDER = "local"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_SENTENCE_TRANSFORMER_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_LOCAL_MODEL_DIRNAME = "intfloat-multilingual-e5-small"
EMBEDDING_MODEL_DIR_ENV = "PAPER_ENGINE_EMBEDDING_MODEL_DIR"
RESOURCE_DIR_ENV = "PAPER_ENGINE_RESOURCE_DIR"
EmbeddingInputType: TypeAlias = Literal["query", "passage"]

_CONFIG_KEYS = (
    "embedding_provider",
    "embedding_api_key",
    "embedding_base_url",
    "embedding_model",
    "embedding_dimension",
)
_REQUIRED_SENTENCE_TRANSFORMER_FILES = ("modules.json",)


class EmbeddingProviderError(RuntimeError):
    """Base error for embedding provider failures."""


class EmbeddingProviderUnavailable(EmbeddingProviderError):
    """Raised when a configured embedding provider cannot be used."""


class EmbeddingProvider(Protocol):
    """Common interface for embedding backends."""

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
        return bool(
            self.model
            and self.base_url
            and (self.api_key or _is_local_url(self.base_url))
        )

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

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_SENTENCE_TRANSFORMER_MODEL,
        model_path: str | Path | None = None,
        model: Any | None = None,
    ) -> None:
        self.model = model_name.strip() or DEFAULT_SENTENCE_TRANSFORMER_MODEL
        load_target = str(model_path) if model_path is not None else self.model
        self._model = model if model is not None else _load_sentence_transformer(load_target)

    def is_configured(self) -> bool:
        return self._model is not None

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        clean_texts = [str(text) for text in texts]
        if not clean_texts:
            return []
        raw_vectors = self._model.encode(clean_texts)
        return [_coerce_vector(vector) for vector in raw_vectors]


def get_embedding_config(conn: sqlite3.Connection | None = None) -> EmbeddingConfig:
    """Return stored embedding configuration, defaulting to local E5."""
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
    provider = values.get("embedding_provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER
    model = values.get("embedding_model", "")
    if not model and _uses_sentence_transformer_provider(provider):
        model = DEFAULT_SENTENCE_TRANSFORMER_MODEL
    return EmbeddingConfig(
        provider=provider,
        api_key=values.get("embedding_api_key", ""),
        base_url=values.get("embedding_base_url", DEFAULT_OPENAI_BASE_URL)
        or DEFAULT_OPENAI_BASE_URL,
        model=model,
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
        raise EmbeddingProviderUnavailable(
            "Embeddings are required; embedding_provider cannot be disabled."
        )
    if provider_name in {"openai", "openai_compatible", "openai_compatible_embeddings"}:
        return OpenAICompatibleEmbeddingProvider(
            api_key=resolved.api_key,
            base_url=resolved.base_url,
            model=resolved.model or DEFAULT_OPENAI_MODEL,
            dimension=resolved.dimension,
            http_client=http_client,
        )
    if provider_name in {"sentence_transformer", "sentence_transformers", "local"}:
        model_name = resolved.model or DEFAULT_SENTENCE_TRANSFORMER_MODEL
        model_path = (
            None
            if sentence_transformer_model is not None
            else resolve_local_embedding_model_path(model_name)
        )
        return SentenceTransformerEmbeddingProvider(
            model_name=model_name,
            model_path=model_path,
            model=sentence_transformer_model,
        )

    raise EmbeddingProviderUnavailable(
        f"Unsupported embedding provider: {resolved.provider}"
    )


def serialize_embedding_vector(vector: Sequence[float]) -> SerializedEmbedding:
    """Serialize an embedding vector for SQLite JSON storage."""
    coerced = _coerce_vector(vector)
    if not coerced:
        raise ValueError("Cannot serialize an empty embedding vector.")
    return SerializedEmbedding(
        embedding_json=json.dumps(coerced, separators=(",", ":")),
        dimension=len(coerced),
    )


def format_embedding_texts(
    texts: Sequence[str],
    *,
    model: str,
    input_type: EmbeddingInputType,
) -> list[str]:
    """Apply model-specific query/passage formatting before embedding."""
    clean_texts = [" ".join(str(text).split()) for text in texts]
    if not _is_e5_model(model):
        return clean_texts

    prefix = f"{input_type}: "
    return [
        text if text.lower().startswith(prefix) else f"{prefix}{text}"
        for text in clean_texts
    ]


def resolve_local_embedding_model_path(
    model_name: str = DEFAULT_SENTENCE_TRANSFORMER_MODEL,
) -> Path:
    """Resolve the packaged sentence-transformer model directory."""
    model_dirname = _model_resource_dirname(model_name)
    explicit_model_dir = os.environ.get(EMBEDDING_MODEL_DIR_ENV, "").strip()
    if explicit_model_dir:
        path = Path(explicit_model_dir).expanduser()
        if _is_sentence_transformer_model_dir(path):
            return path
        raise EmbeddingProviderUnavailable(
            "Bundled embedding model is missing or incomplete at "
            f"{EMBEDDING_MODEL_DIR_ENV}={path}. "
            "Run `python scripts/download_embedding_model.py` before starting the app."
        )

    for candidate in _local_model_dir_candidates(model_dirname):
        if _is_sentence_transformer_model_dir(candidate):
            return candidate

    raise EmbeddingProviderUnavailable(
        "Bundled embedding model is missing. Expected "
        f"{model_dirname} under {RESOURCE_DIR_ENV}/models or resources/models. "
        "Run `python scripts/download_embedding_model.py` before starting the app."
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


def _uses_sentence_transformer_provider(provider: str) -> bool:
    return _normalize_provider_name(provider) in {
        "sentence_transformer",
        "sentence_transformers",
        "local",
    }


def _is_e5_model(model: str) -> bool:
    return "e5" in model.strip().lower()


def _model_resource_dirname(model_name: str) -> str:
    normalized = model_name.strip() or DEFAULT_SENTENCE_TRANSFORMER_MODEL
    if normalized == DEFAULT_SENTENCE_TRANSFORMER_MODEL:
        return DEFAULT_LOCAL_MODEL_DIRNAME
    return normalized.replace("/", "-")


def _local_model_dir_candidates(model_dirname: str) -> list[Path]:
    candidates: list[Path] = []
    resource_dir = os.environ.get(RESOURCE_DIR_ENV, "").strip()
    if resource_dir:
        resource_root = Path(resource_dir).expanduser()
        candidates.extend([
            resource_root / "models" / model_dirname,
            resource_root / "resources" / "models" / model_dirname,
            resource_root / model_dirname,
        ])

    project_root = Path(__file__).resolve().parents[2]
    candidates.append(project_root / "resources" / "models" / model_dirname)
    return candidates


def _is_sentence_transformer_model_dir(path: Path) -> bool:
    return path.is_dir() and all(
        (path / filename).is_file()
        for filename in _REQUIRED_SENTENCE_TRANSFORMER_FILES
    )


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
            "sentence-transformers is required to use local embeddings."
        ) from exc

    sentence_transformer = getattr(module, "SentenceTransformer", None)
    if sentence_transformer is None:
        raise EmbeddingProviderUnavailable(
            "sentence_transformers.SentenceTransformer is unavailable."
        )
    return cast(Any, sentence_transformer)(model_name)
