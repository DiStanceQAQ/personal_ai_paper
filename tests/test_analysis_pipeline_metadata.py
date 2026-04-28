"""Tests for source-prioritized metadata extraction."""

from typing import Any
import tomllib
from pathlib import Path

import pytest

from paper_engine.analysis.models import PaperMetadataExtraction
from paper_engine.pdf.models import ParseElement, PassageRecord


def _passage(
    passage_id: str,
    text: str,
    *,
    page_number: int = 1,
    section: str = "",
    heading_path: list[str] | None = None,
) -> PassageRecord:
    return PassageRecord(
        id=passage_id,
        paper_id="paper-1",
        space_id="space-1",
        section=section,
        page_number=page_number,
        paragraph_index=0,
        original_text=text,
        passage_type="abstract" if section.lower() == "abstract" else "body",
        heading_path=heading_path or [],
    )


def _element(
    element_id: str,
    text: str,
    *,
    element_type: str = "title",
    page_number: int = 1,
    element_index: int = 0,
) -> ParseElement:
    return ParseElement(
        id=element_id,
        element_index=element_index,
        element_type=element_type,
        text=text,
        page_number=page_number,
        extraction_method="native_text",
    )


@pytest.mark.asyncio
async def test_metadata_stage_prefers_grobid_fields_and_first_page_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GROBID scholarly fields should beat LLM guesses, title element beats LLM."""
    import paper_engine.analysis.pipeline as analysis_pipeline

    async def fake_call_llm_schema(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "title": "LLM Guessed Title",
            "authors": ["Wrong Author"],
            "year": 1999,
            "venue": "LLM Venue",
            "doi": "10.9999/wrong",
            "arxiv_id": "",
            "abstract": "LLM abstract.",
            "source_passage_ids": ["passage-abstract"],
            "confidence": 0.4,
            "metadata": {"source": "llm"},
        }

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_metadata_stage(
        "paper-1",
        passages=[
            _passage(
                "passage-abstract",
                "Abstract: This paper studies local paper knowledge engines.",
                section="Abstract",
            ),
        ],
        elements=[_element("title-1", "Source-Prioritized Paper Metadata")],
        grobid_metadata={
            "authors": ["Ada Lovelace", "Grace Hopper"],
            "year": "2026",
            "doi": "https://doi.org/10.1234/grobid.2026",
        },
    )

    assert isinstance(result, PaperMetadataExtraction)
    assert result.title == "Source-Prioritized Paper Metadata"
    assert result.authors == ["Ada Lovelace", "Grace Hopper"]
    assert result.year == 2026
    assert result.doi == "10.1234/grobid.2026"
    assert result.venue == "LLM Venue"


@pytest.mark.asyncio
async def test_metadata_stage_uses_llm_fallback_when_grobid_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing GROBID metadata should be filled by the strict LLM fallback."""
    import paper_engine.analysis.pipeline as analysis_pipeline

    calls: list[dict[str, Any]] = []

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
            "title": "LLM Guessed Title",
            "authors": ["Katherine Johnson"],
            "year": 2025,
            "venue": "Journal of Local AI",
            "doi": "",
            "arxiv_id": "",
            "abstract": "The paper describes a local-first analysis pipeline.",
            "source_passage_ids": ["passage-abstract"],
            "confidence": 0.71,
            "metadata": {},
        }

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_metadata_stage(
        "paper-1",
        passages=[
            _passage(
                "passage-abstract",
                "The paper describes a local-first analysis pipeline.",
                section="Abstract",
                heading_path=["Abstract"],
            ),
        ],
        elements=[_element("title-1", "A Local-First Analysis Pipeline")],
        grobid_metadata=None,
    )

    assert result.title == "A Local-First Analysis Pipeline"
    assert result.authors == ["Katherine Johnson"]
    assert result.year == 2025
    assert result.venue == "Journal of Local AI"
    assert result.abstract == "The paper describes a local-first analysis pipeline."
    assert calls[0]["schema_name"] == "paper_metadata_extraction"
    assert "passage-abstract" in calls[0]["user_prompt"]


@pytest.mark.asyncio
async def test_metadata_stage_extracts_doi_and_arxiv_from_source_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DOI and arXiv regex hits should beat conflicting LLM fallback values."""
    import paper_engine.analysis.pipeline as analysis_pipeline

    async def fake_call_llm_schema(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "title": "",
            "authors": [],
            "year": None,
            "venue": "",
            "doi": "10.9999/llm-wrong",
            "arxiv_id": "9999.99999",
            "abstract": "",
            "source_passage_ids": [],
            "confidence": 0.5,
            "metadata": {},
        }

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", fake_call_llm_schema)

    result = await analysis_pipeline.extract_metadata_stage(
        "paper-1",
        passages=[
            _passage(
                "passage-frontmatter",
                (
                    "Preprint. DOI: https://doi.org/10.5555/example.2026. "
                    "Also available as arXiv:2401.01234v2."
                ),
            )
        ],
        elements=[],
        grobid_metadata={},
    )

    assert result.doi == "10.5555/example.2026"
    assert result.arxiv_id == "2401.01234v2"


@pytest.mark.asyncio
async def test_metadata_stage_returns_rule_based_values_when_llm_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured-source metadata should not fail just because LLM is unavailable."""
    import paper_engine.analysis.pipeline as analysis_pipeline

    async def unavailable_llm(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("LLM API Key is missing")

    monkeypatch.setattr(analysis_pipeline, "call_llm_schema", unavailable_llm)

    result = await analysis_pipeline.extract_metadata_stage(
        "paper-1",
        passages=[
            _passage(
                "passage-frontmatter",
                "Contact: authors@example.org. DOI: 10.7777/no-llm-needed",
            )
        ],
        elements=[_element("title-1", "Metadata Without Network Calls")],
        grobid_metadata=None,
    )

    assert result.title == "Metadata Without Network Calls"
    assert result.doi == "10.7777/no-llm-needed"


def test_analysis_pipeline_is_in_packaged_runtime_modules() -> None:
    """The analysis pipeline should ship with packaged runtime modules."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "analysis_pipeline" in pyproject["tool"]["setuptools"]["py-modules"]
