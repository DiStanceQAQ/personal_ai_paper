"""Tests for paper import and management routes."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.storage.database import get_connection
from paper_engine.storage.database import DATABASE_PATH, init_db
from paper_engine.api.app import app


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        yield db_file


@pytest.fixture
def client(db_path: str) -> Generator[AsyncClient, None, None]:
    """Create a test client with a temporary database + temp spaces dir."""
    import paper_engine.storage.database as db_module
    import paper_engine.core.config as config_module

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


async def _create_and_activate_space(client: AsyncClient, name: str = "Test Space") -> str:
    """Helper: create a space and set it as active."""
    resp = await client.post(
        "/api/spaces", json={"name": name, "description": "Test"}
    )
    assert resp.status_code == 200
    space_id = resp.json()["id"]

    resp = await client.put(f"/api/spaces/active/{space_id}")
    assert resp.status_code == 200
    return str(space_id)


def _make_minimal_pdf() -> bytes:
    """Create a minimal valid PDF file for testing."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n190\n%%EOF"
    )


def _make_text_pdf(text: str) -> bytes:
    """Create a simple valid PDF with extractable text."""
    import pymupdf

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = pymupdf.open()  # type: ignore[no-untyped-call]
        page = doc.new_page()
        page.insert_text((72, 150), text, fontsize=11)
        doc.save(f.name)  # type: ignore[no-untyped-call]
        doc.close()  # type: ignore[no-untyped-call]
        pdf_bytes = Path(f.name).read_bytes()
        Path(f.name).unlink()
    return pdf_bytes


