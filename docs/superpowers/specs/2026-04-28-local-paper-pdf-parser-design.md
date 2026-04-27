# Local Paper PDF Parser Design

## Goal

Build a fully local, paper-specific PDF parsing layer that replaces cloud parser fallback with a deterministic local pipeline. The parser should quickly show scholarly metadata, produce source-grounded document elements for retrieval, and degrade to usable raw text instead of failing silently.

## Non-Goals

- No LlamaParse or other paid cloud parser in the default pipeline.
- No online metadata enrichment in the core path. Crossref or similar APIs are outside this local parser design.
- No general-purpose document ingestion beyond academic papers.

## Target Flow

```text
Upload
  -> validate / hash / store / create parse_job

Parse Worker
  -> PyMuPDF Profiler -> ParsePlan

  -> parallel:
       GROBID metadata + references
       Body parser:
         normal -> PyMuPDF4LLM
         complex/scanned -> MinerU

  -> normalize to ParseDocument
  -> quality gate
       if low and primary != MinerU -> rerun MinerU
       if still low -> raw PyMuPDF fallback + review_needed

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

Validation should reject non-PDF content, encrypted PDFs that cannot be opened locally, obviously invalid files, and files above the configured size/page limit.

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
- `failed`

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
- `primary_backend`
- `fallback_backend`
- `run_grobid`

Normal selectable papers should choose `pymupdf4llm` first. Scanned, table-heavy, formula-heavy, or unstable layout papers should choose `mineru` first.

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

### Body Parsers

Use `PyMuPDF4LLM` as the fast normal-PDF parser. Use raw PyMuPDF only for profiling and last-resort text fallback.

Use `MinerU` as the advanced local parser for scanned or complex academic PDFs. MinerU should be integrated behind the same `PdfParserBackend` contract as the existing backends and normalize its output into the current `ParseDocument`, `ParseElement`, `ParseTable`, and `ParseAsset` models.

Remove LlamaParse from the parser router. This local parser layer does not include a cloud parser path.

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

The quality gate runs after the body parser normalizes a document. It should score:

- page coverage
- extracted text density
- heading coverage
- table preservation
- formula block preservation when expected
- reference filtering
- garbled text ratio
- empty element ratio

If quality is low and the primary backend was not MinerU, rerun the body parse with MinerU. If MinerU also produces low quality, fall back to raw PyMuPDF text, preserve warnings, and mark the parse as `review_needed` or `completed_with_warnings` depending on whether searchable text exists.

### Academic Enrichment

Academic enrichment merges parser output and GROBID output:

- attach structured references to parse metadata
- bind figure/table captions to the nearest asset or table by page and bbox
- preserve formulas as equation elements when MinerU provides them
- locate citation mentions in body text where cheap enough

This step should not block basic searchability. If enrichment fails, the parse can still complete with warnings.

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

## Evaluation

Use `reference_paper/` as the first real-paper local evaluation corpus. These PDFs should not be committed by default unless explicitly requested.

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

Minimum acceptance for the first implementation:

- every reference PDF completes as `completed`, `completed_with_warnings`, or `review_needed`
- no reference PDF crashes the worker
- every parsed reference PDF has searchable passages or an explicit `review_needed` reason
- no passage exceeds the hard chunk token budget
- GROBID failure does not fail body parsing
- MinerU failure falls back to raw PyMuPDF text when possible

## Testing

Add unit tests for:

- `ParsePlan` generation
- router selection without LlamaParse
- MinerU backend normalization with mocked output
- quality gate fallback decisions
- GROBID metadata provenance merge
- worker status transitions

Add an evaluation script for `reference_paper/` that can run locally and write a JSON report. Keep it opt-in so normal CI does not depend on large local PDFs.

## Migration Strategy

1. Add job tables and worker state without changing the existing parse endpoint behavior.
2. Introduce `ParsePlan` and local-only router selection.
3. Add MinerU backend behind the existing backend protocol.
4. Move `/parse` to enqueue a job and optionally add a synchronous compatibility path for tests.
5. Add quality gate fallback and review status.
6. Add GROBID metadata provenance and reference enrichment.
7. Add real-paper evaluation using `reference_paper/`.

## Decisions

- Use an in-process background worker for the first implementation.
- Keep `papers.parse_status` compatible with the existing enum. Store `review_needed` on `parse_jobs.status`, `parse_runs.status`, and parse diagnostics. A paper with searchable fallback text can still show `parsed` at the paper row level.
- Configure GROBID as a local service URL. Do not bundle or auto-install GROBID in this implementation.
