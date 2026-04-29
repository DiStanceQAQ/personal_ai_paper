"""Tests for paper core metadata extraction and promotion."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from paper_engine.papers.metadata import (
    mark_user_edited_metadata_fields,
    promote_core_metadata_from_parse,
)
from paper_engine.storage.database import init_db


def _conn() -> sqlite3.Connection:
    db_path = Path(tempfile.mkdtemp()) / "test.db"
    conn = init_db(database_path=db_path)
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (
            id, space_id, title, file_path,
            metadata_sources_json, metadata_confidence_json
        )
        VALUES (
            'paper-1',
            'space-1',
            'filename fallback',
            '/tmp/filename-fallback.pdf',
            '{"title":"filename.fallback"}',
            '{"title":0.1}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO parse_runs (
            id, paper_id, space_id, backend, status, metadata_json
        )
        VALUES ('parse-run-1', 'paper-1', 'space-1', 'docling', 'running', ?)
        """,
        (json.dumps({"parser": "docling.DocumentConverter"}),),
    )
    conn.execute(
        """
        INSERT INTO document_elements (
            id, parse_run_id, paper_id, space_id, element_index,
            element_type, text, page_number
        )
        VALUES (
            'element-title-1', 'parse-run-1', 'paper-1', 'space-1',
            0, 'title', 'Document Title', 1
        )
        """
    )
    conn.execute(
        """
        INSERT INTO passages (
            id, paper_id, space_id, parse_run_id, section, passage_type,
            page_number, paragraph_index, original_text
        )
        VALUES (
            'passage-1',
            'paper-1',
            'space-1',
            'parse-run-1',
            'Abstract',
            'abstract',
            1,
            0,
            'Abstract: Document abstract. DOI: https://doi.org/10.2222/regex. arXiv:2401.01234v2'
        )
        """
    )
    conn.commit()
    return conn


def test_parse_metadata_promotes_core_fields_without_ai() -> None:
    conn = _conn()

    promote_core_metadata_from_parse(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id="parse-run-1",
    )

    row = conn.execute(
        """
        SELECT title, authors, year, venue, doi, arxiv_id, abstract,
               metadata_status, metadata_sources_json
        FROM papers
        WHERE id = 'paper-1'
        """
    ).fetchone()
    assert row["title"] == "Document Title"
    assert row["authors"] == ""
    assert row["year"] is None
    assert row["venue"] == ""
    assert row["doi"] == "10.2222/regex"
    assert row["arxiv_id"] == "2401.01234v2"
    assert row["abstract"] == "Document abstract. DOI: https://doi.org/10.2222/regex. arXiv:2401.01234v2"
    assert row["metadata_status"] == "extracted"
    sources = json.loads(row["metadata_sources_json"])
    assert sources["title"] == "document.title"
    assert sources["doi"] == "regex.doi"


def test_user_edited_metadata_is_not_overwritten_by_parse_promotion() -> None:
    conn = _conn()
    conn.execute(
        "UPDATE papers SET title = 'Manual Title' WHERE id = 'paper-1' AND space_id = 'space-1'"
    )
    mark_user_edited_metadata_fields(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        fields=["title"],
    )
    conn.commit()

    promote_core_metadata_from_parse(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id="parse-run-1",
    )

    row = conn.execute(
        """
        SELECT title, doi, metadata_status, user_edited_fields_json
        FROM papers
        WHERE id = 'paper-1'
        """
    ).fetchone()
    assert row["title"] == "Manual Title"
    assert row["doi"] == "10.2222/regex"
    assert row["metadata_status"] == "user_edited"
    assert json.loads(row["user_edited_fields_json"]) == ["title"]
