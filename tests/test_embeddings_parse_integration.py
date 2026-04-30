"""Integration tests for required embeddings after PDF parsing."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.storage.database import get_connection, init_db
from paper_engine.retrieval.embeddings import EmbeddingProviderError
from paper_engine.api.app import app
from paper_engine.pdf.models import (
    ParseDocument,
    ParseElement,
    PassageRecord,
    PdfQualityReport,
)
from paper_engine.pdf.worker import ParseWorker, ParserFactory
from paper_engine.retrieval.embedding_worker import EmbeddingWorker


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    """Create a temporary database for integration tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        yield db_file


@pytest.fixture
def client(db_path: str) -> Generator[AsyncClient, None, None]:
    """Create a test client with isolated database and spaces directory."""
    import paper_engine.core.config as config_module
    import paper_engine.storage.database as db_module

    with tempfile.TemporaryDirectory() as spaces_tmpdir:
        original_db_path = db_module.DATABASE_PATH
        original_spaces_dir = config_module.SPACES_DIR

        db_module.DATABASE_PATH = Path(db_path)
        config_module.SPACES_DIR = Path(spaces_tmpdir)

        transport = ASGITransport(app=app)
        test_client = AsyncClient(transport=transport, base_url="http://test")

        yield test_client

        db_module.DATABASE_PATH = original_db_path
        config_module.SPACES_DIR = original_spaces_dir


class RecordingEmbeddingProvider:
    """Configured test embedding provider that records input texts."""

    provider = "test-provider"

    def __init__(self, vectors: list[list[float]], *, model: str = "test-model") -> None:
        self.vectors = vectors
        self.model = model
        self.calls: list[list[str]] = []

    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return self.vectors


