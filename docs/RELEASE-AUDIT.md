# Release audit — overall description of every aspect (for the weekend release)

Goal: ship GTAV-CLAUDE-MCP simple to install + use, including **exactly what's needed**. This is the
deep-dive: every subsystem, what it does, its files, and a **ship classification** —
**[CORE]** must ship · **[OPTIONAL]** useful, keep but not required · **[HOLD]** experimental/this-session,
exclude from the simple release (move to an `experimental/` folder or a later "advanced" drop).

## The product in one line
An MCP bridge that embeds CPython inside GTA5.exe (PyLoaderV + ScriptHookV) so Claude can read/write live
game memory, call any native safely by name, read the extracted game data, run a custom radio, and chat
in-game — single-player.

---

## Subsystems

### 1. In-game bridge — `pyscript/bridge.py` (3,499 lines)  [CORE]
The heart: a background socket server (127.0.0.1:27015, 4-byte LE + JSON), the command dispatch table
(~90 commands), the memory primitives (read/write/scan, entity address, off-thread/paused-safe path), the
deferred (per-frame) mechanism, the WAL crash log, and the F9 hot-reload + module-wiring block.
*Audit note:* 3.5k lines is large — a candidate for a dead-code/redundancy pass and splitting the module
groups. This is THE thing to review for optimization.

### 2. MCP server — `mcp_server/server.py` (1,611 lines)  [CORE]
Exposes bridge commands as MCP tools (`@mcp.tool` → `_send_command`). *Audit note:* grew with every
feature; many tools are for [HOLD] subsystems — trimming those shrinks it a lot.

### 3. Native DB — `pyscript/native_db.json` (~3 MB) + `tools/build_native_db.py`  [CORE]
The verified allowlist (6,700 natives) that makes "call any native by name" crash-safe. Essential.

### 4. Extracted-game-data tools — `mcp_server/gtadata.py` (259 lines)  [CORE]
Read the NG-decrypted game files (radio/handling/meta) — the "answer game-data questions accurately" layer.
*Dependency:* points at an extracted data root (machine-local `gtadata_local.json`).

### 5. RE toolkit — `pyscript/re_tools*.py` (5 files, ~1,560 lines)  [OPTIONAL→CORE]
Reverse-engineering: AOB scan, .pdata function bounds, RTTI/identify, value-scan, code-patch/NOP, par-dump
labels. This is the "reverse-engineer anything" value prop. Keep, but it's optional for a basic install
(capstone/numpy are soft deps).

### 6. Claude FM radio — `mcp_server/radio.py` (322 lines) + C# `ClaudeRadio.dll`  [CORE feature]
The flagship custom-radio add-on. *Dependencies:* ffmpeg/ffprobe (download separately), the C# DLL, a
music folder. Heaviest install footprint — worth a dedicated install section.

### 7. Build-anything knowledge layer  [SPLIT]
- `pyscript/gta_catalog.py` + `catalogs/*.json` (name→hash) and `gta_recipes.py` + `recipes/recipes.json` —
  small, useful, self-contained. **[OPTIONAL] keep.**
