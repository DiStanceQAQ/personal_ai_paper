"""Tests for durable AI analysis persistence."""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from analysis_models import (
    AnalysisQualityReport,
    CardExtraction,
    MergedAnalysisResult,
    PaperMetadataExtraction,
)
from analysis_pipeline import persist_analysis_result
from db import init_db


def _test_conn() -> sqlite3.Connection:
    db_path = Path(tempfile.mkdtemp()) / "test.db"
    return init_db(database_path=db_path)


def _seed_space_paper_and_passages(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO spaces (id, name, description) VALUES (?, ?, ?)",
        ("space-1", "Space", ""),
    )
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES (?, ?, ?)",
        ("paper-1", "space-1", "Paper"),
    )
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES (?, ?, ?)",
        ("paper-2", "space-1", "Other Paper"),
    )
    for index in range(1, 4):
        conn.execute(
            """
            INSERT INTO passages (
                id, paper_id, space_id, section, page_number,
                paragraph_index, original_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"passage-{index}",
                "paper-1",
                "space-1",
                "Methods",
                index,
                index,
                f"Source text {index}",
            ),
        )
    conn.execute(
        """
        INSERT INTO passages (
            id, paper_id, space_id, section, page_number,
            paragraph_index, original_text
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("paper-2-passage", "paper-2", "space-1", "Methods", 1, 0, "Other"),
    )
    conn.commit()


def _insert_prior_analysis_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        """
        INSERT INTO analysis_runs (id, paper_id, space_id, model, provider)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, "paper-1", "space-1", "old-model", "old-provider"),
    )


def _insert_card(
    conn: sqlite3.Connection,
    card_id: str,
    *,
    paper_id: str = "paper-1",
    created_by: str,
    user_edited: int = 0,
    analysis_run_id: str | None = None,
) -> None:
    source_passage_id = "paper-2-passage" if paper_id == "paper-2" else "passage-1"
    conn.execute(
        """
        INSERT INTO knowledge_cards (
            id, space_id, paper_id, source_passage_id, card_type, summary,
            confidence, user_edited, created_by, analysis_run_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            "space-1",
            paper_id,
            source_passage_id,
            "Method",
            card_id,
            0.5,
            user_edited,
            created_by,
            analysis_run_id,
        ),
    )


def _analysis_result() -> MergedAnalysisResult:
    return MergedAnalysisResult(
        paper_id="paper-1",
        space_id="space-1",
        metadata=PaperMetadataExtraction(
            title="Grounded PDF Analysis",
            source_passage_ids=["passage-1"],
            confidence=0.77,
        ),
        cards=[
            CardExtraction(
                card_type="Method",
                summary="The system persists source-grounded method cards.",
                source_passage_ids=["passage-1", "passage-2"],
                evidence_quote="Source text",
                confidence=0.91,
                reasoning_summary="Both cited passages support the method card.",
                quality_flags=["source_verified"],
                metadata={"batch_index": 0},
            ),
            CardExtraction(
                card_type="Result",
                summary="The persistence layer records every cited source.",
                source_passage_ids=["passage-3"],
                evidence_quote="Source text 3",
                confidence=0.86,
                reasoning_summary="The cited passage supports the result card.",
            ),
        ],
        quality=AnalysisQualityReport(
            accepted_card_count=2,
            rejected_card_count=1,
            warnings=["one rejected card omitted"],
            diagnostics={"rejected_cards": [{"reason": "evidence_mismatch"}]},
        ),
        model="gpt-test",
        provider="unit",
        extractor_version="analysis-v2",
        metadata_extra={"route": "unit"},
    )


