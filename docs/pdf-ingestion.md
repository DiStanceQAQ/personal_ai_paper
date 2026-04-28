# PDF Ingestion Configuration

The PDF ingestion pipeline is local-first. It profiles every PDF, selects a
parser backend, normalizes the result into document elements, chunks those
elements into source-grounded passages, and persists parse diagnostics with the
paper.

## Backend Order

The router uses `pdf_profile.inspect_pdf` to decide whether a file needs OCR or
layout-aware parsing.

1. If `app_state.pdf_forced_backend` is configured, that backend is tried first.
   Supported values are `pymupdf4llm`, `docling`, `llamaparse`, `legacy`, and
   `legacy-pymupdf`.
2. For image-only PDFs, the default local order is Docling, then configured
   LlamaParse, then legacy PyMuPDF.
3. For table-heavy or multi-column PDFs with native text, the default local
   order is Docling, then configured LlamaParse, then PyMuPDF4LLM, then legacy
   PyMuPDF.
4. For normal digital PDFs, the default local order is PyMuPDF4LLM, then
   configured LlamaParse, then legacy PyMuPDF.
5. If legacy PyMuPDF is selected for OCR- or layout-sensitive PDFs, the router
   records `router_degraded:legacy-pymupdf:advanced_parser_unavailable_for_layout_pdf`
   so callers can show a quality warning instead of treating the fallback as a
   normal high-fidelity parse.
6. GROBID is optional post-parse enrichment. When `app_state.grobid_base_url` is
   configured and the service is alive, GROBID metadata and references are
   merged into the parse metadata after the selected parser succeeds.

Every attempted backend writes router diagnostics into parse warnings, including
`router_attempt:*`, `router_selected:*`, `router_unavailable:*`, and
`router_failed:*`.

## Quality Modes

| Mode | Backend | Runs where | Best for | Tradeoffs |
| --- | --- | --- | --- | --- |
| Default digital | PyMuPDF4LLM | Local process | Clean academic PDFs with native text | Fast and private, but not OCR-first. |
| Advanced local | Docling | Local process | Scanned, table-heavy, figure-heavy, or multi-column PDFs | Better structure extraction, but heavier install and packaging cost. |
| Scholarly enrichment | GROBID | Local or remote HTTP service | Titles, authors, abstracts, DOIs, citations, and references | Excellent scholarly metadata, but requires a separate service. |
| Cloud rescue | LlamaParse | External API | Difficult PDFs where local parsing fails | Sends PDF content to a third party and requires an API key. |
| Compatibility fallback | Legacy PyMuPDF | Local process | Keeping older behavior available | Lower structure fidelity. |

## Default Install

The normal project install includes PyMuPDF and PyMuPDF4LLM:

```bash
pip install -e ".[dev]"
```

This is enough for local parsing of digital PDFs. No external parser service is
required.

## Advanced Local Parsing with Docling

Install the optional advanced parser extra:

```bash
pip install -e ".[pdf-advanced]"
```

For development environments that also need tests and packaging tools, install
both extras:

```bash
pip install -e ".[dev,pdf-advanced]"
```

Confirm that the optional backend is visible:

```bash
python -c "from pdf_backend_docling import DoclingBackend; print(DoclingBackend().is_available())"
```

Docling stays local, but it is a heavy dependency. Build default sidecars
without it unless you explicitly want advanced local parsing bundled.

## GROBID Setup

GROBID is configured through the `grobid_base_url` value in `app_state`. Start a
GROBID service first:

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

GROBID is optional. If it is not configured, not reachable, or returns invalid
TEI, parsing continues without scholarly metadata enrichment and records a
warning.

## LlamaParse Setup

LlamaParse is disabled unless an API key is stored. The backend uses the default
base URL `https://api.cloud.llamaindex.ai`.

With this app's backend running locally, configure LlamaParse through the app
API:

```bash
curl -X PUT http://127.0.0.1:8000/api/agent/config \
  -H "Content-Type: application/json" \
  -d '{"llamaparse_base_url":"https://api.cloud.llamaindex.ai","llamaparse_api_key":"YOUR_LLAMA_CLOUD_API_KEY"}'
```

Or write the values directly after the database exists:

```bash
sqlite3 "${PAPER_ENGINE_DATA_DIR:-app-data}/paper_engine.db" \
  "INSERT INTO app_state (key, value) VALUES ('llamaparse_api_key', 'YOUR_LLAMA_CLOUD_API_KEY') ON CONFLICT(key) DO UPDATE SET value = excluded.value;"
```

LlamaParse is tried only when configured. In the default order it is a fallback
after the preferred local backend fails or is unavailable. To make it the first
attempt for troubleshooting, force it:

```bash
sqlite3 "${PAPER_ENGINE_DATA_DIR:-app-data}/paper_engine.db" \
  "INSERT INTO app_state (key, value) VALUES ('pdf_forced_backend', 'llamaparse') ON CONFLICT(key) DO UPDATE SET value = excluded.value;"
```

Clear the forced backend with:

```bash
sqlite3 "${PAPER_ENGINE_DATA_DIR:-app-data}/paper_engine.db" \
  "DELETE FROM app_state WHERE key = 'pdf_forced_backend';"
```

## Privacy Implications

- PyMuPDF4LLM, Docling, and legacy PyMuPDF parse PDFs in the local Python
  process.
- GROBID receives the PDF over HTTP at the configured `grobid_base_url`. Treat a
  remote GROBID endpoint as third-party processing.
- LlamaParse uploads PDF bytes to the configured cloud API. Do not configure it
  for confidential papers unless that service is acceptable for the data.
- Parse results, warnings, source IDs, tables, assets, chunks, and provenance are
  stored in the local SQLite database under `PAPER_ENGINE_DATA_DIR` or
  `app-data/` by default.

## Troubleshooting

- `router_unavailable:docling`: install `pip install -e ".[pdf-advanced]"` or
  let the router fall back.
- `router_grobid_unavailable:is_alive returned false`: start GROBID or update
  `grobid_base_url`.
- `llamaparse_api_key is not configured`: add the key or clear
  `pdf_forced_backend` if LlamaParse was forced accidentally.
- Empty or low-quality passages usually mean the PDF has no usable native text
  and needs Docling or explicit cloud fallback. GROBID can improve scholarly
  metadata and references, but it is not a replacement body-text parser.

## References

- GROBID: <https://grobid.readthedocs.io/>
- Docling: <https://docling-project.github.io/docling/>
- LlamaParse: <https://docs.cloud.llamaindex.ai/llamaparse/getting_started>
