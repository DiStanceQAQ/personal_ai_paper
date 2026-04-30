# PDF Ingestion Configuration

PDF parsing is settings-driven. The app stores the selected parser in
`app_state.pdf_parser_backend` and snapshots that choice into every queued
`parse_run`. Upload and manual re-parse no longer run the parser synchronously;
they create durable queued jobs for the parse worker.

Available parser modes:

| Mode | Backend | Runs where | Best for | Requirements |
| --- | --- | --- | --- | --- |
| MinerU API | `mineru` | Configured HTTP service | Higher-fidelity structure, tables, formulas, difficult layout | `mineru_base_url` and, when required by the service, `mineru_api_key` |
| Docling local | `docling` | Local Python process | Local-first parsing without sending PDFs to a parser service | `pip install docling` or the project `pdf-advanced` extra |

`MinerU API` is shown as the recommended option in the UI. `Docling local` is
the default so a fresh install stays local and does not require parser service
credentials.

## Queue Behavior

`POST /api/papers/upload` validates, hashes, stores, and deduplicates the PDF,
then queues a parse run automatically. Duplicate uploads in the same space reuse
the existing paper and queue another parse run for that paper.

`POST /api/papers/{paper_id}/parse` is now an explicit re-parse action. It
returns `status="queued"` with the new `parse_run_id`; parsing continues in the
background worker.

Each queued run stores a parser snapshot in `parse_runs.config_json`, for
example:

```json
{
  "parser_backend": "mineru",
  "mineru_base_url": "http://127.0.0.1:8000",
  "grobid_enabled": true,
  "worker_version": "pdf-parser-selection-v1"
}
```

API keys are not stored in parse-run diagnostics. The worker reads secrets from
`app_state` at execution time.

## Worker Pipeline

The worker claims one queued run atomically, avoiding two simultaneous active
runs for the same paper. It sets the run to `running`, sets the paper to
`parsing`, and increments `attempt_count`.

For each claimed run:

1. Validate that the PDF file still exists.
2. Run the selected body parser directly:
   `mineru` uses the MinerU HTTP backend; `docling` uses the local Docling
   backend.
3. Start GROBID enrichment in parallel with the body parser when GROBID is
   configured. GROBID failures become warnings and do not fail a successful body
   parse.
4. Normalize the body parser output to `ParseDocument`.
5. Chunk the document into passages.
6. Persist document elements, tables, assets, passages, and FTS rows.
7. Queue an `embedding_runs` job for the persisted `parse_run`.
8. Mark the run `completed` and the paper `parsed`, or mark the run `failed`
   and the paper `error` when there is no previous completed parse.

Embedding generation is decoupled from parsing. If embeddings fail, the paper
remains parsed and `papers.embedding_status` becomes `failed`; inspect
`GET /api/papers/{paper_id}/embedding-runs` for `last_error` and batch stats.

The embedding worker batches passages (`PAPER_ENGINE_EMBEDDING_BATCH_SIZE`),
preloads the configured provider when possible, skips already embedded
passages, and reuses embeddings with the same `content_hash`,
provider, and model.

## Worker Recovery