- `Examples/PATTERNS/*.md` (14 files) + `manifest.json` — the harvested technique knowledge. **[OPTIONAL]
  reference** (great for the community, but it's docs, not runtime).

### 8. In-game UI companion — `ui_companion/` → `ClaudeChatUI.dll` (LemonUI)  [CORE]
The F10/F11 chat panel that reads shared memory. Needed for the in-game conversation experience.

### 9. Headless host — `gtav_host.py` (492 lines)  [CORE]
Drives Claude via the Agent SDK (reuses `claude /login`), writes the transcript to shared memory. The
seamless chat path. *Dependency:* `claude_agent_sdk` (pip).

### 10. Vehicle tuning — `pyscript/vehicle_tuning.py` (187 lines)  [OPTIONAL]
Native wheel fitment + live handling. ExtendedLSC-adjacent; keep if ExtendedLSC ships alongside, else optional.

### 11. Autonomous agent + mission stack  [HOLD — exclude from the simple release]
Built this session, **logic-tested but NOT yet verified in-game**, and a big complexity/install add:
- `pyscript/`: `world_sense.py`, `mission_sense.py`, `agent_actions.py`, `commentary.py` (+ data
  `landmarks.json`, `speech_contexts.json`, `mission_dialogue.json`).
- `tools/`: `play_agent.py`, `claude_strategist.py`, `mission_runner.py`, `play_heist.py`, `scenario_gen.py`
  (+ `scenarios/`).
Impressive, but not part of the core "Claude in GTA" value prop and unverified. **Recommend: hold for a
separate "experimental" drop**, keep it out of the simple installer.

### 12. Custom-animation pipeline — `tools/author_animation.py`, `blender_export_ycd.py`  [HOLD]
Needs Blender+Sollumz+CodeWalker.API. Advanced; exclude from the simple release.

### 13. Docs — 11 root `.md` + 8 `docs/` + 14 `Examples/PATTERNS/`  [TRIM]
Sprawling — many are session working notes. **For release, consolidate to: `README` (what+why+quickstart),
`INSTALL` (step-by-step + prerequisites + verify), `USAGE` (how to use), `CLAUDE.md` (kept — it's the
in-tool guide).** Keep `STUDIES.md` / `PATTERNS/` / `RE-TOOLKIT*.md` as optional reference. Archive or drop
the rest (NORTH-STAR, PLAY-AND-WATCH, GROUNDED-MISSIONS, WORLD-SENSING, the handoff/runbook working docs).

---

## Recommended LEAN release (exactly what's needed to function)
**Ship:** bridge.py (reviewed/trimmed), mcp_server (server+gtadata+radio), native_db, RE toolkit,
ClaudeChatUI.dll + ClaudeRadio.dll (prebuilt), gtav_host.py, catalogs+recipes, CLAUDE.md, and clean
**README + INSTALL + USAGE**. Plus `.mcp.json`/`requirements.txt`/`run_host.bat`.
**Hold (experimental folder or later):** the agent/mission/commentary/scenario stack (#11), the custom-anim
pipeline (#12).
**Trim:** the docs down to the 3–4 user-facing ones + reference.

## Path to release
1. **This audit** ✅ — agree the scope (what ships vs holds).
2. **Optimize/clean** — review the core code against the reference sources (SHVDN/YimMenuV2/the PATTERNS
   findings) for better/safer patterns; dead-code pass on bridge.py + server.py; confirm the install deps.
3. **Restructure** — core vs `experimental/`; one clean dependency list.
4. **Install docs** — a single, foolproof INSTALL.md (prereqs → files → load → verify) + USAGE.md.

---

# ExtendedLSC — audit (`Projects/ExtendedLSC`)

A net48 **ScriptHookVDotNet3** mod (Extended Los Santos Customs): vehicle customization menu + **wheel
fitment/stance** + **manual transmission** + custom HUD/art. Deps: `ScriptHookVDotNet3 3.6.0`,
`LemonUI.SHVDN3 2.1.1`, `Newtonsoft.Json 13.0.3`.

## Source layout (`src/`, clean)  [CORE]
- `Main.cs` (**8,359 lines** — the monolith; everything routes through it)
- `WheelFitment/` — `WheelFitment.cs`, `WheelFitmentNative.cs` (the native VStancer-style path), `WheelMemory.cs`,
  `WheelBones.cs`, `VehicleStanceManager.cs`, `PatternScanner.cs`
- `ManualTransmission/` — `ELSCTransmission.cs`, `VehicleMemory.cs`, `TransmissionHUD.cs`, `DragHUD.cs`
- menu/config: `MenuConfig.cs`, `ModCategories.cs`, `ModPricing.cs`, `ModSettings.cs`, `CategoryConfig.cs`,
  `VehicleColors.cs`, `VehicleSaveData.cs`, `WheelManager.cs`, `CustomBanner.cs`

## Runtime data the mod needs  [CORE — ship these]
`hud/` (gear/tach/nos gauge PNGs), `custom_art/` (banner + font), `textures/`, `native/`, `ExtendedLSC.ini`.

## Build output  [CORE — ship the built DLLs]
`bin/Release/net48/`: `ExtendedLSC.dll` + `LemonUI.SHVDN3.dll` + `Newtonsoft.Json.dll` (+ SHVDN3 is the
user's existing install, don't redistribute it unless license-clear).

## Release BLOAT — exclude (≈330 MB)  [DROP]
- `ExtendedLSC_backup_/` — **219 MB** (a full nested backup, with its own `integrated_mods`)
- `integrated_mods/` — **101 MB** (the reference mods used during dev — NOT part of the release)
- `ExtendedLSC-backup-2026-06-07/` — 9.1 MB · `obj/` 1.1 MB (build temp) · `STUDY-NOTES.md` (dev notes)

## ExtendedLSC release = the slim mod folder
Ship: `ExtendedLSC.dll` + dep DLLs + `hud/` + `custom_art/` + `textures/` + `native/` + `ExtendedLSC.ini` +
INSTALL/USAGE. Source (`src/`, `.csproj`, `.sln`) → GitHub, not the user download.
*Optimization note:* `Main.cs` at 8.3k lines is the obvious refactor target, but **refactoring a working
8k-line file the weekend of release is risky** — recommend a LIGHT pass only (dead code, obvious dupes),
not a restructure, before shipping.

---

# Combined release manifest (both)

| | GTAV-CLAUDE-MCP | ExtendedLSC |
|---|---|---|
| **Ship (core)** | bridge.py, mcp_server/ (server+gtadata+radio), native_db.json, re_tools*, catalogs+recipes, ClaudeChatUI.dll + ClaudeRadio.dll, gtav_host.py, .mcp.json, requirements.txt, run_host.bat, CLAUDE.md | ExtendedLSC.dll + dep DLLs, hud/ custom_art/ textures/ native/, ExtendedLSC.ini |
| **Docs (ship)** | new README + INSTALL + USAGE (+ STUDIES/PATTERNS/RE-TOOLKIT as optional reference) | new README + INSTALL + USAGE |
| **Hold → `experimental/`** | world_sense, mission_sense, agent_actions, commentary, vehicle_tuning + tools/(play_agent, claude_strategist, mission_runner, play_heist, scenario_gen, author_animation, blender_export_ycd) + their data/docs | — |
| **Drop (bloat)** | obj/__pycache__, session working docs (NORTH-STAR, PLAY-AND-WATCH, GROUNDED-MISSIONS, WORLD-SENSING, RELEASE-AUDIT after use), tools/_session-reconstruction* | ExtendedLSC_backup_/ (219MB), integrated_mods/ (101MB), *-backup-*/, obj/, STUDY-NOTES.md |

## Execution order (next)
1. **Restructure MCP** — move the [HOLD] modules to `experimental/` (bridge already loads via try/except, so
   the core is unaffected); strip the experimental `@mcp.tool` wrappers from server.py.
2. **Optimize/clean core** — dead-code pass on bridge.py + server.py; verify deps; light pass on ExtendedLSC.
3. **Slim ExtendedLSC package** — exclude the 330 MB of backups/integrated_mods (a release `.gitignore` + a
   "what to zip" list).
4. **Install docs** — one foolproof INSTALL.md per project (prereqs → files → load → verify) + USAGE.md.
