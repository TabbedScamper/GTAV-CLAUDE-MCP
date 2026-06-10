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
- The bridge socket times out while the game is **paused** — do before/after reads, not during a menu.
- The host (`gtav_host.py`) uses the **Claude subscription via the Agent SDK** (`claude /login`) — no
  API key in the repo.
