"""Download the local Docling models required for PDF parsing."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "resources" / "models" / "docling-hf-cache" / "hub"
LAYOUT_REPO_DIRNAME = "docling-project--docling-layout-heron"
TABLE_REPO_DIRNAME = "docling-project--docling-models"
HF_CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"
TABLE_CONFIG_PATH = Path(
    f"{TABLE_REPO_DIRNAME}/model_artifacts/tableformer/accurate/tm_config.json"
)
HF_LAYOUT_REPO_DIR = HF_CACHE_DIR / f"models--{LAYOUT_REPO_DIRNAME}"
HF_TABLE_REPO_DIR = HF_CACHE_DIR / f"models--{TABLE_REPO_DIRNAME}"


def docling_models_ready(target: Path) -> bool:
    """Return whether the required Docling model artifacts are already present."""
    return (target / f"models--{LAYOUT_REPO_DIRNAME}" / "refs" / "main").is_file() and (
        target / f"models--{TABLE_REPO_DIRNAME}" / "refs" / "v2.3.0"
    ).is_file()


def download_required_docling_models(target: Path) -> None:
    """Download the layout and table-structure models used by Docling."""
    try:
        from docling.models.stages.layout.layout_model import LayoutModel
        from docling.models.stages.table_structure.table_structure_model import (
            TableStructureModel,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed. Run `python -m pip install -e '.[pdf-advanced]'` first."
        ) from exc

    target.mkdir(parents=True, exist_ok=True)
    LayoutModel.download_models()
    TableStructureModel.download_models()
    _sync_repo_tree(HF_LAYOUT_REPO_DIR, target / HF_LAYOUT_REPO_DIR.name)
    _sync_repo_tree(HF_TABLE_REPO_DIR, target / HF_TABLE_REPO_DIR.name)


def _sync_repo_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"Expected Docling cache repo not found: {source}")
    shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a bundled Hugging Face cache with the Docling layout and table models.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=f"Target bundled HF cache directory. Defaults to {DEFAULT_TARGET}",
    )
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="Skip downloads when the required model files are already present.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    target = args.target.expanduser()

    if args.if_missing and docling_models_ready(target):
        print(f"Docling models already exist at {target}")
        return 0

    download_required_docling_models(target)
    print(f"Docling models ready at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
