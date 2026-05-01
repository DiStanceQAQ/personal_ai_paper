"""Tests for full-text search with SQLite FTS5."""

import tempfile
import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.storage.database import DATABASE_PATH, get_connection, init_db
from paper_engine.api.app import app
from paper_engine.retrieval.lexical import (
    FTS_TABLE,
    ensure_fts_index,
    rebuild_fts_index,
    search_passages,
)


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        yield db_file


@pytest.fixture
def client(db_path: str) -> Generator[AsyncClient, None, None]:
    import paper_engine.storage.database as db_module
    import paper_engine.core.config as config_module

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
    """Create a space, upload a PDF, index one passage, return space_id."""
    resp = await client.post("/api/spaces", json={"name": "Search Test"})
    space_id = resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    # Create a custom PDF with the given text
    import pymupdf
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 150), text, fontsize=11)
        doc.save(f.name)
        doc.close()
        pdf_bytes = Path(f.name).read_bytes()
        Path(f.name).unlink()

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    paper_id = resp.json()["id"]

    import paper_engine.storage.database as db_module

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            """
            INSERT INTO passages (
                id, paper_id, space_id, section, original_text,
                parser_backend, extraction_method
            )
            VALUES ('passage-1', ?, ?, 'method', ?, 'test', 'native_text')
            """,
            (paper_id, space_id, text),
        )
        conn.execute(
            f"""
            INSERT INTO {FTS_TABLE} (
                passage_id, paper_id, space_id, section, original_text
            )
            VALUES ('passage-1', ?, ?, 'method', ?)
            """,
            (paper_id, space_id, text),
        )
        conn.execute(
            "UPDATE papers SET parse_status = 'parsed' WHERE id = ?",
            (paper_id,),
        )
        conn.commit()
    finally:
        conn.close()

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


def test_search_falls_back_to_any_term_when_strict_match_is_empty() -> None:
    """Natural-language FTS should not look empty because one term is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A')")
        conn.execute(
            "INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 'space-1', 'P1', 'parsed')"
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, section, original_text)
            VALUES
              ('pass1', 'p1', 'space-1', 'method', 'transformer attention mechanism'),
              ('pass2', 'p1', 'space-1', 'result', 'retrieval augmented grounding')
            """
        )
        conn.commit()
        rebuild_fts_index(database_path=db_path)

        results = search_passages(
            "transformer retrieval",
            "space-1",
            database_path=db_path,
            mode="fts",
        )

        assert {row["passage_id"] for row in results} == {"pass1", "pass2"}
        conn.close()


def test_search_keeps_strict_results_when_all_terms_match() -> None:
    """The any-term fallback should not dilute already precise FTS matches."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A')")
        conn.execute(
            "INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 'space-1', 'P1', 'parsed')"
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, section, original_text)
            VALUES
              ('pass1', 'p1', 'space-1', 'method', 'transformer retrieval model'),
              ('pass2', 'p1', 'space-1', 'result', 'transformer attention mechanism')
            """
        )
        conn.commit()
        rebuild_fts_index(database_path=db_path)

        results = search_passages(
            "transformer retrieval",
            "space-1",
            database_path=db_path,
            mode="fts",
        )

        assert [row["passage_id"] for row in results] == ["pass1"]
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
    assert "snippet" in data[0]
    assert "original_text" not in data[0]


