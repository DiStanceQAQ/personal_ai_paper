"""Packaged FastAPI sidecar entry point for the Tauri desktop app."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import uvicorn

from main import app


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    data_dir: Path | None


def parse_args(argv: Sequence[str] | None = None) -> ServerSettings:
    parser = argparse.ArgumentParser(
        prog="paper-engine-api",
        description="Run the Local Paper Knowledge Engine API server.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    data_dir = args.data_dir.resolve() if args.data_dir else None
    return ServerSettings(host=str(args.host), port=int(args.port), data_dir=data_dir)


def main(argv: Sequence[str] | None = None) -> None:
    settings = parse_args(argv)
    if settings.data_dir is not None:
        os.environ["PAPER_ENGINE_DATA_DIR"] = str(settings.data_dir)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
