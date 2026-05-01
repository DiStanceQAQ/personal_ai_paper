<h1 align="center">Local Paper Knowledge Engine</h1>

<p align="center">
  A local paper knowledge base.
  <br />
  Use AI to break down papers and expose your knowledge base through MCP.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-0f766e"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3776AB">
  <img alt="FastAPI" src="https://img.shields.io/badge/api-FastAPI-009688">
  <img alt="React" src="https://img.shields.io/badge/frontend-React%20%2B%20Vite-61DAFB">
  <img alt="Tauri" src="https://img.shields.io/badge/desktop-Tauri-FFC131">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-ready-111827">
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.zh-CN.md">简体中文</a>
</p>

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
- Batch PDF import, vectorization, and AI-generated knowledge cards.
- Retrieval with FTS and semantic search.
- PDF source viewer for checking original evidence.
- MCP server for external coding/research agents to read the active workspace
  and its knowledge cards.

## Screenshots

![Main interface](docs/assets/interface.png)

![Search](docs/assets/search.png)

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

## Run the Tauri Desktop App

```bash
source .venv/bin/activate
make tauri-dev
```

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

## License

MIT License. See [LICENSE](LICENSE).
