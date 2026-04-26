"""Tests for full-text search with SQLite FTS5."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from db import DATABASE_PATH, get_connection, init_db
from main import app
from search import ensure_fts_index, rebuild_fts_index, search_passages


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        yield db_file


@pytest.fixture
def client(db_path: str) -> Generator[AsyncClient, None, None]:
    import db as db_module
    import config as config_module

    with tempfile.TemporaryDirectory() as spaces_tmpdir:
        original_db_path = db_module.DATABASE_PATH
        original_spaces = config_module.SPACES_DIR
        db_module.DATABASE_PATH = Path(db_path)
        config_module.SPACES_DIR = Path(spaces_tmpdir)

        transport = ASGITransport(app=app)
        test_client = AsyncClient(transport=transport, base_url="http://test")
        yield test_client

        db_module.DATABASE_PATH = original_db_path
        config_module.SPACES_DIR = original_spaces


def _make_minimal_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    )


async def _setup_space_with_passage(client: AsyncClient, text: str = "transformer attention mechanism") -> str:
    """Create a space, upload a PDF with custom text, parse it, return space_id."""
    resp = await client.post("/api/spaces", json={"name": "Search Test"})
    space_id = resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    # Create a custom PDF with the given text
    import pymupdf
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), text, fontsize=11)
        doc.save(f.name)
        doc.close()
        pdf_bytes = Path(f.name).read_bytes()
        Path(f.name).unlink()

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    paper_id = resp.json()["id"]
    await client.post(f"/api/papers/{paper_id}/parse")

    return space_id


def test_fts_index_creation() -> None:
    """Test that FTS5 index can be created and rebuilt."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        init_db(database_path=db_path)

        # FTS table should exist
        conn = get_connection(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='passages_fts'"
        ).fetchall()
        assert len(tables) == 1
        conn.close()


def test_search_returns_results() -> None:
    """Test that FTS5 search returns matching passages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        # Insert test data
        conn.execute(
            "INSERT INTO spaces (id, name) VALUES ('space-1', 'Test')"
        )
        conn.execute(
            "INSERT INTO papers (id, space_id, title, parse_status) VALUES ('paper-1', 'space-1', 'Test Paper', 'parsed')"
        )
        conn.execute(
            """INSERT INTO passages (id, paper_id, space_id, section, original_text)
               VALUES ('passage-1', 'paper-1', 'space-1', 'method',
                       'We use a transformer architecture with self-attention.')"""
        )
        conn.commit()

        rebuild_fts_index(database_path=db_path)

        results = search_passages("transformer", "space-1", database_path=db_path)
        assert len(results) >= 1
        assert "transformer" in results[0]["snippet"].lower()

        conn.close()


def test_search_space_isolation() -> None:
    """Test that search is isolated by space."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A'), ('space-2', 'B')")
        conn.execute("INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 'space-1', 'P1', 'parsed'), ('p2', 'space-2', 'P2', 'parsed')")
        conn.execute(
            "INSERT INTO passages (id, paper_id, space_id, section, original_text) VALUES "
            "('pass1', 'p1', 'space-1', 'method', 'transformer model'),"
            "('pass2', 'p2', 'space-2', 'method', 'transformer model')"
        )
        conn.commit()
        rebuild_fts_index(database_path=db_path)

        r1 = search_passages("transformer", "space-1", database_path=db_path)
        r2 = search_passages("transformer", "space-2", database_path=db_path)
        assert len(r1) == 1
        assert len(r2) == 1

        conn.close()


def test_search_treats_fts_syntax_characters_as_plain_text() -> None:
    """User search text containing FTS punctuation should not raise syntax errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A')")
        conn.execute(
            "INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 'space-1', 'P1', 'parsed')"
        )
        conn.execute(
            """INSERT INTO passages (id, paper_id, space_id, section, original_text)
               VALUES ('pass1', 'p1', 'space-1', 'method',
                       'alpha beta C++ query syntax should be treated as text')"""
        )
        conn.commit()
        rebuild_fts_index(database_path=db_path)

        for query in ("alpha-beta", "C++", '"unterminated'):
            results = search_passages(query, "space-1", database_path=db_path)
            assert isinstance(results, list)

        conn.close()


@pytest.mark.asyncio
async def test_search_api_requires_active_space(client: AsyncClient) -> None:
    """Test search returns 400 without active space."""
    resp = await client.get("/api/search", params={"q": "test"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_api_returns_results(client: AsyncClient) -> None:
    """Test search API with actual PDF content."""
    await _setup_space_with_passage(client, "transformer attention mechanism BERT")

    resp = await client.get("/api/search", params={"q": "transformer"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_search_api_no_results(client: AsyncClient) -> None:
    """Test search with no matching results."""
    await _setup_space_with_passage(client, "transformer model")

    resp = await client.get("/api/search", params={"q": "xyzzy_notfound"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_search_api_rejects_deleted_active_space(client: AsyncClient) -> None:
    """A stale deleted active space should not be searchable."""
    space_id = await _setup_space_with_passage(client, "transformer model")
    delete_resp = await client.delete(f"/api/spaces/{space_id}")
    assert delete_resp.status_code == 200

    resp = await client.get("/api/search", params={"q": "transformer"})

    assert resp.status_code == 400
    assert "active space" in resp.json()["detail"].lower()
