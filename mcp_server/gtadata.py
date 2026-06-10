"""
gtadata - fast "deep dive" toolkit over the EXTRACTED + DECRYPTED GTA V game files.

The hard parts of a deep dive (locate a file in a 379k-file tree, decode a binary audio
.rel/dat151 to XML, resolve JOAAT hashes to readable names) are all cacheable. This module
caches them so repeat lookups are near-instant:

  - MANIFEST   : prebuilt list of every extracted file path  -> find() greps it (no recursive scan)
  - names.txt  : master JOAAT name dictionary (harvested nametables + every name we crack)
  - xmlcache/  : CodeWalker.Core decode of each .rel/.meta, kept so we never re-parse
  - resolve()  : hash -> name via the dictionary (instant); crack() brute-forces by convention

Every name we ever crack is appended to names.txt, which is also fed into CodeWalker's JenkIndex
before each decode -> so future XML exports show readable names automatically. It only gets smarter.

Paths are machine-specific constants (single-user modding rig); adjust here if the rig changes.
"""
import os
import re
import json
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

# --- machine config (the extracted/decrypted Legacy data + CodeWalker) ----------------------
# These are machine-specific paths. Override per-machine WITHOUT editing this file via either
# environment variables or a gitignored mcp_server/gtadata_local.json: {"data_root","manifest",
# "codewalker_dir"}. Generic defaults below contain no personal info.
def _local_cfg():
    try:
        with open(os.path.join(HERE, "gtadata_local.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
_CFG = _local_cfg()
DATA_ROOT      = _CFG.get("data_root")      or os.environ.get("GTAV_RPF_DATA")     or r"C:\Tools\gtautil\GTA 5 Rpf Data\Legacy"
MANIFEST       = _CFG.get("manifest")       or os.environ.get("GTAV_RPF_MANIFEST") or os.path.join(os.path.dirname(DATA_ROOT), "Legacy_manifest.txt")
CODEWALKER_DIR = _CFG.get("codewalker_dir") or os.environ.get("CODEWALKER_DIR")    or r"C:\CodeWalker"
GTADATA_DIR    = os.path.join(PROJ, "gtadata")
NAMES_FILE     = os.path.join(GTADATA_DIR, "names.txt")
XML_CACHE      = os.path.join(GTADATA_DIR, "xmlcache")
DECODE_PS1     = os.path.join(PROJ, "tools", "decode_rel.ps1")

# Extensions that are already human-readable text (no decode needed)
TEXT_EXT = {".meta", ".xml", ".txt", ".ymt", ".cfg", ".json"}   # .ymt is sometimes xml-as-text
# Extensions that are binary audio metadata -> CodeWalker.Core decode to XML
REL_EXT  = (".rel",)


# =============================================================================
# JOAAT + name dictionary
# =============================================================================
def joaat(s: str) -> int:
    """Jenkins one-at-a-time hash (lowercased), as GTA/CodeWalker uses for names."""
    h = 0
    for c in s.lower():
        h = (h + ord(c)) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= (h >> 6)
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= (h >> 11)
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h


_HASH2NAME = None  # lazy {int_hash: name}


def _names() -> dict:
    global _HASH2NAME
    if _HASH2NAME is None:
        _HASH2NAME = {}
        if os.path.exists(NAMES_FILE):
            with open(NAMES_FILE, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    n = line.strip()
                    if n:
                        _HASH2NAME[joaat(n)] = n
    return _HASH2NAME


def _as_hash(h) -> int:
    """Accept int, '0x1A2B', '1A2B', or 'hash_1A2B' -> int."""
    if isinstance(h, int):
        return h & 0xFFFFFFFF
    s = str(h).strip().lower()
    if s.startswith("hash_"):
        s = s[5:]
    if s.startswith("0x"):
        s = s[2:]
    return int(s, 16) & 0xFFFFFFFF


def resolve(h) -> str | None:
    """hash -> readable name, or None if unknown."""
    return _names().get(_as_hash(h))


def add_name(name: str) -> bool:
    """Permanently learn a name: append to names.txt + in-memory dict. Returns True if new."""
    name = name.strip()
    if not name:
        return False
    d = _names()
    hh = joaat(name)
    if d.get(hh) == name:
        return False
    d[hh] = name
    os.makedirs(GTADATA_DIR, exist_ok=True)
    # newline-safe append: if the file doesn't end in '\n', a bare append would glue this name
    # onto the previous last line (corrupting both). Add a leading '\n' in that case.
    prefix = ""
    if os.path.exists(NAMES_FILE) and os.path.getsize(NAMES_FILE) > 0:
        with open(NAMES_FILE, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"
    with open(NAMES_FILE, "a", encoding="utf-8") as f:
        f.write(prefix + name + "\n")
    return True


# =============================================================================
# Manifest search
# =============================================================================
_MANIFEST_LINES = None


def _manifest() -> list:
    global _MANIFEST_LINES
    if _MANIFEST_LINES is None:
        _MANIFEST_LINES = []
        if os.path.exists(MANIFEST):
            with open(MANIFEST, encoding="utf-8", errors="ignore") as f:
                _MANIFEST_LINES = [ln.rstrip("\n") for ln in f if ln.strip()]
    return _MANIFEST_LINES


def find(pattern: str, limit: int = 50, regex: bool = False) -> list:
    """Find file paths in the extracted tree by substring (default) or regex. Instant (no FS scan)."""
    lines = _manifest()
    if regex:
        # search both the raw (backslash) and slash-normalized path so [\\/] or / both work,
        # but DON'T mangle the regex pattern itself (that would break escapes like \.)
        rx = re.compile(pattern, re.I)
        out = [p for p in lines if rx.search(p) or rx.search(p.replace("\\", "/"))]
    else:
        # slash-insensitive substring match (manifest uses backslashes; accept either)
        pl = pattern.lower().replace("\\", "/")
        out = [p for p in lines if pl in p.lower().replace("\\", "/")]
    return out[:limit]


# =============================================================================
# Read / decode
# =============================================================================
def read_text(path: str, pattern: str | None = None, limit: int = 200, context: int = 0) -> str:
    """Read a text game file (.meta/.xml/...). If pattern given, return matching lines (grep)."""
    if not os.path.exists(path):
        return f"(not found: {path})"
    with open(path, encoding="utf-8", errors="ignore") as f:
        if pattern is None:
            return "".join(f.readlines()[:limit])
        lines = f.readlines()
    rx = re.compile(pattern, re.I)
    hits = []
    for i, ln in enumerate(lines):
        if rx.search(ln):
            lo, hi = max(0, i - context), min(len(lines), i + context + 1)
            hits.append("".join(f"{j+1}: {lines[j]}" for j in range(lo, hi)))
            if len(hits) >= limit:
                break
    return ("\n".join(hits)) if hits else f"(no match for /{pattern}/ in {os.path.basename(path)})"


def _cache_path(src: str) -> str:
    base = os.path.basename(src)
    return os.path.join(XML_CACHE, f"{base}.{joaat(src):08x}.xml")


_HASH_TOKEN = re.compile(r"hash_([0-9A-Fa-f]{8})")


def _resolve_xml(text: str) -> str:
    """Replace every `hash_XXXXXXXX` in decoded XML with its readable name from our dictionary
    (CodeWalker's RelXml leaves SoundRef/track-list refs as raw hashes; we resolve them ourselves)."""
    d = _names()
    def sub(m):
        return d.get(int(m.group(1), 16), m.group(0))
    return _HASH_TOKEN.sub(sub, text)


def decode(path: str, force: bool = False) -> dict:
    """
    Make a game file readable. Text files are returned as-is; binary audio .rel files are
    decoded to XML via CodeWalker.Core (cached). Returns {ok, kind, path/xml_path, note}.
    """
    if not os.path.exists(path):
        return {"ok": False, "note": f"not found: {path}"}
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        return {"ok": True, "kind": "text", "path": path, "note": "already readable"}
    if ext not in REL_EXT:
        return {"ok": True, "kind": "binary", "path": path,
                "note": "not a .rel/text file - open in CodeWalker GUI (e.g. .ydr/.yft/.ytd)"}

    os.makedirs(XML_CACHE, exist_ok=True)
    out = _cache_path(path)
    if os.path.exists(out) and not force and os.path.getmtime(out) >= os.path.getmtime(path):
        return {"ok": True, "kind": "xml", "xml_path": out, "note": "cached"}

    # CodeWalker decode (PowerShell). We resolve hashes ourselves afterwards (see _resolve_xml),
    # so the helper only needs its small built-in strings.txt - keeps the decode fast.
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", DECODE_PS1,
           "-In", path, "-Out", out, "-CwDir", CODEWALKER_DIR]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        return {"ok": False, "note": f"decode failed: {e}"}
    if os.path.exists(out) and (r.returncode == 0):
        # Resolve hash_XXXX -> names via our dictionary, then save the readable XML.
        # Best-effort: if a file decodes to content Windows won't re-write (stray control bytes
        # in some non-audio rel types), keep the raw decoded XML rather than failing the file.
        try:
            with open(out, encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            res = _resolve_xml(txt)
            if res != txt:
                # strip NULs/invalid control chars that break a Windows text write
                res = res.replace("\x00", "")
                with open(out, "w", encoding="utf-8", errors="ignore", newline="") as f:
                    f.write(res)
        except Exception as e:
            return {"ok": True, "kind": "xml", "xml_path": out, "note": f"decoded (hashes unresolved: {e})"}
        msg = (r.stdout or "").strip().splitlines()[-1:] or [""]
        return {"ok": True, "kind": "xml", "xml_path": out, "note": msg[0]}
    return {"ok": False, "note": f"decode error: {r.stdout.strip()} {r.stderr.strip()}"}


# =============================================================================
# Hash cracking by naming convention
# =============================================================================
def crack(targets: list, conventions: list, learn: bool = True) -> dict:
    """
    Brute-force JOAAT: test each convention string against the target hashes; on a hit, optionally
    learn the name (add to names.txt). conventions are full candidate names (e.g.
    "RADIO_06_COUNTRY_CONVOY"); generate them in the caller from a known list/pattern.
    Returns {hex_hash: name} for matches.
    """
    tset = {_as_hash(t) for t in targets}
    found = {}
    for name in conventions:
        hh = joaat(name)
        if hh in tset:
            found[f"{hh:08X}"] = name
            if learn:
                add_name(name)
    return found
