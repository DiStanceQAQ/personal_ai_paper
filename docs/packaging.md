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
