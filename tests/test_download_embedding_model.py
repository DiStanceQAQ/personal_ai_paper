"""Tests for preparing the bundled embedding model resource."""

from pathlib import Path
from typing import Any

from scripts import download_embedding_model


def test_download_embedding_model_saves_multilingual_e5_resource(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    saved_targets: list[Path] = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str) -> None:
            assert model_name == "intfloat/multilingual-e5-small"

        def save(self, target: str) -> None:
            target_path = Path(target)
            saved_targets.append(target_path)
            target_path.mkdir(parents=True, exist_ok=True)
            (target_path / "modules.json").write_text("[]")

    monkeypatch.setattr(
        download_embedding_model,
        "_load_sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    target = download_embedding_model.download_embedding_model(tmp_path / "model")

    assert target == tmp_path / "model"
    assert saved_targets == [tmp_path / "model"]


def test_download_embedding_model_if_missing_skips_existing_resource(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "modules.json").write_text("[]")

    def fail_loader() -> object:
        raise AssertionError("existing model should not be downloaded again")

    monkeypatch.setattr(
        download_embedding_model,
        "_load_sentence_transformer_class",
        fail_loader,
    )

    target = download_embedding_model.download_embedding_model(
        model_dir,
        if_missing=True,
    )

    assert target == model_dir
