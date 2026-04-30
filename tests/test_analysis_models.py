"""Tests for strict AI analysis data contract models."""

import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from paper_engine.api.routes.cards import CARD_TYPES as ROUTE_CARD_TYPES

from paper_engine.analysis.models import (
    CARD_TYPES,
    AnalysisQualityReport,
    CardExtraction,
    CardExtractionBatch,
    MergedAnalysisResult,
    PaperMetadataExtraction,
    PaperUnderstandingExtraction,
)


def _card_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "card_type": "Method",
        "summary": "The paper introduces a retrieval-augmented parser.",
        "source_passage_ids": ["passage-1", "passage-2"],
        "evidence_quote": "We introduce a retrieval-augmented parser.",
        "confidence": 0.82,
        "reasoning_summary": "The source passage explicitly describes the proposed method.",
    }
    payload.update(overrides)
    return payload


def test_analysis_models_is_in_packaged_runtime_modules() -> None:
    """The analysis schema module should be included in packaged builds."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "paper_engine*"
    ]


def test_card_type_vocabulary_matches_card_api_vocabulary() -> None:
    """AI cards should use the same vocabulary accepted by knowledge_cards."""
    assert CARD_TYPES == tuple(ROUTE_CARD_TYPES)


def test_card_extraction_requires_source_grounding_fields() -> None:
    """Every AI card includes sources, evidence, confidence, and reasoning."""
    card = CardExtraction(**_card_payload())

    assert card.card_type == "Method"
    assert card.source_passage_ids == ["passage-1", "passage-2"]
    assert card.evidence_quote == "We introduce a retrieval-augmented parser."
    assert card.confidence == pytest.approx(0.82)
    assert card.reasoning_summary.startswith("The source passage")

    schema = CardExtraction.model_json_schema()
    required = set(schema["required"])
    assert {
        "source_passage_ids",
        "evidence_quote",
        "confidence",
        "reasoning_summary",
    }.issubset(required)


@pytest.mark.parametrize(
    "card_type",
    [
        "Background",
        "Methodology",
        "Finding",
        "practical tip",
    ],
)
def test_card_extraction_rejects_values_outside_db_card_type_vocabulary(
    card_type: str,
) -> None:
    """CardExtraction should reject card types not accepted by the DB schema."""
    with pytest.raises(ValidationError):
        CardExtraction(**_card_payload(card_type=card_type))


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_passage_ids": []},
        {"source_passage_ids": ["passage-1", "passage-1"]},
        {"source_passage_ids": [""]},
        {"evidence_quote": "   "},
        {"reasoning_summary": ""},
        {"confidence": -0.01},
        {"confidence": 1.01},
    ],
)
def test_card_extraction_rejects_invalid_grounding_fields(
    overrides: dict[str, Any],
) -> None:
    """Source grounding fields should be non-empty and confidence bounded."""
    with pytest.raises(ValidationError):
        CardExtraction(**_card_payload(**overrides))


def test_card_extraction_rejects_unknown_extra_fields() -> None:
    """LLM outputs should not silently accept undocumented card fields."""
    with pytest.raises(ValidationError):
        CardExtraction(**_card_payload(unsupported="ignored?"))


def test_card_extraction_batch_keeps_batch_sources_and_cards() -> None:
    """A batch groups source passages and strict AI cards."""
    batch = CardExtractionBatch(
        paper_id="paper-1",
        space_id="space-1",
        batch_index=2,
        source_passage_ids=["passage-1", "passage-2"],
        cards=[CardExtraction(**_card_payload())],
    )

    assert batch.paper_id == "paper-1"
    assert batch.space_id == "space-1"
    assert batch.batch_index == 2
    assert batch.cards[0].source_passage_ids == ["passage-1", "passage-2"]


def test_card_extraction_batch_rejects_cards_outside_batch_sources() -> None:
    """Cards in a batch must cite passages available to that batch."""
    with pytest.raises(ValidationError):
        CardExtractionBatch(
            paper_id="paper-1",
            space_id="space-1",
            batch_index=0,
            source_passage_ids=["passage-1"],
            cards=[
                CardExtraction(
                    **_card_payload(source_passage_ids=["passage-2"]),
                ),
            ],
        )


def test_metadata_extraction_and_merged_result_contract() -> None:
    """Merged results combine metadata, cards, and quality diagnostics."""
    metadata = PaperMetadataExtraction(
        title="Grounded PDF Analysis",
        authors=["Ada Lovelace", "Grace Hopper"],
        year=2026,
        venue="Journal of Local AI",
        doi="10.1234/example",
        source_passage_ids=["passage-1"],
    )
    quality = AnalysisQualityReport(
        accepted_card_count=1,
        rejected_card_count=0,
        source_coverage=0.75,
        warnings=["low evidence overlap on one candidate"],
    )
    result = MergedAnalysisResult(
        paper_id="paper-1",
        space_id="space-1",
        metadata=metadata,
        understanding=PaperUnderstandingExtraction(
            one_sentence="这篇论文提出了一个有来源约束的 PDF 分析流程。",
            problem="本地论文解析缺少可追溯的结构化理解。",
            method="论文使用来源切片和结构化模型抽取知识。",
            results="系统生成了可验证的知识卡片。",
            conclusion="来源约束可以提升论文知识库的可信度。",
            source_passage_ids=["passage-1"],
            confidence=0.8,
        ),
        cards=[CardExtraction(**_card_payload())],
        quality=quality,
    )

    assert result.metadata.title == "Grounded PDF Analysis"
    assert result.understanding is not None
    assert result.understanding.one_sentence.startswith("这篇论文")
    assert result.quality.accepted_card_count == 1
    assert result.cards[0].card_type == "Method"


def test_paper_understanding_extraction_requires_grounded_chinese_summary() -> None:
    """Whole-paper understanding requires core Chinese reader-facing fields."""
    understanding = PaperUnderstandingExtraction(
        one_sentence="这篇论文研究本地论文解析如何生成可追溯知识。",
        problem="现有流程难以把 PDF 内容转成可信知识卡片。",
        method="论文使用来源切片、结构化输出和证据校验。",
        results="系统能够生成带来源的论文级知识卡片。",
        conclusion="证据约束有助于减少 AI 摘要幻觉。",
        limitations="测试集中扫描件仍可能受 OCR 质量影响。",
        reusable_insights=["先建立全局理解，再生成局部卡片。"],
        source_passage_ids=["passage-1"],
        confidence=0.82,
    )

    assert understanding.method.startswith("论文使用")
    assert understanding.reusable_insights == ["先建立全局理解，再生成局部卡片。"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_passage_ids": []},
        {"source_passage_ids": ["passage-1", "passage-1"]},
        {"confidence": 1.01},
        {"problem": ""},
    ],
)
def test_paper_understanding_extraction_rejects_invalid_values(
    kwargs: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {
        "one_sentence": "这篇论文研究本地论文解析。",
        "problem": "问题是论文理解不够结构化。",
        "method": "方法是结构化抽取。",
        "results": "结果是生成知识卡片。",
        "conclusion": "结论是流程可用。",
        "source_passage_ids": ["passage-1"],
        "confidence": 0.8,
    }
    payload.update(kwargs)

    with pytest.raises(ValidationError):
        PaperUnderstandingExtraction(**payload)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"year": "2026"},
        {"authors": ["Ada", ""]},
        {"source_passage_ids": ["passage-1", "passage-1"]},
    ],
)
def test_metadata_extraction_rejects_invalid_values(kwargs: dict[str, Any]) -> None:
    """Metadata should keep strict typing and valid source IDs."""
    with pytest.raises(ValidationError):
        PaperMetadataExtraction(**kwargs)
