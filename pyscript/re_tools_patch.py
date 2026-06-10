"""
re_tools_patch.py  -  CODE MODIFICATION primitives (change behavior, not just data)   [Tier B]
================================================================================================
    patch_bytes(addr, hexbytes)   write bytes with snapshot + reversible restore (VirtualProtect dance)
    nop(addr, n_instructions)     NOP whole instructions (length-aware via disasm; never half an instr)
    restore_patch(id) / list_patches / restore_all
    alloc_cave(size, near)        VirtualAlloc executable memory within +/-2GB of `near` (rel32 reach)
    capture_stack()               RtlCaptureStackBackTrace -> who called (symbolized via func_bounds)

Sources: kylehalladay "X64 Function Hooking by Example" (AllocatePageNearAddress, +-0x7FFFFF00, NOP-before-
overwrite), MS docs (VirtualAlloc/VirtualQuery/RtlCaptureStackBackTrace), x64dbg per-byte patch tracking.
SAFETY: every patch is WAL-logged BEFORE the write and stored for exact restore; targets are validated
executable + in-module unless allow_outside=true. These RUN ON THE GAME THREAD (NOT off-thread).
"""
import ctypes
from ctypes import wintypes

_P = {}
def bind(bg):
    for n in ("read_bytes", "is_executable_address", "_get_main_module",
              "wal_write", "log_message", "_disasm_lengths"):
        if n in bg:
            _P[n] = bg[n]

# PRIVATE WinDLL instances - setting .argtypes/.restype here must NOT pollute the shared
# ctypes.windll.kernel32 that bridge.py uses (that caused a VirtualQuery type clash).
_k32 = ctypes.WinDLL("kernel32")
_ntdll = ctypes.WinDLL("ntdll")
PAGE_EXECUTE_READWRITE = 0x40
MEM_COMMIT = 0x1000; MEM_RESERVE = 0x2000; MEM_FREE = 0x10000; MEM_RELEASE = 0x8000
PAGE_GUARD = 0x100
EXECUTABLE = 0x10 | 0x20 | 0x40 | 0x80

class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]

_k32.GetModuleHandleW.restype = ctypes.c_void_p   # else the 64-bit handle truncates -> wrong base
_k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
_k32.VirtualProtect.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_k32.VirtualProtect.restype = wintypes.BOOL
_k32.VirtualAlloc.restype = wintypes.LPVOID
_k32.VirtualAlloc.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
_k32.VirtualQuery.argtypes = [wintypes.LPVOID, ctypes.POINTER(_MBI), ctypes.c_size_t]
_ntdll.RtlCaptureStackBackTrace.restype = wintypes.USHORT
_ntdll.RtlCaptureStackBackTrace.argtypes = [wintypes.ULONG, wintypes.ULONG,
                                            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]

def _resolve(v):
    return (int(v, 16) if v.lower().startswith("0x") else int(v)) if isinstance(v, str) else int(v)
def _read(a, n):
    f = _P.get("read_bytes")
    if f: return f(a, n)
    try: return bytes((ctypes.c_char * n).from_address(a))
    except Exception: return None
def _main_module():
    f = _P.get("_get_main_module")
    if f: return f()
    base = _k32.GetModuleHandleW(None)
    return base, None
def _is_exec(a):
    f = _P.get("is_executable_address")
    if f: return f(a)
    mbi = _MBI()
    if _k32.VirtualQuery(ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0: return False
    return mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD) and bool(mbi.Protect & EXECUTABLE)
def _wal(rec):
    f = _P.get("wal_write")
    if f: f(rec)