@pytest.mark.asyncio
async def test_upload_pdf_to_active_space(client: AsyncClient) -> None:
    """Uploading a PDF stores the paper and queues a parse run."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["parse_status"] == "pending"
    assert data["file_hash"]
    assert "paper" in data["file_path"] or data["file_path"].endswith(".pdf")
    assert data["queued_parse_run_id"]

    runs = await client.get(f"/api/papers/{data['id']}/parse-runs")
    assert runs.status_code == 200
    assert runs.json()[0]["status"] == "queued"
    assert runs.json()[0]["backend"] in {"docling", "mineru"}


@pytest.mark.asyncio
async def test_get_paper_metadata_returns_parsed_provenance(client: AsyncClient) -> None:
    """The metadata endpoint returns core fields with parsed source diagnostics."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    assert resp.status_code == 200
    paper_id = resp.json()["id"]

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            """
            UPDATE papers
            SET title = 'Parsed Title',
                authors = 'Ada Lovelace',
                year = 2026,
                doi = '10.1234/example',
                metadata_status = 'extracted',
                metadata_sources_json = '{"title":"document.title","doi":"regex.doi"}',
                metadata_confidence_json = '{"title":0.9,"doi":0.98}',
                user_edited_fields_json = '["authors"]'
            WHERE id = ?
            """,
            (paper_id,),
        )
        conn.commit()
    finally:
        conn.close()

    metadata = await client.get(f"/api/papers/{paper_id}/metadata")

    assert metadata.status_code == 200
    data = metadata.json()
    assert data["paper_id"] == paper_id
    assert data["title"] == "Parsed Title"
    assert data["authors"] == "Ada Lovelace"
    assert data["year"] == 2026
    assert data["doi"] == "10.1234/example"
    assert data["metadata_status"] == "extracted"
    assert data["metadata_sources"] == {"title": "document.title", "doi": "regex.doi"}
    assert data["metadata_confidence"] == {"title": 0.9, "doi": 0.98}
    assert data["user_edited_fields"] == ["authors"]


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(client: AsyncClient) -> None:
    """Test that non-PDF files are rejected."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_requires_active_space(client: AsyncClient) -> None:
    """Test that uploading without an active space returns 400."""
    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_rejects_deleted_active_space(client: AsyncClient) -> None:
    """A deleted space left in app_state must not accept new papers."""
    space_id = await _create_and_activate_space(client)
    delete_resp = await client.delete(f"/api/spaces/{space_id}")
    assert delete_resp.status_code == 200

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )

    assert resp.status_code == 400
    assert "active space" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_rejects_archived_active_space(client: AsyncClient) -> None:
    """An archived space left in app_state must not accept new papers."""
    space_id = await _create_and_activate_space(client)
    archive_resp = await client.patch(f"/api/spaces/{space_id}/archive")
    assert archive_resp.status_code == 200

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )

    assert resp.status_code == 400
    assert "active space" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_duplicate_upload_reuses_existing_paper_and_queues_parse(
    client: AsyncClient,
) -> None:
    """Duplicate PDFs in one space reuse the paper and queue another parse."""
    await _create_and_activate_space(client)

    pdf = _make_minimal_pdf()

    # First upload
    resp1 = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", pdf, "application/pdf")},
    )
    assert resp1.status_code == 200

    resp2 = await client.post(
        "/api/papers/upload",
        files={"file": ("test2.pdf", pdf, "application/pdf")},
    )
    assert resp2.status_code == 200
    assert resp2.json()["id"] == resp1.json()["id"]
    assert resp2.json()["queued_parse_run_id"] != resp1.json()["queued_parse_run_id"]


@pytest.mark.asyncio
async def test_duplicate_allowed_across_spaces(client: AsyncClient) -> None:
    """Test that the same PDF can be imported in different spaces."""
    # Space 1
    await _create_and_activate_space(client)

    pdf = _make_minimal_pdf()
    resp1 = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", pdf, "application/pdf")},
    )
    assert resp1.status_code == 200

    # Create Space 2 and activate it
    resp2 = await client.post(
        "/api/spaces", json={"name": "Space 2"}
    )
    assert resp2.status_code == 200
    space2_id = resp2.json()["id"]
    await client.put(f"/api/spaces/active/{space2_id}")

    resp3 = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", pdf, "application/pdf")},
    )
    assert resp3.status_code == 200  # Should succeed in different space


@pytest.mark.asyncio
async def test_list_papers(client: AsyncClient) -> None:
    """Test listing papers in the active space."""
    await _create_and_activate_space(client)

    pdf = _make_minimal_pdf()
    await client.post(
        "/api/papers/upload",
        files={"file": ("a.pdf", pdf, "application/pdf")},
    )
    await client.post(
        "/api/papers/upload",
        files={"file": ("b.pdf", pdf + b"extra", "application/pdf")},
    )

    resp = await client.get("/api/papers")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for paper in data:
        assert "space_id" in paper


@pytest.mark.asyncio
async def test_list_papers_rejects_explicit_non_active_space(
    client: AsyncClient,
) -> None:
    """Listing papers must not expose an inactive space through query params."""
    active_space_id = await _create_and_activate_space(client, "Active Papers")
    other_space_resp = await client.post("/api/spaces", json={"name": "Other Papers"})
    assert other_space_resp.status_code == 200
    other_space_id = other_space_resp.json()["id"]
    assert other_space_id != active_space_id

    resp = await client.get("/api/papers", params={"space_id": other_space_id})

    assert resp.status_code == 403
    assert "active space" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_paper(client: AsyncClient) -> None:
    """Test getting a single paper by ID."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    resp = await client.get(f"/api/papers/{paper_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == paper_id


@pytest.mark.asyncio
async def test_single_paper_routes_are_scoped_to_active_space(
    client: AsyncClient,
) -> None:
    """Single-paper HTTP routes must not expose papers from inactive spaces."""
    original_space_id = await _create_and_activate_space(client, "Original Space")
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    assert upload.status_code == 200
    paper_id = upload.json()["id"]

    other_space_id = await _create_and_activate_space(client, "Other Space")
    assert other_space_id != original_space_id

    get_resp = await client.get(f"/api/papers/{paper_id}")
    patch_resp = await client.patch(
        f"/api/papers/{paper_id}",
        json={"title": "Should not change from another space"},
    )
    parse_resp = await client.post(f"/api/papers/{paper_id}/parse")
    passages_resp = await client.get(f"/api/papers/{paper_id}/passages")
    delete_resp = await client.delete(f"/api/papers/{paper_id}")

    assert get_resp.status_code == 404
    assert patch_resp.status_code == 404
    assert parse_resp.status_code == 404
    assert passages_resp.status_code == 404
    assert delete_resp.status_code == 404

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        row = conn.execute(
            "SELECT title FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, original_space_id),
        ).fetchone()
        assert row is not None
        assert row["title"] == "test"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_update_paper_metadata(client: AsyncClient) -> None:
    """Test updating paper metadata fields."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/papers/{paper_id}",
        json={
            "title": "Test Paper Title",
            "authors": "Jane Doe, John Smith",
            "year": 2024,
            "doi": "10.1234/test.1",
            "abstract": "This is a test abstract.",
            "relation_to_idea": "supports",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Test Paper Title"
    assert data["authors"] == "Jane Doe, John Smith"
    assert data["year"] == 2024
    assert data["doi"] == "10.1234/test.1"
    assert data["abstract"] == "This is a test abstract."
    assert data["relation_to_idea"] == "supports"


@pytest.mark.asyncio
async def test_update_paper_rejects_invalid_relation_to_idea(
    client: AsyncClient,
) -> None:
    """Paper relation values should be validated before hitting SQLite checks."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/papers/{paper_id}",
        json={"relation_to_idea": "surprisingly-related"},
    )

    assert resp.status_code == 422
    assert "relation_to_idea" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_paper_partial(client: AsyncClient) -> None:
    """Test partial metadata update preserves other fields."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    # Set title first
    await client.patch(f"/api/papers/{paper_id}", json={"title": "Initial Title"})
    # Then update only authors
    resp = await client.patch(
        f"/api/papers/{paper_id}", json={"authors": "New Author"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Initial Title"  # preserved
    assert data["authors"] == "New Author"


@pytest.mark.asyncio
async def test_parse_paper(client: AsyncClient) -> None:
    """The parse endpoint queues a re-parse run."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    resp = await client.post(f"/api/papers/{paper_id}/parse")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paper_id"] == paper_id
    assert data["status"] == "queued"
    assert set(data) >= {
        "status",
        "paper_id",
        "passage_count",
        "parse_run_id",
        "backend",
        "quality_score",
        "warnings",
    }
    assert isinstance(data["warnings"], list)
    assert data["passage_count"] == 0
    assert data["parse_run_id"]
    assert data["backend"] in {"docling", "mineru"}
    assert data["quality_score"] is None


