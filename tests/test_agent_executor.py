"""Tests for the built-in LLM agent executor module."""

from __future__ import annotations

import py_compile
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis_models import (
    AnalysisQualityReport,
    CardExtraction,
    MergedAnalysisResult,
    PaperMetadataExtraction,
)


def test_agent_executor_module_compiles() -> None:
    """Agent executor must remain importable for PyInstaller sidecar packaging."""
    py_compile.compile(str(Path("agent_executor.py")), doraise=True)


@pytest.mark.asyncio
async def test_analyze_paper_with_llm_delegates_to_analysis_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent analysis should use the multi-stage pipeline and return route-compatible counts."""
    import agent_executor

    calls: list[tuple[str, str]] = []

    async def fake_run_paper_analysis(paper_id: str, space_id: str) -> SimpleNamespace:
        calls.append((paper_id, space_id))
        return SimpleNamespace(
            analysis_run_id="analysis-run-1",
            result=MergedAnalysisResult(
                paper_id=paper_id,
                space_id=space_id,
                metadata=PaperMetadataExtraction(
                    title="Pipeline Paper",
                    confidence=0.82,
                    source_passage_ids=["passage-1"],
                ),
                cards=[
                    CardExtraction(
                        card_type="Method",
                        summary="The paper introduces a pipeline method.",
                        source_passage_ids=["passage-1"],
                        evidence_quote="pipeline method",
                        confidence=0.91,
                        reasoning_summary="The cited passage states the method.",
                    )
                ],
                quality=AnalysisQualityReport(
                    accepted_card_count=1,
                    rejected_card_count=2,
                ),
                model="gpt-test",
                provider="unit",
                extractor_version="analysis-v2",
            ),
        )

    monkeypatch.setattr(
        agent_executor,
        "run_paper_analysis",
        fake_run_paper_analysis,
        raising=False,
    )

    result = await agent_executor.analyze_paper_with_llm("paper-1", "space-1")

    assert calls == [("paper-1", "space-1")]
    assert result == {
        "status": "success",
        "card_count": 1,
        "analysis_run_id": "analysis-run-1",
        "accepted_card_count": 1,
        "rejected_card_count": 2,
        "metadata_confidence": 0.82,
    }
