# Backend Rearchitecture Design

## Goal

Refactor the Python backend from a flat collection of root-level modules into a
formal `paper_engine` package with clear domain boundaries, thinner HTTP routes,
and packaging entry points that look like a maintained project.

The frontend HTTP API contract should remain compatible. Backend module paths,
test imports, Python packaging configuration, PyInstaller sidecar build inputs,
and Tauri sidecar packaging configuration may change.

## Current Problems

The current backend places API routes, SQLite access, PDF parsing, analysis,
retrieval, MCP tools, sidecar startup, and configuration in root-level modules.
This has several concrete costs:

- `pyproject.toml` has to list many individual `py-modules`.
- Route modules mix HTTP handling, validation, filesystem work, database writes,
  and domain behavior.
- Large files such as `analysis_pipeline.py`, `pdf_chunker.py`, and PDF backend
  modules are hard to navigate and test in isolation.
- Sidecar packaging relies on root module names such as `api_sidecar.py`,
  `main.py`, and `mcp_server.py`.
- MCP, API, and tests reach into low-level modules directly, which makes future
  parser and analysis changes harder to stage safely.

## Non-Goals

- Do not redesign the frontend UI.
- Do not change frontend-visible HTTP paths or JSON response semantics as part
  of this refactor.
- Do not replace SQLite, FastAPI, PyInstaller, or Tauri.
- Do not introduce a dependency injection framework.
- Do not move parsing to an external worker system in this refactor. The package
  structure should leave room for a worker later.
- Do not implement new product features while reorganizing backend ownership.

## Recommended Approach

Use a domain package layout with explicit API, storage, PDF, analysis, retrieval,
agent, MCP, and sidecar boundaries. This is intentionally stronger than a simple
directory move, but lighter than a full clean-architecture rewrite.

Target structure:

```text
paper_engine/
  __init__.py
  api/
    __init__.py
    app.py
    dependencies.py
    routes/
      __init__.py
      spaces.py
      papers.py
      cards.py
      search.py
      agent.py
  core/
    __init__.py
    config.py
    errors.py
    startup.py
  storage/
    __init__.py
    database.py
    migrations.py
    repositories/
      __init__.py
      spaces.py
      papers.py
      cards.py
      settings.py
  spaces/
    __init__.py
    service.py
  papers/
    __init__.py
    service.py
  cards/
    __init__.py
    extraction.py
    service.py
  pdf/
    __init__.py
    models.py
    router.py
    profile.py
    chunking.py
    persistence.py
    backends/
      __init__.py
      base.py
      docling.py
      grobid.py
      legacy.py
      llamaparse.py
      pymupdf4llm.py
  analysis/
    __init__.py
    models.py
    pipeline.py
    prompts.py
    verifier.py
  retrieval/
    __init__.py
    embeddings.py
    hybrid.py
    lexical.py
  agent/
    __init__.py
    executor.py
    llm_client.py
  mcp/
    __init__.py
    server.py
  sidecar/
    __init__.py
    api.py
```

The initial migration can preserve most function names inside the new modules.
After imports and packaging are stable, large modules can be split further within
their new domains.

## Public Compatibility

Keep these frontend-facing contracts stable:

- `/api/spaces`
- `/api/papers`
- `/api/cards`
- `/api/search`
- `/api/agent`
- `/health`
- `/api/info`
- root static HTML behavior

Keep these sidecar runtime contracts stable:

- binary names: `paper-engine-api`, `paper-engine-mcp`
- API sidecar arguments: `--host`, `--port`, `--data-dir`
- default sidecar host and port behavior
- Tauri `externalBin` names

Allowed breaking changes:

- Python import paths in tests and internal modules
- console script implementation targets
- PyInstaller entrypoint file paths and hidden imports
- Makefile development command internals
- root-level Python module presence, except for short-lived compatibility shims

## Module Boundaries

### API

`paper_engine.api.app` owns FastAPI application creation, middleware, static UI
mounting, route registration, lifespan startup, `/health`, and `/api/info`.

