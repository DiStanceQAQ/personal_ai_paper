"""Tests for MCP server tools."""

import json
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from paper_engine.storage.database import DATABASE_PATH, init_db, get_connection


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        import paper_engine.storage.database as db_module
        orig = db_module.DATABASE_PATH
        db_module.DATABASE_PATH = Path(db_file)
        yield db_file
        db_module.DATABASE_PATH = orig


def _setup_data(db_path_str: str) -> str:
    """Insert test data and return active space id."""
    conn = get_connection(Path(db_path_str))
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Test')")
    conn.execute("INSERT INTO papers (id, space_id, title, parse_status) VALUES ('paper-1', 'space-1', 'Test Paper', 'parsed')")
    conn.execute(
        """INSERT INTO parse_runs (
               id, paper_id, space_id, backend, extraction_method, status
           ) VALUES (
               'run-1', 'paper-1', 'space-1', 'pymupdf4llm',
               'native_text', 'completed'
           )"""
    )
    conn.execute(
        """INSERT INTO passages (
               id, paper_id, space_id, section, original_text, parse_run_id,
               heading_path_json, parser_backend, quality_flags_json
           ) VALUES (
               'p1', 'paper-1', 'space-1', 'method', 'transformer model',
               'run-1', '["Methods", "Architecture"]', 'pymupdf4llm',
               '["low_confidence"]'
           )"""
    )
    conn.execute(
        """INSERT INTO knowledge_cards (
               id, space_id, paper_id, source_passage_id, card_type, summary,
               evidence_json, quality_flags_json
           ) VALUES (
               'c1', 'space-1', 'paper-1', 'p1', 'Method',
               'Test method card', ?, '["needs_review"]'
           )""",
        (json.dumps({"source_passage_ids": ["p1"]}),),
    )
    conn.execute(
        """INSERT INTO knowledge_card_sources (
               id, card_id, passage_id, paper_id, space_id, evidence_quote
           ) VALUES (
               'source-1', 'c1', 'p1', 'paper-1', 'space-1',
               'transformer model'
           )"""
    )
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_space', 'space-1')")
    conn.execute("INSERT INTO app_state (key, value) VALUES ('agent_access', 'enabled')")
    conn.commit()
    # Insert into FTS
    conn.execute("INSERT INTO passages_fts (passage_id, paper_id, space_id, section, original_text) VALUES ('p1', 'paper-1', 'space-1', 'method', 'transformer model')")
    conn.commit()
    conn.close()
    return 'space-1'


def test_list_spaces(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import list_spaces
    result = list_spaces()
    assert len(result) >= 1
    assert any(s["id"] == "space-1" for s in result)


def test_get_active_space(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import get_active_space
    result = get_active_space()
    assert result["id"] == "space-1"


def test_list_papers(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import list_papers
    result = list_papers()
    assert len(result) >= 1
    assert result[0]["title"] == "Test Paper"


def test_search_literature(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import search_literature
    result = search_literature("transformer")
    assert len(result) >= 1


def test_search_literature_includes_source_metadata(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import search_literature

    result = search_literature("transformer")

    assert result[0]["passage_id"] == "p1"
    assert result[0]["parse_run_id"] == "run-1"
    assert result[0]["heading_path"] == ["Methods", "Architecture"]
    assert result[0]["parser_backend"] == "pymupdf4llm"
    assert result[0]["quality_flags"] == ["low_confidence"]
    assert result[0]["source_passage_ids"] == ["p1"]


def test_get_paper_summary(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import get_paper_summary
    result = get_paper_summary("paper-1")
    assert result["paper"]["title"] == "Test Paper"
    assert result["passage_count"] >= 1
    assert result["card_count"] >= 1


def test_get_citation(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import get_citation
    result = get_citation("paper-1")
    assert "title" in result


def test_get_methods(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import get_methods
    result = get_methods()
    assert len(result) >= 1
    assert result[0]["card_type"] == "Method"


def test_card_tools_include_source_passage_metadata(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import get_methods

    result = get_methods()

    assert result[0]["source_passage_ids"] == ["p1"]
    assert result[0]["quality_flags"] == ["needs_review"]
    assert result[0]["source_passages"] == [
        {
            "passage_id": "p1",
            "parse_run_id": "run-1",
            "heading_path": ["Methods", "Architecture"],
            "parser_backend": "pymupdf4llm",
            "quality_flags": ["low_confidence"],
        }
    ]


def test_get_evidence_for_claim(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import get_evidence_for_claim
    result = get_evidence_for_claim("transformer")
    assert len(result) >= 1


def test_get_limitations(db_path: str) -> None:
    _setup_data(db_path)
    from paper_engine.mcp.server import get_limitations
    result = get_limitations()
    assert isinstance(result, list)
