# GTAV-Claude-MCP — Talk to Claude inside GTA V

Let **Claude see and safely act in a live GTA V game** — spawn vehicles, change weather/time,
call any of ~6,700 game natives, and read/write vehicle memory (e.g. a wheel-fitment mod) — and
**chat with Claude from inside the game** (press F10), with replies rendered in an in-game panel.

**Single-player only.** This is a memory-editing/modding tool; never use it in GTA Online.

> ## 🚧 Work in progress
> This project is an active **WIP** and not yet fully tested in-game — expect rough edges, breaking
> changes, and things that don't work yet. It's implemented and validated off-game (compiles,
> unit/round-trip tests pass), but full in-game end-to-end testing on a live GTA V install is still
> ongoing. Use at your own risk. Feedback and issues welcome.

> **Credit:** if you use, fork, or build on this, please credit **TabbedScamper** and link back to
> this repo (<https://github.com/TabbedScamper/GTAV-CLAUDE-MCP>). See [Credits](#credits).

## How it works

```
        You (in-game, press F10)
                │  message
                ▼
   pyscript/bridge.py  ──────────────►  gtav_host.py  ──────►  Claude (Agent SDK)
   (runs in GTA V via PyLoaderV)   long-poll        owns the     │ uses game tools via MCP
   reads/writes game memory        (≈instant)       session      ▼
                ▲                                          mcp_server/server.py
                │  reply (streamed to the in-game panel)         │ TCP :27015, JSON
                └──────────────────────────────────────── bridge.py
```

- **`gtav_host.py`** drives Claude via the **Claude Agent SDK** (reuses your `claude` login — no API
  key, no terminal, no focus stealing). It long-polls the bridge for your F10 messages, queries
  Claude, and streams replies into the in-game panel's shared memory. It's also **crash-aware**:
  as a separate process from GTA, it detects a game crash and reports the last operation from the
  on-disk write-ahead log.
- **`mcp_server/server.py`** exposes the game as MCP tools to Claude.
- **`pyscript/bridge.py`** runs *inside* GTA V (PyLoaderV) and does the actual memory access — with
  a verified-hash allowlist so Claude can call natives **by name** without crashing on a bad hash.
- **`ui_companion/`** is a ScriptHookVDotNet + LemonUI panel that renders the chat in-game.

## Prerequisites

In your GTA V install:
- [ScriptHookV](http://www.dev-c.com/gtav/scripthookv/)
- [PyLoaderV 0.6](https://www.gta5-mods.com/tools/pyloader-python-scripts-for-gta-v-enhanced) (ships an embedded Python 3.12 for the bridge)
- [ScriptHookVDotNet](https://github.com/scripthookvdotnet/scripthookvdotnet) (for the in-game panel)

On your PC (for the host):
- Python 3.10+ (tested on 3.14)
- A Claude subscription, logged in once via `claude /login` (the host reuses it)

## Setup

```powershell
# 1. Python deps (in a venv)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Log in to Claude once (no API key needed)
claude /login

# 3. Copy the runtime files into your GTA V install (auto-detects Steam/Epic/Rockstar)
powershell -ExecutionPolicy Bypass -File deploy.ps1
#    ...or: deploy.ps1 -GtaPath "X:\...\Grand Theft Auto V"
```

`deploy.ps1` copies `bridge.py` + `native_db.json` into `<GTA>\pyscript\` and the prebuilt
`ClaudeChatUI.dll` + `LemonUI.SHVDN3.dll` into `<GTA>\scripts\`.

## Run

1. Launch **GTA V** (story mode), press **F9** to load the Python bridge.
2. Run **`run_host.bat`** — it prints "Connected to Claude. Ready." (the in-game panel, F11, shows the same).
3. Press **F10** in-game and chat. Try: `spawn an adder` · `set thunder weather and midnight` ·
   `god mode on, give me a minigun`.

See [`TESTING.md`](TESTING.md) for the full first-run checklist and smoke tests.

## What Claude can do

- **Situational awareness:** `get_context` reads a full live snapshot (location, vehicle + class/
  plate/engine, speed, weapon + ammo, activity flags, time/weather, wanted level, health, game
  state) in one ~30ms call. The host pulls it on every message and **trims to what's relevant to
  that message** — e.g. *"in a ADDER (112 mph), in VINEWOOD, 21:45, THUNDER, wanted 3★, weapon
  CARBINERIFLE x90, (sprinting, shooting)"* for a combat message — so replies are aware without
  bloating the conversation. Claude can also call `get_context(detail="full")` on demand.
- **Convenience tools:** `spawn_vehicle`, `teleport`, `set_weather`, `set_time`, `give_weapon`,
  `repair_vehicle`, `get_player_ped`, `set_invincible`, `set_health`, `set_wanted_level`.
- **Any native, safely:** `search_natives`, `native_info`, and `call_native` (by name). The native
  is resolved to a verified hash for your edition (Legacy/Enhanced) and refused if not on the
  allowlist — so a wrong hash can't crash the game. Args are coerced to the declared types.
- **Memory / RE:** `read_memory`, `write_memory` (validated + page-protection-safe + undoable,
  returns before/after + verified), `snapshot`/`diff`, `watch`, `scan_pattern` (AOB),
  `resolve_rip_relative`.
- **Debugging workbench (build/debug anything):**
  - `inspect(handle | address)` — decode an entity or memory region into labeled, typed slots
    (float / int / pointer / zero / raw) with entity type + model; follow pointers to walk structs.
  - `get_environment` — ground truth: edition (Legacy/Enhanced), exe, module base/size, native-DB
    stats, and which offsets are verified for *this* build (so it never guesses the ground).
  - `set_goal` / `note_finding` / `get_findings` — a session memory of what you're building and the
    offsets/labels discovered, persisted across F9 reloads and auto-applied as `inspect` labels.
- **Vehicle wheels:** `get_wheel_values`, `set_wheel_value`, continuous per-frame re-assert.

## Safety / design

- **Measure, don't guess** — verify offsets on the live game via the bridge.
- **Never crash the host** — validate every address before deref, restore page protection after
  writes, single-step, snapshot + revert. A wrong native hash is refused, not called.
- **Single-player only** — GTA Online with mods = ban.
- **Antivirus/EDR note:** ScriptHookV/PyLoaderV inject into the game and edit memory, which AV/EDR
  (and especially enterprise EDR like SentinelOne) may quarantine or block. Use a personal machine
  and add the GTA folder to your AV exclusions if needed.

## Known wheel offsets (Legacy, verified)

| Field | Offset | Description |
|-------|--------|-------------|
| Y Rotation | 0x008 | Camber |
| Inv Y Rotation | 0x010 | Inverse camber |
| X Offset | 0x030 | Track width |
| Tyre Radius | 0x110 | Visual + collision size |
| Rim Radius | 0x114 | Visual rim |
| Tyre Width | 0x118 | Visual width |

## Crash diagnostics

- **Write-ahead log:** each write is logged to `pyscript/crash_logs/last_op.jsonl` (fsync'd) *before*
  execution, so it survives a crash.
- **faulthandler** dumps a Python traceback on hard crashes.
- The host reads the WAL from disk on a detected crash and reports the last operation.

## Project layout

```
gtav_host.py            # headless Claude host (Agent SDK) - the chat brain
mcp_server/server.py    # MCP server exposing the game as tools
pyscript/bridge.py      # in-game socket server + memory access (PyLoaderV)
pyscript/native_db.json # verified native-hash allowlist (~6,700 natives)
ui_companion/           # ScriptHookVDotNet + LemonUI in-game panel (prebuilt DLL included)
deploy.ps1              # copy runtime files into the GTA V install
run_host.bat            # launch the host
requirements.txt        # Python deps (claude-agent-sdk, mcp)
TESTING.md / CLAUDE.md  # first-run guide / project notes
```

## Credits

Created by **TabbedScamper** — <https://github.com/TabbedScamper/GTAV-CLAUDE-MCP>

If you use, fork, or build on this project, please **credit TabbedScamper and link back to this
repo**. The MIT license also requires retaining the copyright and license notice. A mention in
your README/mod description or a link back is all I ask. 🙏

## License

MIT — see [LICENSE](LICENSE). © 2026 TabbedScamper.
