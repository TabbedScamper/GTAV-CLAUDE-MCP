# First-run testing guide

## Prereqs (Python side)
- Create a venv and install deps:  `python -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`
  (verified working on Python 3.14 with claude-agent-sdk 0.2.94 + mcp 1.27.2)
- `claude` CLI present + logged in via `claude /login` (the host reuses your subscription — no API key)

## One-time setup (after GTA V is installed)
1. Install the modding prereqs into the GTA V folder:
   - **ScriptHookV** (dev-c.com) — `ScriptHookV.dll` + ASI loader
   - **PyLoaderV 0.6** (gta5-mods) — `PyloaderV.asi`, `pyscript/`, `shvpy_runtime/` (embedded Python 3.12)
   - **ScriptHookVDotNet** — for the in-game `ClaudeChatUI.dll` panel
   - (Enhanced edition: use the Enhanced builds + OpenRPF's `dsound.dll` loader)
2. Deploy our files:  `powershell -ExecutionPolicy Bypass -File deploy.ps1`
   (auto-detects GTA; or `-GtaPath "X:\...\Grand Theft Auto V"`). It copies `bridge.py` +
   `native_db.json` into `pyscript\` and `ClaudeChatUI.dll` into `scripts\`.

## Launch order
1. Start **GTA V** (story mode, single-player; BattlEye off).
2. In-game press **F9** → loads `bridge.py`. You should see "Claude MCP Bridge starting...".
3. Run **`run_host.bat`** (uses the venv). It prints "Connected to Claude. Ready." and the
   in-game panel (F11) shows the same. It self-registers so the UI can auto-launch it next time.
4. Press **F10** in-game to chat.

## The 3 smoke-test messages (validate the whole pipeline)
| # | Type in F10 | Proves |
|---|---|---|
| 1 | `hi, are you connected?` | chat in → Claude → reply renders in panel (full loop) |
| 2 | `spawn an adder` | native calls + the non-blocking spawn (no freeze) |
| 3 | `god mode on, set thunder weather, give me a minigun` | multi-action scenario + convenience tools |

A good result for #2: an Adder appears ~5m ahead, panel says something like "Done — Adder spawned."

## If something's off
- **Panel empty / "Waiting...":** host not running or shared memory not created → run `run_host.bat`; check its console for errors.
- **"bridge offline":** GTA not running or F9 not pressed → press F9 in-game.
- **Host import error:** confirm it ran via the venv (run_host.bat), not system python.
- **A native didn't work:** it won't crash now (allowlist) — ask Claude to `native_info` it / `search_natives`.
- **Crash:** the panel reports the last op from the WAL; also check `pyscript\crash_logs\`.

## Notes
- Single-player only. Never go to GTA Online with mods (ban).
- ConsoleTrigger.exe is retired — the host writes the panel directly.
- Reload the bridge after edits: **F9** (delete `pyscript\__pycache__` if it doesn't take).
