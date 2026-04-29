"""Tests for agent access controls and space isolation (US-016)."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.storage.database import DATABASE_PATH, get_connection, init_db
from paper_engine.api.app import app


@pytest.fixture
def client() -> Generator[AsyncClient, None, None]:
    import paper_engine.storage.database as db_module
    import paper_engine.core.config as config_module

    original_db_path = db_module.DATABASE_PATH
    original_spaces_dir = config_module.SPACES_DIR
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test.db"
        init_db(database_path=db_file)
        db_module.DATABASE_PATH = db_file
        config_module.SPACES_DIR = Path(tmpdir) / "spaces"

        transport = ASGITransport(app=app)
        test_client = AsyncClient(transport=transport, base_url="http://test")
        try:
            yield test_client
        finally:
            db_module.DATABASE_PATH = original_db_path
            config_module.SPACES_DIR = original_spaces_dir


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        yield db_file


# ── Agent Access Toggle ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_status_default_disabled(client: AsyncClient) -> None:
    """Agent access should default to disabled."""
    resp = await client.get("/api/agent/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["server_name"] == "paper-knowledge-engine"
    assert data["transport"] == "stdio"


@pytest.mark.asyncio
async def test_enable_agent(client: AsyncClient) -> None:
    """Enable agent access via API."""
    resp = await client.put("/api/agent/enable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "enabled"

    resp = await client.get("/api/agent/status")
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_disable_agent(client: AsyncClient) -> None:
    """Disable agent access via API."""
    await client.put("/api/agent/enable")
    resp = await client.put("/api/agent/disable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"

    resp = await client.get("/api/agent/status")
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_set_agent_status(client: AsyncClient) -> None:
    """Set agent status with boolean body."""
    resp = await client.put("/api/agent/status", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    resp = await client.put("/api/agent/status", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_agent_status_shows_active_space(client: AsyncClient) -> None:
    """Agent status endpoint shows the active space."""
    # Create a space and set active
    resp = await client.post("/api/spaces", json={"name": "Agent Test Space"})
    space_id = resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    resp = await client.get("/api/agent/status")
    data = resp.json()
    assert data["active_space"] is not None
    assert data["active_space"]["name"] == "Agent Test Space"


@pytest.mark.asyncio
async def test_agent_config_redacts_llamaparse_api_key(client: AsyncClient) -> None:
    """Config GET should expose LlamaParse key presence but not the key itself."""
    resp = await client.put(
        "/api/agent/config",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model": "gpt-4o",
            "llm_api_key": "llm-secret",
            "llamaparse_base_url": "https://llamaparse.example/api",
            "llamaparse_api_key": "llama-secret",
        },
    )
    assert resp.status_code == 200

    resp = await client.get("/api/agent/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["llamaparse_base_url"] == "https://llamaparse.example/api"
    assert data["has_llamaparse_api_key"] is True
    assert data["has_api_key"] is True
    assert "llamaparse_api_key" not in data
    assert "llm_api_key" not in data
    assert "llama-secret" not in resp.text
    assert "llm-secret" not in resp.text


@pytest.mark.asyncio
async def test_agent_config_empty_llamaparse_key_does_not_overwrite(
    client: AsyncClient,
) -> None:
    """Empty LlamaParse key updates should preserve an existing configured key."""
    await client.put(
        "/api/agent/config",
        json={
            "llamaparse_base_url": "https://first.example/api",
            "llamaparse_api_key": "keep-me",
        },
    )

    resp = await client.put(
        "/api/agent/config",
        json={
            "llamaparse_base_url": "https://second.example/api",
            "llamaparse_api_key": "",
        },
    )
    assert resp.status_code == 200

    resp = await client.get("/api/agent/config")
    data = resp.json()
    assert data["llamaparse_base_url"] == "https://second.example/api"
    assert data["has_llamaparse_api_key"] is True

    import paper_engine.storage.database as db_module

    conn = db_module.get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            ("llamaparse_api_key",),
        ).fetchone()
    finally:
        conn.close()
    assert row["value"] == "keep-me"


@pytest.mark.asyncio
async def test_agent_config_llamaparse_only_update_preserves_llm_settings(
    client: AsyncClient,
) -> None:
    """Partial LlamaParse config updates must not reset existing LLM settings."""
    resp = await client.put(
        "/api/agent/config",
        json={
            "llm_provider": "anthropic",
            "llm_base_url": "https://llm.example/v1",
            "llm_model": "claude-custom",
        },
    )
    assert resp.status_code == 200

    resp = await client.put(
        "/api/agent/config",
        json={
            "llamaparse_base_url": "https://llamaparse.example",
            "llamaparse_api_key": "llama-key",
        },
    )
    assert resp.status_code == 200

    resp = await client.get("/api/agent/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_provider"] == "anthropic"
    assert data["llm_base_url"] == "https://llm.example/v1"
    assert data["llm_model"] == "claude-custom"
    assert data["llamaparse_base_url"] == "https://llamaparse.example"
    assert data["has_llamaparse_api_key"] is True


@pytest.mark.asyncio
async def test_legacy_analyze_route_is_removed(client: AsyncClient) -> None:
    """AI analysis is now created through paper-scoped analysis run routes."""
    resp = await client.post("/api/agent/analyze/paper-1")

    assert resp.status_code == 404


# ── MCP Tool Access Control ──────────────────────────────────────────


def test_mcp_tool_blocked_when_disabled(db_path: str) -> None:
    """MCP tools return error when agent_access is disabled."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('s1', 'Test')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 's1')")
    # agent_access not inserted → default disabled
    conn.commit()
    conn.close()

    from paper_engine.mcp.server import list_spaces, get_active_space, search_literature, get_paper_summary, get_methods, get_evidence_for_claim

    # All tools should return error
    r1 = list_spaces()
    assert isinstance(r1, list)
    assert len(r1) == 1
    assert "error" in r1[0]

    r2 = get_active_space()
    assert "error" in r2

    r3 = search_literature("test")
    assert len(r3) == 1 and "error" in r3[0]

    r4 = get_paper_summary("paper-1")
    assert "error" in r4

    r5 = get_methods()
    assert len(r5) == 1 and "error" in r5[0]

    r6 = get_evidence_for_claim("test")
    assert len(r6) == 1 and "error" in r6[0]

    db_module.DATABASE_PATH = orig


