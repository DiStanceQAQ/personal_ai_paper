# Desktop Packaging

## macOS

Build sidecars and the Tauri app:

```bash
.venv/bin/python -m pip install -e ".[dev]"
npm install
npm --prefix frontend install
.venv/bin/python scripts/build_sidecars.py --target all
npm run tauri build
```

This default command bundles the normal local parser stack: PyMuPDF,
PyMuPDF4LLM, the parser router, structured chunking, persistence, and analysis
modules. It does not intentionally bundle heavy optional parser dependencies.

To build a sidecar that includes Docling for advanced local PDF parsing, install
the optional extra before building:

```bash
.venv/bin/python -m pip install -e ".[dev,pdf-advanced]"
.venv/bin/python scripts/build_sidecars.py --target all
npm run tauri build
```

GROBID is not bundled in the desktop app. Run it as a separate HTTP service and
configure `grobid_base_url` in the app database. LlamaParse is also not bundled;
it uses the runtime HTTP client and only runs when a LlamaParse API key is
configured. See [PDF ingestion configuration](pdf-ingestion.md) for parser
backend order, setup commands, and privacy notes.

The root `npm run tauri build` script sets CI mode for the Tauri CLI. On macOS this skips Finder AppleScript DMG window customization, which keeps local automation and CI builds from hanging while still producing a valid unsigned DMG.

The unsigned `.dmg` is generated under:

```txt
src-tauri/target/release/bundle/dmg/
```

Unsigned builds may trigger macOS Gatekeeper warnings on other machines. Formal distribution requires Developer ID signing and notarization.

## Windows

Build Windows installers on a Windows machine or Windows CI runner:

```powershell
python -m pip install -e ".[dev]"
npm install
npm --prefix frontend install
python scripts/build_sidecars.py --target all
npm run tauri build
```

Use `python -m pip install -e ".[dev,pdf-advanced]"` instead when building a
Windows sidecar that should include Docling.

Formal distribution requires Windows code signing.

## MCP Sidecar

The packaged MCP executable is included as a Tauri sidecar named `paper-engine-mcp`.

First release behavior:

- The app does not modify Claude Code, Codex, Cursor, or other agent settings.
- Users copy the MCP executable path into their agent configuration.
- The MCP tool only exposes the currently active idea space after Agent Access is enabled in the app.

Example configuration shape:

```json
{
  "mcpServers": {
    "paper-knowledge-engine": {
      "command": "/path/to/paper-engine-mcp"
    }
  }
}
```
