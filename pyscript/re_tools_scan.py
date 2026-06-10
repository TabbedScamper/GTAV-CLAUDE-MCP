"""
re_tools_scan.py  -  VALUE SCAN + SUCCESSIVE NARROWING  (Cheat-Engine-style)   [Tier A, paused-safe]
================================================================================================
Find an address you can SEE in-game but don't know (health, ammo, cash, cooldowns):
    scan_first(vtype, scan_type, value)   first scan  (exact | unknown | bigger | smaller | between)
    scan_next(scan_type, value)           narrow      (changed|unchanged|increased|decreased|exact|
                                                        incby|decby) vs the PREVIOUS scan's values
    scan_results(limit)  scan_count()  scan_undo()  scan_reset()

Algorithm follows scanmem/Cheat Engine (every compare is `cur` vs `user_value` OR `old_value`):
  - first "unknown" scan  -> SNAPSHOT mode (keep region byte-copies as the baseline)
  - first narrowing scan  -> convert to LIST mode (sparse (addr,value) survivors)
  - aligned typed scan via numpy when available (~zero-copy frombuffer), pure-python fallback otherwise.

INTEGRATION (bridge.py, after COMMANDS): `import re_tools_scan as rts; rts.bind(globals());
COMMANDS.update(rts.SCAN_COMMANDS); _OFFTHREAD_COMMANDS |= rts.SCAN_OFFTHREAD` (all read-only).
"""
import ctypes
import struct
from ctypes import wintypes

try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:
    _np = None
    HAVE_NUMPY = False

# ---- primitive provider (bridge's when bound, else fallback) --------------------------------
_P = {}
def bind(bg):
    for n in ("read_bytes", "is_valid_address", "_get_main_module", "log_message"):
        if n in bg:
            _P[n] = bg[n]

_k32 = ctypes.WinDLL("kernel32")   # private instance - don't pollute the shared windll bridge uses
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
WRITABLE = 0x04 | 0x08 | 0x40 | 0x80   # READWRITE, WRITECOPY, EXECUTE_READWRITE, EXECUTE_WRITECOPY

class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]

def _read(addr, n):
    f = _P.get("read_bytes")
    if f:
        return f(addr, n)
    try:
        buf = (ctypes.c_char * n).from_address(addr)
        return bytes(buf)
    except Exception:
        return None

# struct fmt + numpy dtype per value type
_VT = {
    "i8": ("b", 1), "u8": ("B", 1), "i16": ("h", 2), "u16": ("H", 2),
    "i32": ("i", 4), "u32": ("I", 4), "i64": ("q", 8), "u64": ("Q", 8),
    "f32": ("f", 4), "f64": ("d", 8),
}
_NP = {"i8": "int8", "u8": "uint8", "i16": "int16", "u16": "uint16", "i32": "int32",
       "u32": "uint32", "i64": "int64", "u64": "uint64", "f32": "float32", "f64": "float64"}

def _is_float(vt): return vt in ("f32", "f64")

def _iter_scan_regions(writable_only=True, private_only=True, max_total=512 * 1024 * 1024):
    """Yield (base, data) over committed regions (default: private+writable heap, where mutable
    game values live). Bounded by max_total bytes so a snapshot can't blow up memory."""
    mbi = _MBI()
    addr = 0
    total = 0
    # walk the user address space
    while addr < 0x7FFFFFFF0000 and total < max_total:
        if _k32.VirtualQuery(ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base = mbi.BaseAddress or addr
        size = mbi.RegionSize or 0x1000
        ok = (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD))
        if writable_only:
            ok = ok and bool(mbi.Protect & WRITABLE)
        if private_only:
            ok = ok and (mbi.Type == MEM_PRIVATE)
        if ok:
            take = min(size, max_total - total)
            data = _read(base, take)
            if data:
                total += len(data)
                yield base, data
        addr = base + size
    return

# ---- scan state (one session) ----------------------------------------------------------------
# mode "list":     {"mode","vt","addrs":[int],"vals":[number]}
# mode "snapshot": {"mode","vt","snaps":[(base,bytes)]}
_STATE = {}
_UNDO = {}

def _np_view(data, vt):
    n = len(data) // _VT[vt][1]
    arr = _np.frombuffer(data, dtype=_np.dtype(_NP[vt]), count=n)
    return arr

def _py_values(data, vt):
    fmt, sz = _VT[vt]
    n = len(data) // sz
    return list(struct.unpack_from("<%d%s" % (n, fmt), data, 0)) if n else []

