"""Configuration behavior tests."""

from paper_engine.core.config import APP_DATA_DIR, DATABASE_PATH, SPACES_DIR


def test_default_data_paths_are_absolute() -> None:
    """MCP and web processes should resolve the same data directory from any cwd."""
    assert APP_DATA_DIR.is_absolute()
    assert DATABASE_PATH.is_absolute()
    assert SPACES_DIR.is_absolute()