class FailingEmbeddingProvider(RecordingEmbeddingProvider):
    """Configured provider that fails during embedding generation."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        raise EmbeddingProviderError("vector service unavailable")


async def _create_and_activate_space(client: AsyncClient) -> str:
    resp = await client.post("/api/spaces", json={"name": "Parse Space"})
    assert resp.status_code == 200
    space_id = str(resp.json()["id"])
    activate = await client.put(f"/api/spaces/active/{space_id}")
    assert activate.status_code == 200
    return space_id


class FakeParserBackend:
    """Selected parser backend used by the worker integration tests."""

    def __init__(self, passage_texts: list[str] | None = None) -> None:
        self.passage_texts = passage_texts or ["alpha method", "beta result"]
        self.calls: list[Path] = []

    def is_available(self) -> bool:
        return True

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        assert file_path.exists()
        self.calls.append(file_path)
        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend="test-parser",
            extraction_method="native_text",
            quality=quality_report,
            elements=[
                ParseElement(
                    id="element-1",
                    element_index=0,
                    element_type="paragraph",
                    text=" ".join(self.passage_texts),
                    page_number=1,
                    extraction_method="native_text",
                    metadata={"passage_texts": self.passage_texts},
                )
            ],
        )


def _fake_chunk_parse_document(document: ParseDocument) -> list[PassageRecord]:
    texts = document.elements[0].metadata.get("passage_texts", [document.elements[0].text])
    assert isinstance(texts, list)
    return [
        PassageRecord(
            id=f"passage-{index + 1}",
            paper_id=document.paper_id,
            space_id=document.space_id,
            section="Body",
            page_number=1,
            paragraph_index=index,
            original_text=str(text),
            parse_confidence=0.99,
            passage_type="body",
            element_ids=["element-1"],
            heading_path=[],
            token_count=len(str(text).split()),
            char_count=len(str(text)),
            content_hash=f"hash-{index + 1}",
            parser_backend=document.backend,
            extraction_method="native_text",
            quality_flags=[],
        )
        for index, text in enumerate(texts)
    ]


def _build_parse_worker(
    *,
    backend: FakeParserBackend,
) -> ParseWorker:
    import paper_engine.storage.database as db_module

    quality = PdfQualityReport(page_count=1, native_text_pages=1, quality_score=0.98)

    return ParseWorker(
        conn_factory=lambda: get_connection(db_module.DATABASE_PATH),
        worker_id="embedding-test-worker",
        parser_factory=ParserFactory(
            mineru=lambda config: backend,
            docling=lambda config: backend,
        ),
        inspect_pdf=lambda file_path: quality,
        chunk_parse_document=_fake_chunk_parse_document,
    )


async def _upload_paper(client: AsyncClient) -> tuple[str, str]:
    await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("paper.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert upload.status_code == 200
    payload = upload.json()
    return str(payload["id"]), str(payload["queued_parse_run_id"])


async def _upload_and_run_worker(
    client: AsyncClient,
    *,
    passage_texts: list[str] | None = None,
) -> dict[str, Any]:
    backend = FakeParserBackend(passage_texts)
    paper_id, parse_run_id = await _upload_paper(client)
    worker = _build_parse_worker(backend=backend)

    assert worker.run_once() is True
    assert backend.calls

    row = _fetch_parse_run(parse_run_id)
    assert row["status"] == "completed"
    return {
        "paper_id": paper_id,
        "parse_run_id": parse_run_id,
        "backend": backend,
        "parse_run": row,
    }


def _fetch_parse_run(parse_run_id: str) -> dict[str, Any]:
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        row = conn.execute(
            """
            SELECT id, status, backend, warnings_json, last_error
            FROM parse_runs
            WHERE id = ?
            """,
            (parse_run_id,),
        ).fetchone()
        assert row is not None
        return dict(row)
    finally:
        conn.close()


def _fetch_paper_parse_status(paper_id: str) -> str:
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        row = conn.execute(
            "SELECT parse_status FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        assert row is not None
        return str(row["parse_status"])
    finally:
        conn.close()


def _fetch_paper_statuses(paper_id: str) -> dict[str, str]:
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        row = conn.execute(
            "SELECT parse_status, embedding_status FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        assert row is not None
        return {
            "parse_status": str(row["parse_status"]),
            "embedding_status": str(row["embedding_status"]),
        }
    finally:
        conn.close()


def _fetch_embedding_runs() -> list[dict[str, Any]]:
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        rows = conn.execute(
            """
            SELECT id, status, parse_run_id, passage_count, embedded_count,
                   reused_count, skipped_count, batch_count, last_error,
                   warnings_json
            FROM embedding_runs
            ORDER BY started_at, id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _count_rows(table: str) -> int:
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _set_app_state(values: dict[str, str]) -> None:
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO app_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def _fetch_embedding_rows() -> list[dict[str, Any]]:
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        rows = conn.execute(
            """
            SELECT passage_id, provider, model, dimension, embedding_json, content_hash
            FROM passage_embeddings
            ORDER BY passage_id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _run_embedding_worker(
    provider: RecordingEmbeddingProvider,
    *,
    batch_size: int = 16,
) -> None:
    import paper_engine.storage.database as db_module

    worker = EmbeddingWorker(
        conn_factory=lambda: get_connection(db_module.DATABASE_PATH),
        worker_id="embedding-test-worker",
        batch_size=batch_size,
        prewarm_provider=False,
        provider_factory=lambda conn: provider,
    )
    assert worker.run_once() is True


@pytest.mark.asyncio
async def test_parse_with_default_local_e5_provider_stores_passage_embeddings(
    client: AsyncClient,
) -> None:
    """The default local E5 provider is queued after parsing and run by worker."""
    provider = RecordingEmbeddingProvider(
        [[0.1, 0.2], [0.3, 0.4]],
        model="intfloat/multilingual-e5-small",
    )

    data = await _upload_and_run_worker(client)

    assert data["parse_run"]["status"] == "completed"
    assert json.loads(data["parse_run"]["warnings_json"]) == []
    assert _fetch_paper_statuses(data["paper_id"]) == {
        "parse_status": "parsed",
        "embedding_status": "pending",
    }
    assert _fetch_embedding_rows() == []
    assert _fetch_embedding_runs()[0]["status"] == "queued"

    _run_embedding_worker(provider)

    assert provider.calls == [["passage: alpha method", "passage: beta result"]]
    assert len(_fetch_embedding_rows()) == 2
    assert _fetch_paper_statuses(data["paper_id"]) == {
        "parse_status": "parsed",
        "embedding_status": "completed",
    }
    runs = _fetch_embedding_runs()
    assert runs[0]["status"] == "completed"
    assert runs[0]["passage_count"] == 2
    assert runs[0]["embedded_count"] == 2
    assert runs[0]["batch_count"] == 1


@pytest.mark.asyncio
async def test_parse_with_configured_embedding_provider_stores_passage_embeddings(
    client: AsyncClient,
) -> None:
    """Configured embeddings are generated by the embedding worker."""
    _set_app_state(
        {
            "embedding_provider": "openai",
            "embedding_model": "test-model",
            "embedding_api_key": "test-key",
        }
    )
    provider = RecordingEmbeddingProvider([[0.1, 0.2], [0.3, 0.4]])

    data = await _upload_and_run_worker(client)
    assert _fetch_embedding_rows() == []

    _run_embedding_worker(provider)

    assert data["parse_run"]["status"] == "completed"
    assert json.loads(data["parse_run"]["warnings_json"]) == []
    assert _fetch_paper_parse_status(data["paper_id"]) == "parsed"
    assert provider.calls == [["alpha method", "beta result"]]

    rows = _fetch_embedding_rows()
    assert [row["provider"] for row in rows] == ["test-provider", "test-provider"]
    assert [row["model"] for row in rows] == ["test-model", "test-model"]
    assert [row["dimension"] for row in rows] == [2, 2]
    assert [json.loads(row["embedding_json"]) for row in rows] == [
        [0.1, 0.2],
        [0.3, 0.4],
    ]
    assert [row["content_hash"] for row in rows] == ["hash-1", "hash-2"]
    assert all(str(row["passage_id"]).startswith(data["parse_run_id"]) for row in rows)


@pytest.mark.asyncio
async def test_embedding_failure_marks_embedding_run_failed_not_parse(
    client: AsyncClient,
) -> None:
    """Embedding failures no longer roll back successful PDF parsing."""
    _set_app_state(
        {
            "embedding_provider": "openai",
            "embedding_model": "test-model",
            "embedding_api_key": "test-key",
        }
    )
    provider = FailingEmbeddingProvider([])

    backend = FakeParserBackend()
    paper_id, parse_run_id = await _upload_paper(client)
    worker = _build_parse_worker(backend=backend)

    assert worker.run_once() is True

    run = _fetch_parse_run(parse_run_id)
    assert run["status"] == "completed"
    assert run["last_error"] is None
    assert _fetch_paper_statuses(paper_id) == {
        "parse_status": "parsed",
        "embedding_status": "pending",
    }

    _run_embedding_worker(provider)

    embedding_runs = _fetch_embedding_runs()
    assert embedding_runs[0]["status"] == "failed"
    assert embedding_runs[0]["last_error"] == "embedding_error:vector service unavailable"
    assert json.loads(embedding_runs[0]["warnings_json"]) == [
        "embedding_error:vector service unavailable"
    ]
    assert _fetch_paper_statuses(paper_id) == {
        "parse_status": "parsed",
        "embedding_status": "failed",
    }
    assert _fetch_embedding_rows() == []
    assert _count_rows("parse_runs") == 1
    assert _count_rows("passages") == 2
