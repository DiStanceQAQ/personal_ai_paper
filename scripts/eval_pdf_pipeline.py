"""Evaluate PDF parsing quality against generated golden fixtures."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdf_chunker import chunk_parse_document, count_text_tokens
from pdf_models import ParseDocument, PassageRecord, PdfQualityReport
from pdf_profile import inspect_pdf
from pdf_router import PdfBackendRouter

DEFAULT_MAX_TOKENS = 900
EXPECTED_METRIC_KEYS = {
    "heading_recall",
    "table_isolation",
    "reference_filter_precision",
    "stable_source_ratio",
    "max_token_violation_count",
    "parse_success_rate",
}
PASSING_THRESHOLDS = {
    "parse_success_rate": 1.0,
    "stable_source_ratio": 0.95,
    "max_token_violation_count": 0,
    "reference_filter_precision": 0.90,
}
_SPACE_ID = "eval-space"


@dataclass(frozen=True)
class GoldenFixture:
    name: str
    factory: Callable[[Path], Path]
    expected_headings: tuple[str, ...] = ()
    reference_terms: tuple[str, ...] = ()
    table_terms: tuple[str, ...] = ()


def _load_pdf_factory(name: str) -> Callable[[Path], Path]:
    module = importlib.import_module("tests.fixtures.pdf_factory")
    factory = getattr(module, name)
    if not callable(factory):
        raise TypeError(f"{name} is not a callable PDF fixture factory")
    return cast(Callable[[Path], Path], factory)


GOLDEN_FIXTURES = (
    GoldenFixture(
        name="simple_academic",
        factory=_load_pdf_factory("simple_academic_pdf"),
        expected_headings=(
            "Abstract",
            "Introduction",
            "Method",
            "Results",
            "Discussion",
        ),
    ),
    GoldenFixture(name="two_column", factory=_load_pdf_factory("two_column_pdf")),
    GoldenFixture(
        name="table",
        factory=_load_pdf_factory("table_pdf"),
        table_terms=("Metric", "Accuracy", "Latency", "Coverage"),
    ),
    GoldenFixture(name="image_only", factory=_load_pdf_factory("image_only_pdf")),
    GoldenFixture(
        name="references",
        factory=_load_pdf_factory("references_pdf"),
        reference_terms=(
            "Smith, J.",
            "Garcia, M.",
            "Journal of Test Artifacts",
            "Proceedings of Local AI Evaluation",
        ),
    ),
    GoldenFixture(
        name="long_section",
        factory=_load_pdf_factory("long_section_pdf"),
        expected_headings=(
            "Long Evaluation Section 1",
            "Long Evaluation Section 2",
            "Long Evaluation Section 3",
        ),
    ),
)


def run_evaluation(
    work_dir: Path | str | None = None,
    *,
    output_path: Path | str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Generate golden PDFs, evaluate parse quality, and optionally write JSON."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")

    if work_dir is None:
        with TemporaryDirectory(prefix="pdf-parse-eval-") as temp_dir:
            report = _run_evaluation_in_dir(Path(temp_dir), max_tokens=max_tokens)
    else:
        report = _run_evaluation_in_dir(Path(work_dir), max_tokens=max_tokens)

    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return report


def _run_evaluation_in_dir(work_dir: Path, *, max_tokens: int) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    fixture_reports: list[dict[str, Any]] = []

    for fixture in GOLDEN_FIXTURES:
        pdf_path = fixture.factory(work_dir / f"{fixture.name}.pdf")
        fixture_reports.append(_evaluate_fixture(fixture, pdf_path, max_tokens))

    metrics = _summarize_metrics(fixture_reports)
    return {
        "config": {
            "max_tokens": max_tokens,
            "fixture_count": len(GOLDEN_FIXTURES),
        },
        "metrics": metrics,
        "thresholds": PASSING_THRESHOLDS,
        "threshold_failures": _threshold_failures(metrics),
        "fixtures": fixture_reports,
    }


def _evaluate_fixture(
    fixture: GoldenFixture,
    pdf_path: Path,
    max_tokens: int,
) -> dict[str, Any]:
    quality = inspect_pdf(pdf_path)
    first = _parse_and_chunk(pdf_path, fixture.name, quality, max_tokens)
    second = _parse_and_chunk(pdf_path, fixture.name, quality.model_copy(deep=True), max_tokens)

    passages = first.get("passages", [])
    assert isinstance(passages, list)
    document = first.get("document")
    expected_headings = list(fixture.expected_headings)
    expected_headings_found = (
        _expected_headings_found(document, fixture.expected_headings)
        if isinstance(document, ParseDocument)
        else []
    )
    reference_passage_count = _matching_passage_count(
        passages,
        fixture.reference_terms,
    )
    table_isolation = _table_isolation_score(passages, fixture.table_terms)
    max_token_count = max(
        (_passage_token_count(passage) for passage in passages),
        default=0,
    )

    return {
        "name": fixture.name,
        "path": str(pdf_path),
        "success": first["success"],
        "second_parse_success": second["success"],
        "error": first.get("error"),
        "second_parse_error": second.get("error"),
        "backend": first.get("backend", ""),
        "extraction_method": first.get("extraction_method", ""),
        "quality": quality.model_dump(mode="json"),
        "element_count": first.get("element_count", 0),
        "table_count": first.get("table_count", 0),
        "passage_count": len(passages),
        "max_token_count": max_token_count,
        "token_violation_count": sum(
            1 for passage in passages if _passage_token_count(passage) > max_tokens
        ),
        "expected_headings": expected_headings,
        "expected_headings_found": expected_headings_found,
        "heading_recall": (
            _ratio(len(expected_headings_found), len(expected_headings))
            if expected_headings
            else None
        ),
        "reference_passage_count": reference_passage_count,
        "reference_filter_precision": (
            _reference_filter_precision(
                len(passages),
                reference_passage_count,
            )
            if fixture.reference_terms
            else None
        ),
        "table_isolation": table_isolation,
        "stable_source_ratio": _stable_source_ratio(
            first.get("source_keys", []),
            second.get("source_keys", []),
        ),
    }


def _parse_and_chunk(
    pdf_path: Path,
    fixture_name: str,
    quality: PdfQualityReport,
    max_tokens: int,
) -> dict[str, Any]:
    router = PdfBackendRouter(
        forced_backend="",
        llamaparse=None,
        grobid_client=None,
    )
    paper_id = f"eval-{fixture_name}"
    try:
        document = router.parse_pdf(pdf_path, paper_id, _SPACE_ID, quality)
        passages = chunk_parse_document(document, max_tokens=max_tokens)
    except Exception as exc:
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "source_keys": [],
            "passages": [],
        }

    return {
        "success": True,
        "document": document,
        "passages": passages,
        "backend": document.backend,
        "extraction_method": document.extraction_method,
        "element_count": len(document.elements),
        "table_count": len(document.tables),
        "source_keys": [_source_key(passage) for passage in passages],
    }


def _summarize_metrics(fixture_reports: Sequence[dict[str, Any]]) -> dict[str, float | int]:
    return {
        "heading_recall": _average_present_metric(fixture_reports, "heading_recall"),
        "table_isolation": _average_present_metric(fixture_reports, "table_isolation"),
        "reference_filter_precision": _average_present_metric(
            fixture_reports,
            "reference_filter_precision",
        ),
        "stable_source_ratio": _average_present_metric(
            fixture_reports,
            "stable_source_ratio",
        ),
        "max_token_violation_count": sum(
            int(report["token_violation_count"]) for report in fixture_reports
        ),
        "parse_success_rate": _ratio(
            sum(1 for report in fixture_reports if report["success"] is True),
            len(fixture_reports),
        ),
    }


def _threshold_failures(metrics: dict[str, float | int]) -> list[str]:
    failures: list[str] = []
    if metrics["parse_success_rate"] < PASSING_THRESHOLDS["parse_success_rate"]:
        failures.append("parse_success_rate")
    if metrics["stable_source_ratio"] < PASSING_THRESHOLDS["stable_source_ratio"]:
        failures.append("stable_source_ratio")
    if (
        metrics["max_token_violation_count"]
        != PASSING_THRESHOLDS["max_token_violation_count"]
    ):
        failures.append("max_token_violation_count")
    if (
        metrics["reference_filter_precision"]
        < PASSING_THRESHOLDS["reference_filter_precision"]
    ):
        failures.append("reference_filter_precision")
    return failures


def _expected_headings_found(
    document: ParseDocument,
    expected_headings: Sequence[str],
) -> list[str]:
    detected = {
        element.text.strip().casefold()
        for element in document.elements
        if element.element_type == "heading"
    }
    return [heading for heading in expected_headings if heading.casefold() in detected]


def _matching_passage_count(
    passages: Sequence[Any],
    terms: Sequence[str],
) -> int:
    if not terms:
        return 0
    lowered_terms = [term.casefold() for term in terms]
    return sum(
        1
        for passage in passages
        if any(term in _passage_text(passage).casefold() for term in lowered_terms)
    )


def _reference_filter_precision(
    passage_count: int,
    reference_passage_count: int,
) -> float:
    if passage_count == 0:
        return 1.0 if reference_passage_count == 0 else 0.0
    non_reference_count = max(0, passage_count - reference_passage_count)
    return _ratio(non_reference_count, passage_count)


def _table_isolation_score(
    passages: Sequence[Any],
    table_terms: Sequence[str],
) -> float | None:
    if not table_terms:
        return None

    lowered_terms = [term.casefold() for term in table_terms]
    matching_passages = [
        passage
        for passage in passages
        if any(term in _passage_text(passage).casefold() for term in lowered_terms)
    ]
    if not matching_passages:
        return 0.0

    table_passages = [
        passage
        for passage in matching_passages
        if isinstance(passage, PassageRecord) and passage.metadata.get("table_id")
    ]
    covered_terms = {
        term
        for term in lowered_terms
        for passage in table_passages
        if term in passage.original_text.casefold()
    }
    non_table_matches = len(matching_passages) - len(table_passages)
    if covered_terms == set(lowered_terms) and non_table_matches == 0:
        return 1.0
    if covered_terms:
        return len(covered_terms) / len(lowered_terms)
    return 0.0


def _stable_source_ratio(first_keys: Any, second_keys: Any) -> float:
    if not isinstance(first_keys, list) or not isinstance(second_keys, list):
        return 0.0
    if not first_keys and not second_keys:
        return 1.0
    first_counts = Counter(str(key) for key in first_keys)
    second_counts = Counter(str(key) for key in second_keys)
    stable = sum((first_counts & second_counts).values())
    total = max(sum(first_counts.values()), sum(second_counts.values()))
    return _ratio(stable, total)


def _average_present_metric(
    fixture_reports: Sequence[dict[str, Any]],
    metric_key: str,
) -> float:
    values = [
        float(report[metric_key])
        for report in fixture_reports
        if metric_key in report and report[metric_key] is not None
    ]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def _source_key(passage: PassageRecord) -> str:
    payload = {
        "section": passage.section,
        "page_number": passage.page_number,
        "paragraph_index": passage.paragraph_index,
        "heading_path": passage.heading_path,
        "text_hash": hashlib.sha256(
            passage.original_text.encode("utf-8"),
        ).hexdigest(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _passage_token_count(passage: Any) -> int:
    if isinstance(passage, PassageRecord) and passage.token_count is not None:
        return passage.token_count
    return count_text_tokens(_passage_text(passage))


def _passage_text(passage: Any) -> str:
    if isinstance(passage, PassageRecord):
        return passage.original_text
    return ""


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the parse evaluation harness from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for generated golden PDFs. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON report output path.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="Hard passage token budget used by the chunker.",
    )
    args = parser.parse_args(argv)

    report = run_evaluation(
        args.work_dir,
        output_path=args.output,
        max_tokens=args.max_tokens,
    )
    if args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report["metrics"], sort_keys=True))
    return 0 if not report["threshold_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
