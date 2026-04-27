"""Evaluation tests for the deterministic AI analysis pipeline harness."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_eval_module():
    script_path = Path(__file__).parents[2] / "scripts" / "eval_analysis_pipeline.py"
    spec = importlib.util.spec_from_file_location("eval_analysis_pipeline", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


eval_analysis_pipeline = _load_eval_module()
EXPECTED_METRIC_KEYS = eval_analysis_pipeline.EXPECTED_METRIC_KEYS
PASSING_THRESHOLDS = eval_analysis_pipeline.PASSING_THRESHOLDS
run_evaluation = eval_analysis_pipeline.run_evaluation


def test_deterministic_analysis_outputs_meet_quality_thresholds(
    tmp_path: Path,
) -> None:
    """Mocked model outputs should satisfy strict analysis quality gates."""
    output_path = tmp_path / "analysis-metrics.json"

    report = run_evaluation(output_path=output_path)

    assert output_path.exists()
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted == report

    metrics = report["metrics"]
    assert set(metrics) == EXPECTED_METRIC_KEYS
    assert metrics["schema_validity_rate"] == PASSING_THRESHOLDS["schema_validity_rate"]
    assert metrics["source_grounding_rate"] == PASSING_THRESHOLDS["source_grounding_rate"]
    assert (
        metrics["user_card_preservation_rate"]
        == PASSING_THRESHOLDS["user_card_preservation_rate"]
    )
    assert metrics["duplicate_card_rate"] <= PASSING_THRESHOLDS["duplicate_card_rate"]
    assert metrics["late_section_coverage"] == PASSING_THRESHOLDS["late_section_coverage"]
    assert report["threshold_failures"] == []


def test_eval_report_covers_analysis_risk_diagnostics() -> None:
    """Report details should prove each task-41 risk area was exercised."""
    report = run_evaluation()
    checks = report["checks"]

    assert checks["schema"]["valid_response_count"] == checks["schema"]["response_count"]
    assert checks["source_verification"]["accepted_card_count"] > 0
    assert checks["source_verification"]["rejected_card_count"] == 0
    assert checks["deduplication"]["surviving_duplicate_pairs"] == 0
    assert checks["deduplication"]["pipeline_duplicate_card_count"] > 0
    assert checks["persistence"]["preserved_card_ids"] == [
        "edited-ai-card",
        "manual-card",
    ]
    assert checks["coverage"]["required_late_source_ids"] == [
        "late-result",
        "late-limitation",
    ]
    assert checks["coverage"]["covered_late_source_ids"] == [
        "late-result",
        "late-limitation",
    ]