def _vp_write(addr, data):
    old = wintypes.DWORD(0); n = len(data)
    if not _k32.VirtualProtect(ctypes.c_void_p(addr), n, PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
        raise OSError("VirtualProtect->RWX failed")
    try:
        ctypes.memmove(addr, (ctypes.c_char * n).from_buffer_copy(data), n)
    finally:
        tmp = wintypes.DWORD(0)
        _k32.VirtualProtect(ctypes.c_void_p(addr), n, old.value, ctypes.byref(tmp))
    _k32.FlushInstructionCache(_k32.GetCurrentProcess(), ctypes.c_void_p(addr), n)

# ---- patch registry ----
_patches = {}
_next_id = [1]

def _parse_hex(h):
    if isinstance(h, (bytes, bytearray)):
        return bytes(h)
    return bytes(int(t, 16) for t in str(h).replace(",", " ").split())

def patch_bytes(address, hexbytes, allow_outside=False):
    addr = _resolve(address)
    data = _parse_hex(hexbytes)
    if not allow_outside:
        base, size = _main_module()
        if not _is_exec(addr):
            return {"error": f"0x{addr:X} not executable (allow_outside=true to override for data)"}
        if base and size and not (base <= addr < base + size):
            return {"error": f"0x{addr:X} outside GTA5.exe; pass allow_outside=true to override"}
    original = _read(addr, len(data))
    if original is None:
        return {"error": f"could not read original bytes at 0x{addr:X}"}
    pid = _next_id[0]; _next_id[0] += 1
    _wal({"op": "patch_bytes", "id": pid, "address": f"0x{addr:X}",
          "original": original.hex(), "new": data.hex()})
    try:
        _vp_write(addr, data)
    except Exception as e:
        return {"error": f"patch failed: {e}"}
    _patches[pid] = {"addr": addr, "original": original, "patched": data}
    return {"success": True, "id": pid, "address": f"0x{addr:X}", "bytes": len(data),
            "before": original.hex(), "after": data.hex()}

def nop(address, n_instructions):
    addr = _resolve(address); n = int(n_instructions)
    try:
        import re_tools
        d = re_tools.disasm(f"0x{addr:X}", count=n)        # capstone-backed; gives per-instr length
    except Exception as e:
        return {"error": f"disasm unavailable ({e}); cannot length-align NOP"}
    if "error" in d:
        return {"error": f"disasm: {d['error']}"}
    insns = d.get("instructions", [])
    if len(insns) < n:
        return {"error": f"only disassembled {len(insns)}/{n} instructions"}
    total = sum(i["len"] for i in insns[:n])               # span whole instructions, never half one
    return patch_bytes(f"0x{addr:X}", b"\x90" * total)

def restore_patch(pid):
    pid = int(pid)
    p = _patches.pop(pid, None)
    if not p:
        return {"error": f"no patch id {pid}"}
    _wal({"op": "restore_patch", "id": pid, "address": f"0x{p['addr']:X}",
          "restored": p["original"].hex()})
    try:
        _vp_write(p["addr"], p["original"])
    except Exception as e:
        _patches[pid] = p
        return {"error": f"restore failed: {e}"}
    return {"success": True, "id": pid, "address": f"0x{p['addr']:X}", "restored": p["original"].hex()}

def list_patches():
    return {"success": True, "patches": [{"id": i, "address": f"0x{p['addr']:X}",
            "bytes": len(p["patched"])} for i, p in _patches.items()]}

def restore_all():
    ids = list(_patches.keys())
    for i in ids:
        restore_patch(i)
    return {"success": True, "restored": len(ids)}

def alloc_cave(size=0x1000, near=None):
    """VirtualAlloc RWX memory within +/-2GB of `near` (default: GTA5.exe base) so a 5-byte rel32
    jmp/call can reach it. Spirals outward probing MEM_FREE pages (kylehalladay AllocatePageNearAddress)."""
    base, _ = _main_module()
    near = _resolve(near) if near is not None else base
    page = 0x1000; gran = 0x10000; span = 0x7FFFFF00
    nearp = near & ~(page - 1)
    mbi = _MBI()
    off = page
    while off < span:
        for cand in (nearp + off, nearp - off):
            if cand <= 0:
                continue
            ab = (cand // gran) * gran
            if _k32.VirtualQuery(ctypes.c_void_p(ab), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                continue
            if mbi.State != MEM_FREE or (mbi.RegionSize or 0) < size:
                continue
            p = _k32.VirtualAlloc(ctypes.c_void_p(ab), size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
            if p:
                dist = abs(int(p) - near)
                return {"success": True, "address": f"0x{int(p):X}", "size": size,
                        "distance": f"0x{dist:X}", "reachable_rel32": dist < span}
        off += gran
    return {"error": "no free page found within +/-2GB of target"}

def capture_stack(skip=1, max_frames=32):
    arr = (ctypes.c_void_p * max_frames)()
    n = _ntdll.RtlCaptureStackBackTrace(int(skip), int(max_frames), arr, None)
    base, size = _main_module()
    frames = []
    for i in range(n):
        ad = arr[i] or 0
        rec = {"addr": f"0x{ad:X}"}
        if base and size and base <= ad < base + size:
            rec["module_off"] = f"GTA5.exe+0x{ad - base:X}"
        frames.append(rec)
    return {"success": True, "count": n, "frames": frames}

# ---- handlers + registration ----
def handle_patch_bytes(p):   return patch_bytes(p.get("address"), p.get("bytes"), bool(p.get("allow_outside")))
def handle_nop(p):           return nop(p.get("address"), int(p.get("count", 1)))
def handle_restore_patch(p): return restore_patch(p.get("id"))
def handle_list_patches(p):  return list_patches()
def handle_restore_all(p):   return restore_all()
def handle_alloc_cave(p):    return alloc_cave(int(p.get("size", 0x1000)), p.get("near"))
def handle_capture_stack(p): return capture_stack(int(p.get("skip", 1)), int(p.get("max_frames", 32)))

PATCH_COMMANDS = {
    "patch_bytes": handle_patch_bytes, "nop": handle_nop,
    "restore_patch": handle_restore_patch, "list_patches": handle_list_patches,
    "restore_all_patches": handle_restore_all, "alloc_cave": handle_alloc_cave,
    "capture_stack": handle_capture_stack,
}
# capture_stack/list/alloc are safe off-thread; patch/nop/restore modify code -> game thread only.
PATCH_OFFTHREAD = {"capture_stack", "list_patches", "alloc_cave"}
