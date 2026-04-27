# PDF Ingestion and AI Analysis Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade PDF parsing, chunking, source grounding, and AI paper analysis from a PyMuPDF block-level MVP into a best-in-class, local-first academic document ingestion pipeline with high-fidelity optional backends and measurable verification gates.

**Architecture:** The upgraded pipeline separates parsing, normalization, chunking, persistence, retrieval, and AI extraction into explicit modules. It defaults to a local route optimized for academic PDFs: PyMuPDF4LLM for clean digital PDFs, GROBID for scholarly metadata/references when configured, Docling for scanned or structure-heavy PDFs when installed, and LlamaParse as an explicit cloud fallback when a user configures an API key. Every parse and analysis run is stored with provenance, diagnostics, stable source IDs, and repeatable evaluation metrics.

**Tech Stack:** FastAPI, SQLite/FTS5, PyMuPDF, PyMuPDF4LLM, optional Docling, optional GROBID REST service, optional LlamaParse REST, Pydantic, httpx, pytest, mypy, Vite/React/TypeScript.

---

## Target Quality Bar

This plan intentionally targets the best practical result for a personal academic paper knowledge engine:

- Preserve document structure: title, headings, page numbers, reading order, tables, figures, captions, references, and source bounding boxes where available.
- Produce RAG chunks that are heading-aware, token-aware, table-safe, and stable across re-parses.
- Analyze the whole paper through a multi-stage pipeline instead of a single prompt over the first 60 passages.
- Require structured LLM outputs with schema validation, source passage verification, retries, and no silent card deletion.
- Keep local-first defaults while allowing high-fidelity backends when the user opts in.
- Add a golden evaluation harness so future parser changes are measured before being trusted.

## Primary Source Alignment

- PyMuPDF4LLM provides Markdown/JSON/TXT output, multi-column support, page chunks, layout analysis, OCR support, images, vector graphics, and table metadata.
- Unstructured-style chunking separates partitioning from chunking and preserves title/section boundaries.
- Docling provides local document conversion with reading order, tables, formulas, images, captions, bounding boxes, and structured chunks.
- GROBID is specialized for scholarly PDF structure, metadata, full text, references, and TEI output.
- Azure/Google/LlamaParse style systems treat layout, OCR, tables, figures, and strict structured extraction as first-class document AI capabilities.
- OpenAI Structured Outputs are preferred over JSON mode when the provider supports `json_schema` because JSON mode does not guarantee schema adherence.

## File Structure

Create or modify these focused modules:

- Create `db_migrations.py`: idempotent SQLite migrations and schema version tracking.
- Modify `db.py`: call migrations after base schema initialization.
- Create `pdf_models.py`: Pydantic models for parse documents, elements, assets, tables, quality reports, and chunks.
- Create `pdf_profile.py`: fast PDF inspection for digital/scanned/table-heavy/multi-column routing decisions.
- Create `pdf_backend_base.py`: parser backend protocol and shared backend result helpers.
- Create `pdf_backend_pymupdf4llm.py`: default high-quality local parser backend.
- Create `pdf_backend_legacy.py`: adapter around current `parser.py` behavior for compatibility and fallback.
- Create `pdf_backend_docling.py`: optional local high-fidelity backend guarded by import checks.
- Create `pdf_backend_grobid.py`: optional scholarly metadata/reference client for a configured GROBID endpoint.
- Create `pdf_backend_llamaparse.py`: optional cloud fallback adapter used only when configured.
- Create `pdf_router.py`: backend selection and fallback policy.
- Create `pdf_chunker.py`: heading-aware, token-aware, table-safe chunker.
- Create `pdf_persistence.py`: transactional storage of parse runs, elements, tables, assets, passages, and FTS rows.
- Create `analysis_models.py`: strict Pydantic schemas for paper metadata, extraction batches, cards, and analysis runs.
- Create `analysis_prompts.py`: prompt builders with source IDs and evidence rules.
- Create `analysis_pipeline.py`: multi-stage metadata, section, card, dedup, and persistence orchestration.
- Create `analysis_verifier.py`: schema/source/card validation and retry decisions.
- Create `embeddings.py`: optional embedding providers and storage for semantic retrieval.
- Create `hybrid_search.py`: FTS + semantic retrieval fusion.
- Modify `llm_client.py`: strict schema response format, provider capability flags, retryable validation errors.
- Modify `parser.py`: keep compatibility wrapper and route new callers to `pdf_router`.
- Modify `routes_papers.py`: use new parse pipeline transactionally and expose parse diagnostics.
- Modify `routes_agent.py`: use new analysis pipeline.
- Modify `routes_search.py`, `search.py`, `mcp_server.py`: expose richer source and hybrid retrieval when available.
- Modify `routes_cards.py`, `card_extractor.py`: preserve user cards and record extractor provenance.
- Modify `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/hooks/usePapers.ts`, `frontend/src/components/layout/Inspector.tsx`, `frontend/src/components/ui/PaperCard.tsx`: parse diagnostics, analysis progress, and source display.
- Modify `pyproject.toml`: runtime dependencies and optional parser extras.
- Create tests listed in each task.

## Global Verification Gates

Run these before any commit that touches backend behavior:

```bash
pytest -q
mypy .
```

Run these before any commit that touches frontend behavior:

```bash
npm run frontend:typecheck
npm run frontend:build
```

Run this before marking the branch complete:

```bash
pytest -q
mypy .
npm run frontend:typecheck
npm run frontend:build
```

Expected final result: all commands pass, parse evaluation report meets thresholds listed in Task 38, and manual smoke validates upload -> parse -> AI analyze -> search -> source display.

