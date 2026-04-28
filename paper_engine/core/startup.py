"""Startup tracing utilities for API and sidecar entry points."""

from __future__ import annotations

import os
import sys
import time

STARTUP_TRACE_ENV = "PAPER_ENGINE_STARTUP_TRACE"


class StartupTracer:
    """Write structured startup timing lines when tracing is enabled."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._started_at = time.perf_counter()

    def trace(self, event: str, **fields: object) -> None:
        if os.environ.get(STARTUP_TRACE_ENV) != "1":
            return

        elapsed_ms = (time.perf_counter() - self._started_at) * 1000
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        suffix = f" {details}" if details else ""
        print(
            f"[paper-engine startup] {self._label} event={event} "
            f"elapsed_ms={elapsed_ms:.1f}{suffix}",
            file=sys.stderr,
            flush=True,
        )
