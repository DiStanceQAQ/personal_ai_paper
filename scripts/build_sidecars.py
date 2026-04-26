"""Build PyInstaller sidecars for Tauri externalBin packaging."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI_BINARIES = ROOT / "src-tauri" / "binaries"


def host_triple() -> str:
    rustc = shutil.which("rustc")
    if rustc is None:
        raise RuntimeError("rustc is required to compute the Tauri sidecar target triple")

    result = subprocess.run(
        [rustc, "--print", "host-tuple"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    result = subprocess.run(
        [rustc, "-Vv"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("Unable to determine rust target triple")


def build_onefile(
    name: str,
    entrypoint: str,
    hidden_imports: tuple[str, ...] = (),
) -> Path:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--name",
        name,
    ]
    for module in hidden_imports:
        command.extend(["--hidden-import", module])
    command.append(entrypoint)

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )
    extension = ".exe" if sys.platform == "win32" else ""
    binary = ROOT / "dist" / f"{name}{extension}"
    if not binary.exists():
        raise RuntimeError(f"Expected PyInstaller output not found: {binary}")
    return binary


def copy_for_tauri(binary: Path, sidecar_name: str, target_triple: str) -> Path:
    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)
    extension = ".exe" if sys.platform == "win32" else ""
    destination = TAURI_BINARIES / f"{sidecar_name}-{target_triple}{extension}"
    shutil.copy2(binary, destination)
    destination.chmod(0o755)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        choices=["api", "mcp", "all"],
        default="all",
    )
    args = parser.parse_args(argv)

    target_triple = host_triple()
    targets: list[tuple[str, str, tuple[str, ...]]] = []
    if args.target in {"api", "all"}:
        targets.append((
            "paper-engine-api", 
            "api_sidecar.py", 
            ("main", "agent_executor", "llm_client", "db", "parser", "config", "search")
        ))
    if args.target in {"mcp", "all"}:
        targets.append(("paper-engine-mcp", "mcp_server.py", ("db", "config")))

    for sidecar_name, entrypoint, hidden_imports in targets:
        binary = build_onefile(sidecar_name, entrypoint, hidden_imports)
        packaged = copy_for_tauri(binary, sidecar_name, target_triple)
        print(f"Built {packaged}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