---

## Phase 0: Safety Nets and Test Assets

### Task 1: Capture Current Parser Behavior as Baseline

**Files:**
- Create: `tests/test_pdf_baseline_current_behavior.py`
- Modify: none

- [x] Add tests that document current behavior for a simple one-page PDF, empty PDF, and invalid PDF using existing `extract_passages_from_pdf`.
- [x] Run: `pytest tests/test_pdf_baseline_current_behavior.py -q`
- [x] Expected: PASS, proving the existing parser behavior is pinned before refactoring.
- [x] Commit:

```bash
git add tests/test_pdf_baseline_current_behavior.py
git commit -m "test: capture baseline PDF parser behavior"
```

### Task 2: Add Golden PDF Fixture Builder

**Files:**
- Create: `tests/fixtures/pdf_factory.py`
- Create: `tests/test_pdf_factory.py`

- [x] Implement fixture helpers that generate: `simple_academic_pdf`, `two_column_pdf`, `table_pdf`, `image_only_pdf`, `references_pdf`, and `long_section_pdf`.
- [x] Keep generated PDFs in temp directories during tests; do not commit binary PDFs.
- [x] Run: `pytest tests/test_pdf_factory.py -q`
- [x] Expected: PASS; each fixture opens with PyMuPDF and has the expected page count/content shape.
- [x] Commit:

```bash
git add tests/fixtures/pdf_factory.py tests/test_pdf_factory.py
git commit -m "test: add generated PDF fixtures"
```

### Task 3: Move `httpx` to Runtime Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_config.py` if dependency assertions exist

- [x] Move `httpx>=0.27.0` from dev-only usage into `[project].dependencies` because `llm_client.py` imports it at runtime.
- [x] Run: `python -m py_compile llm_client.py`
- [x] Run: `pytest tests/test_config.py tests/test_agent_executor.py -q`
- [x] Expected: PASS.
- [x] Commit:

```bash
git add pyproject.toml tests/test_config.py
git commit -m "fix: declare httpx runtime dependency"
```

---

## Phase 1: Durable Schema and Migrations

### Task 4: Add Idempotent SQLite Migration Runner

**Files:**
- Create: `db_migrations.py`
- Modify: `db.py`
- Test: `tests/test_db_migrations.py`

- [x] Create `app_state`-based schema version helpers: `get_schema_version(conn)`, `set_schema_version(conn, version)`, `apply_migrations(conn)`.
- [x] Call `apply_migrations(conn)` at the end of `init_db`.
- [x] Add tests for fresh database and repeated initialization.
- [x] Run: `pytest tests/test_db_migrations.py tests/test_db.py -q`
- [x] Expected: PASS; running `init_db` twice does not fail and leaves one schema version value.
- [x] Commit:

```bash
git add db_migrations.py db.py tests/test_db_migrations.py
git commit -m "feat: add SQLite schema migrations"
```

### Task 5: Add Parse Run and Document Element Tables

**Files:**
- Modify: `db_migrations.py`
- Test: `tests/test_db_migrations.py`

- [x] Add migration `1` creating `parse_runs`, `document_elements`, `document_tables`, and `document_assets`.
- [x] Use JSON text columns for backend-specific metadata: `warnings_json`, `config_json`, `bbox_json`, `heading_path_json`, `metadata_json`, `cells_json`.
- [x] Add indexes on `paper_id`, `space_id`, `parse_run_id`, and `(paper_id, element_index)`.
- [x] Run: `pytest tests/test_db_migrations.py -q`
- [x] Expected: PASS; table names and key indexes are present.
- [x] Commit:

```bash
git add db_migrations.py tests/test_db_migrations.py
git commit -m "feat: store parse runs and document elements"
```

### Task 6: Add Passage Provenance Columns

**Files:**
- Modify: `db_migrations.py`
- Test: `tests/test_db_migrations.py`
- Test: `tests/test_routes_papers.py`

- [x] Add migration `2` extending `passages` with `parse_run_id`, `element_ids_json`, `heading_path_json`, `bbox_json`, `token_count`, `char_count`, `content_hash`, `parser_backend`, `extraction_method`, and `quality_flags_json`.
- [x] Preserve existing rows by leaving new fields nullable or defaulted.
- [x] Add a unique index on `(paper_id, content_hash)` where `content_hash IS NOT NULL`.
- [x] Run: `pytest tests/test_db_migrations.py tests/test_routes_papers.py -q`
- [x] Expected: PASS; existing parse route remains functional.
- [x] Commit:

```bash
git add db_migrations.py tests/test_db_migrations.py tests/test_routes_papers.py
git commit -m "feat: add passage provenance fields"
```

### Task 7: Add Analysis Run and Card Provenance Schema

**Files:**
- Modify: `db_migrations.py`
- Modify: `db.py`
- Test: `tests/test_db_migrations.py`
- Test: `tests/test_routes_cards.py`

- [x] Add migration `3` creating `analysis_runs` and `knowledge_card_sources`.
- [x] Extend `knowledge_cards` with `created_by`, `extractor_version`, `analysis_run_id`, `evidence_json`, and `quality_flags_json`.
- [x] Default existing cards to `created_by='user'` when `user_edited=1`, otherwise `created_by='heuristic'`.
- [x] Run: `pytest tests/test_db_migrations.py tests/test_routes_cards.py -q`
- [x] Expected: PASS; existing card APIs still return prior fields.
- [x] Commit:

```bash
git add db_migrations.py db.py tests/test_db_migrations.py tests/test_routes_cards.py
git commit -m "feat: track analysis and card provenance"
```

---

## Phase 2: Parser Data Contracts and Routing

