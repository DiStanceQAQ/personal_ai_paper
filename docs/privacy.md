# Privacy and Data Handling

Local Paper Knowledge Engine is designed as a local-first research tool. This
document summarizes what stays on your machine and what may leave it when you
enable optional services.

## Local Data

By default, development data is stored under:

```text
app-data/
```

Desktop builds use the operating system's application data directory. You can
override the data directory with:

```bash
PAPER_ENGINE_DATA_DIR=/path/to/data
```

The local data directory may contain:

- Uploaded PDF files.
- SQLite database `paper_engine.db`.
- Parsed passages, document elements, tables, and source metadata.
- Knowledge cards and AI analysis run diagnostics.
- Embeddings for local retrieval.
- Runtime configuration saved from the app settings UI.

Do not commit your data directory to a public repository.

## LLM Analysis

AI analysis sends selected paper passages and prompt instructions to the
configured OpenAI-compatible LLM endpoint. The app stores the structured result
locally as:

- `analysis_runs.metadata_json.paper_understanding_zh`
- Derived `knowledge_cards`
- `knowledge_card_sources`

If you use a remote LLM provider, the selected paper text sent in prompts is
processed by that provider. Review your provider's data policy before using
private or unpublished papers.

## PDF Parsing Services

The local Docling backend runs in the local Python process.

Optional HTTP parser services may receive PDF content:

- MinerU receives PDFs at the configured `mineru_base_url`.
- GROBID receives PDFs at the configured `grobid_base_url`.

Only configure services you trust for the documents you import.

## MCP Agent Access

The MCP server is disabled by default. External agents can only access the
current active space after Agent Access is enabled in the app.

The MCP server is scoped to the active space. Requests for another space are
rejected to reduce accidental cross-project context leakage.

## Secrets

Never commit real API keys. Use `.env.example` as a template and keep local
secrets in `.env` or in the app settings database.

If a secret was committed accidentally, rotate the key with the provider before
publishing the repository.
