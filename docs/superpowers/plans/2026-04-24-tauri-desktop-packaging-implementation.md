# Tauri Desktop Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a distributable Tauri desktop app with a polished Chinese three-column research workspace, a PyInstaller-packaged Python API sidecar, macOS `.dmg` output, and Windows packaging hooks.

**Architecture:** Keep the existing Python FastAPI backend as the source of truth for spaces, papers, search, cards, and MCP. Add a React/Vite desktop frontend and a Tauri 2 shell that starts the Python API sidecar on app launch, passes a system app-data directory, and exposes the backend URL to the UI.

**Tech Stack:** Tauri 2, React, Vite, TypeScript, Rust, PyInstaller, FastAPI, SQLite, PyMuPDF, pytest, mypy.

---

## Scope Check

This is one migration plan with five connected slices:

- React/Vite UI replacement for `static/index.html`.
- Tauri shell and process lifecycle.
- Python API/MCP sidecar packaging.
- macOS `.dmg` build path with Windows build hooks.
- Product wording correction for domain-neutral paper structure and heuristic extraction.

The slices are sequential because Tauri build depends on frontend build, and sidecar packaging depends on the Python entry points.

## File Structure

Create:

- `api_sidecar.py` - CLI entry point for the packaged FastAPI API server.
- `scripts/build_sidecars.py` - builds PyInstaller sidecars and applies Tauri target-triple suffixes.
- `frontend/package.json` - React/Vite frontend package.
- `frontend/index.html` - Vite HTML entry.
- `frontend/tsconfig.json` - TypeScript configuration.
- `frontend/tsconfig.node.json` - Vite config TypeScript configuration.
- `frontend/vite.config.ts` - Vite build/dev configuration.
- `frontend/src/main.tsx` - React app entry.
- `frontend/src/App.tsx` - desktop workspace shell.
- `frontend/src/api.ts` - typed HTTP client for existing FastAPI routes.
- `frontend/src/types.ts` - shared UI/API types.
- `frontend/src/styles.css` - high-density Chinese research-workbench styling.
- `src-tauri/Cargo.toml` - Tauri Rust crate dependencies.
- `src-tauri/build.rs` - Tauri build script.
- `src-tauri/tauri.conf.json` - Tauri app and bundle config.
- `src-tauri/capabilities/default.json` - default desktop capability set.
- `src-tauri/src/main.rs` - Tauri startup, sidecar lifecycle, commands.
- `src-tauri/binaries/.gitkeep` - keeps sidecar binary directory in git.
- `docs/packaging.md` - distribution, DMG, Windows, and MCP configuration notes.
- `tests/test_api_sidecar.py` - API sidecar CLI/settings tests.
- `tests/test_card_extractor_domain_neutral.py` - heuristic extraction positioning tests.

Modify:

- `pyproject.toml` - add PyInstaller dev dependency and API sidecar script.
- `Makefile` - add frontend, Tauri, sidecar, and package commands.
- `card_extractor.py` - broaden generic heuristic keywords and lower confidence wording.
- `routes_cards.py` - return extraction metadata that calls results heuristic.
- `docs/product-overview.md` - update UI, packaging, sidecar, and heuristic extraction wording.

Existing `static/index.html` remains as a fallback until the Tauri UI is verified. It is not the main desktop UI after this plan.

---

### Task 1: Add Python API Sidecar Entry Point

**Files:**
- Create: `api_sidecar.py`
- Create: `tests/test_api_sidecar.py`
- Modify: `pyproject.toml`
- Test: `tests/test_api_sidecar.py`

- [ ] **Step 1: Write the failing sidecar settings tests**

Create `tests/test_api_sidecar.py`:

```python
"""Tests for packaged API sidecar entry point."""

from pathlib import Path

from api_sidecar import ServerSettings, parse_args


def test_parse_args_uses_defaults() -> None:
    settings = parse_args([])
    assert settings == ServerSettings(
        host="127.0.0.1",
        port=8765,
        data_dir=None,
    )


def test_parse_args_accepts_port_and_data_dir(tmp_path: Path) -> None:
    settings = parse_args([
        "--host",
        "127.0.0.1",
        "--port",
        "9412",
        "--data-dir",
        str(tmp_path),
    ])
    assert settings.host == "127.0.0.1"
    assert settings.port == 9412
    assert settings.data_dir == tmp_path.resolve()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_api_sidecar.py
```

Expected: FAIL because `api_sidecar.py` does not exist.

- [ ] **Step 3: Implement the API sidecar entry**

Create `api_sidecar.py`:

```python
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
```

- [ ] **Step 4: Add the console script and PyInstaller dependency**

Modify `pyproject.toml`:

```toml
[project.scripts]
paper-engine-api = "api_sidecar:main"
paper-engine-mcp = "mcp_server:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23.0",
    "mypy>=1.8",
    "httpx>=0.27.0",
    "pyinstaller>=6.0",
]
```

- [ ] **Step 5: Run the sidecar tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_api_sidecar.py
```

Expected: PASS.

- [ ] **Step 6: Run typecheck for the new file**

Run:

```bash
.venv/bin/python -m mypy api_sidecar.py tests/test_api_sidecar.py
```

Expected: Success with no issues.

- [ ] **Step 7: Commit**

```bash
git add api_sidecar.py tests/test_api_sidecar.py pyproject.toml
git commit -m "feat: add packaged API sidecar entry"
```

---

### Task 2: Reframe Automatic Extraction as Domain-Neutral Heuristics

**Files:**
- Create: `tests/test_card_extractor_domain_neutral.py`
- Modify: `card_extractor.py`
- Modify: `routes_cards.py`
- Modify: `docs/product-overview.md`
- Test: `tests/test_card_extractor_domain_neutral.py`, `tests/test_routes_cards.py`

- [ ] **Step 1: Write the failing domain-neutral extraction tests**

Create `tests/test_card_extractor_domain_neutral.py`:

```python
"""Domain-neutral heuristic extraction tests."""

from card_extractor import extract_cards_from_passages