### Task 8: Define Parser Data Models

**Files:**
- Create: `pdf_models.py`
- Test: `tests/test_pdf_models.py`

- [x] Define Pydantic models: `PdfQualityReport`, `ParseElement`, `ParseTable`, `ParseAsset`, `ParseDocument`, `ChunkCandidate`, and `PassageRecord`.
- [x] Enforce element types: `title`, `heading`, `paragraph`, `list`, `table`, `figure`, `caption`, `equation`, `code`, `reference`, `page_header`, `page_footer`, `unknown`.
- [x] Enforce extraction methods: `native_text`, `ocr`, `layout_model`, `llm_parser`, `legacy`.
- [x] Run: `pytest tests/test_pdf_models.py -q && mypy pdf_models.py`
- [x] Expected: PASS.
- [x] Commit:

```bash
git add pdf_models.py tests/test_pdf_models.py
git commit -m "feat: define PDF parse data contracts"
```

### Task 9: Implement PDF Quality Profiler

**Files:**
- Create: `pdf_profile.py`
- Test: `tests/test_pdf_profile.py`

- [x] Implement `inspect_pdf(file_path: Path) -> PdfQualityReport`.
- [x] Report `page_count`, `native_text_pages`, `image_only_pages`, `estimated_table_pages`, `estimated_two_column_pages`, `needs_ocr`, `needs_layout_model`, `warnings`.
- [x] Use PyMuPDF only; do not add heavy dependencies.
- [x] Run: `pytest tests/test_pdf_profile.py -q`
- [x] Expected: PASS for all generated fixtures.
- [x] Commit:

```bash
git add pdf_profile.py tests/test_pdf_profile.py
git commit -m "feat: inspect PDF quality for parser routing"
```

### Task 10: Add Parser Backend Interface

**Files:**
- Create: `pdf_backend_base.py`
- Test: `tests/test_pdf_backend_base.py`

- [x] Define `PdfParserBackend` protocol with `name`, `is_available()`, and `parse(file_path, paper_id, space_id, quality_report) -> ParseDocument`.
- [x] Add `ParserBackendUnavailable` and `ParserBackendError` exceptions.
- [x] Run: `pytest tests/test_pdf_backend_base.py -q && mypy pdf_backend_base.py`
- [x] Expected: PASS.
- [x] Commit:

```bash
git add pdf_backend_base.py tests/test_pdf_backend_base.py
git commit -m "feat: define parser backend interface"
```

