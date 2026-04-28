# MinerU API-First Paper PDF Parser Design

## Goal

Build a paper-specific PDF parsing layer whose body parser uses the configured MinerU Precision Parsing API as the primary parser. The parser should quickly show scholarly metadata, produce source-grounded document elements for retrieval, and degrade to local parsing or usable raw text instead of failing silently.

## Non-Goals

- No LlamaParse or non-MinerU parser API in the default pipeline.
- No online metadata enrichment in the core path. Crossref or similar APIs are outside this parser design.
- No general-purpose document ingestion beyond academic papers.
- No V1 citation-mention matching between body text and references. V1 stores structured references only.

## Target Flow

```text
Upload
  -> validate / hash / store / reuse cached parse or create parse_job

Parse Worker
  -> PyMuPDF Profiler -> ParsePlan

  -> parallel:
       GROBID metadata + references
       Body parser:
         configured MinerU API -> MinerU Precision Parsing API
         no API config -> local fallback
           normal -> PyMuPDF4LLM
           complex/scanned -> local MinerU when available
           last resort -> raw PyMuPDF

  -> normalize to ParseDocument
  -> quality gate
       if API result is low -> local fallback
       if local fallback is low -> raw PyMuPDF fallback + review_needed

  -> academic enrichment
       merge GROBID references
       bind figure/table captions
       keep formulas/tables/figures

  -> persist parse_run/elements/tables/assets/passages
  -> chunk/index
```

## Components

### Ingestion

Reuse the current upload route for file storage, hash calculation, and same-space duplicate detection. Extend ingestion so upload creates a durable `parse_job` record with `pending` status. The upload request should return quickly after the job is created.

Validation should reject non-PDF content, encrypted PDFs that cannot be opened locally, obviously invalid files, and files exceeding the maximum size/page limit.

Ingestion should also support global content-hash parse reuse. If the same file hash already has a successful parse result, the new paper should clone or re-link the existing parse output instead of rerunning expensive parsing. Same-space duplicate detection can still return the existing paper, but cross-space reuse should avoid duplicate MinerU/GROBID work.

### Parse Worker

The first implementation uses an in-process background worker. The interface should not depend on that choice, so the same job contract can later move to RQ, Celery, or another local queue without changing parser contracts.

The worker owns parse state transitions:

- `pending`
- `profiling`
- `metadata`
- `body_parsing`
- `enriching`
- `indexing`
- `completed`
- `completed_with_warnings`
- `review_needed`
- `cancelled`
- `failed`

V1 must support cancellation. The UI or API can set a job to `cancelled`; the worker checks cancellation before starting each stage and before starting any MinerU API or local MinerU run. Pausing is out of scope for V1.

Local MinerU is resource constrained. V1 must enforce a global local MinerU concurrency limit of one active local MinerU task per application process. Other jobs that need local MinerU wait in the queue with a timeout. The hosted MinerU API path uses HTTP timeouts and does not consume the local MinerU gate.

### PyMuPDF Profiler

The profiler opens the PDF cheaply and emits a structured `ParsePlan`.

The plan should include:

- page count
- native text page ratio
- image-only page ratio
- estimated table pages
- estimated formula/math density
- detected language when cheap enough
- `is_scanned`
- `is_complex_layout`
- `prefer_mineru_api`
- `local_fallback_backend`
- `last_resort_backend`
- `run_grobid`

All papers prefer the MinerU API when configured. The profiler only decides which local fallback backend should be used if the API is not configured or the API result fails quality checks.

V1 profiler rules are intentionally simple:

```python
is_scanned = native_text_page_ratio < 0.8
is_complex_layout = has_tables or has_formulas or has_multi_column_pages
prefer_mineru_api = True
local_fallback_backend = "mineru-local" if is_scanned or is_complex_layout else "pymupdf4llm"
last_resort_backend = "raw-pymupdf"
```