@pytest.mark.asyncio
async def test_create_analysis_run_queues_and_reuses_active_run(
    client: AsyncClient,
) -> None:
    """Analysis runs are durable background jobs and duplicate active posts reuse one."""
    await _create_and_activate_space(client)

    upload_resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = upload_resp.json()["id"]

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            "UPDATE papers SET parse_status = 'parsed' WHERE id = ?",
            (paper_id,),
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, original_text)
            VALUES ('passage-1', ?, ?, 'Parsed text')
            """,
            (paper_id, upload_resp.json()["space_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    first = await client.post(f"/api/papers/{paper_id}/analysis-runs")
    second = await client.post(f"/api/papers/{paper_id}/analysis-runs")

    assert first.status_code == 202
    assert second.status_code == 202
    first_data = first.json()
    second_data = second.json()
    assert first_data["id"] == second_data["id"]
    assert first_data["status"] == "queued"
    assert first_data["paper_id"] == paper_id

    listed = await client.get(f"/api/papers/{paper_id}/analysis-runs")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == first_data["id"]

    fetched = await client.get(
        f"/api/papers/{paper_id}/analysis-runs/{first_data['id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == first_data["id"]


@pytest.mark.asyncio
async def test_create_analysis_run_requires_parsed_passages(client: AsyncClient) -> None:
    """AI analysis cannot start before PDF parsing produces passages."""
    await _create_and_activate_space(client)

    upload_resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = upload_resp.json()["id"]

    resp = await client.post(f"/api/papers/{paper_id}/analysis-runs")

    assert resp.status_code == 409
    assert "PDF parsing has not completed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_analysis_run_status_transitions(client: AsyncClient) -> None:
    """Queued analysis runs can be cancelled and repeated cancels are idempotent."""
    await _create_and_activate_space(client)

    upload_resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = upload_resp.json()["id"]
    space_id = upload_resp.json()["space_id"]

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            "UPDATE papers SET parse_status = 'parsed' WHERE id = ?",
            (paper_id,),
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, original_text)
            VALUES ('passage-1', ?, ?, 'Parsed text')
            """,
            (paper_id, space_id),
        )
        conn.commit()
    finally:
        conn.close()

    run_resp = await client.post(f"/api/papers/{paper_id}/analysis-runs")
    assert run_resp.status_code == 202
    run_id = run_resp.json()["id"]

    cancel_resp = await client.post(
        f"/api/papers/{paper_id}/analysis-runs/{run_id}/cancel"
    )
    repeat_resp = await client.post(
        f"/api/papers/{paper_id}/analysis-runs/{run_id}/cancel"
    )

    assert cancel_resp.status_code == 200
    assert repeat_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"
    assert cancel_resp.json()["last_error"] == "cancelled_by_user"
    assert repeat_resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_analysis_run_rejects_terminal_status(client: AsyncClient) -> None:
    """Completed or failed analysis runs cannot be cancelled."""
    await _create_and_activate_space(client)

    upload_resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = upload_resp.json()["id"]
    space_id = upload_resp.json()["space_id"]

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            "UPDATE papers SET parse_status = 'parsed' WHERE id = ?",
            (paper_id,),
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, original_text)
            VALUES ('passage-1', ?, ?, 'Parsed text')
            """,
            (paper_id, space_id),
        )
        conn.commit()
    finally:
        conn.close()

    run_resp = await client.post(f"/api/papers/{paper_id}/analysis-runs")
    assert run_resp.status_code == 202
    run_id = run_resp.json()["id"]

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            "UPDATE analysis_runs SET status = 'completed' WHERE id = ?",
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    cancel_resp = await client.post(
        f"/api/papers/{paper_id}/analysis-runs/{run_id}/cancel"
    )

    assert cancel_resp.status_code == 409


@pytest.mark.asyncio
async def test_parse_preserves_error_status(client: AsyncClient) -> None:
    """Queueing a parse keeps the paper available while the worker is pending."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    await client.post(f"/api/papers/{paper_id}/parse")

    # Paper should still exist
    resp = await client.get(f"/api/papers/{paper_id}")
    assert resp.status_code == 200
    assert resp.json()["parse_status"] == "pending"