### Task 11: Add PyMuPDF4LLM Dependency and Availability Test

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_pdf_backend_pymupdf4llm_import.py`

- [x] Add `pymupdf4llm>=0.0.20` to runtime dependencies.
- [x] Add an import-time test that skips cleanly only when dependency installation has not been refreshed in the current environment.
- [x] Run: `pytest tests/test_pdf_backend_pymupdf4llm_import.py -q`
- [x] Expected: PASS or SKIP with explicit dependency-refresh message.
- [x] Commit:

```bash
git add pyproject.toml tests/test_pdf_backend_pymupdf4llm_import.py
git commit -m "build: add PyMuPDF4LLM dependency"
```

### Task 12: Implement PyMuPDF4LLM Backend

**Files:**
- Create: `pdf_backend_pymupdf4llm.py`
- Test: `tests/test_pdf_backend_pymupdf4llm.py`

- [x] Implement Markdown/page chunk parsing with `page_chunks=True`, `use_ocr=True`, `write_images=False`, `embed_images=False`.
- [x] Convert page chunk text, `toc_items`, `tables`, `images`, `graphics`, and `page_boxes` into `ParseDocument` elements.
- [x] Mark headers/footers as filtered elements when PyMuPDF4LLM identifies them or quality heuristics indicate repetition.
- [x] Run: `pytest tests/test_pdf_backend_pymupdf4llm.py -q`
- [x] Expected: PASS; simple academic PDF yields title/heading/paragraph elements and table fixture yields at least one table or table-like element.
- [x] Commit:

```bash
git add pdf_backend_pymupdf4llm.py tests/test_pdf_backend_pymupdf4llm.py
git commit -m "feat: parse PDFs with PyMuPDF4LLM"
```

### Task 13: Wrap Current Parser as Legacy Backend

**Files:**
- Create: `pdf_backend_legacy.py`
- Modify: `parser.py`
- Test: `tests/test_pdf_backend_legacy.py`
- Test: `tests/test_parser.py`

- [x] Move the current block extraction behavior behind `LegacyPyMuPDFBackend`.
- [x] Keep `extract_passages_from_pdf` returning the existing list-of-dicts shape.
- [x] Run: `pytest tests/test_pdf_backend_legacy.py tests/test_parser.py -q`
- [x] Expected: PASS; compatibility tests remain green.
- [x] Commit:

```bash
git add pdf_backend_legacy.py parser.py tests/test_pdf_backend_legacy.py tests/test_parser.py
git commit -m "refactor: wrap legacy PyMuPDF parser backend"
```

### Task 14: Add Optional Docling Backend

**Files:**
- Modify: `pyproject.toml`
- Create: `pdf_backend_docling.py`
- Test: `tests/test_pdf_backend_docling.py`

- [x] Add optional dependency group `pdf-advanced = ["docling>=2.0.0"]`.
- [x] Implement availability detection without importing Docling at app startup.
- [x] Convert Docling document text, tables, pictures, captions, formulas, headings, and reading order into `ParseDocument`.
- [x] Tests must mock Docling objects when Docling is absent and run live conversion when installed.
- [x] Run: `pytest tests/test_pdf_backend_docling.py -q`
- [x] Expected: PASS with mocked backend in default environment.
- [x] Commit:

```bash
git add pyproject.toml pdf_backend_docling.py tests/test_pdf_backend_docling.py
git commit -m "feat: add optional Docling parser backend"
```

### Task 15: Add Optional GROBID Client

**Files:**
- Create: `pdf_backend_grobid.py`
- Test: `tests/test_pdf_backend_grobid.py`

- [x] Implement `GrobidClient(base_url)` with `is_alive()`, `process_header(file_path)`, `process_fulltext(file_path)`, and TEI parsing helpers.
- [x] Extract title, authors, year, venue, DOI, abstract, section headings, and references from TEI.
- [x] Use configured endpoint from `app_state` key `grobid_base_url`; never require GROBID for default parsing.
- [x] Run: `pytest tests/test_pdf_backend_grobid.py -q`
- [x] Expected: PASS using mocked HTTP responses.
- [x] Commit:

```bash
git add pdf_backend_grobid.py tests/test_pdf_backend_grobid.py
git commit -m "feat: add optional GROBID scholarly parser"
```

### Task 16: Add Optional LlamaParse Adapter

**Files:**
- Create: `pdf_backend_llamaparse.py`
- Modify: `routes_agent.py`
- Test: `tests/test_pdf_backend_llamaparse.py`

- [x] Implement adapter for a configured `llamaparse_api_key` and `llamaparse_base_url`; default disabled.
- [x] Convert returned Markdown/JSON pages into `ParseDocument` with `extraction_method='llm_parser'`.
- [x] Add config API fields without exposing full API key in GET responses.
- [x] Run: `pytest tests/test_pdf_backend_llamaparse.py tests/test_agent.py -q`
- [x] Expected: PASS using mocked HTTP responses and redacted config responses.
- [x] Commit:

```bash
git add pdf_backend_llamaparse.py routes_agent.py tests/test_pdf_backend_llamaparse.py tests/test_agent.py
git commit -m "feat: add configured LlamaParse fallback"
```

### Task 17: Implement Backend Router

**Files:**
- Create: `pdf_router.py`
- Test: `tests/test_pdf_router.py`

- [x] Implement selection order: forced backend from settings, PyMuPDF4LLM for normal digital PDFs, Docling for OCR/layout-heavy PDFs when installed, LlamaParse for configured cloud fallback, legacy backend as last local fallback.
- [x] Merge GROBID metadata/references after primary parse when GROBID is configured and healthy.
- [x] Record attempted backends and fallback reasons in `ParseDocument.quality.warnings`.
- [x] Run: `pytest tests/test_pdf_router.py -q`
- [x] Expected: PASS for clean, scanned, Docling-unavailable, and cloud-configured routing cases.
- [x] Commit:

```bash
git add pdf_router.py tests/test_pdf_router.py
git commit -m "feat: route PDFs to best available parser backend"
```

---

## Phase 3: Chunking, Persistence, and Parse API

### Task 18: Implement Heading-Aware Chunker Tests

**Files:**
- Create: `tests/test_pdf_chunker.py`

- [x] Add failing tests for section boundary preservation, max token budget, overlap, reference filtering, table isolation, and stable content hashes.
- [x] Run: `pytest tests/test_pdf_chunker.py -q`
- [x] Expected: FAIL because `pdf_chunker.py` does not exist yet.
- [x] Commit:

```bash
git add tests/test_pdf_chunker.py
git commit -m "test: specify structure-aware PDF chunking"
```

### Task 19: Implement Heading-Aware Chunker

**Files:**
- Create: `pdf_chunker.py`
- Modify: `pyproject.toml`
- Test: `tests/test_pdf_chunker.py`

- [x] Add `tiktoken>=0.7.0` as a runtime dependency for token estimates with deterministic character fallback if encoder load fails.
- [x] Implement `chunk_parse_document(doc, max_tokens=900, soft_tokens=700, overlap_tokens=100) -> list[PassageRecord]`.
- [x] Keep a table as its own passage unless it exceeds max budget; then split by rows while preserving header rows.
- [x] Generate deterministic `content_hash` from normalized text, heading path, page range, and element IDs.
- [x] Run: `pytest tests/test_pdf_chunker.py -q && mypy pdf_chunker.py`
- [x] Expected: PASS.
- [x] Commit:

```bash
git add pyproject.toml pdf_chunker.py tests/test_pdf_chunker.py
git commit -m "feat: add heading-aware PDF chunker"
```

### Task 20: Implement Parse Persistence Layer

**Files:**
- Create: `pdf_persistence.py`
- Test: `tests/test_pdf_persistence.py`

- [x] Implement `persist_parse_result(conn, paper_id, space_id, parse_document, passages) -> str` returning `parse_run_id`.
- [x] Insert parse run, elements, assets, tables, passages, and FTS rows in one transaction.
- [x] Delete old generated parse rows only after new parse data is fully validated.
- [x] Preserve existing knowledge cards by nulling stale source IDs only when old passage hashes are absent from the new parse.
- [x] Run: `pytest tests/test_pdf_persistence.py -q`
- [x] Expected: PASS for successful parse, re-parse, and transaction rollback.
- [x] Commit:

```bash
git add pdf_persistence.py tests/test_pdf_persistence.py
git commit -m "feat: persist structured parse results transactionally"
```

### Task 21: Wire Parse Route to New Pipeline

**Files:**
- Modify: `routes_papers.py`
- Modify: `parser.py`
- Test: `tests/test_routes_papers.py`
- Test: `tests/test_search.py`

- [x] Replace direct `extract_passages_from_pdf` persistence in `parse_paper` with `inspect_pdf -> route_parse -> chunk_parse_document -> persist_parse_result`.
- [x] Keep response shape compatible: `status`, `paper_id`, `passage_count`; add `parse_run_id`, `backend`, `quality_score`, and `warnings`.
- [x] Run: `pytest tests/test_routes_papers.py tests/test_search.py -q`
- [x] Expected: PASS; upload and parse still populate `passages` and FTS.
- [x] Commit:

```bash
git add routes_papers.py parser.py tests/test_routes_papers.py tests/test_search.py
git commit -m "feat: use structured PDF parse pipeline"
```

### Task 22: Add Parse Diagnostics API

**Files:**
- Modify: `routes_papers.py`
- Test: `tests/test_routes_papers.py`

- [x] Add `GET /api/papers/{paper_id}/parse-runs` returning parse runs ordered by `created_at DESC`.
- [x] Add `GET /api/papers/{paper_id}/elements` with query params `type`, `page`, and `limit`.
- [x] Add `GET /api/papers/{paper_id}/tables`.
- [x] Run: `pytest tests/test_routes_papers.py -q`
- [x] Expected: PASS; diagnostics are scoped to the paper and active space.
- [x] Commit:

```bash
git add routes_papers.py tests/test_routes_papers.py
git commit -m "feat: expose PDF parse diagnostics"
```

### Task 23: Update API Client and Types for Parse Diagnostics

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

- [x] Add TypeScript types `ParseRun`, `DocumentElement`, `DocumentTable`, and extended parse response fields.
- [x] Add API methods `listParseRuns`, `listDocumentElements`, and `listDocumentTables`.
- [x] Run: `npm run frontend:typecheck`
- [x] Expected: PASS.
- [x] Commit:

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat: add frontend parse diagnostics API"
```