def test_mcp_tool_works_when_enabled(db_path: str) -> None:
    """MCP tools succeed when agent_access is enabled."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('s1', 'Test')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 's1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    from paper_engine.mcp.server import list_spaces

    result = list_spaces()
    assert len(result) >= 1
    assert any(s["id"] == "s1" for s in result)

    db_module.DATABASE_PATH = orig


# ── Space Isolation ──────────────────────────────────────────────────


def test_mcp_space_isolation_papers(db_path: str) -> None:
    """MCP tools never return papers from other spaces."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A'), ('space-2', 'B')")
    conn.execute("INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 'space-1', 'Paper A', 'parsed'), ('p2', 'space-2', 'Paper B', 'parsed')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 'space-1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    from paper_engine.mcp.server import list_papers

    # Only space-1 papers should be returned
    papers = list_papers(space_id="space-1")
    paper_ids = {p["id"] for p in papers}
    assert "p1" in paper_ids
    assert "p2" not in paper_ids

    # Explicitly querying a non-active space should be refused.
    papers2 = list_papers(space_id="space-2")
    assert len(papers2) == 1
    assert "error" in papers2[0]

    db_module.DATABASE_PATH = orig


def test_mcp_space_isolation_cards(db_path: str) -> None:
    """MCP tools never return cards from other spaces."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A'), ('space-2', 'B')")
    conn.execute("INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 'space-1', 'Paper A', 'parsed'), ('p2', 'space-2', 'Paper B', 'parsed')")
    conn.execute("INSERT INTO knowledge_cards (id, space_id, paper_id, card_type, summary) VALUES ('c1', 'space-1', 'p1', 'Method', 'Method A'), ('c2', 'space-2', 'p2', 'Method', 'Method B')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 'space-1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    from paper_engine.mcp.server import get_methods

    # Only space-1 cards should be returned
    cards = get_methods(space_id="space-1")
    card_ids = {c["id"] for c in cards}
    assert "c1" in card_ids
    assert "c2" not in card_ids

    # Explicitly querying a non-active space should be refused.
    cards2 = get_methods(space_id="space-2")
    assert len(cards2) == 1
    assert "error" in cards2[0]

    db_module.DATABASE_PATH = orig


def test_mcp_space_isolation_search(db_path: str) -> None:
    """MCP search never returns passages from other spaces."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    from paper_engine.retrieval.lexical import rebuild_fts_index

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A'), ('space-2', 'B')")
    conn.execute("INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 'space-1', 'P1', 'parsed'), ('p2', 'space-2', 'P2', 'parsed')")
    conn.execute(
        "INSERT INTO passages (id, paper_id, space_id, section, original_text) VALUES "
        "('pass1', 'p1', 'space-1', 'method', 'transformer model'),"
        "('pass2', 'p2', 'space-2', 'method', 'transformer model')"
    )
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 'space-1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    rebuild_fts_index(database_path=Path(db_path))

    from paper_engine.mcp.server import search_literature

    results = search_literature("transformer", space_id="space-1")
    passage_ids = {r["passage_id"] for r in results}
    assert "pass1" in passage_ids
    assert "pass2" not in passage_ids

    results2 = search_literature("transformer", space_id="space-2")
    assert len(results2) == 1
    assert "error" in results2[0]

    db_module.DATABASE_PATH = orig


def test_mcp_space_isolation_single_paper_tools(db_path: str) -> None:
    """Single-paper MCP tools should reject papers outside the active space."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A'), ('space-2', 'B')")
    conn.execute(
        "INSERT INTO papers (id, space_id, title, parse_status) VALUES "
        "('p1', 'space-1', 'Paper A', 'parsed'),"
        "('p2', 'space-2', 'Paper B', 'parsed')"
    )
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 'space-1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    from paper_engine.mcp.server import get_citation, get_paper_summary

    assert get_paper_summary("p1")["paper"]["id"] == "p1"
    assert "error" in get_paper_summary("p2")
    assert get_citation("p1")["title"] == "Paper A"
    assert "error" in get_citation("p2")

    db_module.DATABASE_PATH = orig


def test_mcp_list_spaces_only_returns_active_space(db_path: str) -> None:
    """Agent access exposes only the active space, not the whole project list."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'A'), ('space-2', 'B')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 'space-1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    from paper_engine.mcp.server import list_spaces

    spaces = list_spaces()
    assert [s["id"] for s in spaces] == ["space-1"]

    db_module.DATABASE_PATH = orig


# ── Source Information ───────────────────────────────────────────────


def test_agent_results_have_source_info(db_path: str) -> None:
    """Every agent-visible result includes source information."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    from paper_engine.retrieval.lexical import rebuild_fts_index

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('s1', 'Test')")
    conn.execute("INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 's1', 'Test Paper', 'parsed')")
    conn.execute("INSERT INTO passages (id, paper_id, space_id, section, original_text) VALUES ('pass1', 'p1', 's1', 'method', 'transformer attention')")
    conn.execute("INSERT INTO knowledge_cards (id, space_id, paper_id, card_type, summary) VALUES ('c1', 's1', 'p1', 'Method', 'Method X')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 's1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    rebuild_fts_index(database_path=Path(db_path))

    from paper_engine.mcp.server import (
        list_papers, search_literature, get_paper_summary,
        get_citation, get_methods, get_evidence_for_claim,
    )

    # list_papers: should have id, space_id, title
    papers = list_papers()
    assert len(papers) >= 1
    assert "id" in papers[0]
    assert "space_id" in papers[0]
    assert "title" in papers[0]

    # search_literature: should have passage_id, paper_id, paper_title, snippet
    results = search_literature("transformer")
    assert len(results) >= 1
    assert "passage_id" in results[0]
    assert "paper_id" in results[0]
    assert "paper_title" in results[0]
    assert "snippet" in results[0] or "original_text" in results[0]

    # get_paper_summary: should have paper, passages, cards
    summary = get_paper_summary("p1")
    assert "paper" in summary
    assert "passage_count" in summary
    assert "card_count" in summary
    assert "cards" in summary

    # get_citation: should have title, authors, doi
    citation = get_citation("p1")
    assert "title" in citation

    # get_methods: should have card info with paper_title
    methods = get_methods()
    assert len(methods) >= 1
    assert "id" in methods[0]
    assert "card_type" in methods[0]
    assert "paper_title" in methods[0]

    # get_evidence_for_claim: should have passages and evidence_cards
    evidence = get_evidence_for_claim("transformer")
    assert len(evidence) >= 1
    assert "passages" in evidence[0]
    assert "evidence_cards" in evidence[0]

    db_module.DATABASE_PATH = orig


def test_no_source_less_results(db_path: str) -> None:
    """Verify that results without paper_id or passage_id are not present."""
    import paper_engine.storage.database as db_module
    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    from paper_engine.retrieval.lexical import rebuild_fts_index

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('s1', 'Test')")
    conn.execute("INSERT INTO papers (id, space_id, title, parse_status) VALUES ('p1', 's1', 'Paper', 'parsed')")
    conn.execute("INSERT INTO passages (id, paper_id, space_id, section, original_text) VALUES ('pass1', 'p1', 's1', 'method', 'transformer')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 's1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    rebuild_fts_index(database_path=Path(db_path))

    from paper_engine.mcp.server import search_literature

    results = search_literature("transformer")
    # Every result must have paper_id and passage_id
    for r in results:
        assert r.get("paper_id"), f"Result missing paper_id: {r}"
        assert r.get("passage_id"), f"Result missing passage_id: {r}"

    db_module.DATABASE_PATH = orig


def test_mcp_add_knowledge_card_creates_card_in_active_space(db_path: str) -> None:
    """MCP card creation should create a card bound to the active space."""
    import paper_engine.storage.database as db_module

    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('s1', 'Test')")
    conn.execute("INSERT INTO papers (id, space_id, title) VALUES ('p1', 's1', 'Paper')")
    conn.execute(
        """INSERT INTO passages (id, paper_id, space_id, original_text)
           VALUES ('pass1', 'p1', 's1', 'source text')"""
    )
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 's1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    try:
        from paper_engine.mcp.server import add_knowledge_card

        result = add_knowledge_card("p1", "Method", "MCP-created method", "pass1")

        assert result["status"] == "success"
        card_id = result["card_id"]

        conn = get_connection()
        card = conn.execute(
            "SELECT * FROM knowledge_cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        conn.close()
        assert card is not None
        assert card["space_id"] == "s1"
        assert card["paper_id"] == "p1"
        assert card["source_passage_id"] == "pass1"
    finally:
        db_module.DATABASE_PATH = orig


def test_mcp_add_knowledge_card_rejects_cross_space_paper(db_path: str) -> None:
    """MCP card creation must not write to a paper outside the active space."""
    import paper_engine.storage.database as db_module

    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('s1', 'A'), ('s2', 'B')")
    conn.execute("INSERT INTO papers (id, space_id, title) VALUES ('p2', 's2', 'Other')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 's1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    try:
        from paper_engine.mcp.server import add_knowledge_card

        result = add_knowledge_card("p2", "Method", "Should not be written")

        assert "error" in result
        assert "active space" in result["error"]

        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) FROM knowledge_cards").fetchone()[0]
        conn.close()
        assert count == 0
    finally:
        db_module.DATABASE_PATH = orig


def test_mcp_add_knowledge_card_rejects_foreign_source_passage(db_path: str) -> None:
    """MCP card source passages must belong to the target paper and active space."""
    import paper_engine.storage.database as db_module

    orig = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    conn = get_connection()
    conn.execute("INSERT INTO spaces (id, name) VALUES ('s1', 'A'), ('s2', 'B')")
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES ('p1', 's1', 'Paper'), ('p2', 's2', 'Other')"
    )
    conn.execute(
        """INSERT INTO passages (id, paper_id, space_id, original_text)
           VALUES ('foreign-pass', 'p2', 's2', 'foreign text')"""
    )
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 's1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    conn.close()

    try:
        from paper_engine.mcp.server import add_knowledge_card

        result = add_knowledge_card("p1", "Method", "Should not be written", "foreign-pass")

        assert "error" in result
        assert "source_passage_id" in result["error"]

        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) FROM knowledge_cards").fetchone()[0]
        conn.close()
        assert count == 0
    finally:
        db_module.DATABASE_PATH = orig
