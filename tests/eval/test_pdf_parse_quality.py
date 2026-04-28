"""Evaluation tests for the generated golden PDF parse pipeline."""

from __future__ import annotations

import json
import importlib.util
import sys
import warnings
from pathlib import Path

import pytest


pytestmark = pytest.mark.filterwarnings(
    "ignore:builtin type SwigPy.* has no __module__ attribute:DeprecationWarning",
    "ignore:builtin type swigvarlink has no __module__ attribute:DeprecationWarning",
)
warnings.filterwarnings(
    "ignore",
    message=r"builtin type SwigPy.* has no __module__ attribute",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"builtin type swigvarlink has no __module__ attribute",
    category=DeprecationWarning,
)


def _load_eval_module():
    script_path = Path(__file__).parents[2] / "scripts" / "eval_pdf_pipeline.py"
    spec = importlib.util.spec_from_file_location("eval_pdf_pipeline", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


eval_pdf_pipeline = _load_eval_module()
EXPECTED_METRIC_KEYS = eval_pdf_pipeline.EXPECTED_METRIC_KEYS
PASSING_THRESHOLDS = eval_pdf_pipeline.PASSING_THRESHOLDS
run_evaluation = eval_pdf_pipeline.run_evaluation


def test_generated_golden_pdfs_meet_parse_quality_thresholds(tmp_path: Path) -> None:
    """The local parser should produce stable, budgeted, reference-safe passages."""
    output_path = tmp_path / "parse-metrics.json"

    report = run_evaluation(tmp_path / "golden-pdfs", output_path=output_path)

    assert output_path.exists()
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted == report

    metrics = report["metrics"]
    assert set(metrics) == EXPECTED_METRIC_KEYS
    assert metrics["parse_success_rate"] == PASSING_THRESHOLDS["parse_success_rate"]
    assert (
        metrics["stable_source_ratio"]
        >= PASSING_THRESHOLDS["stable_source_ratio"]
    )
    assert (
        metrics["max_token_violation_count"]
        == PASSING_THRESHOLDS["max_token_violation_count"]
    )
    assert (
        metrics["reference_filter_precision"]
        >= PASSING_THRESHOLDS["reference_filter_precision"]
    )


def test_eval_report_covers_parser_routing_and_structure_diagnostics(
    tmp_path: Path,
) -> None:
    """Diagnostics should show that the golden set exercises key parser risks."""
    report = run_evaluation(tmp_path / "golden-pdfs")

    fixtures = {fixture["name"]: fixture for fixture in report["fixtures"]}
    assert set(fixtures) == {
        "simple_academic",
        "two_column",
        "table",
        "image_only",
        "references",
        "long_section",
    }

    assert fixtures["simple_academic"]["expected_headings_found"] == [
        "Abstract",
        "Introduction",
        "Method",
        "Results",
        "Discussion",
    ]
    assert fixtures["table"]["quality"]["estimated_table_pages"] >= 1
    assert fixtures["two_column"]["quality"]["estimated_two_column_pages"] >= 1
    assert fixtures["image_only"]["quality"]["needs_ocr"] is True
    assert fixtures["references"]["reference_passage_count"] == 0
    assert fixtures["long_section"]["max_token_count"] <= report["config"]["max_tokens"]
    assert report["metrics"]["table_isolation"] == fixtures["table"]["table_isolation"]
    assert report["thresholds"]["table_isolation"] == 0.90
    assert report["metrics"]["table_isolation"] >= 0.90