### Task 24: Show Parse Quality in the UI

**Files:**
- Modify: `frontend/src/hooks/usePapers.ts`
- Modify: `frontend/src/components/ui/PaperCard.tsx`
- Modify: `frontend/src/components/layout/Inspector.tsx`
- Modify: `frontend/src/styles.css`

- [x] Display parser backend, quality score, warning count, passage count, table count, and last parse time in the paper card or inspector.
- [x] Keep the UI compact and work-focused; do not add a marketing-style explanation panel.
- [x] Run: `npm run frontend:typecheck && npm run frontend:build`
- [x] Expected: PASS.
- [x] Commit:

```bash
git add frontend/src/hooks/usePapers.ts frontend/src/components/ui/PaperCard.tsx frontend/src/components/layout/Inspector.tsx frontend/src/styles.css
git commit -m "feat: show PDF parse quality diagnostics"
```

---

## Phase 4: Strict AI Analysis Pipeline

### Task 25: Define Analysis Schemas

**Files:**
- Create: `analysis_models.py`
- Test: `tests/test_analysis_models.py`

- [x] Define Pydantic models: `PaperMetadataExtraction`, `CardExtraction`, `CardExtractionBatch`, `MergedAnalysisResult`, `AnalysisQualityReport`.
- [x] Enforce card types using the DB card type vocabulary.
- [x] Require every AI card to include `source_passage_ids: list[str]`, `evidence_quote`, `confidence`, and `reasoning_summary`.
- [x] Run: `pytest tests/test_analysis_models.py -q && mypy analysis_models.py`
- [x] Expected: PASS.
- [x] Commit:

```bash
git add analysis_models.py tests/test_analysis_models.py
git commit -m "feat: define strict AI analysis schemas"
```

### Task 26: Add Structured Output Support to LLM Client

**Files:**
- Modify: `llm_client.py`
- Test: `tests/test_llm_client.py`

- [x] Add `call_llm_schema(system_prompt, user_prompt, schema_name, schema, provider_capabilities=None)` using `response_format={"type":"json_schema","json_schema":{"name": schema_name,"strict": true,"schema": schema}}` for compatible providers.
- [x] Keep JSON mode fallback for local OpenAI-compatible providers that reject schema format.
- [x] Add retry handling for invalid JSON, schema validation failure, and model refusal fields.
- [x] Run: `pytest tests/test_llm_client.py -q`
- [x] Expected: PASS with mocked Chat Completions responses.
- [x] Commit:

```bash
git add llm_client.py tests/test_llm_client.py
git commit -m "feat: support strict LLM structured outputs"
```

### Task 27: Add Analysis Prompt Builders

**Files:**
- Create: `analysis_prompts.py`
- Test: `tests/test_analysis_prompts.py`

- [x] Implement prompt builders for metadata extraction, section summary extraction, card batch extraction, and merge/dedup.
- [x] Include source passage IDs and page numbers in every prompt input.
- [x] Explicitly instruct the model to return only facts supported by source passage IDs.
- [x] Run: `pytest tests/test_analysis_prompts.py -q`
- [x] Expected: PASS; prompts contain source IDs and do not contain raw database internals.
- [x] Commit:

```bash
git add analysis_prompts.py tests/test_analysis_prompts.py
git commit -m "feat: build source-grounded analysis prompts"
```

### Task 28: Implement Metadata Extraction Stage

**Files:**
- Create: `analysis_pipeline.py`
- Modify: `pyproject.toml`
- Test: `tests/test_analysis_pipeline_metadata.py`

- [x] Implement `extract_metadata_stage(paper_id, passages, elements, grobid_metadata)` using first-page/title elements, DOI/arXiv regexes, GROBID metadata, and LLM schema fallback.
- [x] Prefer GROBID DOI/authors/year when available; prefer first-page title element over LLM guesses.
- [x] Run: `pytest tests/test_analysis_pipeline_metadata.py -q`
- [x] Expected: PASS for GROBID-present, GROBID-absent, and DOI-regex cases.
- [x] Commit:

```bash
git add analysis_pipeline.py tests/test_analysis_pipeline_metadata.py
git commit -m "feat: extract scholarly metadata with source priority"
```

### Task 29: Implement Passage Selection and Batching

**Files:**
- Modify: `analysis_pipeline.py`
- Test: `tests/test_analysis_pipeline_selection.py`

- [x] Select passages from the full paper, not just the first 60.
- [x] Group by heading path and passage type; prioritize abstract, introduction, method, result, discussion, limitation; exclude references unless analyzing citations.
- [x] Cap each LLM request by token budget using the same tokenizer helper as `pdf_chunker.py`.
- [x] Run: `pytest tests/test_analysis_pipeline_selection.py -q`
- [x] Expected: PASS; long fixture includes late result/limitation sections in selected batches.
- [x] Commit:

```bash
git add analysis_pipeline.py tests/test_analysis_pipeline_selection.py
git commit -m "feat: batch full-paper passages for AI analysis"
```

### Task 30: Implement Source Verification

**Files:**
- Create: `analysis_verifier.py`
- Test: `tests/test_analysis_verifier.py`

- [x] Verify every returned `source_passage_id` exists for the paper.
- [x] Verify `evidence_quote` appears as a normalized substring or high-overlap token subset of at least one source passage.
- [x] Reject cards with unsupported card type, empty summary, missing source, or evidence mismatch.
- [x] Run: `pytest tests/test_analysis_verifier.py -q`
- [x] Expected: PASS; hallucinated sources and unsupported evidence are rejected.
- [x] Commit:

```bash
git add analysis_verifier.py tests/test_analysis_verifier.py
git commit -m "feat: verify AI card source grounding"
```

### Task 31: Implement Card Batch Extraction Stage

**Files:**
- Modify: `analysis_pipeline.py`
- Test: `tests/test_analysis_pipeline_cards.py`

- [x] For each selected batch, call strict schema extraction and run `analysis_verifier`.
- [x] Retry once with a repair prompt when schema validation passes but source verification fails.
- [x] Return accepted cards and rejected-card diagnostics.
- [x] Run: `pytest tests/test_analysis_pipeline_cards.py -q`
- [x] Expected: PASS using mocked LLM responses for success, repair, and rejection.
- [x] Commit:

```bash
git add analysis_pipeline.py tests/test_analysis_pipeline_cards.py
git commit -m "feat: extract source-grounded AI card batches"
```

### Task 32: Implement Deduplication and Ranking

**Files:**
- Modify: `analysis_pipeline.py`
- Test: `tests/test_analysis_pipeline_dedup.py`

- [x] Deduplicate cards by normalized summary similarity, same card type, and overlapping source passage IDs.
- [x] Prefer higher confidence, stronger source coverage, and cards from method/result/limitation sections.
- [x] Limit final AI-generated cards to 20 by default while keeping rejected and overflow diagnostics in `analysis_runs`.
- [x] Run: `pytest tests/test_analysis_pipeline_dedup.py -q`
- [x] Expected: PASS; duplicate method/result cards collapse deterministically.
- [ ] Commit:

```bash
git add analysis_pipeline.py tests/test_analysis_pipeline_dedup.py
git commit -m "feat: deduplicate and rank extracted AI cards"
```

### Task 33: Persist Analysis Without Deleting User Work

**Files:**
- Modify: `analysis_pipeline.py`
- Modify: `routes_cards.py`
- Test: `tests/test_analysis_persistence.py`

- [ ] Create an `analysis_run` per AI run.
- [ ] Replace only prior cards where `created_by='ai'` and `paper_id` matches.
- [ ] Preserve cards with `created_by='user'`, `user_edited=1`, or `created_by='heuristic'`.
- [ ] Fill `knowledge_card_sources` with all source passage IDs.
- [ ] Run: `pytest tests/test_analysis_persistence.py tests/test_routes_cards.py -q`
- [ ] Expected: PASS; manually edited cards survive AI re-analysis.
- [ ] Commit:

```bash
git add analysis_pipeline.py routes_cards.py tests/test_analysis_persistence.py tests/test_routes_cards.py
git commit -m "feat: preserve user cards during AI analysis"
```

### Task 34: Route Agent Analysis Through New Pipeline

**Files:**
- Modify: `agent_executor.py`
- Modify: `routes_agent.py`
- Test: `tests/test_agent_executor.py`
- Test: `tests/test_agent.py`

- [ ] Replace single-prompt analysis with `run_paper_analysis(paper_id, space_id)`.
- [ ] Keep `/api/agent/analyze/{paper_id}` response compatible and add `analysis_run_id`, `accepted_card_count`, `rejected_card_count`, `metadata_confidence`.
- [ ] Run: `pytest tests/test_agent_executor.py tests/test_agent.py -q`
- [ ] Expected: PASS with mocked analysis pipeline.
- [ ] Commit:

```bash
git add agent_executor.py routes_agent.py tests/test_agent_executor.py tests/test_agent.py
git commit -m "feat: use multi-stage AI paper analysis"
```

### Task 35: Display Analysis Run Sources in UI

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/ui/KnowledgeCardFancy.tsx`
- Modify: `frontend/src/components/layout/Inspector.tsx`
- Modify: `frontend/src/styles.css`

- [ ] Show source passage count, primary page number, confidence, and card origin (`AI`, `Heuristic`, `Manual`).
- [ ] Add expandable evidence text from `evidence_json` without crowding the card list.
- [ ] Run: `npm run frontend:typecheck && npm run frontend:build`
- [ ] Expected: PASS.
- [ ] Commit:

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/components/ui/KnowledgeCardFancy.tsx frontend/src/components/layout/Inspector.tsx frontend/src/styles.css
git commit -m "feat: show AI card source grounding"
```