def _exact_mask_py(vals, vt, value, eps):
    if _is_float(vt):
        return [i for i, v in enumerate(vals) if abs(v - value) <= eps]
    return [i for i, v in enumerate(vals) if v == value]

def scan_first(vtype, scan_type="exact", value=None, lo=None, hi=None, eps=0.01,
               start=None, size=None, max_results=200000, writable_only=True):
    """First scan. scan_type: exact|unknown|bigger|smaller|between. Returns {count,mode}."""
    vt = vtype.lower()
    if vt not in _VT:
        return {"error": f"vtype must be one of {list(_VT)}"}
    sz = _VT[vt][1]
    _UNDO.clear(); _UNDO.update(_STATE)        # keep prior state for undo
    _STATE.clear()

    # region source: a specific (start,size) or the auto region walk
    if start is not None and size is not None:
        regions = [(int(start), _read(int(start), int(size)))]
        regions = [(b, d) for b, d in regions if d]
    else:
        regions = _iter_scan_regions(writable_only=writable_only)

    if scan_type == "unknown":
        snaps, total = [], 0
        for base, data in regions:
            usable = (len(data) // sz) * sz
            snaps.append((base, data[:usable]))
            total += usable
        _STATE.update({"mode": "snapshot", "vt": vt, "snaps": snaps})
        return {"success": True, "scan_type": "unknown", "mode": "snapshot",
                "bytes": total, "note": "baseline stored; now do scan_next(increased/decreased/...)"}

    addrs, vals = [], []
    for base, data in regions:
        usable = (len(data) // sz) * sz
        if not usable:
            continue
        if HAVE_NUMPY:
            arr = _np_view(data[:usable], vt)
            if   scan_type == "exact":
                mask = (_np.abs(arr - value) <= eps) if _is_float(vt) else (arr == value)
            elif scan_type == "bigger":  mask = arr > value
            elif scan_type == "smaller": mask = arr < value
            elif scan_type == "between": mask = (arr >= lo) & (arr <= hi)
            else: return {"error": f"bad first scan_type '{scan_type}'"}
            idx = _np.flatnonzero(mask)
            for i in idx:
                addrs.append(base + int(i) * sz); vals.append(arr[i].item())
                if len(addrs) >= max_results: break
        else:
            v = _py_values(data[:usable], vt)
            if   scan_type == "exact":   idx = _exact_mask_py(v, vt, value, eps)
            elif scan_type == "bigger":  idx = [i for i, x in enumerate(v) if x > value]
            elif scan_type == "smaller": idx = [i for i, x in enumerate(v) if x < value]
            elif scan_type == "between": idx = [i for i, x in enumerate(v) if lo <= x <= hi]
            else: return {"error": f"bad first scan_type '{scan_type}'"}
            for i in idx:
                addrs.append(base + i * sz); vals.append(v[i])
                if len(addrs) >= max_results: break
        if len(addrs) >= max_results:
            break
    _STATE.update({"mode": "list", "vt": vt, "addrs": addrs, "vals": vals})
    return {"success": True, "scan_type": scan_type, "mode": "list",
            "count": len(addrs), "truncated": len(addrs) >= max_results}

def _cmp(cur, old, scan_type, value, eps, is_float):
    if   scan_type == "increased": return cur > old
    elif scan_type == "decreased": return cur < old
    elif scan_type == "changed":   return (abs(cur - old) > eps) if is_float else (cur != old)
    elif scan_type == "unchanged": return (abs(cur - old) <= eps) if is_float else (cur == old)
    elif scan_type == "incby":     return abs(cur - (old + value)) <= eps if is_float else cur == old + value
    elif scan_type == "decby":     return abs(cur - (old - value)) <= eps if is_float else cur == old - value
    elif scan_type == "exact":     return abs(cur - value) <= eps if is_float else cur == value
    return False

def scan_next(scan_type="unchanged", value=None, eps=0.01, max_results=200000):
    """Narrow the previous scan. scan_type: changed|unchanged|increased|decreased|exact|incby|decby."""
    if not _STATE:
        return {"error": "no active scan - call scan_first first"}
    vt = _STATE["vt"]; sz = _VT[vt][1]; isf = _is_float(vt)
    _UNDO.clear(); _UNDO.update({k: (list(v) if isinstance(v, list) else v) for k, v in _STATE.items()})
    addrs, vals = [], []

    if _STATE["mode"] == "snapshot":
        for base, old in _STATE["snaps"]:
            cur = _read(base, len(old))
            if not cur or len(cur) < len(old):
                continue
            n = len(old) // sz
            if HAVE_NUMPY:
                a_old = _np.frombuffer(old, dtype=_np.dtype(_NP[vt]), count=n)
                a_cur = _np.frombuffer(cur[:len(old)], dtype=_np.dtype(_NP[vt]), count=n)
                if   scan_type == "increased": m = a_cur > a_old
                elif scan_type == "decreased": m = a_cur < a_old
                elif scan_type == "changed":   m = (_np.abs(a_cur - a_old) > eps) if isf else (a_cur != a_old)
                elif scan_type == "unchanged": m = (_np.abs(a_cur - a_old) <= eps) if isf else (a_cur == a_old)
                elif scan_type == "incby":     m = (_np.abs(a_cur - (a_old + value)) <= eps) if isf else (a_cur == a_old + value)
                elif scan_type == "decby":     m = (_np.abs(a_cur - (a_old - value)) <= eps) if isf else (a_cur == a_old - value)
                else: return {"error": f"bad next scan_type '{scan_type}'"}
                idx = _np.flatnonzero(m)
                for i in idx:
                    addrs.append(base + int(i) * sz); vals.append(a_cur[i].item())
                    if len(addrs) >= max_results: break
            else:
                vo = _py_values(old, vt); vc = _py_values(cur[:len(old)], vt)
                for i in range(min(len(vo), len(vc))):
                    if _cmp(vc[i], vo[i], scan_type, value, eps, isf):
                        addrs.append(base + i * sz); vals.append(vc[i])
                        if len(addrs) >= max_results: break
            if len(addrs) >= max_results:
                break
    else:  # list mode: re-read each survivor
        fmt = _VT[vt][0]
        for a, old in zip(_STATE["addrs"], _STATE["vals"]):
            b = _read(a, sz)
            if not b or len(b) < sz:
                continue
            cur = struct.unpack_from("<" + fmt, b, 0)[0]
            if _cmp(cur, old, scan_type, value, eps, isf):
                addrs.append(a); vals.append(cur)
    _STATE.update({"mode": "list", "vt": vt, "addrs": addrs, "vals": vals})
    _STATE.pop("snaps", None)
    return {"success": True, "scan_type": scan_type, "count": len(addrs)}

def scan_count():
    if not _STATE: return {"error": "no active scan"}
    if _STATE["mode"] == "snapshot":
        return {"mode": "snapshot", "note": "unknown baseline; count is per-byte until first narrow"}
    return {"mode": "list", "count": len(_STATE["addrs"])}

def scan_results(limit=50):
    if not _STATE or _STATE["mode"] != "list":
        return {"error": "no narrowed results yet"}
    out = [{"address": f"0x{a:X}", "value": v}
           for a, v in list(zip(_STATE["addrs"], _STATE["vals"]))[:limit]]
    return {"success": True, "count": len(_STATE["addrs"]), "shown": len(out), "results": out}

def scan_undo():
    if not _UNDO:
        return {"error": "nothing to undo"}
    _STATE.clear(); _STATE.update(_UNDO); _UNDO.clear()
    return {"success": True, "restored_mode": _STATE.get("mode")}

def scan_reset():
    _STATE.clear(); _UNDO.clear()
    return {"success": True, "reset": True}

# ---- handlers + registration ----
def handle_scan_first(p):
    return scan_first(p.get("vtype", "i32"), p.get("scan_type", "exact"),
                      value=p.get("value"), lo=p.get("lo"), hi=p.get("hi"),
                      eps=float(p.get("eps", 0.01)),
                      start=p.get("start"), size=p.get("size"),
                      max_results=int(p.get("max_results", 200000)),
                      writable_only=bool(p.get("writable_only", True)))
def handle_scan_next(p):
    return scan_next(p.get("scan_type", "unchanged"), value=p.get("value"),
                     eps=float(p.get("eps", 0.01)), max_results=int(p.get("max_results", 200000)))
def handle_scan_results(p): return scan_results(int(p.get("limit", 50)))
def handle_scan_count(p):   return scan_count()
def handle_scan_undo(p):    return scan_undo()
def handle_scan_reset(p):   return scan_reset()

SCAN_COMMANDS = {
    "scan_first": handle_scan_first, "scan_next": handle_scan_next,
    "scan_results": handle_scan_results, "scan_count": handle_scan_count,
    "scan_undo": handle_scan_undo, "scan_reset": handle_scan_reset,
}
SCAN_OFFTHREAD = set(SCAN_COMMANDS)   # read-only -> paused-safe
