"""Download the embedding model used as a Tauri resource."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence, cast

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_DIRNAME = "intfloat-multilingual-e5-small"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "resources" / "models" / MODEL_DIRNAME
REQUIRED_MODEL_FILES = ("modules.json",)


def download_embedding_model(
    target: Path = DEFAULT_TARGET,
    *,
    if_missing: bool = False,
) -> Path:
    """Download and save the sentence-transformer model to a local directory."""
    if if_missing and has_required_model_files(target):
        print(f"Embedding model already exists at {target}")
        return target

    target.mkdir(parents=True, exist_ok=True)
    sentence_transformer = _load_sentence_transformer_class()
    model = sentence_transformer(MODEL_NAME)
    model.save(str(target))

    if not has_required_model_files(target):
        raise RuntimeError(f"Downloaded embedding model is incomplete at {target}")

    print(f"Embedding model saved to {target}")
    return target


def has_required_model_files(path: Path) -> bool:
    return path.is_dir() and all((path / filename).is_file() for filename in REQUIRED_MODEL_FILES)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download multilingual-e5-small into the Tauri resource directory.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=f"Model output directory. Defaults to {DEFAULT_TARGET}",
    )
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="Skip the download when the target already looks complete.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    download_embedding_model(args.target, if_missing=bool(args.if_missing))


def _load_sentence_transformer_class() -> type[Any]:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required before downloading the embedding model. "
            "Run `python -m pip install -e .` first."
        ) from exc

    return cast(type[Any], SentenceTransformer)


if __name__ == "__main__":
    main()