---

## Phase 5: Hybrid Retrieval and MCP Source Quality

### Task 36: Add Embedding Storage Schema and Providers

**Files:**
- Modify: `db_migrations.py`
- Create: `embeddings.py`
- Test: `tests/test_embeddings.py`

- [ ] Add migration `4` creating `passage_embeddings` with `passage_id`, `provider`, `model`, `dimension`, `embedding_json`, `content_hash`, and `created_at`.
- [ ] Implement provider interface for `none`, OpenAI-compatible embeddings, and local sentence-transformer when optional dependency is installed.
- [ ] Default provider is `none`; FTS remains functional without embeddings.
- [ ] Run: `pytest tests/test_embeddings.py tests/test_db_migrations.py -q`
- [ ] Expected: PASS with mocked providers.
- [ ] Commit:

```bash
git add db_migrations.py embeddings.py tests/test_embeddings.py tests/test_db_migrations.py
git commit -m "feat: add optional passage embeddings"
```

### Task 37: Generate Embeddings After Parse

**Files:**
- Modify: `pdf_persistence.py`
- Modify: `routes_papers.py`
- Test: `tests/test_embeddings_parse_integration.py`

- [ ] After passages are persisted, generate embeddings only when an embedding provider is configured.
- [ ] Store failures as parse warnings; do not fail parsing if embeddings fail.
- [ ] Run: `pytest tests/test_embeddings_parse_integration.py tests/test_routes_papers.py -q`
- [ ] Expected: PASS; parse succeeds with embedding provider disabled and stores embeddings when mocked provider is enabled.
- [ ] Commit:

```bash
git add pdf_persistence.py routes_papers.py tests/test_embeddings_parse_integration.py
git commit -m "feat: embed passages after parsing when configured"
```

### Task 38: Add Hybrid Search

**Files:**
- Create: `hybrid_search.py`
- Modify: `search.py`
- Modify: `routes_search.py`
- Test: `tests/test_hybrid_search.py`
- Test: `tests/test_search.py`

- [ ] Implement reciprocal rank fusion over FTS results and semantic vector results.
- [ ] Keep default route behavior compatible when embeddings are disabled.
- [ ] Add query param `mode=fts|hybrid` defaulting to `hybrid` when embeddings exist and `fts` otherwise.
- [ ] Run: `pytest tests/test_hybrid_search.py tests/test_search.py -q`
- [ ] Expected: PASS.
- [ ] Commit:

```bash
git add hybrid_search.py search.py routes_search.py tests/test_hybrid_search.py tests/test_search.py
git commit -m "feat: add hybrid passage search"
```

### Task 39: Upgrade MCP Source Output

**Files:**
- Modify: `mcp_server.py`
- Test: `tests/test_mcp.py`

- [ ] Include `parse_run_id`, `heading_path`, `parser_backend`, `quality_flags`, and `source_passage_ids` where applicable.
- [ ] Keep current MCP space isolation rules.
- [ ] Run: `pytest tests/test_mcp.py -q`
- [ ] Expected: PASS; MCP search and card tools include richer source metadata.
- [ ] Commit:

```bash
git add mcp_server.py tests/test_mcp.py
git commit -m "feat: expose richer MCP source metadata"
```

---

## Phase 6: Evaluation, Performance, and Packaging

### Task 40: Add Parse Evaluation Harness

**Files:**
- Create: `tests/eval/test_pdf_parse_quality.py`
- Create: `scripts/eval_pdf_pipeline.py`
- Test: `tests/eval/test_pdf_parse_quality.py`

- [ ] Evaluate generated golden PDFs for heading detection, table isolation, OCR routing, references filtering, source stability, and passage token budgets.
- [ ] Output JSON metrics with keys: `heading_recall`, `table_isolation`, `reference_filter_precision`, `stable_source_ratio`, `max_token_violation_count`, `parse_success_rate`.
- [ ] Run: `pytest tests/eval/test_pdf_parse_quality.py -q`
- [ ] Expected thresholds: `parse_success_rate=1.0`, `stable_source_ratio>=0.95`, `max_token_violation_count=0`, `reference_filter_precision>=0.90`.
- [ ] Commit:

```bash
git add tests/eval/test_pdf_parse_quality.py scripts/eval_pdf_pipeline.py
git commit -m "test: add PDF parse quality evaluation"
```

### Task 41: Add AI Analysis Evaluation Harness

**Files:**
- Create: `tests/eval/test_ai_analysis_quality.py`
- Create: `scripts/eval_analysis_pipeline.py`

- [ ] Evaluate mocked and deterministic model outputs for schema adherence, source verification, deduplication, user-card preservation, and full-paper coverage.
- [ ] Output JSON metrics with keys: `schema_validity_rate`, `source_grounding_rate`, `duplicate_card_rate`, `user_card_preservation_rate`, `late_section_coverage`.
- [ ] Run: `pytest tests/eval/test_ai_analysis_quality.py -q`
- [ ] Expected thresholds: `schema_validity_rate=1.0`, `source_grounding_rate=1.0`, `user_card_preservation_rate=1.0`, `duplicate_card_rate<=0.05`, `late_section_coverage=1.0`.
- [ ] Commit:

```bash
git add tests/eval/test_ai_analysis_quality.py scripts/eval_analysis_pipeline.py
git commit -m "test: add AI analysis quality evaluation"
```

