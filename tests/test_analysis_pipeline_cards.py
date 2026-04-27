"""Tests for source-grounded AI card batch extraction."""

from typing import Any

import pytest

import analysis_pipeline


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
