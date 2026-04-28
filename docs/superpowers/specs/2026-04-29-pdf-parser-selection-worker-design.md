# PDF Parser Selection and Worker Design

## Goal

Simplify the PDF parsing pipeline by replacing automatic multi-backend routing
with an explicit user-selected parser mode. The frontend settings page controls
which parser is used for future parse jobs:

- `mineru`: MinerU API parsing, shown as the recommended option.
- `docling`: local Docling parsing, used when no setting has been saved.

Parsing should run through a queued `parse_run` and an asynchronous worker. The
worker should use the parser choice captured when the run is created, normalize
the backend result into the existing `ParseDocument` contract, enrich scholarly
metadata with GROBID when configured, then persist and index the result.

## Current Problems

The current parse flow is synchronous and over-coupled:

- `/api/papers/{paper_id}/parse` performs status updates, PDF inspection,
  backend routing, chunking, parse persistence, embedding, and error handling in
  one request path.
- `paper_engine.pdf.router` profiles each PDF and tries several backends based
  on quality signals and fallback policy.
- GROBID is merged inside the router after the primary parser succeeds.
- Users cannot clearly choose between cloud/high-fidelity parsing and local
  parsing.
- Backend warnings explain routing attempts, but they make the product behavior
  harder to reason about than a direct parser selection.

## Non-Goals

- Do not keep the existing automatic backend routing as the primary parse path.
- Do not silently fall back from MinerU to Docling when MinerU is selected and
  fails.
- Do not store API keys in `parse_runs.config_json` or other parse diagnostics.
- Do not require users to select a parser on every upload.
- Do not redesign chunking, parse persistence, search, or embedding semantics
  beyond what is needed to run them from a worker.
- Do not remove GROBID enrichment; it remains optional and independent of the
  selected body parser.

## Recommended Approach

Use a global parser setting stored in `app_state` and captured into each
`parse_run` when the run is queued.

```text
Frontend settings
  -> save pdf_parser_backend = mineru | docling

Upload
  -> ingest PDF
  -> create parse_run(status=queued, config_json parser snapshot)
  -> return paper_id and parse_run_id

Parse worker
  -> load parse_run config_json
  -> run selected body parser
       mineru  -> MinerU API -> ParseDocument
       docling -> Docling local -> ParseDocument
  -> run GROBID enrichment in parallel when configured
  -> merge metadata
  -> chunk passages
  -> persist document, passages, FTS, and embeddings
  -> mark run completed or failed
```

The parser selection is intentionally explicit. If the user chooses MinerU, the
run either completes with MinerU output or fails with a MinerU-specific error. A
separate future setting can allow automatic local fallback, but it should not be
the default behavior.

## Settings Contract

Add these settings keys in `app_state`:

- `pdf_parser_backend`: `mineru` or `docling`; default is `docling`.
- `mineru_base_url`: MinerU API base URL.
- `mineru_api_key`: MinerU API key, written only when the user provides a new
  non-empty value.
- `grobid_base_url`: existing GROBID endpoint setting, still optional.

The current settings API can be extended, or a new `/api/settings` route can be
introduced. The lower-risk incremental path is to extend the existing
`/api/agent/config` response and update payload because the frontend settings
modal already reads and writes app configuration there.

The settings API should also expose parser availability so the frontend can
warn before the user queues work:

```json
{
  "parsers": {
    "docling": {
      "available": false,
      "install_hint": "pip install docling"
    },
    "mineru": {
      "configured": true,
      "last_check_status": "unknown"
    }
  }
}
```

The response should expose whether secrets are present without returning them:

```json
{
  "pdf_parser_backend": "docling",
  "mineru_base_url": "",
  "has_mineru_api_key": false
}
```

The update payload may include:

```json
{
  "pdf_parser_backend": "mineru",
  "mineru_base_url": "https://mineru.example",
  "mineru_api_key": "secret"
}
```

If `mineru_api_key` is empty or omitted, the existing stored key is preserved.

Saving settings should not require a successful MinerU network check. Instead,
provide an explicit "test connection" action from the settings UI. The backend
can implement this as a lightweight MinerU client health check when the
configured deployment supports one. If the deployment does not expose a stable
health endpoint, the test should report that credentials are present but
connectivity could not be verified, rather than blocking the saved setting.

## Frontend Behavior

Add a "PDF 解析方式" setting to the existing settings modal:

- `MinerU API（推荐）`
- `Docling 本地解析`

When `MinerU API` is selected, show MinerU Base URL and MinerU API Key fields.
When `Docling 本地解析` is selected, hide or disable MinerU-specific fields.

The UI default should match the backend default: `docling`. The label can still
mark MinerU as recommended because it may provide better layout fidelity, but
the default must remain local and usable without cloud configuration.

The settings modal should display parser availability:

- If Docling is selected but the backend reports `available=false`, show
  `请安装 docling: pip install docling` for development builds. In this repo,
  the developer-oriented install can also point to
  `pip install -e ".[pdf-advanced]"`. Packaged desktop builds should show an
  app-specific installation or bundling message.
