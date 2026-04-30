"""Tests for optional sqlite-vec passage embedding acceleration."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

from paper_engine.retrieval.vector_index import (
    delete_passage_embedding_vector_index,
    semantic_search_with_sqlite_vec,
    upsert_passage_embedding_vector_index,
)
from paper_engine.storage.database import get_connection, init_db


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlite_vec") is None,
    reason="sqlite-vec is not installed",
)


def _seed_rows(db_path: Path) -> None:
    conn = init_db(database_path=db_path)
    try:
        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Vector')")
        conn.execute(
            """
            INSERT INTO papers (id, space_id, title, parse_status)
            VALUES ('paper-1', 'space-1', 'Vector Paper', 'parsed')
            """
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, section, original_text)
            VALUES
              ('passage-1', 'paper-1', 'space-1', 'method', 'alpha vector text'),
              ('passage-2', 'paper-1', 'space-1', 'result', 'beta vector text')
            """
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


def test_sqlite_vec_search_syncs_and_queries_embeddings() -> None:
    """The optional vector index returns nearest passage embeddings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _seed_rows(db_path)
        conn = get_connection(db_path)
        try:
            results = semantic_search_with_sqlite_vec(
                conn,
                query_vector=[1.0, 0.0],
                space_id="space-1",
                provider="openai",
                model="test-model",
                limit=5,
            )
        finally:
            conn.close()

    assert [row["passage_id"] for row in results] == ["passage-2", "passage-1"]


def test_sqlite_vec_search_uses_cosine_distance() -> None:
    """Vector index ranking stays aligned with the Python cosine fallback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _seed_rows(db_path)
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO passages (id, paper_id, space_id, section, original_text)
                VALUES ('passage-3', 'paper-1', 'space-1', 'result', 'scaled same direction')
                """
            )
            conn.execute(
                """
                INSERT INTO passage_embeddings (
                    passage_id, provider, model, dimension, embedding_json
                )
                VALUES ('passage-3', 'openai', 'test-model', 2, ?)
                """,
                (json.dumps([10.0, 0.0]),),
            )
            conn.commit()
            results = semantic_search_with_sqlite_vec(
                conn,
                query_vector=[1.0, 0.0],
                space_id="space-1",
                provider="openai",
                model="test-model",
                limit=3,
            )
        finally:
            conn.close()

    assert [row["passage_id"] for row in results] == [
        "passage-2",
        "passage-3",
        "passage-1",
    ]


def test_sqlite_vec_search_respects_candidate_passage_ids() -> None:
    """Candidate IDs keep the vec search aligned with two-stage hybrid recall."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _seed_rows(db_path)
        conn = get_connection(db_path)
        try:
            results = semantic_search_with_sqlite_vec(
                conn,
                query_vector=[1.0, 0.0],
                space_id="space-1",
                provider="openai",
                model="test-model",
                limit=5,
                candidate_passage_ids=["passage-1"],
            )
        finally:
            conn.close()

    assert [row["passage_id"] for row in results] == ["passage-1"]


def test_sqlite_vec_upsert_and_delete_are_best_effort() -> None:
    """Incremental helpers can maintain and remove a single indexed vector."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _seed_rows(db_path)
        conn = get_connection(db_path)
        try:
            upsert_passage_embedding_vector_index(
                conn,
                passage_id="passage-2",
                provider="openai",
                model="test-model",
                vector=[0.0, 1.0],
            )
            conn.execute(
                """
                DELETE FROM passage_embeddings
                WHERE passage_id = 'passage-2'
                  AND provider = 'openai'
                  AND model = 'test-model'
                """
            )
            conn.commit()
            indexed = conn.execute(
                """
                SELECT passage_id
                FROM passage_embedding_vec_2
                WHERE passage_id = 'passage-2'
                """
            ).fetchall()
            delete_passage_embedding_vector_index(
                conn,
                passage_ids=["passage-2"],
                provider="openai",
                model="test-model",
            )
            remaining = conn.execute(
                """
                SELECT passage_id
                FROM passage_embedding_vec_2
                WHERE passage_id = 'passage-2'
                """
            ).fetchall()
        finally:
            conn.close()

    assert [row["passage_id"] for row in indexed] == ["passage-2"]
    assert remaining == []
