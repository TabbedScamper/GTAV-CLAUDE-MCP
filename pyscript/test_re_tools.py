"""
Off-game validation of re_tools.py (Tier A) against THIS process's own memory.
Crafts synthetic structures (RTTI, pointer chains) and tests against real loaded modules.
"""
import ctypes, struct, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re_tools as rt

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), "-", name, ("  "+extra) if extra else "")

# ---------- A. regex AOB scan (region-aware) ----------
mz = rt.re_scan("4D 5A", limit=1)                       # 'MZ' at module base
check("re_scan finds MZ in main module", mz.get("count", 0) >= 1, str(mz.get("matches")))
pe = rt.re_scan("50 45 00 00", limit=1)                 # 'PE\0\0' deeper in header
check("re_scan finds PE signature (region-aware)", pe.get("count", 0) >= 1)
masked = rt.re_scan("4D 5A ?? ??", limit=1)             # wildcard
check("re_scan handles masked/wildcard pattern", masked.get("count", 0) >= 1)

# ---------- B. .pdata function table ----------
# self-consistency against a module with many functions (kernelbase or kernel32)
kb = rt._k32.GetModuleHandleW("kernelbase.dll") or rt._k32.GetModuleHandleW("kernel32.dll")
tab = rt.parse_pdata(kb)
check("parse_pdata returns a populated function table", bool(tab) and len(tab[1]) > 100,
      f"{len(tab[1]) if tab else 0} funcs")
if tab:
    starts, funcs = tab
    s, e = funcs[len(funcs)//2]                         # a function in the middle
    b1 = rt.func_bounds(s, kb)
    check("func_bounds maps a function-start to its own bounds", b1 == (s, e), str(b1))
    b2 = rt.func_bounds(s + (e - s)//2, kb) if e - s > 1 else b1
    check("func_bounds maps a mid-function address correctly", b2 == (s, e))
    b3 = rt.func_bounds(starts[0] + kb - 1, kb)         # just before first function
    check("func_bounds returns None below the first function", b3 is None)
# realistic end-to-end: a real API address -> its module -> bounds
rt._k32.GetProcAddress.restype = ctypes.c_void_p
rt._k32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
fa = rt._k32.GetProcAddress(rt._k32.GetModuleHandleW("kernelbase.dll"), b"VirtualAlloc")
e2e = rt.func_bounds(fa) if fa else None
check("func_bounds resolves a real API addr end-to-end (module auto-detect)",
      bool(e2e) and e2e[0] <= fa < e2e[1], f"VirtualAlloc=0x{fa:X} -> {rt._fmt_bounds(e2e)}")

# ---------- C. RTTI class-name recovery (synthetic COL/TypeDescriptor) ----------
buf = (ctypes.c_ubyte * 0x400)()
base = ctypes.addressof(buf)
TD, COL, VT = 0x100, 0x200, 0x300
def wr(off, data): ctypes.memmove(base + off, data, len(data))
wr(TD + 0x10, b".?AVCTestVehicle@@\x00")                # TypeDescriptor.name (mangled)
wr(COL, struct.pack("<6I", 1, 0, 0, TD, 0, COL))        # sig=1, pTypeDesc=TD rva, pSelf=COL rva
wr(VT - 8, struct.pack("<Q", base + COL))               # vtable[-1] -> COL (absolute)
name = rt.rtti_class_name(base + VT, image_base=base)
check("rtti_class_name recovers demangled class from synthetic RTTI", name == "CTestVehicle", repr(name))
check("_demangle handles namespaced names",
      rt._demangle(".?AVFoo@Bar@@") == "Bar::Foo", rt._demangle(".?AVFoo@Bar@@"))

# ---------- D. pointer tools ----------
buf2 = (ctypes.c_ubyte * 0x400)()
b2 = ctypes.addressof(buf2)
ctypes.memmove(b2 + 0x00, struct.pack("<Q", b2 + 0x100), 8)         # A -> B
ctypes.memmove(b2 + 0x100 + 0x10, struct.pack("<Q", b2 + 0x200), 8) # B+0x10 -> C
rc = rt.read_chain(b2 + 0x00, [0x00, 0x10, 0x08])
check("read_chain resolves a 2-level pointer path", rc.get("address") == f"0x{b2 + 0x208:X}", str(rc.get("address")))
fp = rt.find_pointers_to(b2 + 0x100, start=b2, size=0x400, limit=8)
check("find_pointers_to locates the slot pointing at target",
      f"0x{b2 + 0x00:X}" in fp.get("pointers", []), str(fp.get("pointers")))

# ---------- E. disasm (capstone optional) ----------
d = rt.disasm(fa or base, count=5) if (fa) else {"error": "no addr"}
if rt._HAVE_CAPSTONE:
    check("disasm returns instructions (capstone present)",
          d.get("success") and d.get("count", 0) >= 1, d.get("instructions", [{}])[0].get("text", ""))
else:
    check("disasm degrades cleanly without capstone", "error" in d and "capstone" in d["error"].lower())

print("\nRESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED:", FAIL); sys.exit(1)
print("ALL PASS - Tier A RE toolkit validated on this machine.")
