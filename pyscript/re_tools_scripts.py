"""
re_tools_scripts.py  -  LAYER 1: live RAGE script (.ysc) enumerator        [read-only, paused-safe]
================================================================================================
"What game / add-on .ysc scripts are running right now?" - the inventory half of the per-frame
profiler. Every running game script (and any add-on .ysc mod: carmod_shop, freemode, a trainer's
.ysc, etc.) is a live `GtaThread` object on the heap. Each carries a human-readable name buffer
(m_Name, e.g. "carmod_shop") and a `scrThreadContext` (ThreadId / State / ProgramCounter / stack).

WHY THIS IS SAFE (no fabricated signatures - project rule):
  We do NOT hard-code a build-specific AOB for the thread collection. Instead we LEARN the layout
  from the running process and VALIDATE every step before dereferencing:
    1. Seed: scan the game heap for a known script name string that is currently running
       (e.g. "carmod_shop" while in LSC, or "main"/"shop_controller"). A name is just a bootstrap.
    2. From a name hit at S, find the owning struct base B in [S-0x300, S-0x10] where read_ptr(B)
       (the vtable) points INTO GTA5.exe's image AND the context dwords look sane. The
       "vtable lands in the exe" gate is strong - random heap bytes won't pass it.
    3. That pins the shared GtaThread vtable V = *B and name_offset = S - B for THIS build.
    4. Scan the heap for every 16-aligned qword == V -> every live GtaThread, regardless of name.
  All reads are validated (is_valid_address) first; nothing is written. -> paused-safe / off-thread.

Commands:
    scripts_list                 enumerate running scripts (auto-bootstraps + caches the vtable)
    scripts_discover             unsupervised: find the GtaThread vtable structurally (no seed name)
    scripts_inspect {address}    hexdump a thread struct so context offsets can be verified in-game
    scripts_reset                drop the cached vtable / name_offset (re-learn next call)

INTEGRATION (bridge.py): `import re_tools_scripts as rs; rs.bind(globals());
COMMANDS.update(rs.SCRIPTS_COMMANDS); _OFFTHREAD_COMMANDS |= rs.SCRIPTS_OFFTHREAD`

NOTE: the scrThreadContext SUB-offsets below are the documented rage layout and are the right
defaults, but they are BUILD-SENSITIVE. name/presence/vtable are rock-solid; the decoded
ThreadId/State are returned as best-effort alongside a raw context dump so they can be confirmed
in-game (and overridden via params) - honesty over false precision.
"""
import ctypes
import os
import re
import struct
from ctypes import wintypes

# ---- ground-truth script-name whitelist (the decisive GtaThread discriminator) ----------------
# A real GtaThread's m_Name is one of GTA's known .ysc script names. Random rage objects that merely
# have a vtable + a printable buffer nearby (texture/index arrays, etc.) are NOT in this set, so it
# cleanly separates threads from look-alikes when LOCATING the vtable. (Enumeration still reports any
# format-valid name so add-on .ysc mods, which won't be in this vanilla list, also show up.)
_CORE_SCRIPTS = frozenset("""
main main_persistent freemode carmod_shop shop_controller appcamera wantedscan selector
respawn_controller pickup_controller vehicle_gen_controller cellphone_flashhand stripclub
fairground_controller scaleformminigame blip_controller player_controller streaming
cutscene_controller dialogue_handler audiotest creation_startup director_mode
spawn_activities clothes_shop_sp tattoo_shop hairdo_shop_sp gunclub_shop traffic
ambientblimp am_mp_property_int gb_carwash gb_taxi savegame_bed shoprobberies
""".split())

