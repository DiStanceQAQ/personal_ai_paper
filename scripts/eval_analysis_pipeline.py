"""Evaluate AI analysis quality against deterministic model outputs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import analysis_pipeline
from analysis_models import (
    AnalysisQualityReport,
    CardExtraction,
    CardExtractionBatch,
    CardType,
    MergedAnalysisResult,
    PaperMetadataExtraction,
)
from paper_engine.storage.database import init_db

EXPECTED_METRIC_KEYS = {
    "schema_validity_rate",
    "source_grounding_rate",
    "duplicate_card_rate",
    "user_card_preservation_rate",
    "late_section_coverage",
}
PASSING_THRESHOLDS = {
    "schema_validity_rate": 1.0,
    "source_grounding_rate": 1.0,
    "duplicate_card_rate": 0.05,
    "user_card_preservation_rate": 1.0,
    "late_section_coverage": 1.0,
}
_PAPER_ID = "eval-paper"
_SPACE_ID = "eval-space"
_REQUIRED_LATE_SOURCE_IDS = ("late-result", "late-limitation")


@dataclass(frozen=True)
class _StructuredResponseRecord:
    schema_name: str
    response: dict[str, Any]
    valid: bool
    error: str = ""


def run_evaluation(
    work_dir: Path | str | None = None,
    *,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run deterministic AI analysis checks and optionally write JSON metrics."""
    if work_dir is None:
        with TemporaryDirectory(prefix="ai-analysis-eval-") as temp_dir:
            report = _run_evaluation_in_dir(Path(temp_dir))
    else:
        report = _run_evaluation_in_dir(Path(work_dir))

    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return report