`paper_engine.api.routes.*` modules should be thin. They translate HTTP inputs to
service calls, map domain exceptions to HTTP errors, and return compatible JSON
payloads. They should avoid large SQL blocks and direct parser orchestration.

### Core

`paper_engine.core.config` owns path and environment configuration. It replaces
root-level `config.py`.

`paper_engine.core.startup` owns startup tracing utilities shared by FastAPI and
sidecar entry points.

`paper_engine.core.errors` contains small domain exception types used by services
and mapped by API routes.

### Storage

`paper_engine.storage.database` owns SQLite connection creation, schema bootstrap,
and `init_db`.

`paper_engine.storage.migrations` owns schema migration logic.

`paper_engine.storage.repositories.*` modules own SQL for their domain tables.
They should use explicit function boundaries rather than exposing raw SQL to
routes and services.

### Spaces, Papers, Cards

Domain service modules own product behavior:

- active-space resolution
- paper upload and deletion coordination
- paper metadata updates
- parse dispatch orchestration at the API boundary
- card creation, updates, deletion, and heuristic extraction

Routes call these services. Services call repositories and lower-level PDF,
retrieval, and analysis modules.

### PDF

`paper_engine.pdf` is the home for parser contracts, quality profiling, backend
routing, chunking, persistence, and parser backend adapters.

Existing names map as follows:

- `pdf_models.py` -> `paper_engine.pdf.models`
- `pdf_router.py` -> `paper_engine.pdf.router`
- `pdf_profile.py` -> `paper_engine.pdf.profile`
- `pdf_chunker.py` -> `paper_engine.pdf.chunking`
- `pdf_persistence.py` -> `paper_engine.pdf.persistence`
- `pdf_backend_base.py` -> `paper_engine.pdf.backends.base`
- `pdf_backend_*` -> `paper_engine.pdf.backends.*`

The PDF package remains the downstream contract for parser work already planned
in the MinerU API-first parser design.

### Analysis

`paper_engine.analysis` owns LLM-based paper analysis, prompt construction,
schema models, and source verification.

The first refactor may move `analysis_pipeline.py` mostly intact to
`paper_engine.analysis.pipeline`. Later tasks can split it into selection,
metadata extraction, card extraction, persistence, and ranking modules.

### Retrieval

`paper_engine.retrieval` owns SQLite FTS, optional embeddings, hybrid search, and
rank fusion.

Suggested mapping:

- `search.py` -> `paper_engine.retrieval.lexical`
- `hybrid_search.py` -> `paper_engine.retrieval.hybrid`
- `embeddings.py` -> `paper_engine.retrieval.embeddings`

### Agent

`paper_engine.agent` owns LLM client configuration and paper analysis execution
used by API routes.

Suggested mapping:

- `agent_executor.py` -> `paper_engine.agent.executor`
- `llm_client.py` -> `paper_engine.agent.llm_client`

### MCP

`paper_engine.mcp.server` owns MCP tool registration and stdio startup. It should
gradually depend on services and retrieval functions instead of duplicating SQL
and access-control behavior.

### Sidecar

`paper_engine.sidecar.api` owns the packaged API server entry point. It parses
sidecar arguments, sets `PAPER_ENGINE_DATA_DIR` when requested, imports
`paper_engine.api.app:app`, and starts Uvicorn.

The console scripts become:

```toml
[project.scripts]
paper-engine-api = "paper_engine.sidecar.api:main"
paper-engine-mcp = "paper_engine.mcp.server:main"
```

## Packaging Changes

Replace `py-modules` with package discovery:

```toml
[tool.setuptools.packages.find]
include = ["paper_engine*"]
```

Development commands should use package paths:

```text
uvicorn paper_engine.api.app:app --reload --host 127.0.0.1 --port 8000
```

PyInstaller sidecar builds should use package entry files or module execution
wrappers under `paper_engine/sidecar` and `paper_engine/mcp`. Hidden imports
should be updated from flat module names to package module names.

