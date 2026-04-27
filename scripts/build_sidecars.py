"""Build PyInstaller sidecars for Tauri externalBin packaging."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI_BINARIES = ROOT / "src-tauri" / "binaries"
PDF_OPTIONAL_PACKAGES = ("docling",)
EMBEDDING_OPTIONAL_PACKAGES = ("sentence_transformers",)


@dataclass(frozen=True)
class SidecarTarget:
    sidecar_name: str
    entrypoint: str
    hidden_imports: tuple[str, ...] = ()
    collect_submodules: tuple[str, ...] = ()
    excluded_modules: tuple[str, ...] = ()


PDF_PIPELINE_HIDDEN_IMPORTS = (
    "db_migrations",
    "pdf_backend_base",
    "pdf_backend_docling",
    "pdf_backend_grobid",
    "pdf_backend_legacy",
    "pdf_backend_llamaparse",
    "pdf_backend_pymupdf4llm",
    "pdf_chunker",
    "pdf_models",
    "pdf_persistence",
    "pdf_profile",
    "pdf_router",
    "pymupdf",
    "pymupdf4llm",
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
)
ANALYSIS_PIPELINE_HIDDEN_IMPORTS = (
    "analysis_models",
    "analysis_pipeline",
    "analysis_prompts",
    "analysis_verifier",
)
RETRIEVAL_HIDDEN_IMPORTS = (
    "embeddings",
    "hybrid_search",
    "search",
)
API_HIDDEN_IMPORTS = (
    "main",
    "agent_executor",
    *ANALYSIS_PIPELINE_HIDDEN_IMPORTS,
    "card_extractor",
    "config",
    "db",
    "llm_client",
    "parser",
    *PDF_PIPELINE_HIDDEN_IMPORTS,
    *RETRIEVAL_HIDDEN_IMPORTS,
    "routes_agent",
    "routes_cards",
    "routes_papers",
    "routes_search",
    "routes_spaces",
)
MCP_HIDDEN_IMPORTS = (
    "config",
    "db",
    "db_migrations",
    *RETRIEVAL_HIDDEN_IMPORTS,
)


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
    collect_submodules: tuple[str, ...] = (),
    excluded_modules: tuple[str, ...] = (),
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
    for package in collect_submodules:
        command.extend(["--collect-submodules", package])
    for module in excluded_modules:
        command.extend(["--exclude-module", module])
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


def build_targets(target: str) -> list[SidecarTarget]:
    api_collections, api_exclusions = _optional_dependency_args(
        (*PDF_OPTIONAL_PACKAGES, *EMBEDDING_OPTIONAL_PACKAGES)
    )
    mcp_collections, mcp_exclusions = _optional_dependency_args(
        EMBEDDING_OPTIONAL_PACKAGES
    )
    mcp_exclusions = _dedupe((*mcp_exclusions, *PDF_OPTIONAL_PACKAGES))

    targets: list[SidecarTarget] = []
    if target in {"api", "all"}:
        targets.append(
            SidecarTarget(
                sidecar_name="paper-engine-api",
                entrypoint="api_sidecar.py",
                hidden_imports=API_HIDDEN_IMPORTS,
                collect_submodules=api_collections,
                excluded_modules=api_exclusions,
            )
        )
    if target in {"mcp", "all"}:
        targets.append(
            SidecarTarget(
                sidecar_name="paper-engine-mcp",
                entrypoint="mcp_server.py",
                hidden_imports=MCP_HIDDEN_IMPORTS,
                collect_submodules=mcp_collections,
                excluded_modules=mcp_exclusions,
            )
        )
    return targets


def _optional_dependency_args(
    package_names: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    collect_submodules: list[str] = []
    excluded_modules: list[str] = []
    for package_name in package_names:
        if importlib.util.find_spec(package_name) is None:
            excluded_modules.append(package_name)
        else:
            collect_submodules.append(package_name)
    return _dedupe(collect_submodules), _dedupe(excluded_modules)


def _dedupe(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        choices=["api", "mcp", "all"],
        default="all",
    )
    args = parser.parse_args(argv)

    target_triple = host_triple()
    targets = build_targets(args.target)

    for sidecar_target in targets:
        binary = build_onefile(
            sidecar_target.sidecar_name,
            sidecar_target.entrypoint,
            sidecar_target.hidden_imports,
            sidecar_target.collect_submodules,
            sidecar_target.excluded_modules,
        )
        packaged = copy_for_tauri(binary, sidecar_target.sidecar_name, target_triple)
        print(f"Built {packaged}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
