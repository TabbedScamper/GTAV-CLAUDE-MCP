"""
re_tools.py  -  RE TOOLKIT ADD-ON for GTAV-Claude-MCP  (NEW - not in the deployed bridge yet)
================================================================================================
This is ADDITIONAL code, separate from the existing bridge.py. It adds deep reverse-engineering
capabilities (Tier A: static analysis - all read-only, paused-safe, testable off-game):

    re_scan          regex-accelerated AOB scan (C-speed; replaces the byte-loop in scan_pattern)
    func_bounds      address -> (function_start, function_end) via the in-memory PE .pdata table
    list_functions   enumerate the module's functions (RUNTIME_FUNCTION exception directory)
    identify         object/vtable pointer -> C++ class name via MSVC RTTI ("CVehicle", "CPed"...)
    read_chain       multi-level pointer-path resolve (module+off -> +off -> ... -> value)
    find_pointers    find pointers TO a target address (pointer-scan primitive)
    disasm           disassemble N instructions (needs capstone; import-guarded, degrades cleanly)

INTEGRATION (on the home machine, next to bridge.py in the legacy game folder):
    1. Drop this file next to bridge.py.
    2. In bridge.py, AFTER the COMMANDS dict is defined, add:
           import re_tools
           re_tools.bind(globals())          # share bridge's ctypes primitives
           COMMANDS.update(re_tools.RE_COMMANDS)
           _OFFTHREAD_COMMANDS |= re_tools.RE_OFFTHREAD   # they're read-only -> paused-safe
    3. Add the matching MCP tools from server_additions.py to mcp_server/server.py.

It uses bridge's primitives when bound; otherwise it falls back to its own ctypes primitives so it
can run/standalone-test on any machine (that's how it was validated on the work machine).
"""
import ctypes
import re
import struct
from ctypes import wintypes

# ============================================================================
# Primitive provider: use bridge.py's primitives when bound, else our own.
# bind(bridge_globals) lets the integrated bridge share its exact memory helpers.
# ============================================================================
_P = {}  # filled by bind(); names: read_bytes, is_valid_address, _get_main_module, log

def bind(bridge_globals: dict):
    """Wire to bridge.py's primitives (call once from bridge after COMMANDS is defined)."""
    for name in ("read_bytes", "is_valid_address", "is_executable_address",
                 "_get_main_module", "log_message", "note_finding"):
        if name in bridge_globals:
            _P[name] = bridge_globals[name]

# ---- standalone fallback primitives (also used by the self-test) ------------
_k32 = ctypes.windll.kernel32
_k32.GetModuleHandleW.restype = ctypes.c_void_p
_k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
READABLE = 0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80
EXECUTABLE = 0x10 | 0x20 | 0x40 | 0x80

class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]

