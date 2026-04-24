"""Application configuration and paths."""

from pathlib import Path

# Root data directory for all local application data
APP_DATA_DIR = Path("app-data")

# SQLite database file path
DATABASE_PATH = APP_DATA_DIR / "paper_engine.db"

# Spaces data directory (per-space subdirectories under here)
SPACES_DIR = APP_DATA_DIR / "spaces"