def test_extracts_generic_protocol_and_measurement_language() -> None:
    passages = [
        {
            "id": "p1",
            "original_text": "The protocol measures sample stability after synthesis.",
        }
    ]

    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    card_types = {card["card_type"] for card in cards}

    assert "Method" in card_types
    assert "Metric" in card_types
    assert all(card["confidence"] <= 0.55 for card in cards)


def test_extracts_intervention_and_statistical_test_language() -> None:
    passages = [
        {
            "id": "p2",
            "original_text": "The cohort received an intervention and the statistical test showed a significant result.",
        }
    ]

    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    card_types = {card["card_type"] for card in cards}

    assert "Method" in card_types
    assert "Result" in card_types
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_card_extractor_domain_neutral.py
```

Expected: FAIL because the current keyword lists do not cover the generic terms and current confidence values are too high.

- [ ] **Step 3: Update the heuristic keyword lists and confidence values**

Modify `card_extractor.py`:

```python
"""Rules-based, domain-neutral heuristic knowledge card extraction from passages."""

import uuid
from typing import Any

METHOD_KEYWORDS = [
    "method", "approach", "architecture", "algorithm", "protocol",
    "pipeline", "framework", "model", "we propose", "we present",
    "we introduce", "we develop", "we design", "procedure", "workflow",
    "intervention", "assay", "synthesis", "experiment", "experimental setup",
]

RESULT_KEYWORDS = [
    "result", "achieve", "outperform", "accuracy", "performance",
    "improve", "increase", "decrease", "score", "f1", "bleu",
    "we obtain", "we report", "shows that", "demonstrates", "significant",
    "statistical test", "yield", "stability", "effect size", "outcome",
]

LIMITATION_KEYWORDS = [
    "limitation", "limited", "future work", "we acknowledge",
    "drawback", "shortcoming", "does not", "fail", "however",
    "although", "despite", "constraint", "bias", "confounder",
    "uncertainty", "threat to validity",
]

METRIC_KEYWORDS = [
    "metric", "measure", "evaluate", "accuracy", "precision",
    "recall", "f1", "bleu", "rouge", "perplexity", "auc",
    "rmse", "mae", "map", "ndcg", "measurement", "endpoint",
    "statistical test", "p-value", "confidence interval", "sample",
    "cohort", "yield", "stability",
]

HEURISTIC_CONFIDENCE = {
    "Method": 0.55,
    "Result": 0.5,
    "Limitation": 0.5,
    "Metric": 0.5,
}


def extract_cards_from_passages(
    passages: list[dict[str, Any]],
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    """Extract low-confidence heuristic cards from passages.

    The extractor is domain-neutral. It does not claim to understand a scientific
    field; users should review and edit generated cards.
    """
    cards: list[dict[str, Any]] = []

    for passage in passages:
        text = passage.get("original_text", "")
        text_lower = text.lower()
        passage_id = str(passage["id"])

        if any(kw in text_lower for kw in METHOD_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Method",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Method"],
            ))

        if any(kw in text_lower for kw in RESULT_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Result",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Result"],
            ))

        if any(kw in text_lower for kw in LIMITATION_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Limitation",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Limitation"],
            ))

        if any(kw in text_lower for kw in METRIC_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Metric",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Metric"],
            ))

    return cards


def _make_card(
    space_id: str,
    paper_id: str,
    passage_id: str,
    card_type: str,
    summary: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "space_id": space_id,
        "paper_id": paper_id,
        "source_passage_id": passage_id,
        "card_type": card_type,
        "summary": summary[:500],
        "confidence": confidence,
        "user_edited": 0,
    }


def _first_sentence(text: str) -> str:
    """Extract the first sentence from text."""
    for delim in [". ", ".\n", ".  "]:
        idx = text.find(delim)
        if idx > 0:
            return text[:idx + 1].strip()
    return text[:200].strip()
```

- [ ] **Step 4: Return extraction metadata from the API**

Modify the return value in `routes_cards.py` inside `extract_cards`:

```python
return {
    "status": "extracted",
    "paper_id": paper_id,
    "card_count": len(cards),
    "mode": "heuristic",
    "message": "启发式抽取结果需要人工检查和修正。",
}
```

Keep the existing `no_passages` response as:

```python
return {
    "status": "no_passages",
    "paper_id": paper_id,
    "card_count": 0,
    "mode": "heuristic",
    "message": "没有可抽取的原文片段。",
}
```

- [ ] **Step 5: Update product wording**

Modify `docs/product-overview.md` in the Knowledge Card section:

```markdown
当前自动抽取是领域无关的启发式抽取。它会根据通用科研表达生成低置信度 knowledge cards，但不声明系统自动理解所有科研领域。用户应检查、编辑并保留有价值的卡片。
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_card_extractor_domain_neutral.py tests/test_routes_cards.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add card_extractor.py routes_cards.py docs/product-overview.md tests/test_card_extractor_domain_neutral.py
git commit -m "feat: mark card extraction as domain-neutral heuristic"
```

---

### Task 3: Add PyInstaller Sidecar Build Script

**Files:**
- Create: `scripts/build_sidecars.py`
- Modify: `Makefile`
- Test: `scripts/build_sidecars.py`

- [ ] **Step 1: Write the sidecar build script**

Create `scripts/build_sidecars.py`:

```python
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