def _vq(addr):
    mbi = _MBI()
    if _k32.VirtualQuery(ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
        return None
    return mbi

def _fb_is_valid(addr, size=1):
    if not addr or size <= 0:
        return False
    mbi = _vq(addr)
    if not mbi or mbi.State != MEM_COMMIT or (mbi.Protect & PAGE_GUARD):
        return False
    if not (mbi.Protect & READABLE):
        return False
    end = (mbi.BaseAddress or 0) + (mbi.RegionSize or 0)
    return addr + size <= end

def _fb_read_bytes(addr, size):
    if not _fb_is_valid(addr, size):
        return None
    try:
        buf = (ctypes.c_ubyte * size)()
        ctypes.memmove(buf, addr, size)
        return bytes(buf)
    except Exception:
        return None

def _fb_is_exec(addr):
    mbi = _vq(addr)
    if not mbi or mbi.State != MEM_COMMIT or (mbi.Protect & PAGE_GUARD):
        return False
    return bool(mbi.Protect & EXECUTABLE)

def _fb_main_module():
    base = _k32.GetModuleHandleW(None)
    if not base:
        return None, None
    if not _fb_is_valid(base + 0x3C, 4):
        return None, None
    e_lfanew = struct.unpack("<I", _fb_read_bytes(base + 0x3C, 4))[0]
    nt = base + e_lfanew
    if not _fb_is_valid(nt + 0x18 + 0x38, 4):
        return base, None
    size = struct.unpack("<I", _fb_read_bytes(nt + 0x18 + 0x38, 4))[0]
    return base, size

# resolved accessors (bridge's if bound, else fallback)
def _read_bytes(a, n): return _P.get("read_bytes", _fb_read_bytes)(a, n)
def _is_valid(a, n=1): return _P.get("is_valid_address", _fb_is_valid)(a, n)
def _is_exec(a):       return _P.get("is_executable_address", _fb_is_exec)(a)
def _main_module():    return _P.get("_get_main_module", _fb_main_module)()
def _log(level, msg):
    f = _P.get("log_message")
    if f:
        f(level, msg)

def _u32(a):
    b = _read_bytes(a, 4); return struct.unpack("<I", b)[0] if b else None
def _u64(a):
    b = _read_bytes(a, 8); return struct.unpack("<Q", b)[0] if b else None
def _cstr(a, maxlen=256):
    b = _read_bytes(a, maxlen)
    if not b:
        return None
    z = b.find(b"\x00")
    return b[:z if z >= 0 else maxlen].decode("ascii", "replace")

def _resolve(v):
    if isinstance(v, str):
        return int(v, 16) if v.lower().startswith("0x") else int(v)
    return int(v)

def _module_base_for(addr):
    """Module base (AllocationBase) that owns `addr`, and that module's image size."""
    mbi = _vq(addr)
    if not mbi or not mbi.AllocationBase:
        return None, None
    base = mbi.AllocationBase
    e = _u32(base + 0x3C)
    if e is None:
        return base, None
    sz = _u32(base + e + 0x18 + 0x38)
    return base, sz

# ============================================================================
# A. Regex-accelerated AOB scan  (the scanner speedup; C-speed vs byte loop)
# ============================================================================
_aob_cache = {}

def compile_aob(pattern: str):
    """'48 8B 05 ?? ?? ?? ?? 90' -> (compiled regex, token_count). '?'/'??'/'*' = wildcard."""
    if pattern in _aob_cache:
        return _aob_cache[pattern]
    toks = pattern.replace(",", " ").split()
    parts = [b"." if t in ("?", "??", "*") else re.escape(bytes([int(t, 16)])) for t in toks]
    res = (re.compile(b"".join(parts), re.DOTALL), len(toks))  # DOTALL so '.' matches 0x0A
    _aob_cache[pattern] = res
    return res

def _iter_chunks(base, end, budget, overlap):
    """Yield (chunk_start, data) over committed-readable regions; clamps reads to one region."""
    cur = base
    while cur < end:
        mbi = _vq(cur)
        if not mbi:
            return
        rbase = mbi.BaseAddress or cur
        rend = rbase + (mbi.RegionSize or 0x1000)
        readable = (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD)
                    and (mbi.Protect & READABLE))
        if not readable:
            cur = rend
            continue
        stop = min(rend, end)
        while cur < stop:
            rl = min(budget + overlap, stop - cur)
            data = _read_bytes(cur, rl)
            if not data:
                break
            yield cur, data
            if cur + (rl - overlap) <= cur:
                break
            cur += max(1, rl - overlap)
            if cur >= stop:
                break
        cur = rend

def re_scan(pattern: str, limit: int = 16, base=None, size=None):
    """Region-aware regex AOB scan of the main module. Returns match addresses (hex)."""
    rx, plen = compile_aob(pattern)
    if base is None:
        base, size = _main_module()
    if not base:
        return {"error": "could not locate main module"}
    end = base + (size or 0)
    seen, matches = set(), []
    for cur, data in _iter_chunks(base, end, 0x200000, max(0, plen - 1)):
        for m in rx.finditer(data):
            a = cur + m.start()
            if a not in seen:
                seen.add(a)
                matches.append(f"0x{a:X}")
                if len(matches) >= limit:
                    return {"success": True, "pattern": pattern, "matches": matches,
                            "count": len(matches), "truncated": True}
    return {"success": True, "pattern": pattern, "matches": matches,
            "count": len(matches), "truncated": False}

# ============================================================================
# B. Function boundaries via the in-memory PE .pdata exception directory
# ============================================================================
_pdata_cache = {}  # base -> (sorted starts[], (abs_begin,abs_end)[])

