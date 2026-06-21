"""
etw_profiler.py  -  HIGH-FIDELITY out-of-process CPU profiler for GTA V (per-mod attribution)
================================================================================================
The accuracy upgrade over the bridge's in-process sampler (re_tools_profile.profile_cpu):

  - Runs in a SEPARATE process, so it CANNOT perturb or crash the game (no thread suspension at all).
  - Uses Windows ETW kernel sampling (the same engine WPA/PerfView use): ~1 kHz stack-walked CPU
    samples across every GTA/SHVDN thread, with far lower overhead than suspend-sampling.
  - With the .NET provider it can attribute managed time PER C# ASSEMBLY (ELSC vs LemonUI vs another
    SHVDN mod) - which the in-process sampler can only lump together as "managed/JIT".

WHAT THIS SCRIPT DOES (headless):
  1. Find the running GTA5.exe / GTA5_Enhanced.exe.
  2. Dump its loaded-module address ranges to gta_modules.json - the map from "address -> which mod".
  3. Record a CPU sampling trace to an .etl  (PerfView if present, else built-in wpr.exe).
  4. If PerfView is available, emit a headless top-by-module CPU report; otherwise tell you how to
     open the .etl in WPA / PerfView (the module JSON makes every address attributable to a mod).

REQUIREMENTS:
  - Run from an ELEVATED terminal (ETW kernel sampling needs admin).
  - Built-in fallback uses wpr.exe (ships with Windows 10/11). For the per-managed-assembly split,
    drop PerfView.exe (single signed Microsoft exe, no install) into this tools/ folder or PATH:
        https://github.com/microsoft/perfview/releases  (PerfView.exe)

USAGE:
    python etw_profiler.py            # 15s capture of the running GTA
    python etw_profiler.py 30         # 30s capture
    python etw_profiler.py --modules  # just dump the module map and exit (no admin needed)
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
GTA_NAMES = ("GTA5.exe", "GTA5_Enhanced.exe")

# ---- cross-process module enumeration (the attribution map) -----------------------------------
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi")
_LP, _DW = ctypes.c_void_p, wintypes.DWORD
_k32.OpenProcess.restype = _LP
_k32.OpenProcess.argtypes = [_DW, wintypes.BOOL, _DW]
_k32.CloseHandle.argtypes = [_LP]
_psapi.EnumProcessModulesEx.restype = wintypes.BOOL
_psapi.EnumProcessModulesEx.argtypes = [_LP, _LP, _DW, ctypes.POINTER(_DW), _DW]
_psapi.GetModuleInformation.restype = wintypes.BOOL
_psapi.GetModuleInformation.argtypes = [_LP, _LP, _LP, _DW]
_psapi.GetModuleFileNameExW.restype = _DW
_psapi.GetModuleFileNameExW.argtypes = [_LP, _LP, ctypes.c_wchar_p, _DW]

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

class _MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", _LP), ("SizeOfImage", _DW), ("EntryPoint", _LP)]

def find_gta_pid():
    """Return (pid, exe_name) for the running GTA, or (None, None)."""
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if not parts:
            continue
        name = parts[0].strip('"')
        for g in GTA_NAMES:
            if name.lower() == g.lower():
                try:
                    return int(parts[1]), name
                except (IndexError, ValueError):
                    pass
    return None, None

def enum_modules(pid):
    """[(base, size, name)] for every module loaded in the target process."""
    h = _k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}) failed err={ctypes.get_last_error()} (need admin / same bitness)")
    try:
        needed = _DW(0)
        LIST_ALL = 0x03
        _psapi.EnumProcessModulesEx(h, None, 0, ctypes.byref(needed), LIST_ALL)
        n = needed.value // ctypes.sizeof(_LP)
        arr = (_LP * n)()
        if not _psapi.EnumProcessModulesEx(h, arr, needed, ctypes.byref(needed), LIST_ALL):
            raise OSError("EnumProcessModulesEx failed")
        mods = []
        for hmod in arr:
            if not hmod:
                continue
            mi = _MODULEINFO()
            if not _psapi.GetModuleInformation(h, _LP(hmod), ctypes.byref(mi), ctypes.sizeof(mi)):
                continue
            buf = ctypes.create_unicode_buffer(520)
            _psapi.GetModuleFileNameExW(h, _LP(hmod), buf, 520)
            mods.append((mi.lpBaseOfDll or 0, mi.SizeOfImage, os.path.basename(buf.value) if buf.value else "?"))
        mods.sort()
        return mods
    finally:
        _k32.CloseHandle(h)

def dump_module_map(pid, exe):
    mods = enum_modules(pid)
    path = os.path.join(HERE, "gta_modules.json")
    data = {"pid": pid, "exe": exe,
            "modules": [{"name": n, "base": f"0x{b:X}", "base_dec": b, "size": s, "end": f"0x{b+s:X}"}
                        for b, s, n in mods]}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    # show the likely mods (.asi + non-system .dll near the game) up top
    interesting = [m for m in mods if m[2].lower().endswith(".asi")
                   or "scripthook" in m[2].lower() or "lemon" in m[2].lower()]
    print(f"  module map -> {path}  ({len(mods)} modules)")
    if interesting:
        print("  mod-looking modules:")
        for b, s, n in interesting:
            print(f"     {n:<32} 0x{b:X}  ({s//1024} KB)")
    return path

# ---- ETW capture ------------------------------------------------------------------------------
def _is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def _which(name):
    for d in [HERE] + os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None

def record_perfview(seconds, etl, perfview):
    """Collect CPU + .NET with PerfView headlessly. Produces <etl> (and a .zip with symbols)."""
    print(f"  PerfView capturing {seconds}s of CPU + .NET ...")
    cmd = [perfview, "/nogui", "/accepteula", "/noview", "/zip:true",
           f"/maxCollectSec:{seconds}", "/merge:true", "collect", etl]
    subprocess.run(cmd, cwd=HERE)
    return os.path.exists(etl) or os.path.exists(etl + ".zip")

def record_wpr(seconds, etl):
    """Built-in fallback: wpr.exe CPU profile. Kernel sampling -> attributes to modules."""
    wpr = _which("wpr.exe") or "wpr.exe"
    print(f"  wpr capturing {seconds}s of CPU ...")
    # CPU = built-in CPU-usage sampling profile; DotNET adds managed events when available.
    start = subprocess.run([wpr, "-start", "CPU", "-filemode"], capture_output=True, text=True)
    if start.returncode != 0:
        print("  wpr -start failed:", start.stderr.strip() or start.stdout.strip())
        return False
    subprocess.run([wpr, "-start", "DotNET", "-filemode"], capture_output=True, text=True)  # best-effort
    time.sleep(seconds)
    stop = subprocess.run([wpr, "-stop", etl], capture_output=True, text=True)
    if stop.returncode != 0:
        print("  wpr -stop failed:", stop.stderr.strip() or stop.stdout.strip())
        subprocess.run([wpr, "-cancel"], capture_output=True, text=True)
        return False
    return os.path.exists(etl)

def main():
    args = [a for a in sys.argv[1:]]
    only_modules = "--modules" in args
    secs = next((int(a) for a in args if a.isdigit()), 15)

    pid, exe = find_gta_pid()
    if not pid:
        print("GTA V is not running (looked for GTA5.exe / GTA5_Enhanced.exe).")
        return 1
    print(f"GTA found: {exe} pid={pid}")

    try:
        dump_module_map(pid, exe)
    except OSError as e:
        print(f"  could not read modules: {e}")

    if only_modules:
        return 0

    if not _is_admin():
        print("\n! ETW kernel sampling needs an ELEVATED terminal. Re-run as Administrator.")
        print("  (The module map above was still written and is admin-free.)")
        return 2

    etl = os.path.join(HERE, "gta_cpu.etl")
    pv = _which("PerfView.exe")
    ok = record_perfview(secs, etl, pv) if pv else record_wpr(secs, etl)
    if not ok:
        print("  capture failed - see messages above.")
        return 3

    print(f"\nDone. Trace: {etl}{' (+ .zip)' if pv else ''}")
    print("Open it and attribute CPU to mods:")
    if pv:
        print(f"  PerfView {os.path.basename(etl)}.zip   ->  CPU Stacks  ->  GroupPats by module/assembly")
        print("  (.NET symbols are in the zip, so SHVDN mods split per C# assembly: ELSC, LemonUI, ...)")
    else:
        print("  Open gta_cpu.etl in Windows Performance Analyzer (WPA): 'CPU Usage (Sampled)' ->")
        print("  group by Module. Use tools\\gta_modules.json to map any raw address to its mod.")
        print("  For a per-C#-assembly split, capture with PerfView.exe (drop it in tools\\).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