def _run_evaluation_in_dir(work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    passages = _synthetic_full_paper_passages()
    batches = analysis_pipeline.select_analysis_passage_batches(
        passages,
        max_batch_tokens=320,
    )
    schema_source_checks = asyncio.run(_evaluate_schema_and_source_grounding(batches))
    deduplication_checks = _evaluate_deduplication(batches)
    persistence_checks = _evaluate_user_card_preservation(
        work_dir / "analysis-eval.db",
        passages,
        schema_source_checks["accepted_cards"],
    )
    coverage_checks = _evaluate_late_section_coverage(
        batches,
        schema_source_checks["accepted_cards"],
    )

    checks = {
        "schema": schema_source_checks["schema"],
        "source_verification": schema_source_checks["source_verification"],
        "deduplication": deduplication_checks,
        "persistence": persistence_checks,
        "coverage": coverage_checks,
    }
    metrics = _summarize_metrics(checks)
    return {
        "config": {
            "paper_id": _PAPER_ID,
            "space_id": _SPACE_ID,
            "passage_count": len(passages),
            "batch_count": len(batches),
        },
        "metrics": metrics,
        "thresholds": PASSING_THRESHOLDS,
        "threshold_failures": _threshold_failures(metrics),
        "checks": checks,
    }


async def _evaluate_schema_and_source_grounding(
    batches: Sequence[analysis_pipeline.AnalysisPassageBatch],
) -> dict[str, Any]:
    records: list[_StructuredResponseRecord] = []

    async def fake_call_llm_schema(
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        del system_prompt, schema
        response = _deterministic_response_for_prompt(schema_name, user_prompt)
        records.append(_validate_structured_response(schema_name, response))
        return response

    with patch.object(analysis_pipeline, "call_llm_schema", fake_call_llm_schema):
        verification = await analysis_pipeline.extract_card_batches_stage(
            _PAPER_ID,
            _SPACE_ID,
            batches,
        )

    response_count = len(records)
    valid_response_count = sum(1 for record in records if record.valid)
    accepted_card_count = len(verification.accepted_cards)
    rejected_card_count = len(verification.rejected_cards)

    return {
        "accepted_cards": verification.accepted_cards,
        "schema": {
            "response_count": response_count,
            "valid_response_count": valid_response_count,
            "invalid_response_count": response_count - valid_response_count,
            "schema_names": sorted({record.schema_name for record in records}),
            "errors": [record.error for record in records if record.error],
            "schema_validity_rate": _ratio(valid_response_count, response_count),
        },
        "source_verification": {
            "accepted_card_count": accepted_card_count,
            "rejected_card_count": rejected_card_count,
            "rejected_reasons": [
                diagnostic.reason for diagnostic in verification.rejected_cards
            ],
            "source_grounding_rate": _ratio(
                accepted_card_count,
                accepted_card_count + rejected_card_count,
            ),
        },
    }


def _deterministic_response_for_prompt(
    schema_name: str,
    user_prompt: str,
) -> dict[str, Any]:
    if schema_name != "card_extraction_batch":
        raise ValueError(f"unsupported deterministic schema {schema_name}")

    context = _context_from_prompt(user_prompt)
    source_passages = _source_passages_from_prompt(user_prompt)
    if not source_passages:
        raise ValueError("card prompt did not include source passages")

    first_source = source_passages[0]
    source_id = str(first_source["source_id"])
    evidence_quote = _evidence_quote_for_source(first_source)
    card_type = _card_type_for_source(first_source)
    return {
        "paper_id": str(context["paper_id"]),
        "space_id": str(context["space_id"]),
        "batch_index": int(context["batch_index"]),
        "source_passage_ids": [str(source_id) for source_id in context["source_passage_ids"]],
        "cards": [
            {
                "card_type": card_type,
                "summary": _summary_for_source(first_source),
                "source_passage_ids": [source_id],
                "evidence_quote": evidence_quote,
                "confidence": 0.91,
                "reasoning_summary": (
                    "The deterministic evaluator copies evidence from the cited "
                    "source passage."
                ),
                "quality_flags": ["deterministic_eval"],
                "metadata": {
                    "batch_index": int(context["batch_index"]),
                    "source_section": str(first_source.get("section", "")),
                },
            }
        ],
        "warnings": [],
        "metadata": {"evaluator": "deterministic"},
    }


def _validate_structured_response(
    schema_name: str,
    response: dict[str, Any],
) -> _StructuredResponseRecord:
    try:
        if schema_name == "card_extraction_batch":
            CardExtractionBatch.model_validate(response)
        else:
            raise ValueError(f"unsupported schema {schema_name}")
    except Exception as exc:
        return _StructuredResponseRecord(
            schema_name=schema_name,
            response=response,
            valid=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return _StructuredResponseRecord(
        schema_name=schema_name,
        response=response,
        valid=True,
    )


def _evaluate_deduplication(
    batches: Sequence[analysis_pipeline.AnalysisPassageBatch],
) -> dict[str, Any]:
    cards = [
        _card(
            "The analysis pipeline keeps source-aware method chunks for extraction.",
            ["method-1"],
            card_type="Method",
            confidence=0.72,
        ),
        _card(
            "Analysis pipeline keeps source aware method chunks for extraction.",
            ["method-1", "method-2"],
            card_type="Method",
            confidence=0.9,
        ),
        _card(
            "The late results report a 37 percent source grounding gain.",
            ["late-result"],
            card_type="Result",
            confidence=0.86,
        ),
        _card(
            "The late limitations require OCR review for scanned papers.",
            ["late-limitation"],
            card_type="Limitation",
            confidence=0.84,
        ),
    ]
    result = analysis_pipeline.deduplicate_and_rank_cards_stage(
        cards,
        batches=batches,
    )
    surviving_duplicate_pairs = _surviving_duplicate_pair_count(result.cards)
    return {
        "input_card_count": len(cards),
        "final_card_count": len(result.cards),
        "pipeline_duplicate_card_count": int(
            result.diagnostics["duplicate_card_count"]
        ),
        "surviving_duplicate_pairs": surviving_duplicate_pairs,
        "duplicate_card_rate": _duplicate_pair_rate(
            surviving_duplicate_pairs,
            len(result.cards),
        ),
        "final_summaries": [card.summary for card in result.cards],
    }


def _evaluate_user_card_preservation(
    database_path: Path,
    passages: Sequence[Mapping[str, Any]],
    accepted_cards: Sequence[CardExtraction],
) -> dict[str, Any]:
    conn = init_db(database_path=database_path)
    try:
        _seed_analysis_database(conn, passages)
        cards_for_persistence = list(accepted_cards[:3]) or [
            _card(
                "The deterministic fallback card has source-grounded evidence.",
                ["method-1"],
            )
        ]
        result = MergedAnalysisResult(
            paper_id=_PAPER_ID,
            space_id=_SPACE_ID,
            metadata=PaperMetadataExtraction(
                title="Deterministic Analysis Evaluation",
                source_passage_ids=["abstract-1"],
                confidence=0.9,
            ),
            cards=cards_for_persistence,
            quality=AnalysisQualityReport(
                accepted_card_count=len(cards_for_persistence),
                rejected_card_count=0,
                source_coverage=1.0,
                diagnostics={"evaluator": "deterministic"},
            ),
            model="deterministic-eval",
            provider="mock",
            extractor_version="analysis-v2",
        )
        analysis_run_id = analysis_pipeline.persist_analysis_result(conn, result)
        conn.commit()

        rows = conn.execute(
            """
            SELECT id, created_by, user_edited, analysis_run_id
            FROM knowledge_cards
            WHERE paper_id = ? AND space_id = ?
            ORDER BY id
            """,
            (_PAPER_ID, _SPACE_ID),
        ).fetchall()
    finally:
        conn.close()

    card_ids = [str(row["id"]) for row in rows]
    expected_preserved = ["edited-ai-card", "manual-card"]
    preserved_card_ids = [card_id for card_id in expected_preserved if card_id in card_ids]
    return {
        "analysis_run_id": analysis_run_id,
        "expected_preserved_card_ids": expected_preserved,
        "preserved_card_ids": preserved_card_ids,
        "removed_unedited_ai_card": "old-ai-card" not in card_ids,
        "new_ai_card_count": sum(
            1 for row in rows if row["analysis_run_id"] == analysis_run_id
        ),
        "user_card_preservation_rate": _ratio(
            len(preserved_card_ids),
            len(expected_preserved),
        ),
    }


def _seed_analysis_database(
    conn: sqlite3.Connection,
    passages: Sequence[Mapping[str, Any]],
) -> None:
    conn.execute(
        "INSERT INTO spaces (id, name, description) VALUES (?, ?, ?)",
        (_SPACE_ID, "Evaluation Space", ""),
    )
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES (?, ?, ?)",
        (_PAPER_ID, _SPACE_ID, "Evaluation Paper"),
    )
    for passage in passages:
        conn.execute(
            """
            INSERT INTO passages (
                id, paper_id, space_id, section, page_number,
                paragraph_index, original_text, passage_type, heading_path_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                passage["id"],
                passage["paper_id"],
                passage["space_id"],
                passage["section"],
                passage["page_number"],
                passage["paragraph_index"],
                passage["original_text"],
                passage["passage_type"],
                json.dumps(passage["heading_path"]),
            ),
        )
    conn.execute(
        """
        INSERT INTO analysis_runs (id, paper_id, space_id, model, provider)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("old-analysis-run", _PAPER_ID, _SPACE_ID, "old-model", "mock"),
    )
    _insert_existing_card(
        conn,
        "old-ai-card",
        created_by="ai",
        user_edited=0,
        analysis_run_id="old-analysis-run",
    )
    _insert_existing_card(
        conn,
        "edited-ai-card",
        created_by="ai",
        user_edited=1,
        analysis_run_id="old-analysis-run",
    )
    _insert_existing_card(conn, "manual-card", created_by="user", user_edited=0)
    conn.commit()


def _insert_existing_card(
    conn: sqlite3.Connection,
    card_id: str,
    *,
    created_by: str,
    user_edited: int,
    analysis_run_id: str | None = None,
) -> None:
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
            _SPACE_ID,
            _PAPER_ID,
            "method-1",
            "Method",
            card_id,
            0.7,
            user_edited,
            created_by,
            analysis_run_id,
        ),
    )


