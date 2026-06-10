# GTAV-Claude-MCP Project Instructions

## Native Library — call ANYTHING, safely (READ FIRST)

The bridge ships a **verified native database** (`pyscript/native_db.json`, ~6700 natives,
generated from alloc8or's Legacy + Enhanced data by `tools/build_native_db.py`). It is the
**allowlist**: any native in it is safe to call; a hash NOT in it is refused (a wrong hash
**crashes** the game instantly — this is exactly what bit us before).

**How to do anything in-game (the rule):**
- **Call natives BY NAME:** `call_native("CREATE_VEHICLE", [args...])`. The bridge resolves the
  correct canonical hash for the running edition (Legacy *and* Enhanced use the same hashes).
- **Discover** with `search_natives("explosion")` / `search_natives(namespace="WEAPON")`.
- **Check args** with `native_info("CREATE_VEHICLE")` (param order + types + return type) BEFORE calling.
- **NEVER guess or type a raw hash.** `call_native_by_hash` is gated and will refuse unknowns anyway.
- Entity/ped/vehicle args are **handles** (ints) — get the player ped via `PLAYER_PED_ID` first, and
  use a FRESH handle each time (stale handles crash).
- The old "Working/Known-Bad Hash" tables below are now **auto-enforced** by the DB generator (working
  hashes are validated against it; bad hashes are baked into `known_bad` and hard-rejected). Keep
  appending tested natives there; re-run `tools/build_native_db.py` to refresh.

**Automatic arg coercion (you usually don't manage types):** `call_native(name, args)` coerces each
arg to the native's declared param type from the DB (int→float where the native wants a float, etc.),
so passing `5` where a coordinate is expected won't become a near-zero garbage float. It only coerces
when arg COUNT matches the signature; on a count mismatch it returns a `warning` and passes args as-is —
check `native_info(name)` for the exact param order/types. **Vector3-returning natives** (GET_ENTITY_COORDS
/ROTATION/VELOCITY) request `return_type=vector3`; `native_info` flags Vector3 returns and output/pointer
params under `usage_notes`. If the DB fails to load, native calls are **refused (fail closed)** — never waved through.

**Convenience tools (correct native + arg order baked in):** `get_player_ped`, `set_invincible(on)`,
`set_health(n)`, `set_wanted_level(n)` (0 clears + applies now), `list_namespaces`, plus `spawn_vehicle`,
`teleport`, `set_weather`, `set_time`, `give_weapon`, `repair_vehicle`. Prefer these over hand-built native
chains for the common actions — they avoid arg-order mistakes.

## Deep-Dive Toolkit — read the EXTRACTED game files (gtadata_* tools)

**Never answer game-data questions from memory** - your training recall of GTA internals (station
internal names, tracklists, offsets) is frequently wrong. The FlyLo FM test proved it: a from-memory
answer listed artists as if they were tracks, when the actual data shows ONE mix-list of 2 tracks.
Always verify with the tools below; if they can't confirm it, say so instead of guessing.

For "how is X DEFINED in the game?" questions (handling, mod kits/carvariations, radio/audio,
vehicles.meta, where a model file lives) use the `gtadata_*` tools. They read the fully
**extracted + NG-decrypted** Legacy game files on disk — *offline data*, distinct from the live-memory
tools. Fast by design: a prebuilt path manifest + a decoded-XML cache + a growing JOAAT name dictionary
(every name cracked is remembered, so repeat lookups are near-instant).

**Workflow (the 5 steps):**
1. **Resolve the friendly name to its INTERNAL id first** — e.g. "Rebel Radio" → `RADIO_06_COUNTRY`
   (grepping "rebel" finds the *Rebel truck*, not the station). If unsure of the internal id, ask the
   PC-side Claude (it can web-search the convention).
2. `gtadata_find("radio_06")` → file paths. **Never** recursive-scan the tree (379k files; it times out).
3. Binary audio (`.rel`/dat54/dat151)? `gtadata_decode(path)` → cached XML with hashes auto-resolved.
   Text (`.meta`/`.xml`) is already readable — skip decode.
4. `gtadata_read(xml_path, pattern="RADIO_06_COUNTRY")` to grep the part you need.
5. See a `hash_XXXXXXXX`? `gtadata_resolve` it; if unknown, `gtadata_crack([hashes], [candidate names])`
   by convention — hits are learned permanently and auto-resolve in all future decodes.

**Known conventions:** radio station = `RADIO_<NN>_<GENRE>` (Rebel = `RADIO_06_COUNTRY`,
FlyLo FM = `RADIO_14_DANCE_02`); its track lists = `RADIO_06_COUNTRY_MUSIC` / `_DJSOLO` / `_IDENTS`;
song sounds = `RADIO_06_COUNTRY_<TITLE>` e.g. `RADIO_06_COUNTRY_CONVOY`. Play a station's music with
`SET_RADIO_TRACK("RADIO_06_COUNTRY", "<tracklist>")`.

**RADIO RECIPE (important - there is NO file named after a station):** all radio station/tracklist/song
DEFINITIONS live INSIDE one file: `update\update\x64\audio\config\game.dat151.rel`. Do NOT search for a
file called "radio_14" or "flylo" - there isn't one. Instead:
1. `gtadata_decode("...\update\update\x64\audio\config\game.dat151.rel")` (already cached -> instant).
2. `gtadata_read(xml_path, pattern="RADIO_14_DANCE_02")` -> the station's `<TrackList>` (track-list hashes).
3. `gtadata_read(xml_path, pattern="<that tracklist name/hash>")` -> the `<SoundRef>` song hashes.
4. `gtadata_resolve` each; unknown ones -> `gtadata_crack` with `RADIO_14_DANCE_02_<TITLE>` candidates.
Note: the radio CONFIG (this file) IS extracted; only the base-game wave AUDIO (.awc) is missing - so you
CAN read the full station/tracklist/song-name structure, you just can't play the raw wave from disk.

## Verify rules — DON'T re-guess on "failure" (this caused crashes)

The Fort-Zancudo loop — clear wanted level → game instantly re-sets it → assume "wrong hash" → try a
new hash → **crash** — was a *verification* bug, not a memory bug. Rules:

1. **Success = IMMEDIATE read-back, not a delayed re-check.** Right after a write/native, read the
   value once. If it's your value, the call **worked** — lock it in.
2. **A value reverting LATER means the game enforces it** — not that you failed. The fix is to
   **re-assert it** (continuous write) or **remove the cause** (e.g. `SET_MAX_WANTED_LEVEL` to 0, or
   teleport out of the restricted zone). It is **never** "try a different hash."
3. **A wrong hash CRASHES; it does not silently fail.** So "it didn't seem to work" is essentially
   never a hash problem. Do **not** escalate to a new hash — re-check args/context or re-assert.
4. Calling by NAME (above) means you never hold a wrong hash in the first place.

## Critical: Script Reloading

**PyLoaderV uses F9 to reload scripts, NOT Insert!**
- Insert is for SHVDN (C# scripts)
- F9 is for PyLoaderV (Python scripts like bridge.py)
- If changes aren't taking effect, make sure to press F9
- Delete `__pycache__/bridge.cpython-*.pyc` if reload still fails

## Crash Debugging

### Crash Log Location
- Write-ahead log: `pyscript/crash_logs/last_op.jsonl`
- Shows the last operation before crash
- Check with: `powershell -Command "Get-Content 'path/to/last_op.jsonl' -Tail 5"`

### ScriptHookV "Can't find native" Errors
- These crash BEFORE our Python code runs
- Won't appear in last_op.jsonl
- The error dialog shows the bad native hash
- Fix: correct the typo in bridge.py and reload with F9

### Known Bad Native Hashes (DO NOT USE)
| Hash | Issue |
|------|-------|
| `0x4F8644AF` | PLAYER_ID - doesn't exist in Legacy |
| `0x98A4EB5D89A0A952` | Typo - should be 0x98A4EB5D89A0**C**952 |
| `0x1E41A5A106A55C66` | Unknown - crashes game |
| `0x114B1F17AA39E53E` | Wrong hash - correct SET_MAX_WANTED_LEVEL is 0xAA5F02DB48D704B9 |

### Working Native Hashes
| Hash | Function | Notes |
|------|----------|-------|
| `0xD80958FC74E988A6` | PLAYER_PED_ID | Returns ped handle (usually 2) |
| `0xE83D4F9BA2A38914` | GET_ENTITY_HEADING | Returns float degrees |
| `0x997ABD671D25CA0B` | IS_PED_IN_ANY_VEHICLE | Returns bool |
| `0x9A9112A0FE9A4713` | GET_VEHICLE_PED_IS_IN | Returns vehicle handle |
| `0xAF35D0D2583051B0` | CREATE_VEHICLE | Spawn vehicle |
| `0x963D27A58DF860AC` | REQUEST_MODEL | Preload model |
| `0x98A4EB5D89A0C952` | HAS_MODEL_LOADED | Check if model ready |
| `0xBF0FD6E56C964FCB` | GIVE_WEAPON_TO_PED | Give weapon |
| `0x06843DA7060A026B` | SET_ENTITY_COORDS | Teleport entity |
| `0x3882114BDE571AD4` | SET_ENTITY_INVINCIBLE | Make entity invincible (get fresh ped handle first!) |
| `0xD5037BA82E12416F` | GET_ENTITY_SPEED | Returns speed in m/s (×2.237 for mph) |
| `0xE28E54788CE8F12D` | GET_PLAYER_WANTED_LEVEL | Returns 0-5 stars |
| `0xB302540597885499` | CLEAR_PLAYER_WANTED_LEVEL | Clears wanted level (use player ID 0) |
| `0x39FF19C64EF7DA5B` | SET_PLAYER_WANTED_LEVEL | Set specific level (player, level) |
| `0xAA5F02DB48D704B9` | SET_MAX_WANTED_LEVEL | Cap max stars (0-5) |

## Spawn Vehicle Direction Fix

### Correct Formula (VERIFIED)
```python
# GTA heading: 0=North(+Y), 90=East(+X), 180=South(-Y), 270=West(-X)
import math
rad = math.radians(heading)
spawn_x = pos_x - math.sin(rad) * distance  # -sin
spawn_y = pos_y + math.cos(rad) * distance  # +cos
spawn_z = pos_z + 0.5
```

### Use Vehicle Position When Driving
When player is in a vehicle, use the VEHICLE's position/heading, not the ped's:
- Ped position is offset to the driver's seat (causes spawns to appear "to the left")
- Check `IS_PED_IN_ANY_VEHICLE`, then `GET_VEHICLE_PED_IS_IN`
- Read position from vehicle entity address + 0x90/0x94/0x98

## Model Preloading (for INSTANT spawns)

NOTE: spawning is now **non-blocking** - `spawn_vehicle`/`preload_model` no longer freeze the
game or time out the bridge (they `REQUEST_MODEL` then poll `HAS_MODEL_LOADED` one check per frame
via the Deferred mechanism, completing the create when the model is ready). Preloading is now just
for LATENCY:
1. Call `preload_model` first (optionally `wait=False` to fire-and-forget)
2. Then spawn completes on the first frame since the model is already in memory

```python
# Bridge command
{"command": "preload_model", "params": {"model": "hydra"}}
# Then spawn
{"command": "spawn_vehicle", "params": {"model": "hydra", "distance": 10}}
```

## In-Game Confirmation Pattern

When the user is playing GTA V and interacting via the MCP bridge, **ALWAYS use in-game confirmation before risky operations**:

```python
# BEFORE any memory write, ask in-game:
result = ask_in_game("Write camber=0.1 to wheel 0? (yes/no)")

# Parse the response
response = json.loads(result)
if response.get("success") and "yes" in response.get("message", "").lower():
    # User approved - proceed
    set_wheel_value(0, "camber", 0.1)
else:
    # User declined or timeout
    chat_post("Cancelled.")
```

## When to Use In-Game Confirmation

ALWAYS use `ask_in_game()` before:
- Writing to memory (`write_memory`, `set_wheel_value`, `write_visual_wheel`)
- Any destructive or risky operation
- When you need user input/decision

DO NOT use terminal prompts - the user is in-game and can't see the terminal.

## Checking for User Messages

The user can press F10 anytime to send you a message. Periodically check:

```python
# Non-blocking check
pending = has_pending_messages()
if json.loads(pending).get("has_messages"):
    messages = get_pending_messages()
    # Process user messages...
```

## Communication Flow

1. User presses F10 in-game -> types message -> you receive via `get_pending_messages()` or `await_user_message()`
2. You respond via `chat_post("your message")`
3. For confirmations, use `ask_in_game("question")` which combines both

## Safety Rules

- Always use `ask_in_game()` before writes
- Check `get_crash_logs()` if the game crashed
- Use `find_wheel_visual_offsets()` before writing to visual wheel structures
- Never write to unvalidated memory addresses
- Get FRESH ped/vehicle handles before operations (stale handles crash)

## Restricted Zones (Fort Zancudo, etc.)

Clearing wanted level at Fort Zancudo doesn't work long-term because:
- It's a restricted military zone
- You instantly get 4 stars again when detected
- Solution: Teleport out of the restricted zone, or repeatedly clear wanted level

## Headless Host (gtav_host.py) - the seamless chat path (REPLACES the terminal + paste hack)

`gtav_host.py` is an always-on process that drives Claude via the **Claude Agent SDK** (reuses
your `claude /login` subscription - NO api key, NO terminal, NO focus stealing, NO paste tool):
- It polls the bridge for in-game F10 messages, queries Claude (persistent session, loads this
  CLAUDE.md, `bypassPermissions`, all `mcp__gtav__*` tools), and streams replies.
- It writes the live transcript into the `GTAV_Claude_Console` shared memory the C# panel reads,
  so **it doubles as the terminal mirror -> ConsoleTrigger.exe is retired.**
- **Crash-aware:** because the host is a SEPARATE process from GTA, a GTA crash does NOT kill it.
  Its bridge poll fails -> it detects GTA is down, reads the WAL (`last_op.jsonl`) from disk, and
  reports the last operation; it auto-reconnects when GTA returns.
- Run: `python gtav_host.py` (or `run_host.bat`, minimized). On first run it self-registers its
  path to `%LOCALAPPDATA%\GTAV-Claude-MCP\host_path.txt` so the C# UI can auto-launch it after.

### In-game keys / mirror
- ClaudeChatUI.dll (LemonUI panel) reads `GTAV_Claude_Console` shared memory and renders it
- F11 toggles the panel; F10 sends a message to Claude from in-game
- ConsoleTrigger.exe is no longer needed (the host writes the shared memory directly)

## File Locations

| File | Purpose |
|------|---------|
| `pyscript/bridge.py` | Main bridge script (reload with F9) |
| `pyscript/crash_logs/last_op.jsonl` | Write-ahead log for crash debugging |
| `pyscript/__pycache__/` | Python bytecode cache (delete if reload fails) |
| `scripts/ClaudeChatUI.dll` | In-game UI companion |
| `ConsoleTrigger.exe` | Terminal mirror to shared memory |
