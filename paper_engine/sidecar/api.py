"""Packaged FastAPI sidecar entry point for the Tauri desktop app."""

from __future__ import annotations

import argparse
import importlib
import multiprocessing
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from paper_engine.core.startup import StartupTracer

_tracer = StartupTracer("python")
startup_trace = _tracer.trace


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    data_dir: Path | None
    resource_dir: Path | None


def parse_args(argv: Sequence[str] | None = None) -> ServerSettings:
    parser = argparse.ArgumentParser(
        prog="paper-engine-api",
        description="Run the Local Paper Knowledge Engine API server.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--resource-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    data_dir = args.data_dir.resolve() if args.data_dir else None
    resource_dir = args.resource_dir.resolve() if args.resource_dir else None
    return ServerSettings(
        host=str(args.host),
        port=int(args.port),
        data_dir=data_dir,
        resource_dir=resource_dir,
    )


def main(argv: Sequence[str] | None = None) -> None:
    multiprocessing.freeze_support()
    startup_trace("main_entry")
    settings = parse_args(argv)
    startup_trace(
        "args_parsed",
        host=settings.host,
        port=settings.port,
        data_dir=settings.data_dir or "",
        resource_dir=settings.resource_dir or "",
    )
    if settings.data_dir is not None:
        os.environ["PAPER_ENGINE_DATA_DIR"] = str(settings.data_dir)
        startup_trace("data_dir_configured", data_dir=settings.data_dir)
    if settings.resource_dir is not None:
        os.environ["PAPER_ENGINE_RESOURCE_DIR"] = str(settings.resource_dir)
        startup_trace("resource_dir_configured", resource_dir=settings.resource_dir)

    startup_trace("uvicorn_import_start")
    import uvicorn

    startup_trace("uvicorn_import_done")
    startup_trace("app_import_start")
    app: Any = importlib.import_module("paper_engine.api.app").app
    startup_trace("app_import_done")
    startup_trace("uvicorn_run_start", host=settings.host, port=settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