def _load_known_scripts():
    """Full known-name set from ysc_script_names.txt beside this module (1100+ names), else the
    embedded core. Names are stable across builds, so the vanilla list locates threads on Enhanced too."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ysc_script_names.txt")
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            names = {ln.strip().lower() for ln in f if ln.strip()}
        if names:
            return frozenset(names | _CORE_SCRIPTS)
    except Exception:
        pass
    return _CORE_SCRIPTS

_KNOWN = _load_known_scripts()

# ---- bridge primitives (filled by bind) -------------------------------------------------------
_P = {}
def bind(bg):
    for n in ("read_bytes", "read_ptr", "is_valid_address", "_get_main_module", "log_message"):
        if n in bg:
            _P[n] = bg[n]

def _read(addr, n):
    f = _P.get("read_bytes")
    if f:
        try: return f(addr, n)
        except Exception: return None
    return None

def _rptr(addr):
    f = _P.get("read_ptr")
    if f:
        try: return f(addr)
        except Exception: return None
    b = _read(addr, 8)
    return struct.unpack("<Q", b)[0] if b and len(b) == 8 else None

def _valid(addr, size=8):
    f = _P.get("is_valid_address")
    if f:
        try: return bool(f(addr, size))
        except Exception: return False
    return _read(addr, size) is not None

def _log(level, msg):
    f = _P.get("log_message")
    if f:
        try: f(level, msg)
        except Exception: pass

# ---- region walk (game heap = private + committed + readable) ----------------------------------
_k32 = ctypes.WinDLL("kernel32")
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
READABLE = 0x02 | 0x04 | 0x20 | 0x40 | 0x08 | 0x80   # R, RW, ExR, ExRW, WC, ExWC

class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]

def _heap_regions(max_total=768 * 1024 * 1024):
    """Yield (base, size) over committed, readable, PRIVATE regions (where GtaThreads live).
    Bounded so a runaway scan can't walk the whole address space."""
    mbi = _MBI()
    addr = 0
    total = 0
    while addr < 0x7FFFFFFF0000 and total < max_total:
        if _k32.VirtualQuery(ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or addr
        size = mbi.RegionSize or 0x1000
        ok = (mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE
              and not (mbi.Protect & PAGE_GUARD) and (mbi.Protect & READABLE))
        if ok:
            yield base, size
            total += size
        addr = base + size

# ---- rage scrThreadContext layout (best-effort defaults, overridable) --------------------------
# GtaThread base B: B+0x00 = vtable. scrThreadContext begins at B+CTX (rage convention 0x10).
# context: +0x00 ThreadId(u32)  +0x04 ScriptHash(u32)  +0x08 State(u32)  +0x0C ProgramCounter(u32)
_CTX = 0x10
_OFF_TID, _OFF_HASH, _OFF_STATE, _OFF_IP = 0x00, 0x04, 0x08, 0x0C
_STATE_LABELS = {0: "idle", 1: "running", 2: "blocked", 3: "aborted", 4: "killed"}
_STRUCT_WINDOW = 0x400          # bytes of a thread struct we scan for the name buffer
_NAME_BACKSCAN = 0x300          # how far before a name hit the struct base can be

_CACHE = {"vtable": None, "name_offset": None}

# RAGE .ysc script names are lowercase C identifiers: start [a-z], body [a-z0-9_], >= 4 chars
# (main, freemode, carmod_shop, shop_controller, am_mp_*, fm_mission_controller, ...). Being strict
# here is what separates a real name buffer from a random 3-byte printable run.
_NAME_BODY = b"abcdefghijklmnopqrstuvwxyz0123456789_"
_NAME_START = b"abcdefghijklmnopqrstuvwxyz"
_NAME_MIN, _NAME_MAX = 4, 31

def _valid_name(s: bytes) -> bool:
    return (_NAME_MIN <= len(s) <= _NAME_MAX and s[0] in _NAME_START
            and all(c in _NAME_BODY for c in s))

def _printable_name(struct_base, name_offset=None):
    """Return (name, offset) of the script-name buffer inside a thread struct, or (None,None).
    If name_offset is known, read strictly there; else scan the struct window for the first name
    that is null-PRECEDED (a real fixed buffer) and null-terminated."""
    if name_offset is not None:
        b = _read(struct_base + name_offset, 32)
        if b:
            s = b.split(b"\x00", 1)[0]
            if _valid_name(s):
                return s.decode("ascii"), name_offset
        return None, None
    data = _read(struct_base, _STRUCT_WINDOW)
    if not data:
        return None, None
    i = 0x40   # name sits past the context header; skip it to avoid matching context noise
    while i < len(data) - _NAME_MIN:
        if data[i] in _NAME_START and (i == 0 or data[i - 1] not in _NAME_BODY):   # token start
            j = i
            while j < len(data) and data[j] in _NAME_BODY:
                j += 1
            if _NAME_MIN <= (j - i) <= _NAME_MAX and j < len(data) and data[j] == 0:
                return data[i:j].decode("ascii"), i
        i += 1
    return None, None

def _looks_like_thread(base, image_lo, image_hi):
    """Strong validation: base must start with a vtable pointer that lands inside GTA5.exe's
    image, and expose a sane context (small nonzero ThreadId, State in known range)."""
    if base & 0x7 or not _valid(base, _CTX + 0x10):
        return False, None
    vt = _rptr(base)
    if not vt or not (image_lo <= vt < image_hi):
        return False, None
    ctx = _read(base + _CTX, 0x10)
    if not ctx or len(ctx) < 0x10:
        return False, None
    tid, shash, state, ip = struct.unpack("<IIII", ctx)
    if state > 8:                      # rage states are tiny enums
        return False, None
    return True, vt

def _decode_thread(base, name_offset, ctx_off):
    """Decode a thread struct, reading the name STRICTLY at name_offset. Returns None if there is
    no valid name there - that filters out sibling objects that merely share the vtable."""
    name, used_off = _printable_name(base, name_offset)
    if name is None:
        return None
    ctx = _read(base + ctx_off, 0x10) or b"\x00" * 0x10
    tid, shash, state, ip = struct.unpack("<IIII", ctx[:16])
    return {
        "name": name,
        "known": name in _KNOWN,           # True = matches a known .ysc name (high confidence)
        "address": f"0x{base:X}",
        "thread_id": tid,
        "state": state,
        "state_label": _STATE_LABELS.get(state, f"state_{state}"),
        "script_hash": f"0x{shash:08X}",
        "program_counter": ip,
        "name_offset": f"0x{used_off:X}",
    }

def _scan_qword(value, max_hits=512):
    """Find 16-aligned occurrences of an 8-byte value across the game heap. Used to find every
    struct sharing the GtaThread vtable."""
    needle = struct.pack("<Q", value)
    hits = []
    for base, size in _heap_regions():
        off = 0
        # read the region in bounded chunks; a thread struct's vtable is 8-aligned in practice
        CHUNK = 4 * 1024 * 1024
        while off < size:
            n = min(CHUNK, size - off)
            data = _read(base + off, n)
            if data:
                start = 0
                while True:
                    k = data.find(needle, start)
                    if k < 0:
                        break
                    addr = base + off + k
                    if addr & 0x7 == 0:
                        hits.append(addr)
                        if len(hits) >= max_hits:
                            return hits
                    start = k + 8
            off += n
    return hits

# A null-terminated lowercase C identifier. The negative lookbehind makes the captured token a
# MAXIMAL identifier (so we read the whole script name), without requiring a leading null - a
# GtaThread's m_Name buffer is preceded by other struct fields, not a null byte.
_NAME_RE = re.compile(rb"(?<![a-z0-9_])([a-z][a-z0-9_]{3,30})\x00")

def _iter_known_name_hits(max_total=600 * 1024 * 1024):
    """Yield (name, address) for every KNOWN script-name buffer found in heap memory. Regex scan at
    C speed; only names in the whitelist are yielded, so look-alike strings are ignored."""
    for rbase, rsize in _heap_regions(max_total=max_total):
        CHUNK = 8 * 1024 * 1024
        OVERLAP = 64                       # so a name straddling a chunk edge isn't missed
        off = 0
        while off < rsize:
            n = min(CHUNK + OVERLAP, rsize - off)
            data = _read(rbase + off, n)
            if data:
                for m in _NAME_RE.finditer(data):
                    nm = m.group(1).decode("ascii")
                    if nm in _KNOWN:
                        yield nm, rbase + off + m.start(1)
            off += CHUNK

def _locate(image_lo, image_hi, max_total=600 * 1024 * 1024, min_votes=4):
    """Find the GtaThread vtable + name_offset via ground truth: for each KNOWN script-name buffer at
    address S, look at every 8-aligned qword B in [S-0x300, S-0x10]; if *B points into the exe image
    it is a candidate vtable, so vote the pair (vtable=*B, name_offset=S-B). The thread's real m_Name
    buffer makes EVERY running thread vote for the SAME (GtaThread vtable, m_Name offset) pair, which
    therefore dominates - while unrelated in-image member pointers scatter harmlessly.
    (No early break, and B is absolute-8-aligned: the offset 0x14C is not 8-aligned vs S, so a
    window-relative scan would miss it.)"""
    from collections import Counter
    pairs = Counter()
    names_seen = set()
    seen_addr = set()
    hits = 0
    WIN = _NAME_BACKSCAN
    for nm, s_addr in _iter_known_name_hits(max_total):
        if s_addr in seen_addr:
            continue
        seen_addr.add(s_addr)
        hits += 1
        b_lo = (s_addr - WIN + 7) & ~0x7          # first 8-aligned base at/after S-WIN
        voted_here = False
        b = b_lo
        while b <= s_addr - 0x10:
            ok, vt = _looks_like_thread(b, image_lo, image_hi)   # vtable-in-image AND sane context
            if ok:
                pairs[(vt, s_addr - b)] += 1
                voted_here = True
            b += 8
        if voted_here:
            names_seen.add(nm)
    if not pairs:
        return None
    (vt, noff), votes = pairs.most_common(1)[0]
    if votes < min_votes:
        return None
    return {"vtable": vt, "name_offset": noff, "votes": votes, "name_hits": hits,
            "distinct_names": len(names_seen),
            "candidates": [{"vtable": f"0x{v:X}", "name_offset": f"0x{o:X}", "votes": c}
                           for (v, o), c in pairs.most_common(6)]}

def _enumerate(vtable, name_offset, ctx_off, image_lo, image_hi):
    seen, uniq = set(), []
    for base in _scan_qword(vtable, max_hits=4096):
        ok, _vt = _looks_like_thread(base, image_lo, image_hi)
        if not ok:
            continue
        t = _decode_thread(base, name_offset, ctx_off)   # None if no name at the offset -> not a thread
        if t and t["address"] not in seen:
            seen.add(t["address"]); uniq.append(t)
    uniq.sort(key=lambda t: (t["name"], t["address"]))
    return uniq

# ---- handlers ----------------------------------------------------------------------------------
def handle_scripts_list(p):
    base, size = _P["_get_main_module"]() if "_get_main_module" in _P else (None, None)
    if not base:
        return {"error": "could not locate GTA5.exe image"}
    image_lo, image_hi = base, base + size
    ctx_off = int(p.get("ctx_offset", _CTX))
    name_off_override = p.get("name_offset")
    name_off_override = int(name_off_override) if name_off_override is not None else None

    vt = _CACHE["vtable"]
    noff = name_off_override if name_off_override is not None else _CACHE["name_offset"]
    if vt is None or noff is None:
        best = _locate(image_lo, image_hi, max_total=int(p.get("max_scan", 600 * 1024 * 1024)))
        if not best:
            return {"error": "could not locate the GtaThread vtable",
                    "hint": "make sure scripts are running; raise max_scan if the heap is large"}
        vt = best["vtable"]
        if noff is None:
            noff = best["name_offset"]
        _CACHE["vtable"], _CACHE["name_offset"] = vt, noff
    threads = _enumerate(vt, noff, ctx_off, image_lo, image_hi)
    running = [t for t in threads if t["state_label"] == "running"]
    return {
        "success": True,
        "count": len(threads),
        "running": len(running),
        "vtable": f"0x{vt:X}",
        "name_offset": f"0x{noff:X}",
        "note": "name is authoritative; state/thread_id use default rage context offsets - confirm with scripts_inspect",
        "scripts": threads,
    }

def handle_scripts_discover(p):
    """Unsupervised: locate the GtaThread vtable + name_offset with no seed name, and report the
    candidate scoring. Caches the winner so the next scripts_list is instant."""
    base, size = _P["_get_main_module"]() if "_get_main_module" in _P else (None, None)
    if not base:
        return {"error": "could not locate GTA5.exe image"}
    image_lo, image_hi = base, base + size
    best = _locate(image_lo, image_hi, max_total=int(p.get("max_scan", 600 * 1024 * 1024)))
    if not best:
        return {"error": "no GtaThread vtable found", "hint": "ensure scripts are running; raise max_scan"}
    _CACHE["vtable"], _CACHE["name_offset"] = best["vtable"], best["name_offset"]
    return {"success": True,
            "vtable": f"0x{best['vtable']:X}",
            "name_offset": f"0x{best['name_offset']:X}",
            "votes": best["votes"],
            "distinct_known_names": best["distinct_names"],
            "known_name_buffers_seen": best["name_hits"],
            "candidates": best.get("candidates"),
            "next": "now call scripts_list"}

def handle_scripts_inspect(p):
    """Hexdump a thread struct so context sub-offsets (ThreadId/State/stack) can be verified."""
    addr = p.get("address")
    try:
        a = int(addr, 16) if isinstance(addr, str) and addr.lower().startswith("0x") else int(addr)
    except (TypeError, ValueError):
        return {"error": f"bad address: {addr}"}
    length = int(p.get("length", 0x200))
    if not _valid(a, length):
        return {"error": f"address 0x{a:X} not readable for {length} bytes"}
    data = _read(a, length) or b""
    base, size = _P["_get_main_module"]() if "_get_main_module" in _P else (None, None)
    vt = struct.unpack_from("<Q", data, 0)[0] if len(data) >= 8 else 0
    rows = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexs = " ".join(f"{c:02X}" for c in chunk)
        asci = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        rows.append(f"+0x{i:03X}  {hexs:<47}  {asci}")
    name, noff = _printable_name(a)
    return {"success": True, "address": f"0x{a:X}",
            "vtable": f"0x{vt:X}",
            "vtable_in_image": bool(base and base <= vt < base + size),
            "detected_name": name, "detected_name_offset": (f"0x{noff:X}" if noff is not None else None),
            "dump": rows}

def handle_scripts_reset(p):
    _CACHE["vtable"] = None
    _CACHE["name_offset"] = None
    return {"success": True, "reset": True}

SCRIPTS_COMMANDS = {
    "scripts_list": handle_scripts_list,
    "scripts_discover": handle_scripts_discover,
    "scripts_inspect": handle_scripts_inspect,
    "scripts_reset": handle_scripts_reset,
}
SCRIPTS_OFFTHREAD = set(SCRIPTS_COMMANDS)   # all read-only -> paused-safe
