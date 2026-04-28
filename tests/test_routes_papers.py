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
    """Test uploading a PDF to the active space."""
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
async def test_duplicate_detection_same_space(client: AsyncClient) -> None:
    """Test that duplicate PDFs in the same space are detected."""
    await _create_and_activate_space(client)

    pdf = _make_minimal_pdf()

    # First upload
    resp1 = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", pdf, "application/pdf")},
    )
    assert resp1.status_code == 200

    # Second upload of same content
    resp2 = await client.post(
        "/api/papers/upload",
        files={"file": ("test2.pdf", pdf, "application/pdf")},
    )
    assert resp2.status_code == 409
    assert "Duplicate" in resp2.json()["detail"]


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
    """Test parsing a paper into passages."""
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = resp.json()["id"]

    resp = await client.post(f"/api/papers/{paper_id}/parse")
    # Minimal PDF may produce no text, so status could be 'error' or 'parsed'
    assert resp.status_code == 200
    data = resp.json()
    assert data["paper_id"] == paper_id
    assert data["status"] in ("parsed", "error")
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
    if data["status"] == "parsed":
        assert data["parse_run_id"]
        assert data["backend"]


@pytest.mark.asyncio
async def test_parse_preserves_error_status(client: AsyncClient) -> None:
    """Test that parse failure preserves paper record with error status."""
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
    # parse_status should be terminal after the parse request completes.
    assert resp.json()["parse_status"] in ("parsed", "error")


@pytest.mark.asyncio
async def test_parse_invalid_pdf_returns_compatible_error_response(
    client: AsyncClient,
) -> None:
    """Invalid PDFs should preserve the parse endpoint's legacy error contract."""
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
    assert data["status"] == "error"
    assert data["paper_id"] == paper_id
    assert data["passage_count"] == 0
    assert data["parse_run_id"] is None
    assert "backend" in data
    assert "quality_score" in data
    assert isinstance(data["warnings"], list)
    assert data["warnings"]

    paper_resp = await client.get(f"/api/papers/{paper_id}")
    assert paper_resp.status_code == 200
    assert paper_resp.json()["parse_status"] == "error"

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
async def test_reparsing_replaces_existing_passages_and_fts_rows(
    client: AsyncClient,
) -> None:
    """Re-running parse on a paper should not duplicate passages or search results."""
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

    first_parse = await client.post(f"/api/papers/{paper_id}/parse")
    assert first_parse.status_code == 200
    first_parse_data = first_parse.json()
    assert first_parse_data["status"] == "parsed"
    assert first_parse_data["parse_run_id"]
    assert first_parse_data["backend"]
    assert "quality_score" in first_parse_data
    assert isinstance(first_parse_data["warnings"], list)

    first_passages = await client.get(f"/api/papers/{paper_id}/passages")
    assert first_passages.status_code == 200
    first_passages_data = first_passages.json()
    first_count = len(first_passages_data)
    assert first_count > 0
    first_passage = first_passages_data[0]
    assert first_passage["parse_run_id"] == first_parse_data["parse_run_id"]
    assert first_passage["element_ids_json"] != "[]"
    assert first_passage["heading_path_json"] is not None
    assert first_passage["token_count"] is not None
    assert first_passage["char_count"] is not None
    assert first_passage["content_hash"]
    assert first_passage["parser_backend"] == first_parse_data["backend"]
    assert first_passage["extraction_method"]
    assert first_passage["quality_flags_json"] is not None

    first_search = await client.get("/api/search", params={"q": "transformer"})
    assert first_search.status_code == 200
    first_search_count = len(first_search.json())
    assert first_search_count > 0

    second_parse = await client.post(f"/api/papers/{paper_id}/parse")
    assert second_parse.status_code == 200
    second_parse_data = second_parse.json()
    assert second_parse_data["status"] == "parsed"
    assert second_parse_data["parse_run_id"]
    assert second_parse_data["parse_run_id"] != first_parse_data["parse_run_id"]

    second_passages = await client.get(f"/api/papers/{paper_id}/passages")
    assert second_passages.status_code == 200
    assert len(second_passages.json()) == first_count

    second_search = await client.get("/api/search", params={"q": "transformer"})
    assert second_search.status_code == 200
    assert len(second_search.json()) == first_search_count


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
    _insert_parse_diagnostic_rows(
        paper_id=paper_id, space_id=space_id, parse_run_id="run-older"
    )

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
    assert parse_resp.json()["status"] == "parsed"

    card_resp = await client.post(
        "/api/cards",
        json={"paper_id": paper_id, "card_type": "Method", "summary": "uses attention"},
    )
    assert card_resp.status_code == 200

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
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
