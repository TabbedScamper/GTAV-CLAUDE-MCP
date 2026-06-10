# SETUP — get a Claude environment using the GTAV MCP tools

The tool chain:
```
Claude client  ->  MCP server (python -m mcp_server.server)  ->  socket 127.0.0.1:27015  ->  bridge.py (in GTA, F9)  ->  game
```
A Claude env needs BOTH: the MCP server registered, AND the bridge loaded in GTA (F9) for live data.

## 1. Make a venv OUTSIDE Dropbox (one time)
**Never put the venv inside the Dropbox project folder** — 10k+ files syncing across machines causes
sync-lock corruption (it deleted our `bridge.py` once). Put it outside:
```bat
python -m venv C:\Users\%USERNAME%\GTAV-Claude-MCP-venv
setx GTAV_MCP_VENV    C:\Users\%USERNAME%\GTAV-Claude-MCP-venv
setx GTAV_MCP_PYTHON  C:\Users\%USERNAME%\GTAV-Claude-MCP-venv\Scripts\python.exe
"C:\Users\%USERNAME%\GTAV-Claude-MCP-venv\Scripts\pip" install -r requirements.txt
```
(`GTAV_MCP_VENV` is used by `run_host.bat`; `GTAV_MCP_PYTHON` is used by `.mcp.json`. Open a NEW
terminal after `setx` so the vars take effect. capstone is optional — `pip install capstone` enables
the `disasm` tool; everything else is pure ctypes.)

## 2. Register the MCP server in your Claude client
- **Claude Code (easiest):** nothing to do — open Claude Code with this project folder as the working
  directory; it auto-reads `.mcp.json` and exposes every tool as `mcp__gtav-memory__*`
  (`read_memory`, `call_function`, `re_scan`, `identify`, `scan_first`/`scan_next`, `make_signature`,
  `patch_bytes`, …). `.mcp.json` uses `${GTAV_MCP_PYTHON:-python}`, so step 1 points it at the right venv.
- **Claude Desktop:** add to `%APPDATA%\Claude\claude_desktop_config.json` (absolute paths):
  ```json
  { "mcpServers": { "gtav-memory": {
    "command": "C:\\Users\\<you>\\GTAV-Claude-MCP-venv\\Scripts\\python.exe",
    "args": ["-m","mcp_server.server"],
    "cwd": "C:\\...\\GTAV-Claude-MCP" } } }
  ```
- **Any user/folder (Claude Code CLI):**
  `claude mcp add gtav-memory -- "%GTAV_MCP_PYTHON%" -m mcp_server.server`
- **Headless in-game host** (`gtav_host.py`): already configures the MCP internally; launch via
  `run_host.bat` (it resolves the venv per step 1). This is the Claude you talk to via in-game F10.

## 3. Load the bridge in GTA
Deploy `pyscript\*.py` to the game's `pyscript\` folder, then in-game press **F9** (PyLoaderV) to load
`bridge.py`. The MCP server connects to it on `127.0.0.1:27015`. Without this, the tools register but
calls error ("can't reach the bridge").

## 4. Use it
Ask the connected Claude to call a tool, e.g. `re_scan("48 8B 05 ?? ?? ?? ??")`, `identify(<ptr>)`,
`scan_first(...)`. Live data requires the bridge running (step 3); on a machine that can't run GTA the
tools still list but won't return game data.

## Notes
- `.mcp.json` is in the repo (Claude Code reads it automatically). The per-machine bit is the venv path,
  carried by the `GTAV_MCP_PYTHON` / `GTAV_MCP_VENV` env vars — no need to edit tracked files per machine.
- Multiple Claude envs can connect at once (the bridge handles one socket request at a time); the
  in-game host and an interactive Claude Code session can both drive it.
