"""Tests for paper import and management routes."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from db import DATABASE_PATH, init_db
from main import app


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
    import db as db_module
    import config as config_module

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
    # parse_status should be either 'parsed' or 'error', not 'pending'
    assert resp.json()["parse_status"] in ("parsed", "error", "parsing")


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
