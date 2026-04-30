from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from paper_engine.pdf.jobs import queue_parse_run
from paper_engine.retrieval.embedding_jobs import queue_embedding_run
from paper_engine.retrieval.embedding_worker import EmbeddingWorker
from paper_engine.storage.database import init_db


class RecordingProvider:
    provider = "test-provider"
    model = "test-model"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(self.calls)), float(index)] for index, _ in enumerate(texts)]


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(database_path=tmp_path / "test.db")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash, parse_status)
        VALUES ('paper-1', 'space-1', ?, 'hash', 'parsed')
        """,
        (str(pdf),),
    )
    parse_run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={},
    )
    conn.execute(
        """
        UPDATE parse_runs
        SET status = 'completed', completed_at = datetime('now')
        WHERE id = ?
        """,
        (parse_run_id,),
    )
    for index, content_hash in enumerate(["hash-1", "hash-2", "hash-3"], start=1):
        conn.execute(
            """
            INSERT INTO passages (
                id, paper_id, space_id, original_text, parse_run_id,
                content_hash, paragraph_index
            )
            VALUES (?, 'paper-1', 'space-1', ?, ?, ?, ?)
            """,
            (
                f"{parse_run_id}:passage-{index}",
                f"text {index}",
                parse_run_id,
                content_hash,
                index,
            ),
        )
    queue_embedding_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id=parse_run_id,
    )
    conn.commit()
    return conn


def test_embedding_worker_batches_passages(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    provider = RecordingProvider()
    worker = EmbeddingWorker(
        conn_factory=lambda: conn,
        worker_id="worker-1",
        batch_size=2,
        prewarm_provider=False,
        close_connection=False,
        provider_factory=lambda conn_arg: provider,
    )

    assert worker.run_once() is True

    run = conn.execute("SELECT status, passage_count, embedded_count, batch_count FROM embedding_runs").fetchone()
    assert provider.calls == [["text 1", "text 2"], ["text 3"]]
    assert run["status"] == "completed"
    assert run["passage_count"] == 3
    assert run["embedded_count"] == 3
    assert run["batch_count"] == 2
    assert conn.execute("SELECT COUNT(*) FROM passage_embeddings").fetchone()[0] == 3


def test_embedding_worker_skips_existing_and_reuses_content_hash(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    provider = RecordingProvider()
    rows = conn.execute("SELECT id, content_hash FROM passages ORDER BY id").fetchall()
    conn.execute(
        """
        INSERT INTO papers (id, space_id, title, parse_status)
        VALUES ('paper-2', 'space-1', 'Reusable Paper', 'parsed')
        """
    )
    conn.execute(
        """
        INSERT INTO passages (id, paper_id, space_id, original_text, content_hash)
        VALUES ('external-passage', 'paper-2', 'space-1', 'reusable text', ?)
        """,
        (rows[1]["content_hash"],),
    )
    conn.execute(
        """
        INSERT INTO passage_embeddings (
            passage_id, provider, model, dimension, embedding_json, content_hash
        )
        VALUES (?, 'test-provider', 'test-model', 2, '[9,9]', ?)
        """,
        (rows[0]["id"], rows[0]["content_hash"]),
    )
    conn.execute(
        """
        INSERT INTO passage_embeddings (
            passage_id, provider, model, dimension, embedding_json, content_hash
        )
        VALUES ('external-passage', 'test-provider', 'test-model', 2, '[8,8]', ?)
        """,
        (rows[1]["content_hash"],),
    )
    conn.commit()

    worker = EmbeddingWorker(
        conn_factory=lambda: conn,
        worker_id="worker-1",
        batch_size=16,
        prewarm_provider=False,
        close_connection=False,
        provider_factory=lambda conn_arg: provider,
    )

    assert worker.run_once() is True

    run = conn.execute(
        """
        SELECT status, passage_count, embedded_count, reused_count, skipped_count,
               batch_count
        FROM embedding_runs
        """
    ).fetchone()
    embeddings = conn.execute(
        """
        SELECT passage_id, embedding_json
        FROM passage_embeddings
        WHERE passage_id LIKE ?
        ORDER BY passage_id
        """,
        (f"{str(rows[0]['id']).split(':', 1)[0]}:%",),
    ).fetchall()
    assert provider.calls == [["text 3"]]
    assert run["status"] == "completed"
    assert run["passage_count"] == 3
    assert run["embedded_count"] == 1
    assert run["reused_count"] == 1
    assert run["skipped_count"] == 1
    assert run["batch_count"] == 1
    assert [row["embedding_json"] for row in embeddings] == ["[9,9]", "[8,8]", "[1.0,0.0]"]


def test_embedding_worker_syncs_vector_index_after_completion(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    provider = RecordingProvider()
    worker = EmbeddingWorker(
        conn_factory=lambda: conn,
        worker_id="worker-1",
        batch_size=3,
        prewarm_provider=False,
        close_connection=False,
        provider_factory=lambda conn_arg: provider,
    )

    assert worker.run_once() is True

    indexed_count = conn.execute(
        "SELECT COUNT(*) FROM passage_embedding_vec_2"
    ).fetchone()[0]
    assert indexed_count == 3
