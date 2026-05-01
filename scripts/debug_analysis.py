"""Debug AI paper analysis prompts and structured outputs.

This script is intentionally side-effect-light by default: it reads an already
parsed paper, calls the same prompt builders and LLM schema client used by the
product pipeline, and dumps prompts/responses/results to a local directory.
Use --persist only when you explicitly want to run the full production pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import paper_engine.analysis.pipeline as analysis_pipeline
from paper_engine.agent.llm_client import call_llm_schema
from paper_engine.analysis.models import (
    PaperUnderstandingExtraction,
)
from paper_engine.analysis.prompts import (
    AnalysisPrompt,
    build_paper_understanding_prompt,
)
from paper_engine.storage.database import get_connection, init_db


DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "analysis"


def main() -> None:
    args = _parse_args()
    init_db()
    asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> None:
    started_at = time.strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir or DEFAULT_OUTPUT_ROOT / f"{args.paper_id}-{started_at}")
    output_dir.mkdir(parents=True, exist_ok=True)

    space_id = args.space_id or _infer_space_id(args.paper_id)
    passages = _load_input_passages(args, space_id)
    if not passages:
        raise SystemExit("No passages found. Parse the PDF first or pass --input-json.")

    _write_json(
        output_dir / "run_config.json",
        {
            "paper_id": args.paper_id,
            "space_id": space_id,
            "stage": args.stage,
            "input_json": str(args.input_json) if args.input_json else "",
            "dry_prompts": args.dry_prompts,
            "persist": args.persist,
            "passage_count": len(passages),
        },
    )
    _write_json(output_dir / "input_passages.json", passages)

    if args.persist:
        result = await analysis_pipeline.run_paper_analysis(args.paper_id, space_id)
        _write_json(output_dir / "persisted_result.json", _jsonable(result.result))
        _write_text(output_dir / "summary.txt", f"analysis_run_id={result.analysis_run_id}\n")
        print(f"Persisted analysis run: {result.analysis_run_id}")
        print(f"Dumped debug files to: {output_dir}")
        return

    understanding = await _debug_understanding(
        passages=passages,
        output_dir=output_dir,
        dry_prompts=args.dry_prompts,
    )
    if understanding is not None:
        derived = analysis_pipeline.derive_cards_from_understanding(
            understanding,
            paper_id=args.paper_id,
            space_id=space_id,
            passages=passages,
        )
        _write_json(output_dir / "derived_cards.json", _jsonable(derived.accepted_cards))
        _write_json(
            output_dir / "derived_card_diagnostics.json",
            _jsonable(derived.rejected_cards),
        )

    print(f"Dumped debug files to: {output_dir}")


async def _debug_understanding(
    *,
    passages: Sequence[Mapping[str, Any]],
    output_dir: Path,
    dry_prompts: bool,
) -> PaperUnderstandingExtraction | None:
    selected = analysis_pipeline._select_understanding_llm_passages(passages)
    stage_dir = output_dir / "understanding"
    stage_dir.mkdir(parents=True, exist_ok=True)
    _write_json(stage_dir / "selected_passages.json", selected)

    if not selected:
        _write_json(stage_dir / "result.json", {"understanding": None, "reason": "no_passages_selected"})
        return None

    prompt = build_paper_understanding_prompt(selected)
    _dump_prompt(stage_dir, prompt)
    if dry_prompts:
        return None

    response = await call_llm_schema(
        prompt.system_prompt,
        prompt.user_prompt,
        prompt.schema_name,
        prompt.schema,
    )
    _write_json(stage_dir / "response.raw.json", response)
    understanding = PaperUnderstandingExtraction.model_validate(response)
    _write_json(stage_dir / "result.json", understanding.model_dump())
    return understanding


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump and run AI analysis prompts for one parsed paper.",
    )
    parser.add_argument("--paper-id", required=True, help="Paper id to debug.")
    parser.add_argument(
        "--space-id",
        help="Space id. If omitted, it is inferred from the paper row.",
    )
    parser.add_argument(
        "--stage",
        choices=["understanding"],
        default="understanding",
        help="AI stage to debug. Understanding is now the primary analysis output.",
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        help=(
            "Optional passages JSON file. Use this to tweak the AI input without "
            "changing the database."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for prompts, responses, and diagnostics.",
    )
    parser.add_argument(
        "--dry-prompts",
        action="store_true",
        help="Only dump prompts and selected inputs; do not call the LLM.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help=(
            "Run the full production pipeline and persist results. This can replace "
            "unedited AI cards for the paper."
        ),
    )
    return parser.parse_args()


def _infer_space_id(paper_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT space_id FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(
            f"Paper not found: {paper_id}. Pass --space-id when using --input-json "
            "without a matching database paper."
        )
    return str(row["space_id"])


def _load_input_passages(
    args: argparse.Namespace,
    space_id: str,
) -> list[dict[str, Any]]:
    if args.input_json is not None:
        if not args.input_json.exists():
            raise SystemExit(f"Input JSON not found: {args.input_json}")
        value = json.loads(args.input_json.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise SystemExit("--input-json must contain a JSON array of passages.")
        return [_dict_item(item) for item in value]

    conn = get_connection()
    try:
        return analysis_pipeline._load_analysis_passages(
            conn,
            args.paper_id,
            space_id,
        )
    finally:
        conn.close()


def _dump_prompt(directory: Path, prompt: AnalysisPrompt) -> None:
    _write_text(directory / "system_prompt.txt", prompt.system_prompt)
    _write_text(directory / "user_prompt.txt", prompt.user_prompt)
    _write_json(
        directory / "schema.json",
        {
            "schema_name": prompt.schema_name,
            "schema": prompt.schema,
        },
    )


def _dict_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SystemExit("Passage items in --input-json must be JSON objects.")
    return dict(value)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


if __name__ == "__main__":
    main()