@pytest.mark.asyncio
async def test_parse_invalid_pdf_queues_worker_validation(
    client: AsyncClient,
) -> None:
    """Invalid PDF bytes are queued; worker validation owns parse failure."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("invalid.pdf", b"not actually a pdf", "application/pdf")},
    )
    assert resp.status_code == 200
    paper_id = resp.json()["id"]

    parse_resp = await client.post(f"/api/papers/{paper_id}/parse")
    assert parse_resp.status_code == 200
    data = parse_resp.json()
    assert data["status"] == "queued"
    assert data["paper_id"] == paper_id
    assert data["passage_count"] == 0
    assert data["parse_run_id"]
    assert data["backend"] in {"docling", "mineru"}
    assert data["quality_score"] is None
    assert isinstance(data["warnings"], list)
    assert data["warnings"] == []

    paper_resp = await client.get(f"/api/papers/{paper_id}")
    assert paper_resp.status_code == 200
    assert paper_resp.json()["parse_status"] == "pending"

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        passage_count = conn.execute(
            "SELECT COUNT(*) FROM passages WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()[0]
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM passages_fts WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    assert passage_count == 0
    assert fts_count == 0


@pytest.mark.asyncio
async def test_get_nonexistent_paper_returns_404(client: AsyncClient) -> None:
    """Test that requesting a nonexistent paper returns 404."""
    resp = await client.get("/api/papers/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_passages(client: AsyncClient) -> None:
    """Test listing passages for a paper."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    await client.post(f"/api/papers/{paper_id}/parse")

    resp = await client.get(f"/api/papers/{paper_id}/passages")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_passages_nonexistent_paper(client: AsyncClient) -> None:
    """Test that listing passages for nonexistent paper returns 404."""
    resp = await client.get("/api/papers/nonexistent-id/passages")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reparsing_queues_distinct_parse_runs(
    client: AsyncClient,
) -> None:
    """Re-running parse queues distinct runs without synchronously parsing."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={
            "file": (
                "test.pdf",
                _make_text_pdf("method transformer attention mechanism"),
                "application/pdf",
            )
        },
    )
    paper_id = resp.json()["id"]
    upload_run_id = resp.json()["queued_parse_run_id"]

    first_parse = await client.post(f"/api/papers/{paper_id}/parse")
    assert first_parse.status_code == 200
    first_parse_data = first_parse.json()
    assert first_parse_data["status"] == "queued"
    assert first_parse_data["parse_run_id"]
    assert first_parse_data["parse_run_id"] != upload_run_id
    assert first_parse_data["backend"]

    second_parse = await client.post(f"/api/papers/{paper_id}/parse")
    assert second_parse.status_code == 200
    second_parse_data = second_parse.json()
    assert second_parse_data["status"] == "queued"
    assert second_parse_data["parse_run_id"]
    assert second_parse_data["parse_run_id"] != first_parse_data["parse_run_id"]

    runs = await client.get(f"/api/papers/{paper_id}/parse-runs")
    assert runs.status_code == 200
    run_ids = {run["id"] for run in runs.json()}
    assert {upload_run_id, first_parse_data["parse_run_id"], second_parse_data["parse_run_id"]}.issubset(run_ids)


def _insert_parse_diagnostic_rows(
    *,
    paper_id: str,
    space_id: str,
    parse_run_id: str = "run-1",
) -> None:
    """Insert structured parse rows for diagnostics route tests."""
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            """
            INSERT INTO parse_runs (
                id, paper_id, space_id, backend, extraction_method, status,
                quality_score, started_at, completed_at, warnings_json,
                config_json, metadata_json
            )
            VALUES (
                ?, ?, ?, 'pymupdf4llm', 'native_text', 'completed',
                0.93, '2026-04-27 10:00:00', '2026-04-27 10:00:02',
                '["minor-layout-warning"]', '{"chunking":"default"}',
                '{"title":"Diagnostics Paper"}'
            )
            """,
            (parse_run_id, paper_id, space_id),
        )
        conn.execute(
            """
            INSERT INTO document_elements (
                id, parse_run_id, paper_id, space_id, element_index,
                element_type, text, page_number, bbox_json,
                heading_path_json, metadata_json
            )
            VALUES
                (?, ?, ?, ?, 0, 'heading', 'Methods', 1, '[0,0,10,10]', '["Methods"]', '{}'),
                (?, ?, ?, ?, 1, 'paragraph', 'Paragraph text', 1, NULL, '["Methods"]', '{}'),
                (?, ?, ?, ?, 2, 'table', 'Table 1', 2, '[0,20,100,60]', '["Results"]', '{}')
            """,
            (
                f"{parse_run_id}:element-heading",
                parse_run_id,
                paper_id,
                space_id,
                f"{parse_run_id}:element-paragraph",
                parse_run_id,
                paper_id,
                space_id,
                f"{parse_run_id}:element-table",
                parse_run_id,
                paper_id,
                space_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO document_tables (
                id, parse_run_id, paper_id, space_id, element_id,
                table_index, page_number, caption, cells_json,
                bbox_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, 0, 2, 'Results table', '[["Metric","Value"],["F1","0.9"]]', '[0,20,100,60]', '{}')
            """,
            (
                f"{parse_run_id}:table-1",
                parse_run_id,
                paper_id,
                space_id,
                f"{parse_run_id}:element-table",
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_list_parse_runs_ordered_and_scoped_to_active_space(
    client: AsyncClient,
) -> None:
    """Parse run diagnostics should be ordered and active-space scoped."""
    space_id = await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_text_pdf("diagnostics"), "application/pdf")},
    )
    paper_id = upload.json()["id"]
    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute("DELETE FROM parse_runs WHERE paper_id = ?", (paper_id,))
        conn.commit()
    finally:
        conn.close()

    _insert_parse_diagnostic_rows(
        paper_id=paper_id, space_id=space_id, parse_run_id="run-older"
    )

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            """
            INSERT INTO parse_runs (
                id, paper_id, space_id, backend, extraction_method, status,
                quality_score, started_at, completed_at, warnings_json,
                config_json, metadata_json
            )
            VALUES (
                'run-newer', ?, ?, 'docling', 'layout_model', 'completed',
                0.98, '2026-04-27 11:00:00', '2026-04-27 11:00:05',
                '[]', '{}', '{}'
            )
            """,
            (paper_id, space_id),
        )
        conn.commit()
    finally:
        conn.close()

    runs = await client.get(f"/api/papers/{paper_id}/parse-runs")
    assert runs.status_code == 200
    data = runs.json()
    assert [run["id"] for run in data] == ["run-newer", "run-older"]
    assert data[0]["created_at"] == "2026-04-27 11:00:00"
    assert data[0]["backend"] == "docling"
    assert "last_error" in data[0]

    other_space_id = await _create_and_activate_space(client, "Other Space")
    assert other_space_id != space_id
    scoped_out = await client.get(f"/api/papers/{paper_id}/parse-runs")
    assert scoped_out.status_code == 404


