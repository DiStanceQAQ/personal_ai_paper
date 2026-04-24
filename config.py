"""Application configuration and paths."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Root data directory for all local application data
APP_DATA_DIR = Path(
    os.environ.get("PAPER_ENGINE_DATA_DIR", PROJECT_ROOT / "app-data")
).expanduser().resolve()

# SQLite database file path
DATABASE_PATH = APP_DATA_DIR / "paper_engine.db"

# Spaces data directory (per-space subdirectories under here)
SPACES_DIR = APP_DATA_DIR / "spaces"
