"""Packaged FastAPI sidecar entry point for the Tauri desktop app."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

PROCESS_STARTED_AT = time.perf_counter()
STARTUP_TRACE_ENV = "PAPER_ENGINE_STARTUP_TRACE"


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    data_dir: Path | None


def startup_trace(event: str, **fields: object) -> None:
    """Write a structured startup timing line when tracing is enabled."""
    if os.environ.get(STARTUP_TRACE_ENV) != "1":
        return

    elapsed_ms = (time.perf_counter() - PROCESS_STARTED_AT) * 1000
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    suffix = f" {details}" if details else ""
    print(
        f"[paper-engine startup] python event={event} elapsed_ms={elapsed_ms:.1f}{suffix}",
        file=sys.stderr,
        flush=True,
    )


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
    startup_trace("main_entry")
    settings = parse_args(argv)
    startup_trace(
        "args_parsed",
        host=settings.host,
        port=settings.port,
        data_dir=settings.data_dir or "",
    )
    if settings.data_dir is not None:
        os.environ["PAPER_ENGINE_DATA_DIR"] = str(settings.data_dir)
        startup_trace("data_dir_configured", data_dir=settings.data_dir)

    startup_trace("uvicorn_import_start")
    import uvicorn

    startup_trace("uvicorn_import_done")
    startup_trace("app_import_start")
    app: Any = importlib.import_module("main").app
    startup_trace("app_import_done")
    startup_trace("uvicorn_run_start", host=settings.host, port=settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
