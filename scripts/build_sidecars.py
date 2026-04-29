"""Build PyInstaller sidecars for Tauri externalBin packaging."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.metadata
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI_BINARIES = ROOT / "src-tauri" / "binaries"
PDF_OPTIONAL_PACKAGES = (
    "docling",
    "docling_ibm_models",
    "transformers.models.rt_detr_v2",
)
PDF_OPTIONAL_DATA_PACKAGES = ("docling_parse",)
PDF_OPTIONAL_METADATA = (
    "docling",
    "docling-core",
    "docling-ibm-models",
    "docling-parse",
)
EMBEDDING_REQUIRED_PACKAGES = ("sentence_transformers",)


@dataclass(frozen=True)
class SidecarTarget:
    sidecar_name: str
    entrypoint: str
    hidden_imports: tuple[str, ...] = ()
    collect_submodules: tuple[str, ...] = ()
    collect_data: tuple[str, ...] = ()
    copy_metadata: tuple[str, ...] = ()
    excluded_modules: tuple[str, ...] = ()


PDF_PIPELINE_HIDDEN_IMPORTS = (
    "paper_engine.storage.migrations",
    "paper_engine.pdf.backends.base",
    "paper_engine.pdf.backends.docling",
    "paper_engine.pdf.backends.legacy",
    "paper_engine.pdf.backends.llamaparse",
    "paper_engine.pdf.backends.pymupdf4llm",
    "paper_engine.pdf.chunking",
    "paper_engine.pdf.models",
    "paper_engine.pdf.persistence",
    "paper_engine.pdf.profile",
    "paper_engine.pdf.router",
    "pymupdf",
    "pymupdf4llm",
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
)
ANALYSIS_PIPELINE_HIDDEN_IMPORTS = (
    "paper_engine.analysis.models",
    "paper_engine.analysis.pipeline",
    "paper_engine.analysis.prompts",
    "paper_engine.analysis.verifier",
)
RETRIEVAL_HIDDEN_IMPORTS = (
    "paper_engine.retrieval.embeddings",
    "paper_engine.retrieval.hybrid",
    "paper_engine.retrieval.lexical",
)
API_HIDDEN_IMPORTS = (
    "paper_engine.api.app",
    "paper_engine.agent.executor",
    *ANALYSIS_PIPELINE_HIDDEN_IMPORTS,
    "paper_engine.core.config",
    "paper_engine.storage.database",
    "paper_engine.agent.llm_client",
    "paper_engine.pdf.compat",
    *PDF_PIPELINE_HIDDEN_IMPORTS,
    *RETRIEVAL_HIDDEN_IMPORTS,
    "paper_engine.api.routes.agent",
    "paper_engine.api.routes.papers",
    "paper_engine.api.routes.search",
    "paper_engine.api.routes.spaces",
    "multipart",
    "multipart.multipart",
)
MCP_HIDDEN_IMPORTS = (
    "paper_engine.core.config",
    "paper_engine.storage.database",
    "paper_engine.storage.migrations",
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
    collect_data: tuple[str, ...] = (),
    copy_metadata: tuple[str, ...] = (),
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
    for package in collect_data:
        command.extend(["--collect-data", package])
    for distribution in copy_metadata:
        command.extend(["--copy-metadata", distribution])
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
    api_optional_collections, api_exclusions = _optional_dependency_args(
        PDF_OPTIONAL_PACKAGES
    )
    api_optional_data, _api_data_exclusions = _optional_dependency_args(
        PDF_OPTIONAL_DATA_PACKAGES
    )
    api_optional_metadata = _optional_metadata_args(PDF_OPTIONAL_METADATA)
    api_collections = _dedupe(
        (*api_optional_collections, *EMBEDDING_REQUIRED_PACKAGES)
    )
    mcp_collections = EMBEDDING_REQUIRED_PACKAGES
    mcp_exclusions = PDF_OPTIONAL_PACKAGES

    targets: list[SidecarTarget] = []
    if target in {"api", "all"}:
        targets.append(
            SidecarTarget(
                sidecar_name="paper-engine-api",
                entrypoint="paper_engine/sidecar/api.py",
                hidden_imports=API_HIDDEN_IMPORTS,
                collect_submodules=api_collections,
                collect_data=api_optional_data,
                copy_metadata=api_optional_metadata,
                excluded_modules=api_exclusions,
            )
        )
    if target in {"mcp", "all"}:
        targets.append(
            SidecarTarget(
                sidecar_name="paper-engine-mcp",
                entrypoint="paper_engine/mcp/server.py",
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


def _optional_metadata_args(distribution_names: tuple[str, ...]) -> tuple[str, ...]:
    collected: list[str] = []
    for distribution_name in distribution_names:
        try:
            importlib.metadata.distribution(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            continue
        collected.append(distribution_name)
    return _dedupe(collected)


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
            sidecar_target.collect_data,
            sidecar_target.copy_metadata,
            sidecar_target.excluded_modules,
        )
        packaged = copy_for_tauri(binary, sidecar_target.sidecar_name, target_triple)
        print(f"Built {packaged}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