@pytest.mark.asyncio
async def test_search_api_passes_mode_to_search(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search API forwards the optional FTS/hybrid mode parameter."""
    space_resp = await client.post("/api/spaces", json={"name": "Mode Test"})
    space_id = space_resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    captured: dict[str, Any] = {}

    def fake_search_passages(
        query: str,
        space_id: str,
        limit: int = 50,
        database_path: Path | None = None,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        captured.update(
            {
                "query": query,
                "space_id": space_id,
                "limit": limit,
                "database_path": database_path,
                "mode": mode,
            }
        )
        return [{"passage_id": "passage-1"}]

    monkeypatch.setattr(
        "paper_engine.retrieval.service.search_passages",
        fake_search_passages,
    )

    resp = await client.get(
        "/api/search",
        params={"q": "transformer", "mode": "fts", "limit": 7},
    )

    assert resp.status_code == 200
    assert resp.json() == [{"passage_id": "passage-1"}]
    assert captured == {
        "query": "transformer",
        "space_id": space_id,
        "limit": 7,
        "database_path": None,
        "mode": "fts",
    }


@pytest.mark.asyncio
async def test_search_api_forwards_explicit_space_id(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search route preserves explicit space scoping supported by the service."""
    captured: dict[str, Any] = {}

    async def fake_search_literature(
        q: str,
        space_id: str | None,
        limit: int,
        mode: str | None,
    ) -> list[dict[str, Any]]:
        captured.update(
            {
                "q": q,
                "space_id": space_id,
                "limit": limit,
                "mode": mode,
            }
        )
        return [{"passage_id": "passage-1"}]

    monkeypatch.setattr(
        "paper_engine.retrieval.service.search_literature",
        fake_search_literature,
    )

    resp = await client.get(
        "/api/search",
        params={
            "q": "transformer",
            "space_id": "space-explicit",
            "mode": "hybrid",
            "limit": 123,
        },
    )

    assert resp.status_code == 200
    assert resp.json() == [{"passage_id": "passage-1"}]
    assert captured == {
        "q": "transformer",
        "space_id": "space-explicit",
        "limit": 123,
        "mode": "hybrid",
    }


@pytest.mark.asyncio
async def test_search_api_rejects_explicit_non_active_space(
    client: AsyncClient,
) -> None:
    """Search must not allow a caller to hop into an inactive space."""
    active_resp = await client.post("/api/spaces", json={"name": "Active Search"})
    active_space_id = active_resp.json()["id"]
    await client.put(f"/api/spaces/active/{active_space_id}")
    other_resp = await client.post("/api/spaces", json={"name": "Other Search"})
    other_space_id = other_resp.json()["id"]

    resp = await client.get(
        "/api/search",
        params={"q": "transformer", "space_id": other_space_id},
    )

    assert resp.status_code == 403
    assert "active space" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_search_api_rejects_invalid_mode(client: AsyncClient) -> None:
    """Search API accepts only the supported FTS and hybrid modes."""
    space_resp = await client.post("/api/spaces", json={"name": "Mode Test"})
    space_id = space_resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    resp = await client.get(
        "/api/search",
        params={"q": "transformer", "mode": "semantic"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_warmup_skips_without_embeddings(client: AsyncClient) -> None:
    """Warmup should be a cheap no-op before a space has semantic embeddings."""
    space_resp = await client.post("/api/spaces", json={"name": "Warmup Empty"})
    space_id = space_resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    resp = await client.post("/api/search/warmup")

    assert resp.status_code == 200
    data = resp.json()
    assert data["space_id"] == space_id
    assert data["status"] == "skipped"


@pytest.mark.asyncio
async def test_search_warmup_starts_when_embeddings_exist(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warmup endpoint should start and expose semantic warmup state."""
    space_resp = await client.post("/api/spaces", json={"name": "Warmup Ready"})
    space_id = space_resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    import paper_engine.storage.database as db_module
    from paper_engine.retrieval import service

    conn = get_connection(db_module.DATABASE_PATH)
    try:
        conn.execute(
            "INSERT INTO papers (id, space_id, title, parse_status) VALUES ('warm-paper', ?, 'Warm', 'parsed')",
            (space_id,),
        )
        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, section, original_text)
            VALUES ('warm-passage', 'warm-paper', ?, 'method', 'semantic warmup passage')
            """,
            (space_id,),
        )
        conn.execute(
            """
            INSERT INTO passage_embeddings (
                passage_id, provider, model, dimension, embedding_json
            )
            VALUES ('warm-passage', 'openai', 'test-model', 2, '[1.0,0.0]')
            """
        )
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('embedding_provider', 'openai'),
                   ('embedding_model', 'test-model'),
                   ('embedding_api_key', 'test-key')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        conn.commit()
    finally:
        conn.close()

    class FastWarmupProvider:
        provider = "openai"
        model = "test-model"

        def is_configured(self) -> bool:
            return True

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    def run_inline(space_id: str, signature: str) -> None:
        service._set_warmup_state(
            signature,
            service._warmup_state(
                space_id=space_id,
                status="ready",
                message="语义检索已准备好。",
                completed_at=service._utc_now(),
                elapsed_ms=1,
            ),
        )

    monkeypatch.setattr(
        "paper_engine.retrieval.service.get_embedding_provider",
        lambda config: FastWarmupProvider(),
    )
    monkeypatch.setattr(
        "paper_engine.retrieval.service._run_search_warmup",
        run_inline,
    )
    service._SEARCH_WARMUP_STATE.clear()

    resp = await client.post("/api/search/warmup")
    status_data: dict[str, Any] = {}
    for _ in range(20):
        status_resp = await client.get("/api/search/warmup")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        if status_data["status"] == "ready":
            break
        await asyncio.sleep(0.01)

    assert resp.status_code == 200
    assert resp.json()["status"] in {"warming", "ready"}
    assert status_data["status"] == "ready"


@pytest.mark.asyncio
async def test_search_api_no_results(client: AsyncClient) -> None:
    """Test search with no matching results."""
    await _setup_space_with_passage(
        client,
        "transformer model with enough surrounding context for body extraction",
    )

    resp = await client.get("/api/search", params={"q": "xyzzy_notfound"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_search_api_rejects_deleted_active_space(client: AsyncClient) -> None:
    """A stale deleted active space should not be searchable."""
    space_id = await _setup_space_with_passage(
        client,
        "transformer model with enough surrounding context for body extraction",
    )
    delete_resp = await client.delete(f"/api/spaces/{space_id}")
    assert delete_resp.status_code == 200

    resp = await client.get("/api/search", params={"q": "transformer"})

    assert resp.status_code == 400
    assert "active space" in resp.json()["detail"].lower()