The API sidecar performs lightweight stale-run recovery on startup. Heavy
worker loops run in `paper-engine-worker` by default, not inside the API
process. Stale `running` parse/embedding/analysis runs are requeued until their
max attempts are reached; after that they are marked `failed`.

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAPER_ENGINE_API_BACKGROUND_WORKERS_ENABLED` | `0` | Optional compatibility switch to run workers inside the API process. |
| `PAPER_ENGINE_PARSE_WORKER_ENABLED` | unset | Explicitly enable/disable API-process parse worker when set to `1` or `0`. |
| `PAPER_ENGINE_EMBEDDING_WORKER_ENABLED` | unset | Explicitly enable/disable API-process embedding worker when set to `1` or `0`. |
| `PAPER_ENGINE_ANALYSIS_WORKER_ENABLED` | unset | Explicitly enable/disable API-process analysis worker when set to `1` or `0`. |
| `PAPER_ENGINE_PARSE_STALE_SECONDS` | `600` | Age threshold for recovering stale `running` jobs on startup. |
| `PAPER_ENGINE_PARSE_MAX_ATTEMPTS` | `3` | Maximum claim attempts before a stale job is failed. |
| `PAPER_ENGINE_PARSE_POLL_SECONDS` | `2` | Worker poll interval when no queued run is available. |
| `PAPER_ENGINE_EMBEDDING_BATCH_SIZE` | `16` | Number of passages embedded per provider call. |
| `PAPER_ENGINE_EMBEDDING_PREWARM_ENABLED` | `1` | Preload the embedding provider in the worker process. |
| `PAPER_ENGINE_BATCH_UPLOAD_MAX_FILES` | `20` | Maximum number of PDFs accepted by one batch upload request. |
| `PAPER_ENGINE_UPLOAD_MAX_BYTES` | `209715200` | Maximum size of one uploaded PDF in bytes. |

## Parser Setup

Parser settings are available from the app settings modal and through
`/api/agent/config`.

For MinerU:

```bash
curl -X PUT http://127.0.0.1:8000/api/agent/config \
  -H "Content-Type: application/json" \
  -d '{"pdf_parser_backend":"mineru","mineru_base_url":"http://127.0.0.1:8000","mineru_api_key":"YOUR_MINERU_API_KEY"}'
```

Then test the saved connection:

```bash
curl -X POST http://127.0.0.1:8000/api/agent/config/mineru/test
```

For Docling in development:

```bash
pip install docling
```

or install the project optional extra:

```bash
pip install -e ".[pdf-advanced]"
```

If Docling is selected but unavailable, parsing fails clearly instead of
falling back to another parser.

## GROBID Setup

GROBID is optional scholarly metadata enrichment. Start a GROBID service:

```bash
docker run --rm --init -p 8070:8070 lfoppiano/grobid:latest
```

Check that the service is alive:

```bash
curl http://127.0.0.1:8070/api/isalive
```

After the app has initialized its database, store the endpoint:

```bash
sqlite3 "${PAPER_ENGINE_DATA_DIR:-app-data}/paper_engine.db" \
  "INSERT INTO app_state (key, value) VALUES ('grobid_base_url', 'http://127.0.0.1:8070') ON CONFLICT(key) DO UPDATE SET value = excluded.value;"
```

If GROBID is not configured, not reachable, or returns invalid TEI, body
parsing still completes and records a warning.

## Privacy Implications

- Docling parses PDFs in the local Python process.
- MinerU receives the PDF at the configured `mineru_base_url`. Treat remote
  MinerU endpoints as third-party processing.
- GROBID receives the PDF over HTTP at the configured `grobid_base_url`.
- Parse results, warnings, source IDs, tables, assets, chunks, embeddings, and
  provenance are stored in the local SQLite database under
  `PAPER_ENGINE_DATA_DIR` or `app-data/` by default.

## Troubleshooting

- `docling is not installed`: install `docling` or the project
  `pdf-advanced` extra.
- `MinerU 连接测试失败`: verify `mineru_base_url`, API key, and service reachability.
- `missing_credentials`: configure `mineru_base_url` and any required API key.
- `worker_heartbeat_timeout`: a previous worker stopped while a run was
  `running`; restart recovery requeued or failed the job according to
  `attempt_count`.
- `embedding_error:*`: embeddings failed, so the parse run was not marked
  successful.

## Deprecated Compatibility Router

`paper_engine.pdf.router` remains only as a deprecated compatibility module for
tests and local migration scripts. Production upload and re-parse paths use the
selected-parser worker and do not call the automatic fallback router.

## References

- GROBID: <https://grobid.readthedocs.io/>
- Docling: <https://docling-project.github.io/docling/>
