# Claude Radio — build spec

A real in-game GTA V radio station, controlled by Claude, that plays **any song on demand** —
**no Spotify app, no Premium, no DRM**. "Claude, play Smokin' And Ridin'" → it plays on a station you
tune to on the radio wheel. Songs are local files; new ones are fetched on demand from YouTube (yt-dlp).

## UX
- In-game **F10**: "play <song>" → Claude finds it in the local library (instant) or fetches it
  (~few seconds: "fetching… now playing"), then plays it on **Claude Radio**.
- Tune the wheel to **Claude Radio** → hear it; tune away → it pauses and GTA radio resumes.
- Claude can: play / queue / skip / pause / stop, report now-playing, build a vibe playlist, and play
  from the local library instantly. It also says "I can't verify"-style honesty if a fetch fails.

## Architecture / data flow
```
in-game F10  ─►  bridge  ─►  host (Claude)  ─►  MCP tool  play_song(query)
                                                     │
                                  mcp_server/radio.py  (library index + yt-dlp fetch)
                                                     │ writes a command (seq-numbered)
                       %LOCALAPPDATA%\GTAV-Claude-MCP\radio_cmd.json   ◄── C# polls each tick
                                                     ▼
                              ClaudeRadio.dll  (SHVDN3 + NAudio)
                                - plays the mp3 through the default audio device
                                - (phase 2) custom wheel station RADIO_47_CLAUDE via add-on dlc
                                - ducks GTA radio while tuned in / engine on
                                                     │ writes radio_status.json (now-playing)
                                                     ▼
                                   Claude reads status → "now playing X"
```
Reuses the existing `%LOCALAPPDATA%\GTAV-Claude-MCP\` channel dir and the bridge↔host↔MCP pipeline.

## Components

### 1. Python — `mcp_server/radio.py` + new MCP tools
**Library**
- `radio/library/` — downloaded audio (`.mp3`).
- `radio/index.json` — `[{id, title, artist, file, url, duration_s, added}]`.

**Fetcher (yt-dlp)**
- `yt-dlp -x --audio-format mp3 --audio-quality 0 "ytsearch1:<query>" -o "library/<id>.%(ext)s"`
  (requires **ffmpeg** on PATH).
- `find_or_fetch(query)`: fuzzy-match the index (rapidfuzz) → return cached file; else fetch, tag,
  add to index, return path. Caches so the 2nd play of a song is instant.

**Channels (files; matches existing DLL file-polling pattern)**
- write `radio_cmd.json` → `{seq, cmd:"play|queue|pause|resume|skip|stop|volume", file, title, volume}`.
- read `radio_status.json` → `{state, title, position_s, duration_s, station_tuned, queue_len}`.

**MCP tools (exposed as `mcp__gtav__*`, so in-game Claude gets them automatically)**
| Tool | Behavior |
|---|---|
| `play_song(query)` | find-or-fetch + play now → returns `{title, fetched, now_playing}` |
| `queue_song(query)` | find-or-fetch + append to queue |
| `radio_skip()` / `radio_pause()` / `radio_resume()` / `radio_stop()` | transport |
| `radio_volume(level 0-100)` | set NAudio volume |
| `now_playing()` | read `radio_status.json` |
| `library_list(search?)` | list/search the local library |

### 2. C# — `ClaudeRadio.dll` (ScriptHookVDotNet3 + NAudio)
- **NAudio** `WaveOutEvent` + `AudioFileReader` for playback; `MediaFoundationReader` fallback.
- Polls `radio_cmd.json` (act only on a new `seq`); maintains a **queue**; auto-advances on track end.
- Writes `radio_status.json` every tick (now-playing, position, state).
- **Station logic**
  - *Phase 1 (soft):* play on command; while playing, mute GTA radio via native
    (`SET_VEH_RADIO_STATION(veh,"OFF")`); restore on stop.
  - *Phase 2 (wheel):* custom station `RADIO_47_CLAUDE`. If
    `GET_PLAYER_RADIO_STATION_NAME == "RADIO_47_CLAUDE"` → resume; else → pause. Engine off → pause.
- Could be a sibling to `ClaudeChatUI.dll` (recommended: separate, single-responsibility) or merged.

### 3. Game asset (Phase 2) — add-on dlc wheel station
- `dlcpacks:/claude_radio/` with a **minimal radio station** audio config so `RADIO_47_CLAUDE`
  registers on the wheel. **No audio ships in the dlc** — NAudio provides the sound; the dlc only
  creates the wheel entry + name/icon.
- HUD.gfx station icon via the Community HUD.gfx method (credit WildBrick142).
- `dlclist.xml` entry: `<Item>dlcpacks:/claude_radio/</Item>`.
- We already have the **CodeWalker `.rel` encode pipeline** (`tools/decode_rel.ps1` + `XmlRel.GetRel`)
  to author the station's audio config if hand-building it is needed.

## Dependencies
- **Python:** `yt-dlp`, `ffmpeg` (on PATH), `rapidfuzz` (matching), `mutagen` (tags, optional).
- **C#:** `NAudio` (NuGet), `ScriptHookVDotNet3` (already used).
- **Game (phase 2):** add-on dlc + Community HUD.gfx.

## Build phases
1. **MVP** — `play_song`/yt-dlp/library + `ClaudeRadio.dll` NAudio playback + mute GTA radio while
   playing. Goal: "Claude, play X" plays any song in-game. *Proves the loop end-to-end.*
2. **Queue + transport** — queue/skip/pause/resume/volume/now-playing + library reuse (instant replays).
3. **Wheel station** — add-on dlc `RADIO_47_CLAUDE` + HUD.gfx → real in-game station with auto-duck.
4. **Polish** — vibe playlists ("play a night-drive set"), MoodMatch-style reactions (Claude adjusts
   weather/time to the song via the bridge), persistent playlists, background prefetch of likely songs.

## Caveats (honest)
- Audio is **YouTube-sourced** (personal-use gray area, like every Self Radio rip).
- **Fetch latency** ~few seconds for a brand-new song; **zero** if pre-cached in the library.
- NAudio plays through the **default audio device** (mixes over the game) — this is how all these mods
  (Self Radio V, the Spotify mod) work; the game engine can't ingest external audio as a true station.
- **ffmpeg** required for yt-dlp audio extraction.

## Open decisions
- Separate `ClaudeRadio.dll` vs extend `ClaudeChatUI.dll` (recommend **separate**).
- Wheel station now vs after MVP (recommend **after** — get song selection working first).
- Library location: in-repo `radio/library/` vs `Documents\…\User Music` (recommend in-repo; keeps it
  independent of GTA's Self Radio scan).