def parse_pdata(base=None):
    """Parse a module's RUNTIME_FUNCTION table -> sorted (start_rva[], (begin,end) abs[]). Cached."""
    if base is None:
        base, _ = _main_module()
    if not base:
        return None
    if base in _pdata_cache:
        return _pdata_cache[base]
    e_lfanew = _u32(base + 0x3C)
    if e_lfanew is None:
        return None
    opt = base + e_lfanew + 0x18                      # IMAGE_OPTIONAL_HEADER64
    exc_dir = opt + 0x70 + 3 * 8                       # DataDirectory[IMAGE_DIRECTORY_ENTRY_EXCEPTION]
    dir_rva, dir_size = _u32(exc_dir), _u32(exc_dir + 4)
    if not dir_rva or not dir_size:
        # GTA5.exe anti-tamper zeroes DataDirectory[EXCEPTION] AND corrupts the PE-header navigation
        # (e_lfanew/COFF), so standard section-table walking also fails. But the IMAGE_SECTION_HEADERs
        # survive intact near the base - scan the header region for the ".pdata" name and read its
        # VirtualAddress/VirtualSize straight from that section header.
        hdr = _read_bytes(base, 0x1000)
        pos = hdr.find(b".pdata\x00\x00") if hdr else -1
        if pos >= 0:
            dir_size = struct.unpack_from("<I", hdr, pos + 8)[0]    # VirtualSize
            dir_rva = struct.unpack_from("<I", hdr, pos + 12)[0]    # VirtualAddress
        if not dir_rva or not dir_size:
            return None
    # The .pdata table is ~1 MB on GTA5 - over the single-read cap and it can span regions, so read it
    # in chunks (same reason re_scan chunks the module). A single read here silently returns None.
    blob = bytearray()
    _off = 0
    while _off < dir_size:
        _part = _read_bytes(base + dir_rva + _off, min(0x40000, dir_size - _off))
        if not _part:
            break
        blob += _part
        _off += len(_part)
    if not blob:
        return None
    blob = bytes(blob)
    starts, funcs = [], []
    for off in range(0, (len(blob) // 12) * 12, 12):
        begin, end, unw = struct.unpack_from("<III", blob, off)
        if unw & 1:           # chained fragment, not a primary function
            continue
        starts.append(begin)
        funcs.append((base + begin, base + end))
    # already sorted by BeginAddress in the table, but be safe
    order = sorted(range(len(starts)), key=lambda i: starts[i])
    starts = [starts[i] for i in order]
    funcs = [funcs[i] for i in order]
    _pdata_cache[base] = (starts, funcs)
    return _pdata_cache[base]

def func_bounds(addr, base=None):
    """Return (start, end) of the function containing `addr`, or None."""
    import bisect
    if base is None:
        base, _ = _module_base_for(addr)
    tab = parse_pdata(base)
    if not tab:
        return None
    starts, funcs = tab
    i = bisect.bisect_right(starts, addr - base) - 1
    if i < 0:
        return None
    s, e = funcs[i]
    return (s, e) if s <= addr < e else None

# ============================================================================
# C. MSVC RTTI -> C++ class name  (x64: 4-byte image-base-relative RVAs)
# ============================================================================
def _demangle(mangled):
    # ".?AVCVehicle@@" -> "CVehicle" ;  ".?AVFoo@Bar@@" -> "Bar::Foo"
    if not mangled or not mangled.startswith(".?A"):
        return mangled
    core = mangled[4:].split("@@", 1)[0]
    parts = [p for p in core.split("@") if p]
    return "::".join(reversed(parts)) if len(parts) > 1 else core

def rtti_class_name(vtable, image_base=None):
    """vtable pointer -> demangled class name via MSVC RTTI, or None."""
    if image_base is None:
        image_base, _ = _main_module()
    col = _u64(vtable - 8)
    if not col:
        return None
    sig = _u32(col + 0x00)
    pself = _u32(col + 0x14)
    if sig != 1 or pself is None:        # x64 COL signature must be 1
        return None
    base = col - pself                   # recover image base from pSelf
    if image_base is not None and base != image_base:
        return None                      # validity gate (rejects bad pointers)
    ptd = _u32(col + 0x0C)
    if ptd is None:
        return None
    name = _cstr(base + ptd + 0x10, 256)  # TypeDescriptor.name (mangled)
    return _demangle(name) if name else None

def identify(address, image_base=None):
    """Object pointer -> {class, vtable, function bounds}. The 'what am I looking at' for objects."""
    addr = _resolve(address)
    if not _is_valid(addr, 8):
        return {"error": f"0x{addr:X} not readable"}
    vtable = _u64(addr)
    out = {"address": f"0x{addr:X}", "vtable": f"0x{vtable:X}" if vtable else None}
    if vtable and _is_exec(vtable):
        cls = rtti_class_name(vtable, image_base)
        out["class"] = cls
    else:
        out["class"] = None
        out["note"] = "first qword is not an executable vtable pointer (maybe not a polymorphic object)"
    return out

def dump_vtable(vtable, max_slots=128):
    """List virtual method addresses until a slot leaves executable memory."""
    methods = []
    a = _resolve(vtable)
    for i in range(max_slots):
        p = _u64(a)
        if not p or not _is_exec(p):
            break
        methods.append({"slot": i, "addr": f"0x{p:X}", "func": _fmt_bounds(func_bounds(p))})
        a += 8
    return {"vtable": f"0x{_resolve(vtable):X}", "count": len(methods), "methods": methods}

def _fmt_bounds(b):
    return f"0x{b[0]:X}-0x{b[1]:X}" if b else None

# ============================================================================
# D. Pointer tools: read_chain + find_pointers_to
# ============================================================================
def _plausible_ptr(p):
    return p is not None and 0x10000 <= p < 0x7FFFFFFFFFFF and (p & 0x7) == 0

def read_chain(base, offsets):
    """Resolve module+off1 -> [+off1] +off2 -> ... -> final ADDRESS. Validates every hop."""
    addr = _resolve(base)
    trail = [f"0x{addr:X}"]
    for off in offsets[:-1]:
        nxt = _u64(addr + off)
        if not _plausible_ptr(nxt):
            return {"error": f"bad hop at 0x{addr:X}+0x{off:X} -> {nxt}", "trail": trail}
        addr = nxt
        trail.append(f"0x{addr:X}")
    final = addr + offsets[-1]
    return {"success": True, "address": f"0x{final:X}", "trail": trail, "offsets": offsets}

def find_pointers_to(target, start=None, size=None, limit=64):
    """Find 8-byte-aligned slots whose qword == target. Scan one region (start,size) or the module."""
    target = _resolve(target)
    if start is None:
        start, size = _main_module()
    end = start + (size or 0)
    hits = []
    for cur, data in _iter_chunks(start, end, 0x200000, 7):
        qcount = (len(data) // 8)              # aligned qwords only
        arr = struct.unpack_from("<%dQ" % qcount, data, 0) if qcount else ()
        for i, v in enumerate(arr):
            if v == target:
                hits.append(f"0x{cur + i*8:X}")
                if len(hits) >= limit:
                    return {"success": True, "target": f"0x{target:X}", "pointers": hits, "truncated": True}
    return {"success": True, "target": f"0x{target:X}", "pointers": hits, "truncated": False}

# ============================================================================
# E. Disassembler (capstone; import-guarded -> degrades cleanly if absent)
# ============================================================================
try:
    import capstone as _cs
    _HAVE_CAPSTONE = True
except Exception:
    _cs = None
    _HAVE_CAPSTONE = False

def disasm(address, count=32, nbytes=512):
    """Disassemble up to `count` instructions at `address`. Extracts [reg+disp] offsets + call targets."""
    if not _HAVE_CAPSTONE:
        return {"error": "capstone not installed (pip install capstone). disasm unavailable; "
                         "find_xrefs/func_bounds still work without it."}
    addr = _resolve(address)
    code = _read_bytes(addr, nbytes)
    if not code:
        return {"error": f"0x{addr:X} not readable"}
    md = _cs.Cs(_cs.CS_ARCH_X86, _cs.CS_MODE_64)
    md.detail = True
    out = []
    for ins in md.disasm(code, addr):
        rec = {"addr": f"0x{ins.address:X}", "len": ins.size,
               "text": f"{ins.mnemonic} {ins.op_str}".strip()}
        for op in ins.operands:
            if op.type == _cs.x86.X86_OP_MEM and op.mem.disp:
                base_reg = ins.reg_name(op.mem.base) if op.mem.base else None
                if base_reg and base_reg != "rip":
                    rec["mem_offset"] = {"base": base_reg, "disp": f"0x{op.mem.disp & 0xFFFFFFFF:X}"}
            if op.type == _cs.x86.X86_OP_IMM and ins.group(_cs.CS_GRP_CALL):
                rec["call_target"] = f"0x{op.imm:X}"
        out.append(rec)
        if len(out) >= count:
            break
    return {"success": True, "address": f"0x{addr:X}", "count": len(out), "instructions": out,
            "capstone": True}

# ============================================================================
# F. Auto-signature generation (reverse of scan: address -> unique patch-stable AOB).
#    Wildcards operand bytes that relocate (call/jmp rel32, RIP-rel disp32, imm64 address); keeps
#    opcodes literal; grows instruction-by-instruction until exactly one module match. Needs capstone.
#    (SigMaker approach: A200K / ajkhoury / kweatherman.)
# ============================================================================
def _wildcard_span(ins):
    """(offset_in_instr, length) of bytes to wildcard, or None to keep the instruction literal."""
    enc = getattr(ins, "encoding", None)
    if enc is None:
        return None
    if (ins.group(_cs.CS_GRP_CALL) or ins.group(_cs.CS_GRP_JUMP)) \
       and getattr(enc, "imm_size", 0) == 4 and getattr(enc, "imm_offset", 0):
        return enc.imm_offset, 4                          # call/jmp rel32 -> relocates
    for op in ins.operands:                               # RIP-relative / disp32 mem operand
        if op.type == _cs.x86.X86_OP_MEM and op.mem.disp and \
           getattr(enc, "disp_size", 0) == 4 and getattr(enc, "disp_offset", 0):
            return enc.disp_offset, 4
    for op in ins.operands:                               # mov reg, imm64 absolute address
        if op.type == _cs.x86.X86_OP_IMM and getattr(enc, "imm_size", 0) == 8 and getattr(enc, "imm_offset", 0):
            return enc.imm_offset, 8
    return None

def _sig_count_upto_two(sig):
    """Module match count for a sig (list of byte|None), early-exit at 2 (the hot path)."""
    parts = [re.escape(bytes([b])) if b is not None else b"." for b in sig]
    rx = re.compile(b"".join(parts), re.DOTALL)
    base, size = _main_module()
    if not base:
        return 2
    cnt = 0
    for cur, data in _iter_chunks(base, base + size, 0x200000, max(0, len(sig) - 1)):
        for _ in rx.finditer(data):
            cnt += 1
            if cnt >= 2:
                return 2
    return cnt

def _fmt_ida(sig):
    return " ".join(("%02X" % b) if b is not None else "?" for b in sig)

def make_signature(address, max_len=64):
    """address -> minimal unique, patch-stable AOB (IDA-style 'E8 ? ? ? ? 48 8B'). Needs capstone."""
    if not _HAVE_CAPSTONE:
        return {"error": "capstone not installed (pip install capstone) - make_signature needs it"}
    addr = _resolve(address)
    code = _read_bytes(addr, max_len + 16)
    if not code:
        return {"error": f"0x{addr:X} not readable"}
    md = _cs.Cs(_cs.CS_ARCH_X86, _cs.CS_MODE_64)
    md.detail = True
    sig = []
    for ins in md.disasm(code, addr):
        ib = code[ins.address - addr: ins.address - addr + ins.size]
        span = _wildcard_span(ins)
        if span is None:
            sig += list(ib)                               # whole instruction literal (best anchors)
        else:
            off, ln = span
            sig += list(ib[:off]) + [None] * ln + list(ib[off + ln:])   # opcode literal, operand wildcard
        if _sig_count_upto_two(sig) == 1:
            while sig and sig[-1] is None:                # trim trailing wildcards
                sig.pop()
            return {"success": True, "address": f"0x{addr:X}", "signature": _fmt_ida(sig),
                    "length": len(sig)}
        if len(sig) > max_len:
            break
    return {"error": "no unique signature within max_len (poor anchor / left function) - try another address"}

# ============================================================================
# Bridge handlers + registration
# ============================================================================
def handle_re_scan(p):        return re_scan(p.get("pattern", ""), int(p.get("limit", 16)))
def handle_func_bounds(p):
    b = func_bounds(_resolve(p.get("address")))
    return {"success": True, "function": _fmt_bounds(b)} if b else {"error": "no function at address"}
def handle_list_functions(p):
    tab = parse_pdata()
    if not tab:
        return {"error": "no .pdata table"}
    starts, funcs = tab
    off = int(p.get("offset", 0)); lim = int(p.get("limit", 50))
    rows = [{"start": f"0x{s:X}", "end": f"0x{e:X}", "size": e - s}
            for s, e in funcs[off:off + lim]]
    return {"success": True, "total": len(funcs), "functions": rows}
def handle_identify(p):       return identify(p.get("address"))
def handle_dump_vtable(p):    return dump_vtable(p.get("vtable"))
def handle_read_chain(p):
    offs = [_resolve(o) for o in (p.get("offsets") or [])]
    return read_chain(p.get("base"), offs)
def handle_find_pointers(p):
    return find_pointers_to(p.get("target"),
                            _resolve(p["start"]) if p.get("start") else None,
                            int(p["size"]) if p.get("size") else None,
                            int(p.get("limit", 64)))
def handle_disasm(p):         return disasm(p.get("address"), int(p.get("count", 32)))
def handle_make_signature(p): return make_signature(p.get("address"), int(p.get("max_len", 64)))

RE_COMMANDS = {
    "re_scan": handle_re_scan,            # regex AOB scan (faster scan_pattern)
    "func_bounds": handle_func_bounds,
    "list_functions": handle_list_functions,
    "identify": handle_identify,
    "dump_vtable": handle_dump_vtable,
    "read_chain": handle_read_chain,
    "find_pointers": handle_find_pointers,
    "disasm": handle_disasm,
    "make_signature": handle_make_signature,
}
# all read-only -> safe to run off the game thread (paused-safe)
RE_OFFTHREAD = set(RE_COMMANDS.keys())
