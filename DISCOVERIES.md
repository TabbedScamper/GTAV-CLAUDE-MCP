# GTAV-Claude-MCP — Discoveries & Build Notes

Hard-won technical notes from building **CLAUDE FM** — a custom add-on radio station for GTA V
(Legacy) that Claude builds and drives in real time via MCP. Everything below was figured out
hands-on; the gotchas are the expensive part, so they're written down.

---

## 1. `reload_scripts` — let Claude reload SHVDN C# scripts with no keypress

The bridge (`pyscript/bridge.py`) has a `reload_scripts` command (also an MCP tool) that **simulates
the Insert key**, so a freshly-built DLL loads without a manual keypress. This made the whole
build/deploy/test loop autonomous.

- **Insert = SHVDN (C# scripts). F9 = PyLoaderV (the bridge.py itself).** Different reload keys.
- Insert is an **extended key** — `keybd_event` needs the `KEYEVENTF_EXTENDEDKEY (0x0001)` flag or it
  sends the wrong code.
- Primary method: **`PostMessageW` to the GTA window** (`FindWindowW("grcWindow", None)`) with
  WM_KEYDOWN/WM_KEYUP — focus-independent and bypasses injected-key filtering. keybd_event is a fallback.
- Verify a reload happened by tailing `ScriptHookVDotNet.log` for new "Started script" lines.
- The bridge can't reload itself, so a `bridge.py` change still needs one manual **F9**.

---

## 2. Custom add-on radio station DLC — the complete recipe (Legacy)

A station that appears on the radio wheel needs an **audio DLC**. The hybrid we shipped: the DLC
registers a real wheel station with a **silent placeholder track**, and `ClaudeRadio.dll` plays the
actual audio (so we keep dynamic downloads + positional audio a baked DLC can't do).

### 2.1 The single most important gotcha — the merge list name
The DLC's `RadioStationList` **must be named `radio_stations_dlc`** (`joaat = 0x953BD40D`). That is
the specific list the game aggregates add-on stations from. With any other name the station **loads
without crashing but never appears / never registers** (it won't even be tunable by name). Both
Chatterbox FM and native-audio-tool use this exact name.

### 2.2 Use the `RadioStationSettings` format, not the FiveM format
`native-audio-tool` emits a FiveM-flavored dat151 (`RadioStation` + `RadioTrack` + `MusicList`). The
**Legacy SP game can't parse that and crashes on load.** Author the dat151 in the stock/Chatterbox
format instead:
```
RadioStationList  (Name = radio_stations_dlc)  -> Stations: [<station>]
RadioStationSettings (Name=<station>, Flags=0xAAA80955, WheelPosition, Genre, RadioName=RADIO_49_COMMUNITYSLOT)
                                                          -> TrackList: [<tracklist>]
RadioStationTrackList (Name=<tracklist>, TrackType=2)     -> Tracks: [{ SoundRef=<sound> }]
```

### 2.3 dat54 (sounds) + AWC container hashing
```
StreamingSound <sound> -> ChildSounds: [<left>, <right>]
SimpleSound <left/right>: ContainerName=<sfx_subfolder>/<awc_basename>  FileName=<wave_name_in_awc>
ContainerPaths: [ <SFX_SUBFOLDER>\<AWC_NAME> ]   (UPPERCASE, backslash)
```
- **ContainerName** is hashed as `joaat("<sfx_subfolder>/<awc_basename>")` — **lowercase, forward
  slash** (verified: `joaat("dlc_chatterbox_fm/chatterbox_fm_mix_full") = 0x07D4AF34`).
- **FileName** = the literal wave name inside the AWC (e.g. `claudefm_left`).
- Runtime container = `<wavepack_folder_basename>/<awc_basename>`, so place the AWC at
  `x64/audio/sfx/<bank>/<awc>.awc` and register `sfx/<bank>` as the wavepack.

### 2.4 DLC file layout + content.xml / setup2.xml
```
dlc.rpf/
  setup2.xml
  content.xml
  common/data/dlctext.meta                         (hasGlobalTextFile=true)
  x64/audio/config/<name>_game.dat151.rel
  x64/audio/config/<name>_sounds.dat54.rel
  x64/audio/sfx/<bank>/<awc>.awc
  x64/data/lang/*dlc.rpf                            (1KB lang stubs, copy from any audio DLC)
```
- **content.xml** references audio as `<base>.dat` (not the real `.dat151.rel`): `AUDIO_GAMEDATA` ->
  `<base>_game.dat` resolves to `<base>_game.dat151.rel`; `AUDIO_SOUNDDATA` -> `.dat54.rel`;
  `AUDIO_WAVEPACK` -> the `sfx/<bank>` folder; `TEXTFILE_METAFILE` -> dlctext.meta. A `GROUP_STARTUP`
  changeset (`<NAME>_AUTOGEN`, `filesToEnable`) turns them on; all dataFiles are `disabled=true`.
- **setup2.xml**: `deviceName=dlc_<name>`, `type=EXTRACONTENT_COMPAT_PACK`, `contentChangeSetGroups
  > GROUP_STARTUP > <NAME>_AUTOGEN`.
- Register in **`mods/update/update.rpf/common/data/dlclist.xml`**: `<Item>dlcpacks:/<name>/</Item>`.

### 2.5 Naming rules
Station display-name label `RADIOSTATION_*` has a **32-char limit**. All custom names must be unique
vs. stock audio or sounds silently fail. Ship at least one valid short track or the audio loader CTDs.

> Proven template to dissect: **Chatterbox FM Radio Station [Add-On]** (gta5-mods). It's the ground
> truth for the dat151/dat54 field layout and the DLC packaging.

---

## 3. CodeWalker.Core, headless — capabilities and the NG wall

Driven from PowerShell (`tools/decode_rel.ps1` pattern). `Unblock-File` the DLLs first (MOTW).

- **Keys:** `GTA5Keys.LoadFromPath(gtaFolder, null)` loads the **decrypt** tables — enough to READ
  NG-encrypted RPFs (update.rpf, scaleform_generic.rpf, add-on dlc.rpf).
- **Write OPEN RPFs headlessly:** `RpfFile.CreateNew(folder, "dlc.rpf", RpfEncryption.OPEN)` +
  `CreateDirectory` + `CreateFile`. The game reads OPEN dlc.rpf from the mods folder (OpenIV.asi).
- **Cannot write NG RPFs headlessly:** `CreateFile` into an NG RPF throws *"Unable to encrypt -
  tables not loaded"*; `LoadFromPath` doesn't load the **encrypt** LUTs, and `Generate(exeBytes,...)`
  throws an NRE via reflection. → **Use OpenIV** for anything that must stay NG (e.g. editing
  `scaleform_generic.rpf` to install a hud.gfx).
- **.rel <-> XML:** build with `XmlRel.GetRel(xmlDoc)` + `RelFile.Save()`; decode with `RelFile.Load`
  + `RelXml.GetXml`. Preload `JenkIndex.Ensure(name)` from `strings.txt` + a names dictionary so
  `hash_XXXXXXXX` resolves to readable names. Set the XML `<Version>` to match a current file
  (dat151 `7516460`, dat54 `7314721`) — native-audio-tool's older versions can be rejected.
- **Textures:** `YtdFile.Load`, `DDSIO.GetDDSFile(tex)` (export), `DDSIO.GetTexture(ddsBytes)` (import),
  then `YtdFile.Save()`. Encode DDS with **texconv** (`-f BC1_UNORM -m 1` to match stock radio sheets).
- **DROPBOX LOCK:** building an RPF *inside* a Dropbox-synced folder fails with *"being used by
  another process"* (Dropbox grabs the file mid-write). **Build RPFs in `%TEMP%`, then copy in.**

---

## 4. The radio-wheel icon (hud.gfx)

- A station with no hud.gfx slot shows up but its **icon floats in the center of the wheel**. Fix =
  the **Community hud.gfx** (WildBrick142), which defines reserved slots `RADIO_30-35, 49-56, 59`.
  Name the station `RADIO_49_COMMUNITYSLOT` to claim slot 49.
- `hud.gfx` + `hud.ytd` install into `update.rpf/x64/data/cdimages/scaleform_generic.rpf` (game
  >= 2545). That RPF is **NG**, so install via **OpenIV** (replace the two files, Edit mode).
- Icons are 5 sheets in hud.ytd: `gtav_radio_stations_texture_512` (stock) + `02`-`05`. **Slot 49 =
  sheet `05`, top-right cell** (4x4 grid, 128px cells, region `(384,0)->(512,128)`). Style is bold
  **white-on-black**. Composite the custom logo there, re-encode BC1, swap the texture in hud.ytd.
- `hud.gfx` is a Scaleform GFX (header `GFX`); slot names are in the (zlib) body — grep for
  `RADIO_49_COMMUNITYSLOT`.

---

## 5. ClaudeRadio.dll — the NAudio engine (`radio/ClaudeRadio/`)

- **Library + shuffle + adverts:** plays the `%LOCALAPPDATA%/GTAV-Claude-MCP/music` library in random
  order; slots a random advert from `.../adverts` every N songs (config `ads_every`, default 3).
- **Positional audio:** inside the car = full & clear; outside = distance falloff (full to ~2.5m,
  silent by ~22m) + a low-pass "muffle" (BiQuad, ~900Hz shut -> ~4500Hz with a door open) + a small
  door-open gain boost. Smoothed per tick to avoid clicks.
- **Volume match:** `Effective = calibration x (in-game Music slider/10) x positional gain x mute`.
  Read the slider with `GET_PROFILE_SETTING(301)` (300 = SFX). Freeze-watcher mutes on pause
  (OnTick heartbeat stall).
- **Station activation:** watches `GET_PLAYER_RADIO_STATION_NAME`; tuning to `RADIO_49_COMMUNITYSLOT`
  starts the broadcast, tuning away keeps it **rolling but muted** (real-radio feel — you rejoin
  mid-song). On foot the last in-vehicle station is retained so the positional effect still works.
  In the station path we do NOT force the game radio off (its baked track is the silent placeholder).
- **3-second crossfade:** two `AudioFileReader`s on two `WaveOutEvent`s; ~3s before the end the next
  track loads on slot B and the volumes ramp opposite; on finish, B is promoted to A.
- **Robust advance:** NAudio's `PlaybackStopped` is flaky — a **position backstop** (`reader.Position
  >= reader.Length`) advances even if the event is missed, so the station never stalls.
- **Player radio vs vehicle radio:** `SET_RADIO_TO_STATION_NAME` controls the PLAYER radio (audible +
  what `GET_PLAYER_RADIO_STATION_NAME` reads); `SET_VEH_RADIO_STATION` is the vehicle's. Mismatching
  set/read was a real bug (set vehicle, read player -> always saw a station -> self-stopped).

---

## 6. Audio sourcing

- **yt-dlp** (`mcp_server/radio.py` `fetch`): `ytsearch1` + FFmpegExtractAudio to mp3, loudness-
  normalized to **`loudnorm=I=-18:TP=-1.5:LRA=11`** (~ -18 LUFS, calm radio level).
- **spotDL** for Spotify playlists (resolves each track to YouTube; Spotify itself is DRM'd). Point
  it at the bundled ffmpeg: `python -m spotdl download <url> --output "<music>/{artists} - {title}.{output-ext}" --ffmpeg <ffmpeg.exe>`. Re-loudnorm the results for consistency.
- **GTA adverts** as mp3s: a SoundCloud set (yt-dlp downloads whole SoundCloud sets). In-game the real
  adverts live under a hidden `HIDDEN_RADIO_ADVERTS` station as baked AWC — downloading mp3s is cleaner.

---

## 7. Misc

- **JOAAT** everywhere = Jenkins one-at-a-time over the **lowercased** string.
- Bridge: call natives **by name** (`call_native_by_name`) — the bridge resolves the verified hash; a
  wrong raw hash crashes the game. The loader can't return **string** natives (e.g.
  `GET_PLAYER_RADIO_STATION_NAME`) — read those from C# (`Function.Call<string>`).
- The host (`gtav_host.py`) uses the **Claude subscription via the Agent SDK** (`claude /login`) — no
  API key in the repo.

---

## 8. The pause menu — what freezes, what still works, and how to keep going

### 8.1 Why everything freezes on pause
The **frontend (ESC / map) pause** freezes the shared **game timer** that drives the rage script
scheduler. SHV scripts are cooperative fibers resumed off that timer, so when it stops: **ScriptHookV →
SHVDN (the C# `ClaudeChatUI` panel) → the PyLoaderV bridge tick all stop together.** Consequences:
- **F10 chat input + the visual panel are dead** (the SHVDN UI script isn't ticking).
- The bridge's **work queue isn't drained**, so any command that needs the game thread (natives, engine
  calls) **stalls until unpause**.
- This is `SET_GAME_PAUSED`-independent and **not** PyLoaderV-specific — it's structural (timer→fiber
  coupling). The "SHV keeps running while paused" claim online = people running a no-pause mod.

### 8.2 What STILL works while paused — the off-thread socket path
The bridge **socket server is a background OS thread**, independent of the game tick and the UI, so it
keeps answering. We route **pure memory/CPU commands** to run directly on that thread (they use ctypes,
not `gta.*`, which needs game-thread context). **These work fine during a real pause:**
`read`, `inspect` (by **address**; by-handle degrades), `write`, `revert_last`, `snapshot`, `diff`,
`scan_pattern`, `find_string`, `find_xrefs`, `resolve_rip_relative`, the `*findings*` tools, and chat
**polling** (`get_pending_messages`). So you can **read and change values with the world frozen** — just
drive it from an **external MCP client** (your Claude coding env or the host), since in-game F10 is dead.
What still waits for unpause: anything calling a **native** or **engine function** (`call_native`,
`call_function`, `spawn`, `get_context`, content reload, `reload_scripts`).
> Implementation: `_OFFTHREAD_COMMANDS` + `_run_offthread()` in `bridge.py`; read helpers honor
> `_is_offthread()` to force the ctypes path. (Also fixed the AOB scanner to be **region-aware** —
> `_next_readable_chunk()` — so `scan_pattern`/`find_string`/`find_xrefs` read across a module's many
> memory regions instead of silently skipping chunks that span a region boundary.)

### 8.3 If you want FULL functionality while paused (chat + natives) — the no-pause recipe
You can keep **everything** ticking during the menu by never letting the pause latch. This is the
**GTA-Online behavior** and is exactly what GameplayFixesV does — **pure verified natives, no memory
patch, identical on Legacy + Enhanced.** Run this EVERY FRAME (and it must be running *before* you pause,
since you can't thaw an already-frozen tick):
```
# keep the world (and thus all scripts/UI/bridge) alive while the pause menu is up:
call_native("SET_PAUSE_MENU_ACTIVE", [false])           # every frame, unless holding a real-pause key
# when the user taps pause, open the menu WITHOUT pausing:
if call_native("IS_CONTROL_JUST_PRESSED", [2, 199]) and \
   call_native("GET_CURRENT_FRONTEND_MENU_VERSION") != joaat("FE_MENU_VERSION_SP_PAUSE"):
    call_native("ACTIVATE_FRONTEND_MENU", [joaat("FE_MENU_VERSION_SP_PAUSE"), false, -1])  # togglePause=false
```
Detect the menu with `IS_PAUSE_MENU_ACTIVE`. **Always include an escape hatch** (a hold-to-*really*-pause
key) so a true pause is still possible — e.g. before saving.

### 8.4 Want a frozen-LOOKING world while staying live? (documented, NOT implemented)
A true global pause that keeps scripts ticking is **impossible** (all-or-nothing — the pause flag freezes
the timer the scripts need). The approximation: layer on top of the no-pause recipe —
```
call_native("SET_TIME_SCALE", [0.0001])   # near-freeze; exactly 0 clamps to a tiny minimum, not a true stop
# and/or pin specific things:  call_native("FREEZE_ENTITY_POSITION", [entity, true])
```
→ live bridge/UI/natives on a near-frozen world (good for stable inspection). We deliberately did **not**
build this — it's here so a future MCP user can add it in one line if they want it.

### 8.5 Caveats for the no-pause path
- **Preventive only** — it stops the freeze from happening; it can't revive a tick that already stopped.
- The pause menu's **blur/desat post-FX** stays unless you clear the TC modifier overrides
  (`CLEAR_ALL_TCMODIFIER_OVERRIDES` / re-add `hud_def_blur`→`default`).
- With a live world the **map/menu can behave oddly** and audio won't duck like a real pause; input
  routing (menu vs world) wants in-game tuning.
- **Don't** call `SET_GAME_PAUSED(true)` from a thread that won't also unpause it — every other thread
  freezes and you lose control.
- Ties to the **pause-vs-crash** signal: a *pause* = tick stalled but the socket still answers off-thread
  reads (not a dead bridge); a *crash* = socket dead. The host can keep reading/recording during a pause
  instead of treating it as a disconnect.

Source: GameplayFixesV `AllowGameExecutionOnPauseMenu()` (player.cpp) + citizenfx natives; SHVDN #30
(`Script.Wait` blocks the whole SHV pump). Natives: `SET_PAUSE_MENU_ACTIVE 0xDF47FC56C71569CF`,
`ACTIVATE_FRONTEND_MENU 0xEF01D36B9C9D0C7B`, `IS_PAUSE_MENU_ACTIVE 0xB0034A223497FFCB`,
`SET_TIME_SCALE 0x1D408577D440E81E`, `SET_GAME_PAUSED 0x577D1284D6873711`.

## 9. RE toolkit — in-game validation (build 3788, 2026-06-10)

All 5 RE modules deployed to the game `pyscript\` and validated live. Drive the bridge directly from a
script: **4-byte little-endian length prefix + JSON body on `127.0.0.1:27015`**; `{"command","params"}` →
`{...}` (same framing back). Call natives with **`call_native_by_name`** (`call_native` is by-hash and
wants a `hash` param). F9 hot-reloads the RE modules because `on_start` calls `_reload_re_toolkit()`
(F9 only re-runs `on_start`, not module-level wiring).

### 9.1 What works
- **Tier A** (`re_tools`): `list_functions` → 87,381 funcs, `func_bounds`, `re_scan`, `disasm` (capstone).
  `identify` can't return a class name — GTA5.exe is compiled `/GR-` (RTTI stripped). **`.pdata` fix:** the
  exception table is ~1.05 MB; a single `read_bytes` whose span crosses a committed-region boundary returns
  None, so `parse_pdata` reads it in 0x40000 chunks.
- **par-dump** (`re_tools_pardump`): `par_status`/`par_struct`/`par_label`/`enum_decode` work. BUT
  alexguirre's GTA5 dump is **PSO/savegame+settings scope only** (every struct carries a `psosig`): you get
  CGraphicsSettings, CSaveGarages, CRadioStationSaveStructure, control bindings — **not** CHandlingData /
  CVehicleModelInfo / CWeaponInfo. Closest build to 3788 = **b3442**. Only ~69/2146 struct names + most
  members are resolved; the rest are JOAAT hashes (the site resolves them client-side from a wordlist).
  `par_index.json` (flattened via `re_tools_pardump.py`) auto-loads from `pyscript\`.
- **Tier B** (`re_tools_dynamic`): `read_global` OK; **vehicle pool OK** — `enumerate_entities("vehicle")`
  returns real CVehicle ptrs (verified: coords (99.1,−1395.9,28.8), vtable in module). `nearby_peds` /
  `nearby_vehicles` (pool-free, native + ctypes buffer) OK.
- **value scanner** (`re_tools_scan`): exact region scan, full-heap exact scan (187→28), successive
  narrowing (`scan_next` 28→3), `scan_undo` all work. Reads are region-aligned so the 1-region read rule is
  never violated. Gotcha: **player health auto-regenerates**, so exact-match on a live value drifts off
  between SET and read — pin the value (or widen `eps`) when hunting regenerating stats.
- **patch tier** (`re_tools_patch`): ALL 7 cmds. `patch_bytes`/`nop`(disasm-length-aware)/`restore_patch`/
  `restore_all_patches`/`list_patches`/`alloc_cave`(rel32-reachable RWX)/`capture_stack`. A real `.text`
  write+restore on int3 padding **survived — Arxan tolerated the brief padding write** (game stayed alive).

### 9.2 Gaps / gotchas
- **ped & object pool AOBs are STALE for 3788** — the `48 8B 05` patterns match the wrong site (resolved
  struct's +0x00 is a code ptr, not a heap data ptr). `enumerate_entities` now shape-checks and fails
  honestly instead of returning module-range garbage. Use `nearby_peds`; re-derive the AOBs to fix.
  Anchor for that: a confirmed **CPed @ `0x23C0C622350`** (vtable `0x7FF73FBA1C60`, health float `+0x280`).
- **Don't patch `.pdata` "gaps"** between RUNTIME_FUNCTION entries — they can be code without unwind info,
  not padding (one disassembled as a real prologue: `mov rax, gs:[0x58]`). Scan for `CC`×16 runs for true
  int3 alignment padding.
- `SET_ENTITY_HEALTH` takes **4 args** on this build (entity, health, instigator, weaponType).
