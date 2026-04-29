"""Tests for source-grounded AI card batch extraction."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import paper_engine.analysis.pipeline as analysis_pipeline
from paper_engine.storage.database import get_connection, init_db


def _passage(
    passage_id: str,
    text: str,
    *,
    passage_type: str = "method",
    heading_path: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": passage_id,
        "paper_id": "paper-1",
        "space_id": "space-1",
        "section": (heading_path or ["Methods"])[-1],
        "page_number": 3,
        "paragraph_index": 0,
        "original_text": text,
        "passage_type": passage_type,
        "heading_path": heading_path or ["Methods"],
    }


def _selected_batch(
    passage: dict[str, Any],
) -> analysis_pipeline.AnalysisPassageBatch:
    return analysis_pipeline.select_analysis_passage_batches(
        [passage],
        max_batch_tokens=500,
    )[0]


def _card(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "card_type": "Method",
        "summary": "The parser chunks passages by source-aware headings.",
        "source_passage_ids": ["passage-1"],
        "evidence_quote": "source-aware headings",
        "confidence": 0.86,
        "reasoning_summary": "The cited method passage states this directly.",
    }
    payload.update(overrides)
    return payload


def _seed_progress_db(db_path: Path) -> None:
    conn = init_db(database_path=db_path)
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, title, parse_status)
        VALUES ('paper-1', 'space-1', 'Paper', 'parsed')
        """
    )
    conn.execute(
        """
        INSERT INTO analysis_runs (id, paper_id, space_id, status, model, provider)
        VALUES ('analysis-run-1', 'paper-1', 'space-1', 'running', 'model', 'unit')
        """
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_card_batches_stage_accepts_verified_llm_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict card extraction should accept cards verified against batch sources."""
    calls: list[dict[str, Any]] = []
    source_passage = _passage(
        "passage-1",
        "The parser chunks passages by source-aware headings.",
    )

    async def fake_call_llm_schema(
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema_name": schema_name,
                "schema": schema,
            }
        )
        return {
            "paper_id": "paper-1",
            "space_id": "space-1",
            "batch_index": 0,
            "source_passage_ids": ["passage-1"],
            "cards": [_card()],
        }

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_card_batches_stage(
        "paper-1",
        "space-1",
        [_selected_batch(source_passage)],
    )

    assert [card.summary for card in result.accepted_cards] == [
        "The parser chunks passages by source-aware headings.",
    ]
    assert result.rejected_cards == []
    assert [call["schema_name"] for call in calls] == ["card_extraction_batch"]
    assert "passage-1" in calls[0]["user_prompt"]


@pytest.mark.asyncio
async def test_card_batches_stage_records_running_batch_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A running analysis run should expose batch progress before final persistence."""
    db_path = tmp_path / "test.db"
    _seed_progress_db(db_path)
    monkeypatch.setattr(
        analysis_pipeline,
        "get_connection",
        lambda: get_connection(db_path),
    )
    source_passages = [
        _passage(
            "passage-1",
            "The parser chunks passages by source-aware headings.",
            heading_path=["Methods"],
        ),
        _passage(
            "passage-2",
            "The result improves retrieval quality.",
            passage_type="result",
            heading_path=["Results"],
        ),
    ]
    batches = analysis_pipeline.select_analysis_passage_batches(
        source_passages,
        max_batch_tokens=500,
    )
    assert len(batches) == 2

    async def fake_call_llm_schema(
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        batch = batches[len(calls)]
        calls.append(batch.batch_index)
        source_id = batch.source_passage_ids[0]
        if source_id == "passage-2":
            card = _card(
                card_type="Result",
                summary="The result improves retrieval quality.",
                source_passage_ids=[source_id],
                evidence_quote="improves retrieval quality",
                reasoning_summary="The cited result passage states this directly.",
            )
        else:
            card = _card(source_passage_ids=[source_id])
        return {
            "paper_id": "paper-1",
            "space_id": "space-1",
            "batch_index": batch.batch_index,
            "source_passage_ids": list(batch.source_passage_ids),
            "cards": [card],
        }

    calls: list[int] = []
    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_card_batches_stage(
        "paper-1",
        "space-1",
        batches,
        analysis_run_id="analysis-run-1",
    )

    assert len(result.accepted_cards) == 2
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT accepted_card_count, rejected_card_count, diagnostics_json
            FROM analysis_runs
            WHERE id = 'analysis-run-1'
            """
        ).fetchone()
    finally:
        conn.close()
    diagnostics = json.loads(row["diagnostics_json"])
    progress = diagnostics["progress"]
    assert row["accepted_card_count"] == 2
    assert row["rejected_card_count"] == 0
    assert progress["stage"] == "card_extraction"
    assert progress["total_batches"] == 2
    assert progress["completed_batches"] == 2
    assert [item["status"] for item in progress["batches"]] == [
        "completed",
        "completed",
    ]


@pytest.mark.asyncio
async def test_card_batches_stage_runs_batches_with_bounded_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_passages = [
        _passage(
            f"passage-{index}",
            f"The parser chunks passage {index} by source-aware headings.",
            heading_path=[f"Methods {index}"],
        )
        for index in range(4)
    ]
    batches = analysis_pipeline.select_analysis_passage_batches(
        source_passages,
        max_batch_tokens=500,
    )
    assert len(batches) == 4
    monkeypatch.setenv("PAPER_ENGINE_CARD_EXTRACTION_CONCURRENCY", "2")

    active_calls = 0
    max_active_calls = 0

    async def fake_call_llm_schema(
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        source_line = next(
            line
            for line in user_prompt.splitlines()
            if line.startswith('{"source_id":')
        )
        source_id = json.loads(source_line)["source_id"]
        return {
            "paper_id": "paper-1",
            "space_id": "space-1",
            "batch_index": 0,
            "source_passage_ids": [source_id],
            "cards": [
                _card(
                    summary=f"The parser chunks {source_id} by source-aware headings.",
                    source_passage_ids=[source_id],
                    evidence_quote="source-aware headings",
                )
            ],
        }

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_card_batches_stage(
        "paper-1",
        "space-1",
        batches,
    )

    assert len(result.accepted_cards) == 4
    assert result.rejected_cards == []
    assert max_active_calls == 2


@pytest.mark.asyncio
async def test_card_batches_stage_repairs_source_verification_failures_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid schema response with unsupported evidence should get one repair call."""
    calls: list[dict[str, Any]] = []
    source_passage = _passage(
        "passage-1",
        "The parser chunks passages by source-aware headings.",
    )

    async def fake_call_llm_schema(
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema_name": schema_name,
                "schema": schema,
            }
        )
        if len(calls) == 1:
            return {
                "paper_id": "paper-1",
                "space_id": "space-1",
                "batch_index": 0,
                "source_passage_ids": ["passage-1"],
                "cards": [
                    _card(
                        summary="The parser performs perfect OCR.",
                        evidence_quote="perfect OCR",
                    )
                ],
            }
        return {
            "paper_id": "paper-1",
            "space_id": "space-1",
            "batch_index": 0,
            "source_passage_ids": ["passage-1"],
            "cards": [_card()],
        }

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_card_batches_stage(
        "paper-1",
        "space-1",
        [_selected_batch(source_passage)],
    )

    assert [card.summary for card in result.accepted_cards] == [
        "The parser chunks passages by source-aware headings.",
    ]
    assert result.rejected_cards == []
    assert len(calls) == 2
    assert "Repair" in calls[1]["user_prompt"]
    assert "evidence_mismatch" in calls[1]["user_prompt"]


@pytest.mark.asyncio
async def test_card_batches_stage_returns_rejected_diagnostics_after_failed_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the one repair attempt is still ungrounded, diagnostics are returned."""
    source_passage = _passage(
        "passage-1",
        "The limitation is OCR quality on scanned documents.",
        passage_type="limitation",
        heading_path=["Limitations"],
    )

    async def fake_call_llm_schema(
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "paper_id": "paper-1",
            "space_id": "space-1",
            "batch_index": 0,
            "source_passage_ids": ["passage-1"],
            "cards": [
                _card(
                    card_type="Result",
                    summary="The system solves all scanned-paper OCR issues.",
                    evidence_quote="solves all scanned-paper OCR issues",
                )
            ],
        }

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_card_batches_stage(
        "paper-1",
        "space-1",
        [_selected_batch(source_passage)],
    )

    assert result.accepted_cards == []
    assert [diagnostic.reason for diagnostic in result.rejected_cards] == [
        "evidence_mismatch",
    ]
    assert result.rejected_cards[0].batch_index == 0
