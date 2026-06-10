"""
radio - Claude Radio backend: a self-contained local music library + on-demand yt-dlp fetch, plus a
file-based command channel the in-game C# player (ClaudeRadio.dll) polls. No Spotify, no Premium, no DRM.

NATIVE TO THE MOD + CONFIGURABLE
--------------------------------
Everything lives under the mod's own runtime home, %LOCALAPPDATA%/GTAV-Claude-MCP/ (the same dir the
SHVDN UI already uses for host_path/chat_history). We do NOT write into the GTA install (Program Files
isn't writable by the Python downloader without admin).

  radio_config.json   - bootstrap: {music_folder, ...}  (Python + C# both read this -> always agree)
  music/              - default library (downloaded songs + any files the USER drops in)
  radio_cmd.json      - Python -> C# commands (seq-numbered)
  radio_status.json   - C# -> Python now-playing

The user can repoint the library anywhere with set_music_folder(path) (or MusicFolder= in the ini the
C# side writes), e.g. their own music collection. rescan() indexes user-dropped files so Claude can
play those too. See radio/SPEC.md.

Flow:  play_song(query) -> find_or_fetch (library hit, else yt-dlp -> mp3) -> write play cmd -> C# plays.
"""
import os
import glob
import json
import time
import threading

try:
    import yt_dlp
except Exception:
    yt_dlp = None
try:
    from rapidfuzz import fuzz, process as rf_process
except Exception:
    rf_process = None
try:
    from mutagen import File as _MutagenFile
except Exception:
    _MutagenFile = None

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

# The mod's runtime home - writable, native to the mod, shared by Python + C#.
BASE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", PROJ), "GTAV-Claude-MCP")
CONFIG_FILE = os.path.join(BASE_DIR, "radio_config.json")
CMD_FILE = os.path.join(BASE_DIR, "radio_cmd.json")
STATUS_FILE = os.path.join(BASE_DIR, "radio_status.json")
FFMPEG_DIR = os.path.join(PROJ, "radio", "bin")          # bundled ffmpeg (yt-dlp -> mp3)
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".wma", ".flac", ".ogg", ".opus")


# =============================================================================
# Config: where the music folder is (user-configurable, default native to the mod)
# =============================================================================
def _read_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_config(cfg: dict):
    os.makedirs(BASE_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


def music_folder() -> str:
    """Resolved library folder. Default: the mod's own `music\\` dir; overridable via set_music_folder."""
    mf = _read_config().get("music_folder")
    if not mf:
        mf = os.path.join(BASE_DIR, "music")
    os.makedirs(mf, exist_ok=True)
    return mf


def set_music_folder(path: str) -> dict:
    """Point the library at any folder (e.g. the user's own music collection)."""
    cfg = _read_config()
    cfg["music_folder"] = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
    _write_config(cfg)
    os.makedirs(cfg["music_folder"], exist_ok=True)
    rescan()
    return {"music_folder": cfg["music_folder"]}


def _index_path() -> str:
    return os.path.join(music_folder(), "_claude_radio_index.json")


# =============================================================================
# Library index (lives inside the music folder so it travels with the music)
# =============================================================================
def _load_index() -> list:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_index(idx: list):
    tmp = _index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _index_path())


def rescan() -> dict:
    """Index every audio file in the music folder (so user-dropped songs are playable too).
    Keeps existing entries (with their fetched metadata); adds loose files by tag/filename."""
    mf = music_folder()
    idx = _load_index()
    known = {os.path.normcase(e.get("file", "")) for e in idx}
    added = 0
    for path in glob.glob(os.path.join(mf, "**", "*"), recursive=True):
        if not path.lower().endswith(AUDIO_EXTS) or os.path.normcase(path) in known:
            continue
        title, artist, dur = os.path.splitext(os.path.basename(path))[0], "", 0
        if _MutagenFile is not None:
            try:
                m = _MutagenFile(path, easy=True)
                if m is not None:
                    title = (m.get("title") or [title])[0]
                    artist = (m.get("artist") or [""])[0]
                    dur = int(getattr(m.info, "length", 0) or 0)
            except Exception:
                pass
        idx.append({"id": None, "title": title, "artist": artist, "file": path,
                    "url": "", "duration_s": dur, "added": int(time.time())})
        added += 1
    if added:
        _save_index(idx)
    return {"music_folder": mf, "added": added, "total": len(idx)}


def find_in_library(query: str, threshold: int = 72):
    idx = _load_index()
    if not idx:
        return None
    if rf_process is None:
        ql = query.lower()
        for e in idx:
            if ql in f"{e.get('title','')} {e.get('artist','')}".lower():
                return e
        return None
    choices = {i: f"{e.get('title','')} {e.get('artist','')}".strip() for i, e in enumerate(idx)}
    best = rf_process.extractOne(query, choices, scorer=fuzz.WRatio)
    if best and best[1] >= threshold:
        return idx[best[2]]
    return None