@pytest.mark.asyncio
async def test_list_document_elements_filters_type_page_and_limit(
    client: AsyncClient,
) -> None:
    """Element diagnostics should honor type, page, and limit filters."""
    space_id = await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_text_pdf("diagnostics"), "application/pdf")},
    )
    paper_id = upload.json()["id"]
    _insert_parse_diagnostic_rows(paper_id=paper_id, space_id=space_id)

    filtered = await client.get(
        f"/api/papers/{paper_id}/elements",
        params={"type": "paragraph", "page": 1, "limit": 1},
    )
    assert filtered.status_code == 200
    data = filtered.json()
    assert len(data) == 1
    assert data[0]["element_type"] == "paragraph"
    assert data[0]["page_number"] == 1
    assert data[0]["text"] == "Paragraph text"

    first_two = await client.get(
        f"/api/papers/{paper_id}/elements",
        params={"limit": 2},
    )
    assert first_two.status_code == 200
    assert [element["element_index"] for element in first_two.json()] == [0, 1]


@pytest.mark.asyncio
async def test_list_document_tables_scoped_to_active_space(
    client: AsyncClient,
) -> None:
    """Table diagnostics should only expose tables for the active paper space."""
    space_id = await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_text_pdf("diagnostics"), "application/pdf")},
    )
    paper_id = upload.json()["id"]
    _insert_parse_diagnostic_rows(paper_id=paper_id, space_id=space_id)

    tables = await client.get(f"/api/papers/{paper_id}/tables")
    assert tables.status_code == 200
    data = tables.json()
    assert len(data) == 1
    assert data[0]["paper_id"] == paper_id
    assert data[0]["space_id"] == space_id
    assert data[0]["caption"] == "Results table"
    assert data[0]["cells_json"] == '[["Metric","Value"],["F1","0.9"]]'

    await _create_and_activate_space(client, "Other Space")
    scoped_out = await client.get(f"/api/papers/{paper_id}/tables")
    assert scoped_out.status_code == 404