The Tauri `externalBin` entries should continue to reference:

```json
[
  "binaries/paper-engine-api",
  "binaries/paper-engine-mcp"
]
```

## Migration Strategy

Use a staged migration to keep each step reviewable:

1. Create the `paper_engine` package and move configuration, database, migrations,
   startup tracing, and FastAPI app creation.
2. Move API routes and update imports while preserving HTTP behavior.
3. Move PDF modules and keep the parser import-laziness tests equivalent.
4. Move analysis, retrieval, agent, cards, and MCP modules.
5. Update `pyproject.toml`, console scripts, Makefile, sidecar build script, and
   tests to package paths.
6. Remove root-level modules that are no longer needed. Keep only temporary shims
   if they are needed to make the migration incremental.
7. Run full backend, frontend contract, and sidecar build verification.

Do not rename HTTP endpoints or response fields during the migration.

## Data Flow

Paper upload remains:

```text
HTTP route -> papers service -> paper repository + filesystem storage
```

PDF parse remains:

```text
HTTP route -> papers service -> pdf profile/router -> pdf chunking
  -> pdf persistence -> retrieval indexing -> paper repository status update
```

Card extraction remains:

```text
HTTP route -> cards service -> passages repository -> cards extraction
  -> cards repository
```

Search remains:

```text
HTTP route -> retrieval lexical or hybrid search -> compatible result payload
```

MCP access remains:

```text
MCP tool -> active-space access check -> domain service or retrieval function
```

## Error Handling

Services should raise domain exceptions such as:

- `ActiveSpaceRequired`
- `NotFound`
- `Conflict`
- `ValidationError`
- `ParserFailed`

API routes map these exceptions to FastAPI `HTTPException` values. This keeps HTTP
status code decisions close to the API layer and avoids leaking FastAPI imports
into domain modules.

Parser warnings remain structured warning strings in existing response payloads
until a later parser diagnostics refactor changes that contract.

## Testing

Update tests to import package paths. Preserve test intent:

- route tests continue to assert existing HTTP behavior
- parser import tests continue to ensure heavy PDF dependencies are lazy where
  startup requires laziness
- DB tests continue to verify schema and migration behavior
- sidecar tests verify argument parsing and startup import targets
- build script tests verify new hidden-import and entrypoint names
- frontend API contract tests should not need semantic changes

Recommended verification commands:

```text
make test
make typecheck
npm run frontend:typecheck
make build-sidecars
```

If `make build-sidecars` is too slow for every small migration step, run its unit
tests during intermediate work and run the real build before completion.

## Risks And Mitigations

Risk: import churn causes circular imports.
Mitigation: keep `api` depending on services, services depending on repositories
and domain modules, and repositories depending only on storage/core utilities.

Risk: PyInstaller misses hidden imports after package migration.
Mitigation: update hidden-import lists in one step and verify with the existing
sidecar build tests plus a real `make build-sidecars` run before completion.

Risk: parser startup becomes slow by importing PyMuPDF too early.
Mitigation: preserve lazy imports in parser-facing modules and keep an equivalent
import-time test.

Risk: frontend breaks despite unchanged route paths.
Mitigation: keep response payload fields stable and run route plus frontend API
contract tests.

Risk: migration is too large for a single patch.
Mitigation: land it in staged commits or staged plan tasks, with tests after each
domain move.

## Acceptance Criteria

- Backend code lives under `paper_engine/` with clear package boundaries.
- `pyproject.toml` uses package discovery instead of enumerating root modules.
- `paper-engine-api` and `paper-engine-mcp` console scripts target package
  modules.
- `make dev` starts `paper_engine.api.app:app`.
- Existing frontend HTTP paths and JSON semantics remain compatible.
- Tauri sidecar binary names and runtime arguments remain compatible.
- Root-level backend modules are removed or reduced to documented temporary
  compatibility shims.
- Full backend tests pass.
- Type checking passes at least at the current project standard.
- Sidecar build script tests pass, and the real sidecar build is verified before
  declaring implementation complete.
