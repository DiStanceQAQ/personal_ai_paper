"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Sequence

import pytest


class _TestEmbeddingProvider:
    provider = "sentence_transformer"
    model = "intfloat/multilingual-e5-small"

    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(index + 1), 0.0, 1.0] for index, _ in enumerate(texts)]


@pytest.fixture(autouse=True)
def _use_test_embedding_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep parse-route tests from loading the real local embedding model."""
    monkeypatch.setattr(
        "paper_engine.pdf.persistence.get_embedding_provider",
        lambda config: _TestEmbeddingProvider(),
    )
    monkeypatch.setattr(
        "paper_engine.retrieval.embedding_worker.get_embedding_provider",
        lambda config: _TestEmbeddingProvider(),
    )