def _evaluate_late_section_coverage(
    batches: Sequence[analysis_pipeline.AnalysisPassageBatch],
    accepted_cards: Sequence[CardExtraction],
) -> dict[str, Any]:
    selected_source_ids = {
        source_id for batch in batches for source_id in batch.source_passage_ids
    }
    accepted_source_ids = {
        source_id for card in accepted_cards for source_id in card.source_passage_ids
    }
    required = list(_REQUIRED_LATE_SOURCE_IDS)
    covered = [
        source_id
        for source_id in required
        if source_id in selected_source_ids and source_id in accepted_source_ids
    ]
    return {
        "required_late_source_ids": required,
        "selected_late_source_ids": [
            source_id for source_id in required if source_id in selected_source_ids
        ],
        "covered_late_source_ids": covered,
        "late_section_coverage": _ratio(len(covered), len(required)),
    }


def _summarize_metrics(checks: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    return {
        "schema_validity_rate": float(checks["schema"]["schema_validity_rate"]),
        "source_grounding_rate": float(
            checks["source_verification"]["source_grounding_rate"]
        ),
        "duplicate_card_rate": float(checks["deduplication"]["duplicate_card_rate"]),
        "user_card_preservation_rate": float(
            checks["persistence"]["user_card_preservation_rate"]
        ),
        "late_section_coverage": float(checks["coverage"]["late_section_coverage"]),
    }


def _threshold_failures(metrics: Mapping[str, float]) -> list[str]:
    failures: list[str] = []
    if metrics["schema_validity_rate"] < PASSING_THRESHOLDS["schema_validity_rate"]:
        failures.append("schema_validity_rate")
    if metrics["source_grounding_rate"] < PASSING_THRESHOLDS["source_grounding_rate"]:
        failures.append("source_grounding_rate")
    if metrics["duplicate_card_rate"] > PASSING_THRESHOLDS["duplicate_card_rate"]:
        failures.append("duplicate_card_rate")
    if (
        metrics["user_card_preservation_rate"]
        < PASSING_THRESHOLDS["user_card_preservation_rate"]
    ):
        failures.append("user_card_preservation_rate")
    if metrics["late_section_coverage"] < PASSING_THRESHOLDS["late_section_coverage"]:
        failures.append("late_section_coverage")
    return failures


def _synthetic_full_paper_passages() -> list[dict[str, Any]]:
    passages = [
        _passage(
            "abstract-1",
            "The abstract introduces deterministic local paper analysis evaluation.",
            page_number=1,
            paragraph_index=0,
            passage_type="abstract",
            heading_path=["Abstract"],
        ),
        _passage(
            "method-1",
            "The analysis pipeline keeps source-aware method chunks for extraction.",
            page_number=3,
            paragraph_index=0,
            passage_type="method",
            heading_path=["Methods"],
        ),
        _passage(
            "method-2",
            "The method records stable source identifiers for every cited passage.",
            page_number=3,
            paragraph_index=1,
            passage_type="method",
            heading_path=["Methods"],
        ),
    ]
    passages.extend(
        _passage(
            f"body-{index:02d}",
            f"Background filler passage {index} establishes context for local analysis.",
            page_number=4 + index,
            paragraph_index=index,
            passage_type="body",
            heading_path=["Background"],
        )
        for index in range(65)
    )
    passages.extend(
        [
            _passage(
                "late-result",
                "The late results section reports a 37 percent source grounding gain.",
                page_number=70,
                paragraph_index=70,
                passage_type="result",
                heading_path=["Results"],
            ),
            _passage(
                "late-limitation",
                "The late limitations section says scanned papers still require OCR review.",
                page_number=71,
                paragraph_index=71,
                passage_type="limitation",
                heading_path=["Limitations"],
            ),
        ]
    )
    return passages


def _passage(
    passage_id: str,
    text: str,
    *,
    page_number: int,
    paragraph_index: int,
    passage_type: str,
    heading_path: list[str],
) -> dict[str, Any]:
    return {
        "id": passage_id,
        "paper_id": _PAPER_ID,
        "space_id": _SPACE_ID,
        "section": heading_path[-1] if heading_path else passage_type,
        "page_number": page_number,
        "paragraph_index": paragraph_index,
        "original_text": text,
        "passage_type": passage_type,
        "heading_path": heading_path,
    }


def _card(
    summary: str,
    source_passage_ids: list[str],
    *,
    card_type: CardType = "Method",
    confidence: float = 0.84,
) -> CardExtraction:
    return CardExtraction(
        card_type=card_type,
        summary=summary,
        source_passage_ids=source_passage_ids,
        evidence_quote=_evidence_for_source_id(source_passage_ids[0]),
        confidence=confidence,
        reasoning_summary="The cited passage directly supports this card.",
    )


def _context_from_prompt(user_prompt: str) -> dict[str, Any]:
    for line in user_prompt.splitlines():
        if line.startswith("Context: "):
            payload = json.loads(line.removeprefix("Context: "))
            if isinstance(payload, dict):
                return payload
    raise ValueError("prompt did not contain context JSON")


def _source_passages_from_prompt(user_prompt: str) -> list[dict[str, Any]]:
    lines = user_prompt.splitlines()
    try:
        start = lines.index("Source passages (JSONL):") + 1
    except ValueError as exc:
        raise ValueError("prompt did not contain source passage JSONL") from exc

    sources: list[dict[str, Any]] = []
    for line in lines[start:]:
        if not line.strip() or not line.startswith("{"):
            break
        payload = json.loads(line)
        if isinstance(payload, dict):
            sources.append(payload)
    return sources


def _card_type_for_source(source: Mapping[str, Any]) -> CardType:
    section = str(source.get("section", "")).casefold()
    if "result" in section:
        return "Result"
    if "limitation" in section:
        return "Limitation"
    if "method" in section:
        return "Method"
    return "Claim"


def _summary_for_source(source: Mapping[str, Any]) -> str:
    source_id = str(source["source_id"])
    if source_id == "late-result":
        return "The late results report a source grounding gain."
    if source_id == "late-limitation":
        return "The late limitations require OCR review for scanned papers."
    if source_id.startswith("method"):
        return "The method keeps source-aware chunks for grounded extraction."
    return f"The paper includes grounded evidence from {source_id}."


def _evidence_quote_for_source(source: Mapping[str, Any]) -> str:
    return _evidence_for_source_id(str(source["source_id"]))


def _evidence_for_source_id(source_id: str) -> str:
    if source_id == "late-result":
        return "37 percent source grounding gain"
    if source_id == "late-limitation":
        return "scanned papers still require OCR review"
    if source_id == "method-1":
        return "source-aware method chunks"
    if source_id == "method-2":
        return "stable source identifiers"
    if source_id == "abstract-1":
        return "deterministic local paper analysis evaluation"
    return "establishes context for local analysis"


def _surviving_duplicate_pair_count(cards: Sequence[CardExtraction]) -> int:
    count = 0
    for left_index, left in enumerate(cards):
        for right in cards[left_index + 1 :]:
            if _cards_are_duplicate(left, right):
                count += 1
    return count


def _cards_are_duplicate(left: CardExtraction, right: CardExtraction) -> bool:
    if left.card_type != right.card_type:
        return False
    if not set(left.source_passage_ids).intersection(right.source_passage_ids):
        return False
    return _jaccard(_summary_tokens(left.summary), _summary_tokens(right.summary)) >= 0.75


def _summary_tokens(value: str) -> set[str]:
    stopwords = {"a", "an", "and", "for", "of", "the", "to", "with"}
    normalized = "".join(char.lower() if char.isalnum() else " " for char in value)
    return {token for token in normalized.split() if token and token not in stopwords}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))


def _duplicate_pair_rate(pair_count: int, card_count: int) -> float:
    possible_pairs = card_count * (card_count - 1) // 2
    return _ratio(pair_count, possible_pairs)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the AI analysis evaluation harness from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for temporary evaluation database. Defaults to a temp dir.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON report output path.",
    )
    args = parser.parse_args(argv)

    report = run_evaluation(args.work_dir, output_path=args.output)
    if args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report["metrics"], sort_keys=True))
    return 0 if not report["threshold_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
