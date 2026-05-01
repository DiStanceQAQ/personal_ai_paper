# Local Paper Knowledge Engine

Local Paper Knowledge Engine is a local-first desktop app for turning research
PDFs into searchable, source-grounded paper understanding. It organizes papers
by idea space, parses PDFs into passages, builds local retrieval indexes,
generates concise Chinese paper understanding, derives stable knowledge cards,
and exposes the active workspace to external coding/research agents through an
MCP server.

> Status: active early release. Core local workflow, desktop packaging, AI
> analysis, retrieval, and MCP access are implemented and covered by tests.

## Features

- Idea spaces for separating research projects.
- PDF import, batch import, background parsing, and parse diagnostics.
- Local SQLite storage for papers, passages, cards, analysis runs, and settings.
- Local-first retrieval with FTS and optional semantic search acceleration.
- AI paper understanding in Chinese with source-backed fields.
- Five stable AI knowledge cards per paper: research problem, method, result,
  conclusion, and limitation.
- PDF source viewer for checking original evidence.
- MCP server for Claude Code, Codex, Cursor, and other external agents.
- Tauri desktop shell with Python API and worker sidecars.

## Architecture

```text
React/Vite UI
    |
Tauri desktop shell
    |
FastAPI sidecar  ---- SQLite + local PDF files
    |
background worker sidecar
    |-- PDF parsing
    |-- embeddings
    |-- AI paper understanding
    |
MCP stdio server
```

The normal AI analysis flow is:

```text
PDF -> passages -> metadata -> paper_understanding_zh -> 5 derived cards
```

The generated cards are stored in `knowledge_cards` and each card keeps source
passage provenance.

## Requirements

- Python 3.11 or newer.
- Node.js and npm.
- Rust/Cargo when running or packaging the Tauri desktop app.

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,pdf-advanced]"

npm install
npm --prefix frontend install
```

You can also use the Makefile:

```bash
make install
make frontend-install
npm install
```

## Run the API Development Server

```bash
source .venv/bin/activate
make dev
```

Open:

```text
http://127.0.0.1:8000
```

Useful endpoints:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

## Run the Tauri Desktop App

```bash
source .venv/bin/activate
make tauri-dev
```

The desktop dev command starts the React/Vite frontend and checks the local
Python sidecars before launch.

## Frontend Development

```bash
npm run frontend:dev
```

The Vite dev server runs on:

```text
http://127.0.0.1:1420
```

If you open the frontend in a browser, also start the backend with `make dev`.

## Build

macOS desktop package:

```bash
source .venv/bin/activate
make package-macos
```

Build outputs are under:

```text
src-tauri/target/release/bundle/
```

More details: [docs/packaging.md](docs/packaging.md).

## MCP Server

Start the MCP server:

```bash
paper-engine-mcp
```

For a packaged desktop app data directory on macOS:

```bash
PAPER_ENGINE_DATA_DIR="$HOME/Library/Application Support/com.local.paperknowledgeengine" paper-engine-mcp
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "paper-knowledge-engine": {
      "command": "/path/to/paper-engine-mcp"
    }
  }
}
```

MCP access is disabled by default. Enable Agent Access in the app before
connecting external agents. MCP tools only expose the current active idea space.

## Data and Privacy

Default development data is stored in:

```text
app-data/
```

You can override it:

```bash
PAPER_ENGINE_DATA_DIR=/path/to/data make dev
```

Do not commit local data or real API keys. Use `.env.example` as a template.

Privacy details: [docs/privacy.md](docs/privacy.md).

## PDF Parsing

The app supports local and service-backed parser modes. Docling runs locally;
MinerU and GROBID are optional HTTP services and may receive PDF content if
configured.

See [docs/pdf-ingestion.md](docs/pdf-ingestion.md).

## Test and Quality Checks

```bash
make test
make typecheck
npm run frontend:typecheck
npm run frontend:build
```

Full backend check:

```bash
make check
```

## Sample Data

This repository does not include third-party research PDFs. See
[docs/sample-data.md](docs/sample-data.md) for local testing guidance.

## License

MIT License. See [LICENSE](LICENSE).