def test_persist_analysis_result_replaces_only_unedited_ai_cards() -> None:
    conn = _test_conn()
    _seed_space_paper_and_passages(conn)
    _insert_prior_analysis_run(conn, "old-run")
    _insert_card(conn, "old-ai-card", created_by="ai", analysis_run_id="old-run")
    _insert_card(conn, "edited-ai-card", created_by="ai", user_edited=1)
    _insert_card(conn, "manual-card", created_by="user")
    _insert_card(conn, "heuristic-card", created_by="heuristic")
    _insert_card(conn, "other-paper-ai-card", paper_id="paper-2", created_by="ai")
    conn.execute(
        """
        INSERT INTO knowledge_card_sources (
            id, card_id, passage_id, paper_id, space_id, analysis_run_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("old-source", "old-ai-card", "passage-1", "paper-1", "space-1", "old-run"),
    )
    conn.commit()

    analysis_run_id = persist_analysis_result(conn, _analysis_result())

    assert analysis_run_id.startswith("analysis-run-")
    analysis_run = conn.execute(
        "SELECT * FROM analysis_runs WHERE id = ?",
        (analysis_run_id,),
    ).fetchone()
    assert analysis_run["paper_id"] == "paper-1"
    assert analysis_run["space_id"] == "space-1"
    assert analysis_run["model"] == "gpt-test"
    assert analysis_run["provider"] == "unit"
    assert analysis_run["extractor_version"] == "analysis-v2"
    assert analysis_run["accepted_card_count"] == 2
    assert analysis_run["rejected_card_count"] == 1
    assert json.loads(analysis_run["warnings_json"]) == ["one rejected card omitted"]
    assert json.loads(analysis_run["diagnostics_json"]) == {
        "rejected_cards": [{"reason": "evidence_mismatch"}]
    }

    card_rows = conn.execute(
        """
        SELECT id, paper_id, summary, created_by, user_edited, analysis_run_id,
               source_passage_id, extractor_version, evidence_json,
               quality_flags_json
        FROM knowledge_cards
        ORDER BY summary
        """
    ).fetchall()
    cards = {str(row["summary"]): dict(row) for row in card_rows}

    assert "old-ai-card" not in {row["id"] for row in card_rows}
    assert "old-source" not in {
        row["id"] for row in conn.execute("SELECT id FROM knowledge_card_sources")
    }
    assert {
        "edited-ai-card",
        "manual-card",
        "heuristic-card",
        "other-paper-ai-card",
    }.issubset(cards)

    first_ai_card = cards["The system persists source-grounded method cards."]
    assert first_ai_card["created_by"] == "ai"
    assert first_ai_card["user_edited"] == 0
    assert first_ai_card["analysis_run_id"] == analysis_run_id
    assert first_ai_card["source_passage_id"] == "passage-1"
    assert first_ai_card["extractor_version"] == "analysis-v2"
    assert json.loads(first_ai_card["quality_flags_json"]) == ["source_verified"]
    assert json.loads(first_ai_card["evidence_json"]) == {
        "source_passage_ids": ["passage-1", "passage-2"],
        "evidence_quote": "Source text",
        "reasoning_summary": "Both cited passages support the method card.",
        "metadata": {"batch_index": 0},
    }

    source_rows = conn.execute(
        """
        SELECT kc.summary, kcs.passage_id, kcs.evidence_quote, kcs.confidence,
               kcs.analysis_run_id, kcs.metadata_json
        FROM knowledge_card_sources kcs
        JOIN knowledge_cards kc ON kc.id = kcs.card_id
        WHERE kcs.analysis_run_id = ?
        ORDER BY kc.summary, kcs.passage_id
        """,
        (analysis_run_id,),
    ).fetchall()
    sources_by_summary: dict[str, list[dict[str, Any]]] = {}
    for row in source_rows:
        sources_by_summary.setdefault(str(row["summary"]), []).append(dict(row))

    assert {
        key: [item["passage_id"] for item in value]
        for key, value in sources_by_summary.items()
    } == {
        "The persistence layer records every cited source.": ["passage-3"],
        "The system persists source-grounded method cards.": [
            "passage-1",
            "passage-2",
        ],
    }
    assert all(
        row["analysis_run_id"] == analysis_run_id
        for rows in sources_by_summary.values()
        for row in rows
    )
    assert all(
        row["evidence_quote"].startswith("Source text")
        for rows in sources_by_summary.values()
        for row in rows
    )