### Task 42: Add Performance Budget Tests

**Files:**
- Create: `tests/test_pdf_pipeline_performance.py`

- [ ] Add a generated 20-page PDF performance test marked with `@pytest.mark.performance`.
- [ ] Assert local PyMuPDF4LLM route completes under a documented local budget when dependencies are installed; skip with clear reason in constrained CI.
- [ ] Run: `pytest tests/test_pdf_pipeline_performance.py -q`
- [ ] Expected: PASS or SKIP with explicit environment reason.
- [ ] Commit:

```bash
git add tests/test_pdf_pipeline_performance.py
git commit -m "test: add PDF pipeline performance budget"
```

### Task 43: Document Parser Configuration and Backend Tradeoffs

**Files:**
- Create: `docs/pdf-ingestion.md`
- Modify: `README.md`
- Modify: `docs/packaging.md`

- [ ] Document backend order, optional dependencies, GROBID setup, Docling setup, LlamaParse setup, privacy implications, and expected quality modes.
- [ ] Include exact commands for installing local advanced parsing: `pip install -e ".[pdf-advanced]"`.
- [ ] Run: `python -m py_compile api_sidecar.py`
- [ ] Expected: PASS.
- [ ] Commit:

```bash
git add docs/pdf-ingestion.md README.md docs/packaging.md
git commit -m "docs: describe PDF ingestion backends"
```

### Task 44: Update Sidecar Packaging

**Files:**
- Modify: `scripts/build_sidecars.py`
- Modify: `tests/test_build_sidecars.py`
- Modify: `pyproject.toml`

- [ ] Ensure new top-level modules are included in PyInstaller hidden imports or package collection.
- [ ] Keep heavy optional dependencies excluded from the default sidecar unless explicitly installed.
- [ ] Run: `pytest tests/test_build_sidecars.py -q`
- [ ] Expected: PASS.
- [ ] Commit:

```bash
git add scripts/build_sidecars.py tests/test_build_sidecars.py pyproject.toml
git commit -m "build: include upgraded PDF pipeline in sidecars"
```

### Task 45: Remove Obsolete Heuristic Assumptions

**Files:**
- Modify: `card_extractor.py`
- Modify: `routes_cards.py`
- Test: `tests/test_card_extractor.py`
- Test: `tests/test_card_extractor_domain_neutral.py`

- [ ] Mark heuristic extraction as `created_by='heuristic'` and low confidence.
- [ ] Prevent heuristic extraction from overwriting AI cards or user cards.
- [ ] Keep endpoint behavior compatible and message clear that heuristic cards require manual review.
- [ ] Run: `pytest tests/test_card_extractor.py tests/test_card_extractor_domain_neutral.py tests/test_routes_cards.py -q`
- [ ] Expected: PASS.
- [ ] Commit:

```bash
git add card_extractor.py routes_cards.py tests/test_card_extractor.py tests/test_card_extractor_domain_neutral.py tests/test_routes_cards.py
git commit -m "fix: isolate heuristic card extraction"
```

### Task 46: Full Backend Regression Gate

**Files:**
- Modify only files needed to fix failures found by the gate.

- [ ] Run: `pytest -q`
- [ ] Run: `mypy .`
- [ ] Expected: both PASS.
- [ ] Commit only if fixes are required:

```bash
git add <fixed-files>
git commit -m "test: satisfy backend regression gate"
```

### Task 47: Full Frontend Regression Gate

**Files:**
- Modify only files needed to fix failures found by the gate.

- [ ] Run: `npm run frontend:typecheck`
- [ ] Run: `npm run frontend:build`
- [ ] Expected: both PASS.
- [ ] Commit only if fixes are required:

```bash
git add <fixed-files>
git commit -m "test: satisfy frontend regression gate"
```

### Task 48: End-to-End Manual Smoke Gate

**Files:**
- Modify only files needed to fix failures found by the smoke test.

- [ ] Start the API and frontend using the project’s normal dev commands.
- [ ] Create a space.
- [ ] Upload a generated or real academic PDF.
- [ ] Run parse.
- [ ] Confirm parse diagnostics show backend, warnings, elements, tables, and passage count.
- [ ] Run AI analysis with a mocked or real configured LLM.
- [ ] Confirm metadata, cards, source evidence, and existing manual cards are correct.
- [ ] Search for a method/result phrase and confirm source page/passage opens in the UI.
- [ ] Run final gate:

```bash
pytest -q
mypy .
npm run frontend:typecheck
npm run frontend:build
```

- [ ] Expected: all automated gates PASS and manual smoke behavior matches the checklist.
- [ ] Commit only if smoke fixes are required:

```bash
git add <fixed-files>
git commit -m "fix: address PDF pipeline smoke issues"
```

---

## Implementation Order Notes

- Phases 0-3 can be implemented and released without configuring any cloud service.
- Phase 4 should be implemented before exposing richer AI controls in the UI.
- Phase 5 can be merged with embeddings disabled by default; FTS remains the baseline.
- Phase 6 is mandatory before declaring the upgrade complete because it prevents parser regressions from appearing as silent AI hallucinations.

## Self-Review

- Spec coverage: the plan covers parser quality, backend routing, structured chunks, transaction-safe persistence, strict AI schemas, source verification, user-card preservation, hybrid retrieval, MCP metadata, UI diagnostics, packaging, docs, and evaluation.
- Placeholder scan: no task uses undefined placeholder work; every task includes concrete files, commands, expected results, and commit messages.
- Type consistency: parser models flow through backends, router, chunker, persistence, routes, analysis pipeline, search, MCP, and UI using named contracts defined in this plan.
