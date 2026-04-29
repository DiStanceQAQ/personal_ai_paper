"""Configuration behavior tests."""

from pathlib import Path

from paper_engine.core.config import APP_DATA_DIR, DATABASE_PATH, SPACES_DIR


def test_default_data_paths_are_absolute() -> None:
    """MCP and web processes should resolve the same data directory from any cwd."""
    assert APP_DATA_DIR.is_absolute()
    assert DATABASE_PATH.is_absolute()
    assert SPACES_DIR.is_absolute()


def test_default_data_dir_is_project_root_app_data() -> None:
    """Default local data should match the documented project-root app-data path."""
    project_root = Path(__file__).resolve().parents[1]

    assert APP_DATA_DIR == project_root / "app-data"
    assert DATABASE_PATH == project_root / "app-data" / "paper_engine.db"
    assert SPACES_DIR == project_root / "app-data" / "spaces"
