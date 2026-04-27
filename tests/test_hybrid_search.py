"""Tests for hybrid FTS and semantic passage search."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from db import init_db
from hybrid_search import reciprocal_rank_fusion
from search import rebuild_fts_index, search_passages


class QueryEmbeddingProvider:
    """Deterministic query embedding provider for hybrid search tests."""

    provider = "openai"
    model = "test-model"

    def __init__(self, query_vector: list[float]) -> None:
        self.query_vector = query_vector
        self.calls: list[list[str]] = []

    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.query_vector for _ in texts]


def _insert_app_state(conn: Any, values: dict[str, str]) -> None:
    for key, value in values.items():
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def _seed_search_rows(db_path: Path, *, with_embeddings: bool) -> None:
    conn = init_db(database_path=db_path)
    try:
        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Hybrid')")
        conn.execute(
            """
            INSERT INTO papers (id, space_id, title, parse_status)
            VALUES ('paper-1', 'space-1', 'Hybrid Paper', 'parsed')
            """
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, section, original_text)
            VALUES
              ('passage-1', 'paper-1', 'space-1', 'method',
               'transformer attention mechanism'),
              ('passage-2', 'paper-1', 'space-1', 'result',
               'retrieval augmented generation improves grounding')
            """
        )
        if with_embeddings:
            _insert_app_state(
                conn,
                {
                    "embedding_provider": "openai",
                    "embedding_model": "test-model",
                    "embedding_api_key": "test-key",
                },
            )
            conn.execute(
                """
                INSERT INTO passage_embeddings (
                    passage_id, provider, model, dimension, embedding_json
                )
                VALUES
                  ('passage-1', 'openai', 'test-model', 2, ?),
                  ('passage-2', 'openai', 'test-model', 2, ?)
                """,
                (json.dumps([0.0, 1.0]), json.dumps([1.0, 0.0])),
            )
        conn.commit()
    finally:
        conn.close()
    rebuild_fts_index(database_path=db_path)


def test_reciprocal_rank_fusion_boosts_results_seen_in_both_lists() -> None:
    """RRF should prefer passages that rank well in both result lists."""
    fused = reciprocal_rank_fusion(
        fts_results=[
            {"passage_id": "passage-1", "score": -2.0},
            {"passage_id": "passage-2", "score": -1.0},
        ],
        semantic_results=[
            {"passage_id": "passage-2", "semantic_score": 0.91},
            {"passage_id": "passage-3", "semantic_score": 0.89},
        ],
        limit=3,
    )

    assert [row["passage_id"] for row in fused] == [
        "passage-2",
        "passage-1",
        "passage-3",
    ]
    assert fused[0]["fts_rank"] == 2
    assert fused[0]["semantic_rank"] == 1
    assert fused[0]["search_mode"] == "hybrid"
    assert fused[0]["score"] == fused[0]["rrf_score"]


def test_search_defaults_to_fts_when_no_embeddings_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto mode should keep the existing FTS behavior when vectors are absent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _seed_search_rows(db_path, with_embeddings=False)

        def fail_provider_lookup(config: object) -> None:
            raise AssertionError("Embedding provider should not be used without vectors")

        monkeypatch.setattr("hybrid_search.get_embedding_provider", fail_provider_lookup)

        results = search_passages(
            "transformer",
            "space-1",
            limit=5,
            database_path=db_path,
        )

    assert [row["passage_id"] for row in results] == ["passage-1"]


def test_search_defaults_to_hybrid_when_embeddings_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto mode should include semantic-only matches when embeddings exist."""
    provider = QueryEmbeddingProvider([1.0, 0.0])
    monkeypatch.setattr("hybrid_search.get_embedding_provider", lambda config: provider)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _seed_search_rows(db_path, with_embeddings=True)

        hybrid_results = search_passages(
            "transformer",
            "space-1",
            limit=5,
            database_path=db_path,
        )
        fts_results = search_passages(
            "transformer",
            "space-1",
            limit=5,
            database_path=db_path,
            mode="fts",
        )

    assert "passage-2" in [row["passage_id"] for row in hybrid_results]
    assert [row["passage_id"] for row in fts_results] == ["passage-1"]
    assert provider.calls == [["transformer"]]