- If MinerU is selected and credentials are missing, show that Base URL and API
  Key are required before parsing can run.
- If MinerU credentials are present, offer a test connection action and display
  its latest result without preventing the user from saving settings.

## Parse Run Snapshot

When a parse run is created, copy the relevant settings into
`parse_runs.config_json`:

```json
{
  "parser_backend": "mineru",
  "mineru_base_url": "https://mineru.example",
  "grobid_enabled": true,
  "worker_version": "pdf-parser-selection-v1"
}
```

Do not include API keys. The worker should read secrets from `app_state` at
execution time, but it should not re-read the selected parser mode from
`app_state`. This prevents queued runs from changing behavior when the user
updates settings after the run is created.

## Parse Run State

Use `parse_runs` as both the durable job table and the parse history table.
Extend it with worker-control fields:

- `status`: `queued`, `running`, `completed`, or `failed`. New jobs use
  `queued`; existing completed parse history can stay `completed`.
- `claimed_at`: nullable time the worker claimed the run.
- `heartbeat_at`: nullable last worker heartbeat while the run is active.
- `worker_id`: nullable identifier of the worker that claimed the run.
- `attempt_count`: integer claim count with default `0`.
- `last_error`: nullable short failure summary for diagnostics.

Add indexes for job claiming and per-paper concurrency:

```sql
CREATE INDEX IF NOT EXISTS idx_parse_runs_status_started
  ON parse_runs(status, started_at);

CREATE INDEX IF NOT EXISTS idx_parse_runs_paper_status
  ON parse_runs(paper_id, status);
```

The paper-level `parse_status` remains the user-facing summary:

- `pending`: uploaded or queued, not currently being parsed.
- `parsing`: a worker is running a parse for the paper.
- `parsed`: the latest successful parse is available.
- `error`: no usable completed parse is available after the latest failure.

## Data Model

Reuse the existing `ParseDocument` contract as the normalization boundary.

MinerU and Docling adapters both return:

- `ParseDocument.backend`: `mineru` or `docling`.
- `ParseDocument.extraction_method`: normally `layout_model`; use `ocr` when
  the backend reports OCR-only extraction.
- `ParseDocument.elements`: normalized title, heading, paragraph, list, table,
  figure, caption, equation, reference, and unknown elements.
- `ParseDocument.tables`: normalized table records where available.
- `ParseDocument.assets`: extracted figures or assets where available.
- `ParseDocument.metadata`: backend-specific metadata under namespaced keys.

GROBID enrichment should not replace the selected parser backend. It writes
scholarly metadata under `metadata.grobid` and contributes merged paper-level
fields during persistence.

## Worker Behavior

The first implementation can be an in-process background worker owned by the API
sidecar. It should be isolated behind a small service boundary so a future
separate process can reuse the same job claiming and execution code.

Required worker steps:

1. Claim a queued parse run by setting `status='running'` and the paper
   `parse_status='parsing'`.
2. Validate that the PDF file still exists.
3. Run the selected body parser from `parse_runs.config_json`.
4. Run GROBID enrichment independently when configured and reachable.
5. Merge metadata with this precedence: user-edited paper fields, GROBID,
   selected body parser, filename-derived fallback.
6. Validate the resulting `ParseDocument`.
7. Chunk the document with the existing chunking module.
8. Persist parse elements, tables, assets, passages, FTS rows, and embeddings.
9. Mark the run `completed` and the paper `parsed`.
10. On failure, mark the run `failed`, record warnings and error metadata, and
    mark the paper `error` if no completed parse exists for that paper.

The worker must avoid running two active parse jobs for the same paper at the
same time.

Claiming must be atomic. A SQLite implementation should use a short
`BEGIN IMMEDIATE` transaction and a conditional update:

```sql
UPDATE parse_runs
SET status = 'running',
    claimed_at = datetime('now'),
    heartbeat_at = datetime('now'),
    worker_id = ?,
    attempt_count = attempt_count + 1,
    last_error = NULL
WHERE id = ?
  AND status = 'queued'
  AND NOT EXISTS (
    SELECT 1
    FROM parse_runs active
    WHERE active.paper_id = parse_runs.paper_id
      AND active.status = 'running'
  );
```

The worker should first select one queued candidate ordered by `started_at, id`,
then run this conditional update inside the same transaction. If the update
affects zero rows, another worker or another run for the same paper won the
claim and this worker should try the next candidate later.

Workers should update `heartbeat_at` periodically during long MinerU or Docling
work. On API startup and on a periodic maintenance interval, stale active runs
should be recovered:

```sql
UPDATE parse_runs
SET status = 'queued',
    worker_id = NULL,
    last_error = 'worker_heartbeat_timeout'
WHERE status = 'running'
  AND heartbeat_at < datetime('now', '-10 minutes');
```