def build_onefile(name: str, entrypoint: str) -> Path:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--name",
            name,
            entrypoint,
        ],
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
    targets = []
    if args.target in {"api", "all"}:
        targets.append(("paper-engine-api", "api_sidecar.py"))
    if args.target in {"mcp", "all"}:
        targets.append(("paper-engine-mcp", "mcp_server.py"))

    for sidecar_name, entrypoint in targets:
        binary = build_onefile(sidecar_name, entrypoint)
        packaged = copy_for_tauri(binary, sidecar_name, target_triple)
        print(f"Built {packaged}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Add Makefile commands**

Modify `Makefile`:

```makefile
.PHONY: dev test typecheck install check frontend-install frontend-dev frontend-build tauri-dev build-sidecars tauri-build package-macos

install:
	pip install -e ".[dev]"

dev:
	uvicorn main:app --reload --host 127.0.0.1 --port 8000

test:
	pytest -v

typecheck:
	mypy main.py api_sidecar.py tests/

check: typecheck test
	@echo "All checks passed!"

frontend-install:
	npm --prefix frontend install

frontend-dev:
	npm --prefix frontend run dev

frontend-build:
	npm --prefix frontend run build

tauri-dev:
	npm run tauri dev

build-sidecars:
	python scripts/build_sidecars.py --target all

tauri-build: frontend-build build-sidecars
	npm run tauri build

package-macos: tauri-build
	@echo "DMG output is under src-tauri/target/release/bundle/dmg"
```

- [ ] **Step 3: Syntax-check the script**

Run:

```bash
.venv/bin/python -m py_compile scripts/build_sidecars.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_sidecars.py Makefile
git commit -m "build: add PyInstaller sidecar packaging script"
```

---

### Task 4: Scaffold React/Vite Desktop Frontend

**Files:**
- Create: `package.json`
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Test: `frontend` build and typecheck

- [ ] **Step 1: Create root package scripts**

Create `package.json`:

```json
{
  "name": "paper-knowledge-engine-desktop",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "frontend:dev": "npm --prefix frontend run dev",
    "frontend:build": "npm --prefix frontend run build",
    "frontend:typecheck": "npm --prefix frontend run typecheck",
    "tauri": "tauri",
    "tauri:dev": "tauri dev",
    "tauri:build": "tauri build"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2.0.0"
  }
}
```

- [ ] **Step 2: Create frontend package files**

Create `frontend/package.json`:

```json
{
  "name": "paper-knowledge-engine-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 1420",
    "build": "tsc --noEmit && vite build",
    "typecheck": "tsc --noEmit",
    "preview": "vite preview --host 127.0.0.1 --port 1421"
  },
  "dependencies": {
    "@tauri-apps/api": "^2.0.0",
    "lucide-react": "^0.468.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.6.0",
    "vite": "^6.0.0"
  }
}
```

Create `frontend/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>本地论文知识引擎</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Create `frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

Create `frontend/vite.config.ts`:

```ts
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
```

- [ ] **Step 3: Create frontend types and API client**

Create `frontend/src/types.ts`:

```ts
export type SpaceStatus = 'active' | 'archived' | 'deleted';
export type ParseStatus = 'pending' | 'parsing' | 'parsed' | 'error';

export interface Space {
  id: string;
  name: string;
  description: string;
  status: SpaceStatus;
  created_at: string;
  updated_at: string;
}

export interface Paper {
  id: string;
  space_id: string;
  title: string;
  authors: string;
  year: number | null;
  doi: string;
  arxiv_id: string;
  pubmed_id: string;
  venue: string;
  abstract: string;
  citation: string;
  user_tags: string;
  relation_to_idea: string;
  file_path: string;
  file_hash: string;
  imported_at: string;
  parse_status: ParseStatus;
}

export interface Passage {
  id: string;
  paper_id: string;
  space_id: string;
  section: string;
  page_number: number;
  paragraph_index: number;
  original_text: string;
  parse_confidence: number;
  passage_type: string;
}

export interface KnowledgeCard {
  id: string;
  space_id: string;
  paper_id: string;
  source_passage_id: string | null;
  card_type: string;
  summary: string;
  confidence: number;
  user_edited: number;
  created_at: string;
  updated_at: string;
}

export interface AgentStatus {
  enabled: boolean;
  server_name: string;
  transport: string;
  active_space: Space | null;
}

export interface SearchResult {
  score: number;
  passage_id: string;
  paper_id: string;
  section: string;
  page_number: number;
  paragraph_index: number;
  snippet: string;
  original_text: string;
  paper_title: string;
}
```

Create `frontend/src/api.ts`:

```ts
import type { AgentStatus, KnowledgeCard, Paper, Passage, SearchResult, Space } from './types';

const DEFAULT_BACKEND = 'http://127.0.0.1:8000';

export function backendBaseUrl(): string {
  return window.localStorage.getItem('paper-engine-backend-url') || DEFAULT_BACKEND;
}

export function setBackendBaseUrl(url: string): void {
  window.localStorage.setItem('paper-engine-backend-url', url.replace(/\/$/, ''));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${backendBaseUrl()}${path}`, {
    headers: init?.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `请求失败：${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; service: string; version: string }>('/health'),
  listSpaces: () => request<Space[]>('/api/spaces'),
  createSpace: (name: string, description: string) =>
    request<Space>('/api/spaces', { method: 'POST', body: JSON.stringify({ name, description }) }),
  setActiveSpace: (spaceId: string) =>
    request<{ active_space_id: string; space: Space }>(`/api/spaces/active/${spaceId}`, { method: 'PUT' }),
  getActiveSpace: () => request<Space>('/api/spaces/active'),
  listPapers: () => request<Paper[]>('/api/papers'),
  getPaper: (paperId: string) => request<Paper>(`/api/papers/${paperId}`),
  updatePaper: (paperId: string, body: Partial<Paper>) =>
    request<Paper>(`/api/papers/${paperId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  uploadPaper: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Paper>('/api/papers/upload', { method: 'POST', body });
  },
  parsePaper: (paperId: string) =>
    request<{ status: string; paper_id: string; passage_count: number }>(`/api/papers/${paperId}/parse`, { method: 'POST' }),
  listPassages: (paperId: string) => request<Passage[]>(`/api/papers/${paperId}/passages`),
  listCards: (paperId?: string, cardType?: string) => {
    const params = new URLSearchParams();
    if (paperId) params.set('paper_id', paperId);
    if (cardType) params.set('card_type', cardType);
    const query = params.toString();
    return request<KnowledgeCard[]>(`/api/cards${query ? `?${query}` : ''}`);
  },
  extractCards: (paperId: string) =>
    request<{ status: string; paper_id: string; card_count: number; mode?: string; message?: string }>(
      `/api/cards/extract/${paperId}`,
      { method: 'POST' },
    ),
  search: (q: string) => request<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}&limit=30`),
  agentStatus: () => request<AgentStatus>('/api/agent/status'),
  setAgentStatus: (enabled: boolean) =>
    request<{ enabled: boolean }>('/api/agent/status', { method: 'PUT', body: JSON.stringify({ enabled }) }),
};
```

- [ ] **Step 4: Create the React entry**

Create `frontend/src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 5: Create the first desktop UI shell**

Create `frontend/src/App.tsx`:

```tsx
import { FileText, FolderOpen, Plug, Search, ShieldCheck, UploadCloud } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type { AgentStatus, KnowledgeCard, Paper, Passage, SearchResult, Space } from './types';

const cardTabs = ['Method', 'Metric', 'Result', 'Failure Mode', 'Limitation', 'Claim'] as const;

function cardLabel(type: string): string {
  const labels: Record<string, string> = {
    Method: '方法',
    Metric: '指标',
    Result: '结果',
    'Failure Mode': '失败模式',
    Limitation: '局限性',
    Claim: '主张',
    Evidence: '证据',
    Problem: '问题',
    Object: '研究对象',
    Variable: '变量',
    Interpretation: '解释',
    'Practical Tip': '实践建议',
  };
  return labels[type] || type;
}

function parseLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待解析',
    parsing: '解析中',
    parsed: '已解析',
    error: '解析失败',
  };
  return labels[status] || status;
}

