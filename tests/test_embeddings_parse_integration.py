"""Integration tests for required embeddings after PDF parsing."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import paper_engine.pdf.persistence as pdf_persistence
import paper_engine.papers.service as papers_service
from paper_engine.storage.database import get_connection, init_db
from paper_engine.retrieval.embeddings import EmbeddingConfig, EmbeddingProviderError
from paper_engine.api.app import app
from paper_engine.pdf.models import (
    ParseDocument,
    ParseElement,
    PassageRecord,
    PdfQualityReport,
)


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


def _install_fake_parser(
    monkeypatch: pytest.MonkeyPatch,
    *,
    passage_texts: list[str] | None = None,
) -> None:
    quality = PdfQualityReport(page_count=1, native_text_pages=1, quality_score=0.98)

    def fake_route_parse(
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        assert file_path.exists()
        assert quality_report == quality
        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend="test-parser",
            extraction_method="native_text",
            quality=quality,
            elements=[
                ParseElement(
                    id="element-1",
                    element_index=0,
                    element_type="paragraph",
                    text=" ".join(passage_texts or ["alpha method", "beta result"]),
                    page_number=1,
                    extraction_method="native_text",
                )
            ],
        )

    def fake_chunk_parse_document(document: ParseDocument) -> list[PassageRecord]:
        texts = passage_texts or ["alpha method", "beta result"]
        return [
            PassageRecord(
                id=f"passage-{index + 1}",
                paper_id=document.paper_id,
                space_id=document.space_id,
                section="Body",
                page_number=1,
                paragraph_index=index,
                original_text=text,
                parse_confidence=0.99,
                passage_type="body",
                element_ids=["element-1"],
                heading_path=[],
                token_count=len(text.split()),
                char_count=len(text),
                content_hash=f"hash-{index + 1}",
                parser_backend=document.backend,
                extraction_method="native_text",
                quality_flags=[],
            )
            for index, text in enumerate(texts)
        ]

    monkeypatch.setattr(papers_service, "inspect_pdf", lambda file_path: quality)
    monkeypatch.setattr(papers_service, "route_parse", fake_route_parse)
    monkeypatch.setattr(papers_service, "chunk_parse_document", fake_chunk_parse_document)


async def _upload_paper(client: AsyncClient) -> str:
    await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("paper.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert upload.status_code == 200
    return str(upload.json()["id"])


async def _upload_and_parse(client: AsyncClient) -> dict[str, Any]:
    paper_id = await _upload_paper(client)
    parse = await client.post(f"/api/papers/{paper_id}/parse")
    assert parse.status_code == 200
    return parse.json()


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


@pytest.mark.asyncio
async def test_parse_with_default_local_e5_provider_stores_passage_embeddings(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default local E5 provider is used during PDF parsing."""
    _install_fake_parser(monkeypatch)
    provider = RecordingEmbeddingProvider(
        [[0.1, 0.2], [0.3, 0.4]],
        model="intfloat/multilingual-e5-small",
    )

    def fake_get_embedding_provider(
        config: EmbeddingConfig,
    ) -> RecordingEmbeddingProvider:
        assert config.provider == "local"
        assert config.model == "intfloat/multilingual-e5-small"
        return provider

    monkeypatch.setattr(
        pdf_persistence,
        "get_embedding_provider",
        fake_get_embedding_provider,
    )

    data = await _upload_and_parse(client)

    assert data["status"] == "parsed"
    assert data["warnings"] == []
    assert provider.calls == [["passage: alpha method", "passage: beta result"]]
    assert len(_fetch_embedding_rows()) == 2


@pytest.mark.asyncio
async def test_parse_with_configured_embedding_provider_stores_passage_embeddings(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured embeddings are generated for persisted passages."""
    _install_fake_parser(monkeypatch)
    _set_app_state(
        {
            "embedding_provider": "openai",
            "embedding_model": "test-model",
            "embedding_api_key": "test-key",
        }
    )
    provider = RecordingEmbeddingProvider([[0.1, 0.2], [0.3, 0.4]])

    def fake_get_embedding_provider(
        config: EmbeddingConfig,
    ) -> RecordingEmbeddingProvider:
        assert config.provider == "openai"
        assert config.model == "test-model"
        return provider

    monkeypatch.setattr(
        pdf_persistence,
        "get_embedding_provider",
        fake_get_embedding_provider,
    )

    data = await _upload_and_parse(client)

    assert data["status"] == "parsed"
    assert data["warnings"] == []
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
async def test_parse_embedding_failure_marks_parse_as_error(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paper is not considered parsed unless passage embeddings are stored."""
    _install_fake_parser(monkeypatch)
    _set_app_state(
        {
            "embedding_provider": "openai",
            "embedding_model": "test-model",
            "embedding_api_key": "test-key",
        }
    )
    provider = FailingEmbeddingProvider([])
    monkeypatch.setattr(
        pdf_persistence,
        "get_embedding_provider",
        lambda config: provider,
    )

    paper_id = await _upload_paper(client)
    parse = await client.post(f"/api/papers/{paper_id}/parse")

    assert parse.status_code == 200
    data = parse.json()
    assert data["status"] == "error"
    assert data["passage_count"] == 0
    assert data["parse_run_id"] is None
    assert data["warnings"] == ["embedding_error:vector service unavailable"]
    assert _fetch_embedding_rows() == []

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        paper = conn.execute(
            "SELECT parse_status FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        parse_run_count = conn.execute("SELECT COUNT(*) FROM parse_runs").fetchone()[0]
        passage_count = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    finally:
        conn.close()

    assert paper["parse_status"] == "error"
    assert parse_run_count == 0
    assert passage_count == 0