The timeout should default to 10 minutes but be configurable because large
MinerU jobs may legitimately take longer. If a run exceeds a maximum attempt
count, the worker should mark it `failed` instead of requeueing indefinitely.

## Router Compatibility

The new production parse path must not call `paper_engine.pdf.router.parse_pdf`
or the automatic fallback candidate logic. The worker should select exactly one
body parser from the parse-run snapshot and call the MinerU or Docling adapter
directly.

During migration, `paper_engine.pdf.router` can remain as a deprecated
compatibility wrapper for older tests or local scripts. It should not be
imported by the paper upload, parse queue, or parse worker code paths. Once
tests and callers have moved to the selected-parser worker, remove the automatic
fallback router rather than leaving commented-out logic.

## Error Handling

MinerU selected but not configured:

- Do not create an ambiguous successful run.
- Return a clear queued-run failure such as
  `mineru_config_missing:mineru_base_url_or_api_key`.

MinerU API failure:

- Mark the run failed with the API status or job error.
- Do not automatically retry with Docling unless a future explicit fallback
  setting is enabled.

Docling unavailable:

- Mark the run failed with `docling_unavailable`.
- The settings UI may still allow Docling because it is the default local mode,
  but the failure should point the developer to install or bundle the optional
  dependency.
- The parser availability API should detect this before work is queued when
  possible.

GROBID unavailable:

- Do not fail the body parse.
- Record a warning and complete the run without GROBID metadata.

Embedding failure:

- Keep the same strict behavior as the current pipeline for the first pass: mark
  the parse run failed if required local embeddings cannot be generated.

## API Behavior

Upload should validate, hash, store, deduplicate or reuse the PDF, then create a
queued parse run using the current parser setting. The paper can keep the
existing `parse_status='pending'` while the parse run is queued; the worker sets
the paper to `parsing` when it claims the run.

`POST /api/papers/upload` should return the created or reused paper plus the
queued `parse_run_id`. If preserving the exact current `Paper` response shape is
required for the first implementation, the parse run must still be queryable
immediately through `/api/papers/{paper_id}/parse-runs`.

`POST /api/papers/{paper_id}/parse` should queue a parse run instead of doing
the full parse synchronously. This endpoint becomes the explicit re-parse action
for an existing paper. It returns:

```json
{
  "status": "queued",
  "paper_id": "paper-1",
  "parse_run_id": "run-1",
  "backend": "mineru"
}
```

Existing parse diagnostics endpoints should continue to list runs, elements,
tables, and passages. The frontend can poll the paper or parse run status until
the run reaches a terminal state.

## Migration Strategy

1. Add parser settings to the settings API and frontend modal.
2. Add a MinerU backend adapter that normalizes MinerU results to
   `ParseDocument`.
3. Simplify parser execution to select `mineru` or `docling` from a parse-run
   config snapshot.
4. Add atomic job claiming, stale-run recovery, and parser availability checks.
5. Move parse execution into a worker service boundary while keeping the
   existing persistence, chunking, FTS, and embedding modules.
6. Retire automatic router usage from the main parse path. Keep the old router
   only as a compatibility wrapper during the transition, or remove it after
   tests prove the new path covers current callers.

## Testing

Backend tests should cover:

- Default settings return `pdf_parser_backend='docling'`.
- Saving `mineru` and `docling` parser settings persists them in `app_state`.
- Empty MinerU API key updates do not erase an existing stored key.
- Parser availability reports missing Docling with the `pip install docling`
  development hint.
- MinerU test connection reports missing credentials distinctly from network
  failure.
- Queuing a parse run snapshots the selected parser into `config_json`.
- Changing settings after queuing does not change the queued run's parser.
- Job claiming is atomic and does not allow two `running` runs for the same
  paper.
- Startup or maintenance recovery requeues stale `running` jobs and stops
  retrying after the configured maximum attempt count.
- MinerU-selected runs call the MinerU adapter and persist `backend='mineru'`.
- Docling-selected runs call the Docling adapter and persist `backend='docling'`.
- Missing MinerU configuration fails clearly without falling back to Docling.
- GROBID failure records a warning but does not fail a successful body parse.
- Worker persistence still writes parse runs, elements, passages, FTS rows, and
  embeddings.
- Production parse code no longer imports or calls the automatic router.

Frontend tests should cover:

- Settings modal renders the parser select.
- MinerU fields appear only when MinerU is selected.
- Saving settings sends `pdf_parser_backend`, `mineru_base_url`, and a provided
  `mineru_api_key`.
- Loaded `has_mineru_api_key` state clears the password input while showing that
  a key has already been saved.
- Missing Docling availability is visible when Docling is selected.
- MinerU test connection status is displayed without blocking settings save.

## User-Facing Semantics

The product behavior should be easy to explain:

> PDF parsing uses the parser selected in Settings. MinerU API is recommended
> for higher-fidelity parsing. Docling runs locally and is the default when no
> parser has been selected.

The parse history should make the same fact visible through the stored backend
and parse-run config snapshot.