@pytest.mark.asyncio
async def test_delete_paper_removes_database_rows_fts_index_and_pdf(
    client: AsyncClient,
) -> None:
    """Deleting a paper should remove its dependent data and stored PDF."""
    space_id = await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={
            "file": (
                "test.pdf",
                _make_text_pdf("method transformer attention mechanism"),
                "application/pdf",
            )
        },
    )
    assert resp.status_code == 200
    paper = resp.json()
    paper_id = paper["id"]
    pdf_path = Path(paper["file_path"])
    assert pdf_path.exists()

    parse_resp = await client.post(f"/api/papers/{paper_id}/parse")
    assert parse_resp.status_code == 200
    assert parse_resp.json()["status"] == "queued"

    card_resp = await client.post(
        f"/api/papers/{paper_id}/cards",
        json={"card_type": "Method", "summary": "uses attention"},
    )
    assert card_resp.status_code == 200

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            """
            INSERT INTO passages (
                id, paper_id, space_id, original_text, parser_backend,
                extraction_method
            )
            VALUES ('passage-1', ?, ?, 'method transformer attention mechanism', 'test', 'native_text')
            """,
            (paper_id, space_id),
        )
        conn.execute(
            """
            INSERT INTO passages_fts (
                passage_id, paper_id, space_id, section, original_text
            )
            VALUES ('passage-1', ?, ?, '', 'method transformer attention mechanism')
            """,
            (paper_id, space_id),
        )
        conn.execute(
            """INSERT INTO notes (id, space_id, paper_id, content)
               VALUES ('note-1', ?, ?, 'keep deletion referentially clean')""",
            (space_id, paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    search_resp = await client.get("/api/search", params={"q": "transformer"})
    assert search_resp.status_code == 200
    assert len(search_resp.json()) > 0

    delete_resp = await client.delete(f"/api/papers/{paper_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"status": "deleted", "paper_id": paper_id}

    assert not pdf_path.exists()
    assert (await client.get(f"/api/papers/{paper_id}")).status_code == 404

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        paper_count = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()[0]
        assert paper_count == 0
        for table in ("passages", "knowledge_cards", "notes"):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()[0]
            assert count == 0
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM passages_fts WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()[0]
        assert fts_count == 0
    finally:
        conn.close()

    search_after_delete = await client.get("/api/search", params={"q": "transformer"})
    assert search_after_delete.status_code == 200
    assert search_after_delete.json() == []