`has_tables` can use PyMuPDF table detection plus cheap keyword signals such as `Table 1`, `TABLE 1`, or dense grid drawings. `has_formulas` can use cheap text signals such as equation-heavy symbols, numbered equations, or MathJax-like fragments. These signals are routing hints, not correctness guarantees.

### GROBID Metadata And References

GROBID runs in parallel with body parsing when configured and healthy. Its output should update user-visible paper fields as soon as reliable values are available:

- title
- authors
- year
- venue
- DOI
- abstract

The final parse result must also preserve provenance:

- source value (`grobid`, `regex`, or `user`)
- confidence when available
- raw GROBID metadata payload
- structured references

References from GROBID are inputs to academic enrichment, not just paper metadata.

GROBID calls must have bounded runtime. V1 uses a total timeout of 60 seconds and one retry. If GROBID still fails, the job records a warning and continues with regex metadata fallback and body parsing. GROBID failure must never block or fail the parse job.

### Body Parsers

Use the MinerU Precision Parsing API as the primary body parser when `mineru_api_base_url` and `mineru_api_key` are configured.

Use `PyMuPDF4LLM` only as the fast normal-PDF local fallback. Use raw PyMuPDF only for profiling and last-resort text fallback.

Use local `MinerU` only as the advanced local fallback for scanned or complex academic PDFs when the MinerU API is not configured or produces low-quality output. MinerU API and local MinerU should both be integrated behind the same `PdfParserBackend` contract and normalize output into the current `ParseDocument`, `ParseElement`, `ParseTable`, and `ParseAsset` models.

Remove LlamaParse from the parser router. This parser layer does not include a non-MinerU parser API path.

If MinerU API is not configured, route directly to local fallback. If MinerU API fails quality checks, route to local fallback. If local fallback fails or times out, fall back to raw PyMuPDF text extraction when possible. That fallback is intentionally incomplete: it only provides searchable text and limited page provenance. The parse must be marked `completed_with_warnings` with a clear diagnostic such as `raw_text_only_fallback`; it must not silently appear equivalent to a structured parse.

### Normalization

All parser outputs must normalize to the existing structured parser contract:

- pages and page numbers
- headings
- paragraphs
- tables and cells
- figures/assets
- formulas when available
- bbox coordinates where available
- parser backend and extraction method
- source metadata

The existing `pdf_chunker` and `pdf_persistence` modules remain the downstream contract.

### Quality Gate

The quality gate runs after the body parser normalizes a document. V1 uses a deliberately small fallback rule:

```python
text_density = extracted_text_chars / max(page_count * 1000, 1)
low_quality = text_density < 0.3 or garbled_ratio > 0.1
```

Other signals are recorded for diagnostics only in V1:

- page coverage
- heading coverage
- table preservation
- formula block preservation when expected
- reference filtering
- empty element ratio

If quality is low and the body backend was `mineru-api`, rerun the body parse through the local fallback backend from `ParsePlan`. If local fallback also produces low quality, fall back to raw PyMuPDF text, preserve warnings, and mark the parse as `review_needed` or `completed_with_warnings` depending on whether searchable text exists.

### Academic Enrichment

Academic enrichment merges parser output and GROBID output:

- attach structured references to parse metadata
- bind figure/table captions to the nearest asset or table by page and bbox
- preserve formulas as equation elements when MinerU provides them

This step should not block basic searchability. If enrichment fails, the parse can still complete with warnings.

V1 does not locate in-text citation mentions or link citations back to individual GROBID reference entries. Citation mention matching is a separate enrichment phase and is disabled by default.

### Persistence And Indexing

Reuse the current parse persistence path:

- `parse_runs`
- `document_elements`
- `document_tables`
- `document_assets`
- `passages`
- FTS
- optional local embeddings

Extend stored parse diagnostics to include job stage timings, selected parse plan, fallback decisions, warnings, parser versions, and review flags.

Schema draft:

```sql
CREATE TABLE parse_jobs (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_backend TEXT NOT NULL DEFAULT '',
    parse_plan_json TEXT NOT NULL DEFAULT '{}',
    stage_timings_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    review_flags_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    FOREIGN KEY (paper_id, space_id) REFERENCES papers(id, space_id) ON DELETE CASCADE
);

ALTER TABLE parse_runs ADD COLUMN parse_plan_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE parse_runs ADD COLUMN stage_timings_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE parse_runs ADD COLUMN review_flags_json TEXT NOT NULL DEFAULT '[]';
```

The existing `parse_runs.metadata_json`, `warnings_json`, `backend`, `extraction_method`, and `quality_score` columns remain the main parse-run record.

## Version Locking

Parser outputs are version-sensitive. V1 must record parser versions in parse diagnostics and provide a reproducible local dependency file for parser extras, such as `requirements-local-parser.txt`.

Version diagnostics should include:

- PyMuPDF version
- PyMuPDF4LLM version
- MinerU API model profile when available
- local MinerU version and model profile when available
- GROBID service version when available

The reference-paper evaluation report should include these versions so quality changes can be compared across upgrades.

## Evaluation

Use `reference_paper/` as the first real-paper evaluation corpus. These PDFs must not be committed. The directory should be ignored by git. Keep only a lightweight manifest, such as `reference_paper/papers.json` or `tests/fixtures/reference_papers.json`, with filename, DOI or source URL when known, and whether the PDF is allowed for local testing.

Evaluation should report per paper:

- selected backend
- parse status
- page coverage
- passage count
- table count
- figure/asset count
- formula/equation count
- metadata fields found
- reference count
- max token violations
- warnings
- elapsed time
- parser versions

Minimum acceptance for the first implementation:

- every reference PDF completes as `completed`, `completed_with_warnings`, or `review_needed`
- no reference PDF crashes the worker
- every parsed reference PDF has searchable passages or an explicit `review_needed` reason
- no passage exceeds the hard chunk token budget
- GROBID failure does not fail body parsing
- MinerU API absence or failure falls back to local parsing
- local fallback failure falls back to raw PyMuPDF text when possible
- raw PyMuPDF fallback is marked `completed_with_warnings` and includes a user-visible "raw text only" warning

## Testing

Add unit tests for:

- `ParsePlan` generation
- router selection without LlamaParse
- MinerU API backend normalization with mocked HTTP output
- local fallback selection when MinerU API is not configured
- quality gate fallback decisions
- GROBID metadata provenance merge
- worker status transitions
- local MinerU concurrency limit
- GROBID timeout/retry fallback
- cancellation before expensive parser stages
- content-hash parse reuse

Add an evaluation script for `reference_paper/` that can run locally and write a JSON report. Keep it opt-in so normal CI does not depend on large local PDFs.

## Migration Strategy

1. Add job tables and worker state without changing the existing parse endpoint behavior.
2. Introduce `ParsePlan` and MinerU API-first router selection.
3. Add MinerU API backend and local fallback backends behind the existing backend protocol.
4. Move `/parse` to enqueue a job and optionally add a synchronous compatibility path for tests.
5. Add quality gate fallback and review status.
6. Add GROBID metadata provenance and reference enrichment.
7. Add real-paper evaluation using `reference_paper/`.

## Decisions

- Use an in-process background worker for the first implementation.
- Keep `papers.parse_status` compatible with the existing enum. Store `review_needed` on `parse_jobs.status`, `parse_runs.status`, and parse diagnostics. A paper with searchable fallback text can still show `parsed` at the paper row level.
- Configure MinerU API with `mineru_api_base_url` and `mineru_api_key` in `app_state`, or `MINERU_API_BASE_URL` and `MINERU_API_KEY` in the environment; if either value is missing, skip the API path and use local fallback.
- Configure GROBID as a local service URL. Do not bundle or auto-install GROBID in this implementation.
