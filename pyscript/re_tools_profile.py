"""
re_tools_profile.py  -  IN-PROCESS CPU SAMPLING PROFILER  (per-mod attribution)   [read-only]
================================================================================================
"Which mod is eating frames?" answered WITHOUT injecting a hook into the game and WITHOUT admin
or ETW: a classic sampling profiler implemented with thread suspend + GetThreadContext.

  module_ranges                 list every loaded module (.exe/.asi/.dll) with base+size - the
                                attribution map (which mod owns an address).
  profile_cpu {seconds,hz}      sample the process for N seconds; for each sample, bin the running
                                instruction pointer (RIP) into the owning module. Returns CPU%
                                per module sorted hot-first.

HOW ATTRIBUTION MAPS TO MODS:
  - native .asi / C++ .dll mods  -> their own module (e.g. YourTrainer.asi 8%)
  - ScriptHookV                  -> ScriptHookV.dll
  - ALL SHVDN C# mods            -> land in JIT-compiled code OUTSIDE any module range, bucketed as
                                    "managed/JIT (SHVDN)". This is the total C# cost (ELSC + others
                                    combined); to split it PER C# assembly you need the ETW path
                                    (tools/etw_profiler.py, .NET symbol rundown) - documented there.
  - the game itself              -> GTA5.exe / GTA5_Enhanced.exe

SAFETY: samples ONE thread at a time (suspend -> read RIP -> resume immediately in a finally),
never holds multiple suspended, never writes memory, skips its own thread. A few seconds of
sampling adds mild stutter (that's the cost of suspend-sampling) but cannot crash the game.

INTEGRATION (bridge.py): `import re_tools_profile as rp; rp.bind(globals());
COMMANDS.update(rp.PROFILE_COMMANDS); _OFFTHREAD_COMMANDS |= rp.PROFILE_OFFTHREAD`
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

_P = {}
def bind(bg):
    for n in ("log_message",):
        if n in bg:
            _P[n] = bg[n]
def _log(level, msg):
    f = _P.get("log_message")
    if f:
        try: f(level, msg)
        except Exception: pass

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi")

# CRITICAL: declare prototypes so 64-bit HANDLEs/pointers are NOT truncated to c_int (a truncated
# process/thread handle silently fails - this is the same trap the bridge flags for GetModuleHandleW).
_LP = ctypes.c_void_p
_DW = wintypes.DWORD
_k32.GetCurrentProcess.restype = _LP
_k32.GetCurrentThreadId.restype = _DW
_k32.GetCurrentProcessId.restype = _DW
_k32.OpenThread.restype = _LP
_k32.OpenThread.argtypes = [_DW, wintypes.BOOL, _DW]
_k32.SuspendThread.restype = _DW
_k32.SuspendThread.argtypes = [_LP]
_k32.ResumeThread.restype = _DW
_k32.ResumeThread.argtypes = [_LP]
_k32.GetThreadContext.restype = wintypes.BOOL
_k32.GetThreadContext.argtypes = [_LP, _LP]
_k32.CloseHandle.argtypes = [_LP]
_k32.CreateToolhelp32Snapshot.restype = _LP
_k32.CreateToolhelp32Snapshot.argtypes = [_DW, _DW]
_k32.Thread32First.argtypes = [_LP, _LP]
_k32.Thread32Next.argtypes = [_LP, _LP]
_k32.VirtualQuery.argtypes = [_LP, _LP, ctypes.c_size_t]
_psapi.EnumProcessModulesEx.restype = wintypes.BOOL
_psapi.EnumProcessModulesEx.argtypes = [_LP, _LP, _DW, ctypes.POINTER(_DW), _DW]
_psapi.GetModuleInformation.restype = wintypes.BOOL
_psapi.GetModuleInformation.argtypes = [_LP, _LP, _LP, _DW]
_psapi.GetModuleFileNameExW.restype = _DW
_psapi.GetModuleFileNameExW.argtypes = [_LP, _LP, ctypes.c_wchar_p, _DW]

# ---- module enumeration (psapi) ---------------------------------------------------------------
class _MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p), ("SizeOfImage", wintypes.DWORD),
                ("EntryPoint", ctypes.c_void_p)]

def _enum_modules():
    """Return sorted [(base, end, name)] for every module in this process."""
    h = _k32.GetCurrentProcess()
    needed = wintypes.DWORD(0)
    LIST_ALL = 0x03
    _psapi.EnumProcessModulesEx(h, None, 0, ctypes.byref(needed), LIST_ALL)
    count = needed.value // ctypes.sizeof(ctypes.c_void_p)
    if count == 0:
        return []
    arr = (ctypes.c_void_p * count)()
    if not _psapi.EnumProcessModulesEx(h, arr, needed, ctypes.byref(needed), LIST_ALL):
        return []
    out = []
    for hmod in arr:
        if not hmod:
            continue
        mi = _MODULEINFO()
        if not _psapi.GetModuleInformation(h, ctypes.c_void_p(hmod), ctypes.byref(mi), ctypes.sizeof(mi)):
            continue
        buf = ctypes.create_unicode_buffer(520)
        _psapi.GetModuleFileNameExW(h, ctypes.c_void_p(hmod), buf, 520)
        name = os.path.basename(buf.value) if buf.value else f"0x{hmod:X}"
        base = mi.lpBaseOfDll or 0
        out.append((base, base + mi.SizeOfImage, name))
    out.sort()
    return out

def _owner(modules, rip):
    """Binary-search the module that owns an address; None if outside all (managed/JIT)."""
    lo, hi = 0, len(modules)
    while lo < hi:
        mid = (lo + hi) // 2
        b, e, name = modules[mid]
        if rip < b:
            hi = mid
        elif rip >= e:
            lo = mid + 1
        else:
            return name
    return None

def handle_module_ranges(p):
    mods = _enum_modules()
    return {"success": True, "count": len(mods),
            "modules": [{"name": n, "base": f"0x{b:X}", "size": e - b} for b, e, n in mods]}

# ---- thread enumeration (toolhelp) ------------------------------------------------------------
TH32CS_SNAPTHREAD = 0x00000004
class _THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]

def _interpreter_thread_ids():
    """Native IDs of THIS Python interpreter's threads (PyLoaderV's bridge threads). We must NOT
    suspend these: a Python thread suspended while holding the GIL deadlocks the interpreter.
    Game + SHVDN threads are native (no GIL) so they remain samplable."""
    ids = set()
    for t in threading.enumerate():
        nid = getattr(t, "native_id", None)
        if nid:
            ids.add(int(nid))
    return ids

def _own_thread_ids():
    pid = _k32.GetCurrentProcessId()
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == -1 or snap == 0xFFFFFFFFFFFFFFFF:
        return []
    ids = []
    try:
        te = _THREADENTRY32()
        te.dwSize = ctypes.sizeof(_THREADENTRY32)
        if _k32.Thread32First(snap, ctypes.byref(te)):
            while True:
                if te.th32OwnerProcessID == pid:
                    ids.append(te.th32ThreadID)
                if not _k32.Thread32Next(snap, ctypes.byref(te)):
                    break
    finally:
        _k32.CloseHandle(snap)
    return ids

# ---- x64 CONTEXT (only need RIP @ 0xF8; ContextFlags @ 0x30) -----------------------------------
_CONTEXT_SIZE = 1232
_CTX_FLAGS_OFF = 0x30
_CTX_RIP_OFF = 0xF8
_CONTEXT_AMD64 = 0x00100000
_CONTEXT_CONTROL = _CONTEXT_AMD64 | 0x1
THREAD_GET_CONTEXT = 0x0008
THREAD_SUSPEND_RESUME = 0x0002
THREAD_QUERY_INFORMATION = 0x0040

def _aligned_context():
    """GetThreadContext requires a 16-byte-aligned CONTEXT. Allocate +16 and align by hand."""
    raw = ctypes.create_string_buffer(_CONTEXT_SIZE + 16)
    addr = ctypes.addressof(raw)
    aligned = (addr + 15) & ~0xF
    return raw, aligned

def _sample_rip(hthread, ctx_addr):
    ctypes.memset(ctx_addr, 0, _CONTEXT_SIZE)
    ctypes.cast(ctx_addr + _CTX_FLAGS_OFF, ctypes.POINTER(wintypes.DWORD))[0] = _CONTEXT_CONTROL
    if not _k32.GetThreadContext(hthread, ctypes.c_void_p(ctx_addr)):
        return None
    return ctypes.cast(ctx_addr + _CTX_RIP_OFF, ctypes.POINTER(ctypes.c_ulonglong))[0]

def handle_profile_cpu(p):
    seconds = max(0.5, min(float(p.get("seconds", 5.0)), 30.0))
    hz = max(50, min(int(p.get("hz", 1000)), 8000))
    top = int(p.get("top", 20))

    modules = _enum_modules()
    if not modules:
        return {"error": "could not enumerate modules"}
    # Exclude every interpreter thread (the bridge's own Python threads) - suspending one that holds
    # the GIL would deadlock us. Only native game/SHVDN threads are sampled.
    skip = _interpreter_thread_ids()
    skip.add(_k32.GetCurrentThreadId())

    # open every sibling thread once (handles may go stale mid-run; we tolerate that)
    handles = {}
    for tid in _own_thread_ids():
        if tid in skip:
            continue
        h = _k32.OpenThread(THREAD_GET_CONTEXT | THREAD_SUSPEND_RESUME | THREAD_QUERY_INFORMATION, False, tid)
        if h:
            handles[tid] = h
    if not handles:
        return {"error": "no native (non-interpreter) threads to sample",
                "hint": "in-game this is the GTA/SHVDN thread set; standalone Python has few/none"}

    raw, ctx_addr = _aligned_context()
    by_module = {}
    by_thread = {}
    managed = 0
    total = 0
    interval = 1.0 / hz
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            for tid, h in list(handles.items()):
                susp = _k32.SuspendThread(h)
                if susp == 0xFFFFFFFF:
                    continue
                try:
                    rip = _sample_rip(h, ctx_addr)
                finally:
                    _k32.ResumeThread(h)
                if not rip:
                    continue
                total += 1
                name = _owner(modules, rip)
                key = name or "managed/JIT (SHVDN)"
                if name is None:
                    managed += 1
                by_module[key] = by_module.get(key, 0) + 1
                by_thread[tid] = by_thread.get(tid, 0) + 1
            time.sleep(interval)
    finally:
        for h in handles.values():
            _k32.CloseHandle(h)

    if total == 0:
        return {"error": "no samples captured"}
    ranked = sorted(by_module.items(), key=lambda kv: -kv[1])[:top]
    hot_threads = sorted(by_thread.items(), key=lambda kv: -kv[1])[:8]
    return {
        "success": True,
        "samples": total,
        "seconds": round(seconds, 2),
        "hz": hz,
        "threads_sampled": len(handles),
        "managed_jit_pct": round(100.0 * managed / total, 2),
        "note": "managed/JIT = ALL SHVDN C# mods combined (ELSC + others). Split per-assembly with "
                "tools/etw_profiler.py. .asi/.dll rows are native mods.",
        "by_module": [{"module": n, "pct": round(100.0 * c / total, 2), "samples": c} for n, c in ranked],
        "hot_threads": [{"thread_id": tid, "pct": round(100.0 * c / total, 2)} for tid, c in hot_threads],
    }

PROFILE_COMMANDS = {
    "module_ranges": handle_module_ranges,
    "profile_cpu": handle_profile_cpu,
}
# read-only (no game-memory writes); safe to run off the game thread.
PROFILE_OFFTHREAD = set(PROFILE_COMMANDS)