# =============================================================================
# Fetch (yt-dlp -> mp3 in the music folder)
# =============================================================================
def fetch(query: str) -> dict:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp not installed (pip install yt-dlp)")
    mf = music_folder()
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(mf, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "default_search": "ytsearch1",
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
    }
    if os.path.isdir(FFMPEG_DIR):
        opts["ffmpeg_location"] = FFMPEG_DIR
    opts["socket_timeout"] = 30  # don't hang forever on a bad network/stream
    # Loudness-normalize every track to a consistent target (~ -18 LUFS, calm radio loudness) so songs
    # don't jump in volume between each other or vs the game.
    opts["postprocessor_args"] = {"extractaudio": ["-af", "loudnorm=I=-18:TP=-1.5:LRA=11"]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=True)
    if info.get("entries"):
        info = info["entries"][0]
    vid = info.get("id")
    path = os.path.join(mf, f"{vid}.mp3")
    entry = {"id": vid, "title": info.get("title", query), "artist": info.get("uploader", ""),
             "file": path, "url": info.get("webpage_url", ""),
             "duration_s": int(info.get("duration") or 0), "added": int(time.time())}
    idx = _load_index()
    if not any(e.get("id") == vid for e in idx):
        idx.append(entry)
        _save_index(idx)
    return entry


def find_or_fetch(query: str) -> dict:
    hit = find_in_library(query)
    if hit and os.path.exists(hit.get("file", "")):
        return {**hit, "fetched": False}
    return {**fetch(query), "fetched": True}


# =============================================================================
# Command channel (Python -> C# player)
# =============================================================================
def _next_seq() -> int:
    try:
        with open(CMD_FILE, encoding="utf-8") as f:
            return int(json.load(f).get("seq", 0)) + 1
    except Exception:
        return 1


def _write_cmd(cmd: str, **fields) -> dict:
    os.makedirs(BASE_DIR, exist_ok=True)
    payload = {"seq": _next_seq(), "cmd": cmd, "ts": int(time.time()), **fields}
    tmp = CMD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, CMD_FILE)
    return payload


def read_status() -> dict:
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"state": "unknown", "note": "ClaudeRadio.dll not running or no status yet"}


# =============================================================================
# High-level actions (called by the MCP tools)
#
# Library hits play instantly. A cache MISS fetches in a BACKGROUND thread and plays/queues when the
# download finishes - so a download NEVER blocks Claude's turn (that was freezing the whole chat).
# The MCP tool returns immediately ("fetching..."); the song starts a few seconds later on its own.
# =============================================================================
_fetching = set()
_fetch_lock = threading.Lock()


def _bg_fetch(query: str, then_cmd: str):
    try:
        t = fetch(query)
        _write_cmd(then_cmd, file=t["file"], title=t["title"], artist=t.get("artist", ""))
    except Exception as e:
        _write_status_note(f"fetch failed for '{query}': {e}")
    finally:
        with _fetch_lock:
            _fetching.discard(query)


def _start_bg_fetch(query: str, then_cmd: str):
    with _fetch_lock:
        if query in _fetching:
            return
        _fetching.add(query)
    threading.Thread(target=_bg_fetch, args=(query, then_cmd), daemon=True).start()


def _write_status_note(note: str):
    """Leave a breadcrumb the in-game player/host can surface (e.g. a failed fetch)."""
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(os.path.join(BASE_DIR, "radio_fetch_note.txt"), "w", encoding="utf-8") as f:
            f.write(f"{int(time.time())}|{note}")
    except Exception:
        pass


def play(query: str) -> dict:
    hit = find_in_library(query)
    if hit and os.path.exists(hit.get("file", "")):
        _write_cmd("play", file=hit["file"], title=hit["title"], artist=hit.get("artist", ""))
        return {"playing": hit["title"], "artist": hit.get("artist", ""), "fetched": False}
    _start_bg_fetch(query, "play")
    return {"fetching": query, "note": "downloading in the background - it'll start playing in a few seconds"}


def queue(query: str) -> dict:
    hit = find_in_library(query)
    if hit and os.path.exists(hit.get("file", "")):
        _write_cmd("queue", file=hit["file"], title=hit["title"], artist=hit.get("artist", ""))
        return {"queued": hit["title"], "fetched": False}
    _start_bg_fetch(query, "queue")
    return {"fetching": query, "note": "downloading in the background - it'll be queued when ready"}


def pause():  return _write_cmd("pause")
def resume(): return _write_cmd("resume")
def skip():   return _write_cmd("skip")
def stop():   return _write_cmd("stop")
def set_volume(level: int): return _write_cmd("volume", volume=max(0, min(100, int(level))))


def clear_library() -> dict:
    """Delete all downloaded songs from the music folder and reset the index. Stops playback first so
    the currently-playing file isn't locked. Reversible - songs re-download on demand."""
    _write_cmd("stop")          # tell the in-game player to release any open file
    time.sleep(1.2)             # give NAudio a moment to dispose the reader
    mf = music_folder()
    removed = failed = 0
    for f in glob.glob(os.path.join(mf, "*.mp3")):
        try:
            os.remove(f); removed += 1
        except Exception:
            failed += 1
    try:
        if os.path.exists(_index_path()):
            os.remove(_index_path())
    except Exception:
        pass
    return {"removed": removed, "failed_locked": failed, "music_folder": mf}


def library_list(search: str = "", limit: int = 40) -> list:
    idx = _load_index()
    if search:
        s = search.lower()
        idx = [e for e in idx if s in f"{e.get('title','')} {e.get('artist','')}".lower()]
    return [{"title": e.get("title"), "artist": e.get("artist"), "duration_s": e.get("duration_s")} for e in idx[:limit]]