export default function App(): JSX.Element {
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [activeSpace, setActiveSpace] = useState<Space | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [selectedPaper, setSelectedPaper] = useState<Paper | null>(null);
  const [passages, setPassages] = useState<Passage[]>([]);
  const [cards, setCards] = useState<KnowledgeCard[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [activeTab, setActiveTab] = useState<(typeof cardTabs)[number]>('Method');
  const [newSpaceName, setNewSpaceName] = useState('');
  const [newSpaceDescription, setNewSpaceDescription] = useState('');
  const [notice, setNotice] = useState('');

  const visibleCards = useMemo(
    () => cards.filter((card) => card.card_type === activeTab),
    [cards, activeTab],
  );

  async function refresh(): Promise<void> {
    const loadedSpaces = await api.listSpaces();
    setSpaces(loadedSpaces);
    try {
      const active = await api.getActiveSpace();
      setActiveSpace(active);
      const loadedPapers = await api.listPapers();
      setPapers(loadedPapers);
      const status = await api.agentStatus();
      setAgentStatus(status);
    } catch {
      setActiveSpace(null);
      setPapers([]);
    }
  }

  async function openPaper(paper: Paper): Promise<void> {
    setSelectedPaper(paper);
    const [paperPassages, paperCards] = await Promise.all([
      api.listPassages(paper.id),
      api.listCards(paper.id),
    ]);
    setPassages(paperPassages);
    setCards(paperCards);
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function createSpace(): Promise<void> {
    if (!newSpaceName.trim()) {
      setNotice('请输入空间名称。');
      return;
    }
    const space = await api.createSpace(newSpaceName.trim(), newSpaceDescription.trim());
    await api.setActiveSpace(space.id);
    setNewSpaceName('');
    setNewSpaceDescription('');
    setNotice('已创建并打开空间。');
    await refresh();
  }

  async function setActive(space: Space): Promise<void> {
    await api.setActiveSpace(space.id);
    setSelectedPaper(null);
    setPassages([]);
    setCards([]);
    await refresh();
  }

  async function upload(file: File): Promise<void> {
    await api.uploadPaper(file);
    setNotice('论文已导入。');
    await refresh();
  }

  async function runSearch(): Promise<void> {
    if (!query.trim()) return;
    const searchResults = await api.search(query.trim());
    setResults(searchResults);
  }

  async function parseSelected(): Promise<void> {
    if (!selectedPaper) return;
    const parsed = await api.parsePaper(selectedPaper.id);
    setNotice(`解析完成：${parsed.passage_count} 个片段。`);
    const updated = await api.getPaper(selectedPaper.id);
    await openPaper(updated);
    await refresh();
  }

  async function extractSelected(): Promise<void> {
    if (!selectedPaper) return;
    const extracted = await api.extractCards(selectedPaper.id);
    setNotice(extracted.message || `启发式抽取完成：${extracted.card_count} 张卡片。`);
    const paperCards = await api.listCards(selectedPaper.id);
    setCards(paperCards);
  }

  async function toggleAgent(): Promise<void> {
    const enabled = agentStatus ? !agentStatus.enabled : true;
    await api.setAgentStatus(enabled);
    setAgentStatus(await api.agentStatus());
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">P</div>
          <div>
            <h1>本地论文知识引擎</h1>
            <p>Idea Space 文献工作台</p>
          </div>
        </div>

        <section className="new-space">
          <label>新空间</label>
          <input value={newSpaceName} onChange={(e) => setNewSpaceName(e.target.value)} placeholder="例如：小样本鲁棒性" />
          <textarea value={newSpaceDescription} onChange={(e) => setNewSpaceDescription(e.target.value)} placeholder="研究目标、假设或约束" />
          <button onClick={() => void createSpace()}>创建空间</button>
        </section>

        <nav className="space-list">
          {spaces.map((space) => (
            <button
              key={space.id}
              className={space.id === activeSpace?.id ? 'space-item active' : 'space-item'}
              onClick={() => void setActive(space)}
            >
              <FolderOpen size={16} />
              <span>{space.name}</span>
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">当前空间</p>
            <h2>{activeSpace?.name || '未选择空间'}</h2>
          </div>
          <div className="topbar-actions">
            <button className={agentStatus?.enabled ? 'status enabled' : 'status'} onClick={() => void toggleAgent()}>
              <Plug size={16} />
              {agentStatus?.enabled ? '智能代理已启用' : '智能代理未启用'}
            </button>
          </div>
        </header>

        <section className="command-row">
          <label className="dropzone">
            <UploadCloud size={20} />
            <span>拖拽或选择 PDF</span>
            <input type="file" accept="application/pdf,.pdf" onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
          </label>
          <div className="searchbox">
            <Search size={18} />
            <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void runSearch()} placeholder="搜索方法、指标、结果或局限性" />
            <button onClick={() => void runSearch()}>检索</button>
          </div>
        </section>

        {notice && <div className="notice">{notice}</div>}

        <section className="content-grid">
          <div className="panel papers-panel">
            <div className="panel-header">
              <h3>论文列表</h3>
              <span>{papers.length} 篇</span>
            </div>
            <div className="paper-list">
              {papers.map((paper) => (
                <button key={paper.id} className={selectedPaper?.id === paper.id ? 'paper-row active' : 'paper-row'} onClick={() => void openPaper(paper)}>
                  <FileText size={18} />
                  <span>
                    <strong>{paper.title || '未命名论文'}</strong>
                    <small>{paper.authors || '作者未填写'} · {parseLabel(paper.parse_status)}</small>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="panel results-panel">
            <div className="panel-header">
              <h3>文献检索</h3>
              <span>{results.length} 条结果</span>
            </div>
            <div className="result-list">
              {results.map((result) => (
                <article key={result.passage_id} className="result-item">
                  <strong>{result.paper_title || result.paper_id}</strong>
                  <p dangerouslySetInnerHTML={{ __html: result.snippet }} />
                  <small>第 {result.page_number} 页 · {result.section}</small>
                </article>
              ))}
            </div>
          </div>
        </section>
      </section>

      <aside className="inspector">
        <div className="inspector-header">
          <p className="eyebrow">论文 Inspector</p>
          <h2>{selectedPaper?.title || '选择一篇论文'}</h2>
        </div>
        {selectedPaper ? (
          <>
            <div className="meta-list">
              <span>作者：{selectedPaper.authors || '未填写'}</span>
              <span>年份：{selectedPaper.year || '未填写'}</span>
              <span>关系：{selectedPaper.relation_to_idea}</span>
              <span>状态：{parseLabel(selectedPaper.parse_status)}</span>
            </div>
            <div className="inspector-actions">
              <button onClick={() => void parseSelected()}>解析 PDF</button>
              <button onClick={() => void extractSelected()}>
                <ShieldCheck size={16} />
                启发式抽取
              </button>
            </div>
            <div className="tabs">
              {cardTabs.map((tab) => (
                <button key={tab} className={tab === activeTab ? 'active' : ''} onClick={() => setActiveTab(tab)}>
                  {cardLabel(tab)}
                </button>
              ))}
            </div>
            <div className="card-list">
              {visibleCards.map((card) => (
                <article key={card.id} className="knowledge-card">
                  <strong>{cardLabel(card.card_type)}</strong>
                  <p>{card.summary}</p>
                  <small>置信度 {card.confidence.toFixed(2)} · {card.source_passage_id ? '有来源' : '手动卡片'}</small>
                </article>
              ))}
            </div>
            <div className="passage-preview">
              <h3>原文片段</h3>
              {passages.slice(0, 6).map((passage) => (
                <p key={passage.id}>{passage.original_text}</p>
              ))}
            </div>
          </>
        ) : (
          <p className="empty">从论文列表选择一篇论文查看详情。</p>
        )}
      </aside>
    </main>
  );
}
```

- [ ] **Step 6: Create the first desktop CSS**

Create `frontend/src/styles.css` with this first working version:

```css
:root {
  color: #141719;
  background: #f4f1eb;
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}

* { box-sizing: border-box; }
body { margin: 0; min-width: 1120px; min-height: 720px; background: #f4f1eb; }
button, input, textarea { font: inherit; }
button { cursor: pointer; }

.app-shell {
  display: grid;
  grid-template-columns: 280px minmax(560px, 1fr) 360px;
  min-height: 100vh;
}

.sidebar {
  background: #171b1d;
  color: #f7f2e8;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: #d7ff72;
  color: #111;
  font-weight: 800;
}
.brand h1 { font-size: 16px; margin: 0; }
.brand p { margin: 3px 0 0; color: #aeb6b8; font-size: 12px; }

.new-space {
  border: 1px solid rgba(255,255,255,0.12);
  padding: 14px;
  border-radius: 8px;
  display: grid;
  gap: 10px;
}
.new-space label { font-size: 12px; color: #aeb6b8; }
.new-space input, .new-space textarea {
  width: 100%;
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 7px;
  padding: 9px 10px;
  color: #f7f2e8;
  background: rgba(255,255,255,0.06);
}
.new-space textarea { resize: vertical; min-height: 68px; }
.new-space button, .topbar button, .searchbox button, .inspector-actions button {
  border: 0;
  border-radius: 7px;
  padding: 9px 12px;
  background: #155e75;
  color: white;
  font-weight: 650;
}

.space-list { display: grid; gap: 8px; }
.space-item {
  border: 0;
  width: 100%;
  text-align: left;
  display: flex;
  align-items: center;
  gap: 9px;
  color: #d8dedf;
  background: transparent;
  padding: 10px;
  border-radius: 7px;
}
.space-item.active { background: rgba(215,255,114,0.14); color: #fff; }

.workspace { padding: 22px; display: flex; flex-direction: column; gap: 18px; }
.topbar, .command-row, .panel, .inspector {
  background: rgba(255,255,255,0.78);
  border: 1px solid rgba(28,31,32,0.1);
  border-radius: 8px;
}
.topbar { padding: 18px; display: flex; justify-content: space-between; align-items: center; }
.eyebrow { margin: 0 0 4px; font-size: 12px; color: #667174; }
.topbar h2, .inspector h2 { margin: 0; font-size: 21px; }
.status { display: inline-flex; align-items: center; gap: 8px; background: #6b7280 !important; }
.status.enabled { background: #0f766e !important; }

.command-row { padding: 14px; display: grid; grid-template-columns: 210px 1fr; gap: 12px; }
.dropzone {
  border: 1px dashed #7a8588;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #334155;
}
.dropzone input { display: none; }
.searchbox { display: grid; grid-template-columns: 22px 1fr 72px; align-items: center; gap: 8px; }
.searchbox input {
  border: 1px solid #ccd2d4;
  border-radius: 7px;
  padding: 10px;
  background: white;
}

.notice {
  background: #ecfccb;
  color: #365314;
  border: 1px solid #bef264;
  border-radius: 7px;
  padding: 10px 12px;
}

.content-grid { display: grid; grid-template-columns: 42% 58%; gap: 18px; min-height: 0; }
.panel { min-height: 420px; overflow: hidden; }
.panel-header { padding: 14px 16px; display: flex; justify-content: space-between; border-bottom: 1px solid #e4e4df; }
.panel-header h3 { margin: 0; }
.paper-list, .result-list { padding: 10px; display: grid; gap: 8px; max-height: 580px; overflow: auto; }
.paper-row {
  border: 1px solid transparent;
  background: #fbfaf6;
  display: flex;
  gap: 10px;
  text-align: left;
  padding: 12px;
  border-radius: 7px;
}
.paper-row.active { border-color: #155e75; background: #eefafa; }
.paper-row strong { display: block; font-size: 14px; }
.paper-row small { color: #697174; }
.result-item { background: #fbfaf6; padding: 12px; border-radius: 7px; border: 1px solid #e7e1d7; }
.result-item p { color: #394245; line-height: 1.5; }
.result-item mark { background: #d7ff72; padding: 0 2px; }

.inspector {
  border-radius: 0;
  border-top: 0;
  border-right: 0;
  border-bottom: 0;
  padding: 22px;
  overflow: auto;
}
.inspector-header { margin-bottom: 18px; }
.meta-list { display: grid; gap: 7px; color: #485154; font-size: 13px; }
.inspector-actions { display: flex; gap: 8px; margin: 16px 0; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.tabs button {
  border: 1px solid #d7d7d1;
  background: #fbfaf6;
  border-radius: 7px;
  padding: 7px 9px;
}
.tabs button.active { background: #171b1d; color: white; }
.card-list, .passage-preview { display: grid; gap: 10px; }
.knowledge-card { border: 1px solid #e5ded2; border-radius: 7px; padding: 11px; background: #fbfaf6; }
.knowledge-card p, .passage-preview p { color: #3f484b; line-height: 1.5; }
.knowledge-card small { color: #6b7280; }
.empty { color: #667174; line-height: 1.6; }
```

- [ ] **Step 7: Run frontend install and build**

Run:

```bash
npm --prefix frontend install
npm --prefix frontend run build
```

Expected: `frontend/dist` is created and TypeScript build passes.

- [ ] **Step 8: Commit**

```bash
git add package.json frontend
git commit -m "feat: add React desktop frontend"
```

---

### Task 5: Add Tauri 2 Shell

**Files:**
- Create: `src-tauri/Cargo.toml`
- Create: `src-tauri/build.rs`
- Create: `src-tauri/tauri.conf.json`
- Create: `src-tauri/capabilities/default.json`
- Create: `src-tauri/src/main.rs`
- Create: `src-tauri/binaries/.gitkeep`
- Test: `cargo check --manifest-path src-tauri/Cargo.toml`

- [ ] **Step 1: Create the Tauri crate files**

Create `src-tauri/Cargo.toml`:

```toml
[package]
name = "paper-knowledge-engine"
version = "0.1.0"
description = "Local Paper Knowledge Engine"
authors = ["Local Paper Knowledge Engine"]
edition = "2021"

[lib]
name = "paper_knowledge_engine_lib"
crate-type = ["staticlib", "cdylib", "rlib"]

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tauri = { version = "2", features = [] }
tauri-plugin-shell = "2"
```

Create `src-tauri/build.rs`:

```rust
fn main() {
    tauri_build::build()
}
```

Create `src-tauri/binaries/.gitkeep`:

```txt

```

- [ ] **Step 2: Create Tauri config**

Create `src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "本地论文知识引擎",
  "version": "0.1.0",
  "identifier": "com.local.paperknowledgeengine",
  "build": {
    "beforeDevCommand": "npm --prefix frontend run dev",
    "devUrl": "http://127.0.0.1:1420",
    "beforeBuildCommand": "npm --prefix frontend run build",
    "frontendDist": "../frontend/dist"
  },
  "app": {
    "windows": [
      {
        "title": "本地论文知识引擎",
        "width": 1280,
        "height": 820,
        "minWidth": 1120,
        "minHeight": 720,
        "resizable": true
      }
    ],
    "security": {
      "csp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": ["dmg", "app", "nsis"],
    "externalBin": [
      "binaries/paper-engine-api",
      "binaries/paper-engine-mcp"
    ],
    "macOS": {
      "minimumSystemVersion": "11.0"
    }
  }
}
```

Create `src-tauri/capabilities/default.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Main desktop window capabilities",
  "windows": ["main"],
  "permissions": ["core:default"]
}
```

- [ ] **Step 3: Create Rust sidecar lifecycle**

Create `src-tauri/src/main.rs`:

```rust
use std::net::TcpListener;
use std::sync::Mutex;

use tauri::{Manager, State};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct BackendState {
    port: u16,
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
fn backend_url(state: State<BackendState>) -> String {
    format!("http://127.0.0.1:{}", state.port)
}

fn choose_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|err| err.to_string())?;
    let port = listener.local_addr().map_err(|err| err.to_string())?.port();
    drop(listener);
    Ok(port)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let port = choose_port().map_err(|err| format!("Unable to choose API port: {err}"))?;
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|err| format!("Unable to resolve app data dir: {err}"))?;
            std::fs::create_dir_all(&data_dir)
                .map_err(|err| format!("Unable to create app data dir: {err}"))?;

            let (_rx, child) = app
                .shell()
                .sidecar("paper-engine-api")
                .map_err(|err| format!("Unable to resolve API sidecar: {err}"))?
                .args([
                    "--host",
                    "127.0.0.1",
                    "--port",
                    &port.to_string(),
                    "--data-dir",
                    data_dir.to_str().ok_or("App data dir is not valid UTF-8")?,
                ])
                .spawn()
                .map_err(|err| format!("Unable to start API sidecar: {err}"))?;

            app.manage(BackendState {
                port,
                child: Mutex::new(Some(child)),
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_url])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.try_state::<BackendState>() {
                    if let Ok(mut child) = state.child.lock() {
                        if let Some(process) = child.take() {
                            let _ = process.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
```

- [ ] **Step 4: Connect frontend to Tauri backend URL**

Modify `frontend/src/api.ts`:

```ts
import { invoke } from '@tauri-apps/api/core';
import type { AgentStatus, KnowledgeCard, Paper, Passage, SearchResult, Space } from './types';

const DEFAULT_BACKEND = 'http://127.0.0.1:8000';

let cachedBackendUrl: string | null = null;

export async function initializeBackendBaseUrl(): Promise<string> {
  if (cachedBackendUrl) return cachedBackendUrl;
  try {
    const url = await invoke<string>('backend_url');
    cachedBackendUrl = url.replace(/\/$/, '');
  } catch {
    cachedBackendUrl = window.localStorage.getItem('paper-engine-backend-url') || DEFAULT_BACKEND;
  }
  return cachedBackendUrl;
}

export function setBackendBaseUrl(url: string): void {
  cachedBackendUrl = url.replace(/\/$/, '');
  window.localStorage.setItem('paper-engine-backend-url', cachedBackendUrl);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = await initializeBackendBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    headers: init?.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `请求失败：${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; service: string; version: string }>('/health'),
  listSpaces: () => request<Space[]>('/api/spaces'),
  createSpace: (name: string, description: string) =>
    request<Space>('/api/spaces', { method: 'POST', body: JSON.stringify({ name, description }) }),
  setActiveSpace: (spaceId: string) =>
    request<{ active_space_id: string; space: Space }>(`/api/spaces/active/${spaceId}`, { method: 'PUT' }),
  getActiveSpace: () => request<Space>('/api/spaces/active'),
  listPapers: () => request<Paper[]>('/api/papers'),
  getPaper: (paperId: string) => request<Paper>(`/api/papers/${paperId}`),
  updatePaper: (paperId: string, body: Partial<Paper>) =>
    request<Paper>(`/api/papers/${paperId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  uploadPaper: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Paper>('/api/papers/upload', { method: 'POST', body });
  },
  parsePaper: (paperId: string) =>
    request<{ status: string; paper_id: string; passage_count: number }>(`/api/papers/${paperId}/parse`, { method: 'POST' }),
  listPassages: (paperId: string) => request<Passage[]>(`/api/papers/${paperId}/passages`),
  listCards: (paperId?: string, cardType?: string) => {
    const params = new URLSearchParams();
    if (paperId) params.set('paper_id', paperId);
    if (cardType) params.set('card_type', cardType);
    const query = params.toString();
    return request<KnowledgeCard[]>(`/api/cards${query ? `?${query}` : ''}`);
  },
  extractCards: (paperId: string) =>
    request<{ status: string; paper_id: string; card_count: number; mode?: string; message?: string }>(
      `/api/cards/extract/${paperId}`,
      { method: 'POST' },
    ),
  search: (q: string) => request<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}&limit=30`),
  agentStatus: () => request<AgentStatus>('/api/agent/status'),
  setAgentStatus: (enabled: boolean) =>
    request<{ enabled: boolean }>('/api/agent/status', { method: 'PUT', body: JSON.stringify({ enabled }) }),
};
```

- [ ] **Step 5: Build frontend and check Rust**

Run:

```bash
npm install
npm --prefix frontend install
npm --prefix frontend run build
cargo check --manifest-path src-tauri/Cargo.toml
```

Expected: frontend build succeeds and Rust compiles.

- [ ] **Step 6: Commit**

```bash
git add package.json frontend/src/api.ts src-tauri
git commit -m "feat: add Tauri desktop shell"
```

---

### Task 6: Build and Bundle Sidecars

**Files:**
- Modify: `src-tauri/tauri.conf.json`
- Modify: `docs/packaging.md`
- Test: sidecar binaries exist with target triple suffix

- [ ] **Step 1: Install PyInstaller dependency**

Run:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

Expected: `pyinstaller` is importable.

- [ ] **Step 2: Build sidecars**

Run:

```bash
.venv/bin/python scripts/build_sidecars.py --target all
```

Expected output includes:

```txt
Built /Users/ljn/personal_ai_paper/src-tauri/binaries/paper-engine-api-<target-triple>
Built /Users/ljn/personal_ai_paper/src-tauri/binaries/paper-engine-mcp-<target-triple>
```

- [ ] **Step 3: Verify sidecar files exist**

Run:

```bash
ls -la src-tauri/binaries
```

Expected: two executable files with the host target triple suffix.

- [ ] **Step 4: Start API sidecar manually**

Run:

```bash
./src-tauri/binaries/paper-engine-api-$(rustc --print host-tuple) --host 127.0.0.1 --port 8765 --data-dir /tmp/paper-engine-sidecar-test
```

Expected: uvicorn starts and logs that it is listening on `127.0.0.1:8765`.

In a second terminal run:

```bash
curl http://127.0.0.1:8765/health
```

Expected JSON:

```json
{"status":"healthy","service":"Local Paper Knowledge Engine","version":"0.1.0"}
```

Stop the manual sidecar with `Ctrl+C`.

- [ ] **Step 5: Create packaging docs**

Create `docs/packaging.md`:

```markdown
# Desktop Packaging

## macOS

Build sidecars and the Tauri app:

```bash
.venv/bin/python -m pip install -e ".[dev]"
npm install
npm --prefix frontend install
.venv/bin/python scripts/build_sidecars.py --target all
npm run tauri build
```

The unsigned `.dmg` is generated under:

```txt
src-tauri/target/release/bundle/dmg/
```

Unsigned builds may trigger macOS Gatekeeper warnings on other machines. Formal distribution requires Developer ID signing and notarization.

## Windows

Build Windows installers on a Windows machine or Windows CI runner:

```powershell
python -m pip install -e ".[dev]"
npm install
npm --prefix frontend install
python scripts/build_sidecars.py --target all
npm run tauri build
```

Formal distribution requires Windows code signing.

## MCP Sidecar

The packaged MCP executable is included as a Tauri sidecar named `paper-engine-mcp`.

First release behavior:

- The app does not modify Claude Code, Codex, Cursor, or other agent settings.
- Users copy the MCP executable path into their agent configuration.
- The MCP tool only exposes the currently active idea space after Agent Access is enabled in the app.

Example configuration shape:

```json
{
  "mcpServers": {
    "paper-knowledge-engine": {
      "command": "/path/to/paper-engine-mcp"
    }
  }
}
```
```

- [ ] **Step 6: Commit**

```bash
git add src-tauri/binaries/.gitkeep docs/packaging.md
git commit -m "build: document sidecar desktop packaging"
```

---

### Task 7: Tauri Dev Smoke Test

**Files:**
- Modify only files needed to fix smoke test failures.
- Test: Tauri dev window launches and UI reaches `/health`.

- [ ] **Step 1: Start Tauri dev**

Run:

```bash
npm run tauri dev
```

Expected:

- Tauri window opens.
- React UI displays Chinese three-column workspace.
- Backend health eventually reports available.

- [ ] **Step 2: Verify backend URL command from the UI**

Open DevTools in the Tauri window and run:

```js
window.__TAURI__.core.invoke('backend_url')
```

Expected: resolves to a URL like:

```txt
http://127.0.0.1:54321
```

- [ ] **Step 3: Exercise core workflow**

In the Tauri app:

1. Create a space named `Tauri 验证空间`.
2. Upload a small PDF.
3. Parse the PDF.
4. Search for a term present in the PDF.
5. Run heuristic extraction.

Expected:

- Space appears in the left sidebar.
- PDF appears in the paper list.
- Parse status changes to `已解析` or a clear parse error.
- Search results show source snippets.
- Heuristic extraction returns editable cards or a clear no-passages message.

- [ ] **Step 4: Commit fixes from smoke test**

If smoke test required changes:

```bash
git add frontend src-tauri api_sidecar.py scripts/build_sidecars.py
git commit -m "fix: stabilize Tauri dev workflow"
```

If no changes were needed:

```bash
git status --short
```

Expected: no smoke-test changes.

---

### Task 8: macOS DMG Build Smoke Test

**Files:**
- Modify packaging config only if build fails.
- Test: `npm run tauri build`

- [ ] **Step 1: Build frontend and sidecars**

Run:

```bash
npm --prefix frontend run build
.venv/bin/python scripts/build_sidecars.py --target all
```

Expected:

- `frontend/dist` exists.
- `src-tauri/binaries/paper-engine-api-$(rustc --print host-tuple)` exists.
- `src-tauri/binaries/paper-engine-mcp-$(rustc --print host-tuple)` exists.

- [ ] **Step 2: Build Tauri bundle**

Run:

```bash
npm run tauri build
```

Expected:

- macOS `.app` bundle exists under `src-tauri/target/release/bundle/macos/`.
- macOS `.dmg` exists under `src-tauri/target/release/bundle/dmg/`.

- [ ] **Step 3: Launch built app**

Run the `.app` from Finder or:

```bash
open src-tauri/target/release/bundle/macos/*.app
```

Expected:

- App opens.
- Backend starts without a terminal.
- UI can create a space and list it after refresh.

- [ ] **Step 4: Verify data directory**

Create a test space in the built app, then inspect:

```bash
find "$HOME/Library/Application Support" -maxdepth 3 -iname "*paper*" -o -iname "*knowledge*"
```

Expected: an application data directory exists and contains `paper_engine.db`.

- [ ] **Step 5: Commit packaging fixes**

If build required changes:

```bash
git add src-tauri scripts docs
git commit -m "fix: stabilize macOS Tauri bundle"
```

If no changes were needed:

```bash
git status --short
```

Expected: no macOS bundle fixes.

---

### Task 9: Final Verification and Documentation

**Files:**
- Modify: `docs/product-overview.md`
- Modify: `docs/packaging.md`
- Test: full verification suite

- [ ] **Step 1: Update product overview for desktop app**

Modify `docs/product-overview.md` to include:

```markdown
## 桌面应用

当前产品迁移为 Tauri 桌面应用。桌面应用使用中文三栏研究工作台界面，并在启动时自动拉起本地 Python API sidecar。用户不需要手动启动 FastAPI 服务。

第一阶段 macOS 支持生成未签名 `.dmg`，Windows 预留 Windows 构建环境和安装包配置。正式对外分发前需要代码签名。
```

- [ ] **Step 2: Run Python checks**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy main.py api_sidecar.py tests/
```

Expected:

- pytest passes.
- mypy reports success.

- [ ] **Step 3: Run frontend checks**

Run:

```bash
npm --prefix frontend run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Run Rust check**

Run:

```bash
cargo check --manifest-path src-tauri/Cargo.toml
```

Expected: Rust check passes.

- [ ] **Step 5: Run sidecar build**

Run:

```bash
.venv/bin/python scripts/build_sidecars.py --target all
```

Expected: API and MCP sidecars exist in `src-tauri/binaries/`.

- [ ] **Step 6: Run Tauri build**

Run:

```bash
npm run tauri build
```

Expected: `.app` and `.dmg` are generated on macOS.

- [ ] **Step 7: Commit final docs**

```bash
git add docs/product-overview.md docs/packaging.md
git commit -m "docs: document desktop packaging workflow"
```

---

## Plan Self-Review

Spec coverage:

- macOS `.dmg` first: Tasks 6, 8, and 9.
- Windows reserved: Tasks 6 and 9 document Windows build commands without making it the first validation target.
- Python sidecar via PyInstaller: Tasks 1, 3, 5, 6, and 8.
- Advanced Chinese three-column UI: Task 4.
- MCP sidecar bundled but not auto-configured: Tasks 3, 6, and 9.
- Domain-neutral structure and heuristic extraction wording: Task 2.
- Existing API/data model preservation: Tasks 1, 4, and 5 keep HTTP API and Python backend.

Placeholder scan:

- No placeholder markers or open-ended implementation steps.
- Each code-changing task includes concrete file paths and code blocks.
- Each verification step includes exact commands and expected outcomes.

Type consistency:

- Frontend API types match current FastAPI JSON fields.
- Tauri command name `backend_url` matches the frontend `invoke<string>('backend_url')`.
- Sidecar names `paper-engine-api` and `paper-engine-mcp` match Tauri `externalBin` entries and build script output.
