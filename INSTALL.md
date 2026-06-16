# GTAV-Claude-MCP — Install

Talk to **Claude live inside GTA V (Legacy / build with ScriptHookV)**: it reads and writes game
memory, calls game natives safely, runs the **Claude FM** radio station, and chats in-game. Single-player
only.

> ⚠️ **Anti-cheat / EDR:** this works by loading code into `GTA5.exe`. Run it only **offline, in story
> mode**, on a machine **without** an injection-blocking EDR (e.g. SentinelOne). Never use it in GTA
> Online — that risks a ban and is not supported.

---

## What you get
| Piece | What it does | Need it? |
|---|---|---|
| **Bridge** (`pyscript\`, loaded with F9) | the in-game server Claude talks to | **required** |
| **MCP server** (`mcp_server\`) | exposes the bridge to any Claude client as tools | **required** |
| **Claude Chat UI** (`ClaudeChatUI.dll`) | the in-game F10 panel to chat with Claude | recommended |
| **Claude FM** (`ClaudeRadio.dll` + station DLC) | your own music as a radio station | optional |
| **Headless host** (`gtav_host.py`) | a Claude that drives the game on its own | optional |

---

## Prerequisites (install these first)
1. **GTA V (Legacy build)** — story mode, run **offline**.
2. **ScriptHookV** (Alexander Blade) → put `ScriptHookV.dll` + `dinput8.dll` in the GTA root.
3. **PyLoaderV** — the loader that embeds CPython in GTA and runs `pyscript\*.py` on **F9**.
4. **ScriptHookVDotNet3** (for the Chat UI + Claude FM) → `ScriptHookVDotNet3.asi` + runtime in the GTA root.
5. **Python 3.10+** on your PC (for the MCP server / host).
6. **A Claude client** — **Claude Code** (easiest) or Claude Desktop.
7. *(Claude FM only)* **OpenIV** with an `OpenIV.asi` **mods** folder, and **ffmpeg**.

---

## Step 1 — Python environment (do this OUTSIDE the project folder)
Never put the venv inside the project folder (Dropbox/Git sync over 10k+ files corrupts it).
```bat
python -m venv C:\Users\%USERNAME%\GTAV-Claude-MCP-venv
setx GTAV_MCP_VENV    C:\Users\%USERNAME%\GTAV-Claude-MCP-venv
setx GTAV_MCP_PYTHON  C:\Users\%USERNAME%\GTAV-Claude-MCP-venv\Scripts\python.exe
"C:\Users\%USERNAME%\GTAV-Claude-MCP-venv\Scripts\pip" install -r requirements.txt
```
Open a **new** terminal afterward so the variables take effect.

## Step 2 — Register the MCP server in your Claude client
- **Claude Code (easiest):** just open this project folder as the working directory. It auto-reads
  `.mcp.json` and exposes every tool as `mcp__gtav-memory__*` (`read_memory`, `call_function`,
  `re_scan`, `identify`, `patch_bytes`, …). You also inherit the `gta-genius` / `re-safety` skills and
  the `/make`, `/endless`, `/harvest`, `/crash-audit` commands automatically.
- **Claude Desktop:** add to `%APPDATA%\Claude\claude_desktop_config.json` (absolute paths):
  ```json
  { "mcpServers": { "gtav-memory": {
    "command": "C:\\Users\\<you>\\GTAV-Claude-MCP-venv\\Scripts\\python.exe",
    "args": ["-m","mcp_server.server"],
    "cwd": "C:\\path\\to\\GTAV-Claude-MCP" } } }
  ```

## Step 3 — Load the bridge in GTA
1. Copy `pyscript\*.py` (and `native_db.json`) into the game's **`pyscript\`** folder.
2. Launch GTA in story mode, then press **F9** (PyLoaderV) to load the bridge. It opens the socket
   server on `127.0.0.1:27015`.
3. Verify: in your Claude client, ask it to read the player position (e.g. `read_player_position`). A
   live answer means the whole chain works.

## Step 4 *(recommended)* — In-game chat panel
- Build `ui_companion\` → `ClaudeChatUI.dll`, drop it in the GTA **`scripts\`** folder (needs
  ScriptHookVDotNet3). Press **F10** in-game to open the panel and talk to Claude.

## Step 5 *(optional)* — Claude FM radio
Full walkthrough in **`dist/INSTALL.md`**. Short version: copy the station DLC to
`mods\update\x64\dlcpacks\claudefm\`, add it to `dlclist.xml`, drop `ClaudeRadio.dll` in `scripts\`,
then add music to `%LOCALAPPDATA%\GTAV-Claude-MCP\music` (ask Claude in-game to "play <song>" and it
fetches + indexes it for you).

## Step 6 *(optional)* — Headless "play and watch" host
Run `run_host.bat` (resolves your venv from Step 1). Requires `claude /login` once. This is a Claude
that talks to the game on its own — you control it via the in-game F10 panel.

---

## ⚠️ Antivirus & EDR
This tool works by running code **inside** `GTA5.exe` and reading/writing the game's live memory. To a
security product that behavior is indistinguishable from a cheat or trojan, so:
- **Antivirus may flag the prebuilt `.dll`s** (`ClaudeChatUI.dll`, `ClaudeRadio.dll`) or `yt-dlp` as a
  false positive. The bridge itself is plain Python source you can read. If quarantined, add your GTA V
  folder to your AV exclusions, or build the DLLs from source.
- **Enterprise EDR (SentinelOne, CrowdStrike, etc.) will likely BLOCK it entirely** — those products stop
  process injection by design, and PyLoaderV/ScriptHookV inject into the game. **Use a personal machine**,
  not a work/managed one. Do not attempt to disable or bypass a managed EDR — that's a policy/security
  violation, not a mod-install step.
- **Don't bundle the prerequisites.** Download ScriptHookV / PyLoaderV / SHVDN from their official pages;
  shipping copies of already-flagged binaries gets the whole folder quarantined.

See **`SECURITY.md`** for exactly what the bridge does to memory, what data leaves your machine, and why.

---

## Troubleshooting
- **Claude can't reach the game** → did you press **F9** in-game? Is GTA actually running? The bridge
  listens on `127.0.0.1:27015`.
- **`anyio` errors / host won't start** → something downgraded `anyio` below 4. Install spotDL in a
  *separate* venv; reinstall `requirements.txt`.
- **Nothing loads on F9** → confirm PyLoaderV is installed and `pyscript\` is in the right place.
- **Game crashes on a native call** → report it; never feed a raw native hash. The bundled `native_db`
  + by-name calls are the safe path.

See **`SETUP.md`** for the deep-dive and **`README.md`** for the architecture.
