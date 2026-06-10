# CLAUDE FM — Install

A custom add-on radio station for **GTA V (Legacy)**. The DLC adds a silent placeholder station to
the radio wheel; `ClaudeRadio.dll` (SHVDN3) plays your own music library through it with shuffle,
adverts, positional audio, and crossfades. Requires an **OpenIV mods folder** (OpenIV.asi).

Artifacts in this folder:
- `claudefm/dlc.rpf` — the station DLC (built by `tools/pack_claudefm_dlc.ps1`)
- `hud/hud.gfx`, `hud/hud.ytd` — community hud.gfx + the CLAUDE FM wheel icon (slot 49)
- `hud/claudefm_icon.png` — the icon source (edit + re-composite to change it)

## 1. Install the station DLC
1. Copy `claudefm/dlc.rpf` to `mods\update\x64\dlcpacks\claudefm\dlc.rpf`.
2. In OpenIV, open `mods\update\update.rpf\common\data\dlclist.xml` (Edit mode) and add:
   ```xml
   <Item>dlcpacks:/claudefm/</Item>
   ```
   (The MCP bridge can also do this headlessly — CodeWalker.Core writes the OPEN dlc.rpf and edits
   the dlclist; only the hud.gfx step below needs OpenIV because that RPF is NG-encrypted.)

## 2. Install the wheel button + icon (OpenIV)
The hud lives in an NG-encrypted RPF, so use OpenIV:
1. Open `mods\update\update.rpf\x64\data\cdimages\scaleform_generic.rpf` (Edit mode).
2. **Replace** `hud.gfx` with `hud/hud.gfx` and `hud.ytd` with `hud/hud.ytd`.

Without this the station still works (tunable), but its icon "floats" in the wheel center.

## 3. Install the script + engine
- `ClaudeRadio.dll` (build `radio/ClaudeRadio/`, needs NAudio + ScriptHookVDotNet3) -> `scripts/`.
- `ClaudeChatUI.dll` (`ui_companion/`) -> `scripts/` for the in-game Claude panel (optional).

## 4. Populate the music library
Songs/adverts live in `%LOCALAPPDATA%\GTAV-Claude-MCP\music` and `...\adverts` (not shipped —
copyrighted; download your own). Easiest:
- Ask Claude in-game to "play <song>" (yt-dlp fetch, loudness-matched, auto-indexed), or
- Bulk: `python -m spotdl download <spotify-playlist-url> --output "<music>\{artists} - {title}.{output-ext}" --ffmpeg <ffmpeg.exe>` then loudnorm to -18 LUFS.
- Adverts: drop GTA advert mp3s into `...\adverts` (e.g. a SoundCloud set via yt-dlp).

## 5. Play
Get in a vehicle, open the radio wheel, tune to **CLAUDE FM**. It shuffles your library with adverts,
muffles when you're outside the car, crossfades between tracks, and keeps "broadcasting" when you
tune away so you rejoin mid-song.
