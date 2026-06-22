"""Replay this session's bridge.py edits onto the verified 0eb6599 baseline. Each edit must match
exactly once (assert) so a transcription mismatch fails loudly instead of corrupting the file."""
import io, sys

BRIDGE = r"C:\Users\mwalton\Dropbox\Personal-Files\GTA 5 Mods\Projects\GTAV-Claude-MCP\pyscript\bridge.py"

EDITS = []
def E(label, old, new): EDITS.append((label, old, new))

# ---- A: off-thread thread-local + ctypes-when-paused reads -------------------------------------
E("A read_float/read_int offthread",
'''def read_float(addr: int) -> Optional[float]:
    """Read a float from memory."""
    if PYLOADER_AVAILABLE and gta:
        try:
            return gta.read_float(addr)
        except OSError:
            return None
    elif is_valid_address(addr, 4):
        try:
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_float)).contents.value
        except:
            return None
    return None

def read_int(addr: int) -> Optional[int]:
    """Read a 32-bit int from memory."""
    if PYLOADER_AVAILABLE and gta:
        try:
            return gta.read_int(addr)
        except OSError:
            return None
    elif is_valid_address(addr, 4):
        try:
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_int32)).contents.value
        except:
            return None
    return None''',
'''# --- Off-game-thread execution (paused-safe) ----------------------------------
# When GTA is paused, PyLoaderV's tick stops, so the work queue (drained in on_tick)
# never runs -> any command marshalled to the game thread stalls until unpause. Pure
# memory/CPU commands (reads, scans, findings) DON'T need the game thread, so we run
# them directly on the socket thread. They must use ctypes (not gta.*, which assumes
# game-thread context), which is why the read helpers below honor _is_offthread().
_offthread = threading.local()
def _is_offthread() -> bool:
    return getattr(_offthread, "active", False)

def read_float(addr: int) -> Optional[float]:
    """Read a float from memory. Off the game thread (paused-safe path), use ctypes, not gta.*."""
    if PYLOADER_AVAILABLE and gta and not _is_offthread():
        try:
            return gta.read_float(addr)
        except OSError:
            return None
    if is_valid_address(addr, 4):
        try:
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_float)).contents.value
        except:
            return None
    return None

def read_int(addr: int) -> Optional[int]:
    """Read a 32-bit int from memory. Off the game thread (paused-safe path), use ctypes, not gta.*."""
    if PYLOADER_AVAILABLE and gta and not _is_offthread():
        try:
            return gta.read_int(addr)
        except OSError:
            return None
    if is_valid_address(addr, 4):
        try:
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_int32)).contents.value
        except:
            return None
    return None''')

# ---- B: get_vehicle_address off-thread guard --------------------------------------------------
E("B get_vehicle_address guard",
'''def get_vehicle_address(handle: int) -> Optional[int]:
    """Get the memory address of a vehicle from its handle."""
    if not PYLOADER_AVAILABLE or not gta:
        return None
    try:
        addr = gta.entity_address(handle)''',
'''def get_vehicle_address(handle: int) -> Optional[int]:
    """Get the memory address of a vehicle from its handle.
    Returns None off the game thread (gta.entity_address needs game-thread context) - so during a
    pause, inspect-by-handle degrades to 'give me an address' while inspect-by-address still works."""
    if not PYLOADER_AVAILABLE or not gta or _is_offthread():
        return None
    try:
        addr = gta.entity_address(handle)''')

# ---- C: region-aware chunk helper + scan_pattern docstring ------------------------------------
E("C _next_readable_chunk",
'''def handle_scan_pattern(params: dict) -> dict:
    """Find an AOB pattern in GTA5.exe. Returns up to `limit` match addresses.
    Chunked across frames (Deferred) to avoid freezing the game."""''',
'''def _next_readable_chunk(pos: int, end: int, budget: int, overlap: int):
    """
    Walk committed+readable memory regions from `pos` up to `end`, returning ONE chunk:
      (chunk_start, data_bytes, next_pos)
    Skips unmapped/guard/no-read regions (a module is many regions with different protections,
    so a fixed-size read across a boundary fails - this clamps each read to within one region).
    `overlap` bytes past the budget are included so a pattern spanning a budget boundary is caught;
    next_pos backs up by `overlap` (matches are de-duped by caller). Returns (None, None, end) when
    nothing readable remains.
    """
    mbi = MEMORY_BASIC_INFORMATION()
    cur = pos
    while cur < end:
        if kernel32.VirtualQuery(ctypes.c_void_p(cur), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            return None, None, end
        region_base = mbi.BaseAddress or cur
        region_end = region_base + (mbi.RegionSize or 0x1000)
        readable = (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD)
                    and (mbi.Protect & READABLE_PROTECTIONS))
        if not readable:
            cur = region_end
            continue
        r_end = min(region_end, end)
        read_len = min(budget + overlap, r_end - cur)
        data = read_bytes(cur, read_len)
        if not data:
            cur = region_end
            continue
        consumed = max(1, read_len - overlap)
        nxt = cur + consumed
        if nxt >= r_end:
            nxt = region_end  # whole region scanned -> jump to the next one
        return cur, data, nxt
    return None, None, end

def handle_scan_pattern(params: dict) -> dict:
    """Find an AOB pattern in GTA5.exe. Returns up to `limit` match addresses.
    Region-aware + chunked across frames (Deferred) to avoid freezing the game."""''')

# ---- D: scan_pattern poll -> region-aware -----------------------------------------------------
E("D scan_pattern poll",
'''    plen = len(pat)
    CHUNK = 0x200000  # 2MB scanned per frame
    state = {"pos": 0}
    matches = []

    def poll():
        scanned_end = min(state["pos"] + CHUNK, size - plen)
        # Read this chunk (+overlap for patterns spanning the boundary)
        read_len = min(CHUNK + plen, size - state["pos"])
        data = read_bytes(base + state["pos"], read_len)
        if data:
            for i in range(0, len(data) - plen + 1):
                ok = True
                for j in range(plen):
                    if mask[j] and data[i + j] != pat[j]:
                        ok = False
                        break
                if ok:
                    matches.append(f"0x{base + state['pos'] + i:X}")
                    if len(matches) >= limit:
                        return {"success": True, "pattern": pattern, "matches": matches,
                                "count": len(matches), "truncated": True}
        state["pos"] += CHUNK
        if state["pos"] >= size - plen:
            return {"success": True, "pattern": pattern, "matches": matches,
                    "count": len(matches), "truncated": False}
        return None

    return Deferred(poll, timeout=60.0)''',
'''    plen = len(pat)
    end = base + size
    BUDGET = 0x200000  # bytes scanned per frame
    state = {"pos": base, "seen": set()}
    matches = []

    def poll():
        cur, data, nxt = _next_readable_chunk(state["pos"], end, BUDGET, plen - 1)
        if data:
            n = len(data)
            for i in range(0, n - plen + 1):
                ok = True
                for j in range(plen):
                    if mask[j] and data[i + j] != pat[j]:
                        ok = False
                        break
                if ok:
                    a = cur + i
                    if a not in state["seen"]:  # de-dup across overlapping chunk boundaries
                        state["seen"].add(a)
                        matches.append(f"0x{a:X}")
                        if len(matches) >= limit:
                            return {"success": True, "pattern": pattern, "matches": matches,
                                    "count": len(matches), "truncated": True}
        state["pos"] = nxt
        if state["pos"] >= end:
            return {"success": True, "pattern": pattern, "matches": matches,
                    "count": len(matches), "truncated": False}
        return None

    return Deferred(poll, timeout=60.0)''')

# ---- F: register the 4 new commands -----------------------------------------------------------
E("F register engine-call commands",
'''    # Pattern scanning (offset discovery)
    "scan_pattern": handle_scan_pattern,
    "resolve_rip_relative": handle_resolve_rip_relative,''',
'''    # Pattern scanning (offset discovery)
    "scan_pattern": handle_scan_pattern,
    "resolve_rip_relative": handle_resolve_rip_relative,
    # Engine-function calling + runtime DLC (rpf) mount/reload
    "call_function": handle_call_function,
    "find_string": handle_find_string,
    "find_xrefs": handle_find_xrefs,
    "reload_content_changeset": handle_reload_content_changeset,''')

# ---- E: engine-function calling block (call_function / find_string / find_xrefs / changeset) ----
E("E engine-function block",
'''        return {"error": f"reload_scripts failed: {e}"}


# Command dispatch table
COMMANDS = {''',
'''        return {"error": f"reload_scripts failed: {e}"}


# =============================================================================
# Engine-function calling + runtime DLC (rpf) mount/reload
# call_function = call an arbitrary engine function (NOT a script native) with a typed signature.
# Runs on the game thread (handlers dispatch via enqueue_work); validates the target is executable.
# See RPF-HOTRELOAD-RUNBOOK.md. Audio does NOT hot-reload (engine ingests it once at boot).
# =============================================================================

EXECUTABLE_PROTECTIONS = 0x10 | 0x20 | 0x40 | 0x80  # EXECUTE / _READ / _READWRITE / _WRITECOPY

def is_executable_address(address: int) -> bool:
    """True if `address` is committed, non-guard, executable code (a callable function)."""
    if not address:
        return False
    mbi = MEMORY_BASIC_INFORMATION()
    if kernel32.VirtualQuery(ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
        return False
    if mbi.State != MEM_COMMIT or (mbi.Protect & PAGE_GUARD):
        return False
    return bool(mbi.Protect & EXECUTABLE_PROTECTIONS)

_CALL_CTYPES = {
    "void": None, "int": ctypes.c_int32, "int32": ctypes.c_int32,
    "uint": ctypes.c_uint32, "uint32": ctypes.c_uint32,
    "int64": ctypes.c_int64, "long": ctypes.c_int64,
    "uint64": ctypes.c_uint64, "ptr": ctypes.c_uint64, "pointer": ctypes.c_uint64,
    "handle": ctypes.c_uint64, "address": ctypes.c_uint64,
    "float": ctypes.c_float, "double": ctypes.c_double,
    "bool": ctypes.c_bool, "byte": ctypes.c_uint8,
    "string": ctypes.c_char_p, "char*": ctypes.c_char_p, "cstr": ctypes.c_char_p,
}

def _resolve_addr(v):
    """Accept a hex string ('0x...'), decimal string, or int -> int."""
    if isinstance(v, str):
        return int(v, 16) if v.lower().startswith("0x") else int(v)
    return int(v)

def handle_call_function(params: dict) -> dict:
    """Call an arbitrary ENGINE function at `address` with a typed signature (DANGER: wrong ABI
    crashes the game; WAL-logged; confined to GTA5.exe + verified executable). See the runbook."""
    addr_in = params.get("address")
    if addr_in is None:
        return {"error": "address required"}
    try:
        addr = _resolve_addr(addr_in)
    except (TypeError, ValueError):
        return {"error": f"bad address: {addr_in}"}
    arg_types = params.get("arg_types", []) or []
    args = params.get("args", []) or []
    return_type = (params.get("return_type") or "int64").lower()
    if len(arg_types) != len(args):
        return {"error": f"arg_types ({len(arg_types)}) and args ({len(args)}) length mismatch"}
    base, size = _get_main_module()
    if base and not params.get("allow_outside_module"):
        if not (base <= addr < base + size):
            return {"error": f"0x{addr:X} is outside GTA5.exe [0x{base:X},0x{base+size:X}); "
                             f"pass allow_outside_module=true to override (rarely correct)."}
    if not is_executable_address(addr):
        return {"error": f"0x{addr:X} is not executable code - refusing to call."}
    try:
        restype = _CALL_CTYPES[return_type]
    except KeyError:
        return {"error": f"unknown return_type '{return_type}'"}
    argctypes = []
    for t in arg_types:
        ct = _CALL_CTYPES.get(str(t).lower())
        if str(t).lower() not in _CALL_CTYPES:
            return {"error": f"unknown arg type '{t}'"}
        if ct is None:
            return {"error": "'void' is not a valid argument type"}
        argctypes.append(ct)
    keepalive = []
    cvals = []
    try:
        for ct, v in zip(argctypes, args):
            if ct is ctypes.c_char_p:
                b = v.encode("utf-8") if isinstance(v, str) else bytes(v)
                buf = ctypes.create_string_buffer(b + b"\\x00")
                keepalive.append(buf)
                cvals.append(ctypes.cast(buf, ctypes.c_char_p))
            elif ct in (ctypes.c_uint64, ctypes.c_int64, ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint8):
                cvals.append(ct(_resolve_addr(v) if isinstance(v, str) else int(v)))
            elif ct in (ctypes.c_float, ctypes.c_double):
                cvals.append(ct(float(v)))
            elif ct is ctypes.c_bool:
                cvals.append(ct(bool(v)))
            else:
                cvals.append(ct(v))
    except (TypeError, ValueError) as e:
        return {"error": f"could not marshal args: {e}"}
    wal_write({"op": "call_function", "address": f"0x{addr:X}", "arg_types": arg_types,
               "args": [str(a) for a in args], "return_type": return_type})
    try:
        proto = ctypes.CFUNCTYPE(restype, *argctypes)
        raw = proto(addr)(*cvals)
    except Exception as e:
        log_message("error", f"call_function @0x{addr:X} failed: {e}")
        return {"error": f"call_function failed: {e}"}
    if return_type == "void":
        out = None
    elif return_type == "string":
        out = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    elif return_type in ("ptr", "pointer", "handle", "address"):
        out = f"0x{int(raw or 0) & 0xFFFFFFFFFFFFFFFF:X}"
    else:
        out = raw
    return {"success": True, "address": f"0x{addr:X}", "result": out, "return_type": return_type}


def handle_find_string(params: dict) -> dict:
    """Find where a literal string lives in GTA5.exe (the discovery anchor for engine functions)."""
    text = params.get("text", "")
    if not text:
        return {"error": "text required"}
    enc = (params.get("encoding") or "ascii").lower()
    try:
        raw = text.encode("utf-16-le") if enc in ("utf16", "utf-16", "wide") else text.encode("ascii")
    except UnicodeEncodeError as e:
        return {"error": f"cannot encode text as {enc}: {e}"}
    pattern = " ".join(f"{b:02X}" for b in raw)
    return handle_scan_pattern({"pattern": pattern, "limit": int(params.get("limit", 16))})


def handle_find_xrefs(params: dict) -> dict:
    """Find code references to `address`: RIP-relative LEA + rel32 CALL/JMP. Walk string->func->callers."""
    try:
        target = _resolve_addr(params.get("address"))
    except (TypeError, ValueError):
        return {"error": f"bad address: {params.get('address')}"}
    limit = int(params.get("limit", 24))
    base, size = _get_main_module()
    if not base:
        return {"error": "Could not locate GTA5.exe module"}
    end = base + size
    BUDGET = 0x200000
    state = {"pos": base, "seen": set()}
    hits = []

    def poll():
        cur, data, nxt = _next_readable_chunk(state["pos"], end, BUDGET, 7)
        if data:
            n = len(data)
            for i in range(0, n - 7):
                b0 = data[i]
                instr = cur + i
                if instr in state["seen"]:
                    continue
                if (b0 == 0x48 or b0 == 0x4C) and data[i + 1] == 0x8D and (data[i + 2] & 0xC7) == 0x05:
                    disp = int.from_bytes(data[i + 3:i + 7], "little", signed=True)
                    if instr + 7 + disp == target:
                        state["seen"].add(instr)
                        hits.append({"addr": f"0x{instr:X}", "kind": "lea"})
                elif b0 == 0xE8 or b0 == 0xE9:
                    disp = int.from_bytes(data[i + 1:i + 5], "little", signed=True)
                    if instr + 5 + disp == target:
                        state["seen"].add(instr)
                        hits.append({"addr": f"0x{instr:X}", "kind": "call" if b0 == 0xE8 else "jmp"})
                if len(hits) >= limit:
                    return {"success": True, "target": f"0x{target:X}", "xrefs": hits, "truncated": True}
        state["pos"] = nxt
        if state["pos"] >= end:
            return {"success": True, "target": f"0x{target:X}", "xrefs": hits, "truncated": False}
        return None

    return Deferred(poll, timeout=180.0)


_HASH_REVERT_CHANGESET = "0x3C1978285B036B25"   # REVERT_CONTENT_CHANGESET_GROUP_FOR_ALL
_HASH_EXECUTE_CHANGESET = "0x6BEDF5769AC2DC07"  # EXECUTE_CONTENT_CHANGESET_GROUP_FOR_ALL

def handle_reload_content_changeset(params: dict) -> dict:
    """Re-apply a DLC content changeset group (REVERT then EXECUTE) via verified natives."""
    if not (PYLOADER_AVAILABLE and gta):
        return {"error": "PyLoaderV not available"}
    group = params.get("group")
    if not group:
        return {"error": "group required (e.g. 'GROUP_STARTUP' or '<PACK>_AUTOGEN')"}
    ghash = _joaat(group)
    steps = []
    for label, h in (("revert", _HASH_REVERT_CHANGESET), ("execute", _HASH_EXECUTE_CHANGESET)):
        r = handle_call_native({"hash": h, "args": [ghash], "return_type": "void"})
        steps.append({label: r})
        if "error" in r:
            return {"error": f"{label} changeset failed: {r['error']}", "group": group,
                    "group_hash": f"0x{ghash:X}", "steps": steps}
    return {"success": True, "group": group, "group_hash": f"0x{ghash:X}", "steps": steps,
            "note": "Changeset re-applied. If edited bytes didn't take effect, the pack's device "
                    "needs a remount (call_function path) - see RPF-HOTRELOAD-RUNBOOK.md."}


# Command dispatch table
COMMANDS = {''')

# ---- G: off-thread dispatch machinery + RE-toolkit wiring (before handle_client) ---------------
E("G offthread + re_tools wiring",
'''def handle_client(conn: socket.socket, addr):
    """Handle a single client connection (one request/response)."""''',
'''# Commands that touch ONLY memory/CPU (no GTA natives, no engine calls) - safe to run directly
# on the socket thread so they KEEP WORKING while the game (and its tick) is paused.
_OFFTHREAD_COMMANDS = {
    "read", "inspect", "snapshot", "diff", "list_snapshots", "get_undo_stack", "list_continuous",
    "write", "revert_last",
    "scan_pattern", "find_string", "find_xrefs", "resolve_rip_relative",
    "set_goal", "note_finding", "get_findings", "clear_findings",
    "get_crash_logs", "get_chat_history", "has_pending_messages", "get_pending_messages",
}
_offthread_lock = threading.Lock()

def _run_offthread(handler, params):
    """Run a pure-memory handler on the socket thread (works while paused). Drains a Deferred
    synchronously here - blocking is fine off the game thread."""
    with _offthread_lock:
        _offthread.active = True
        try:
            res = handler(params)
            if isinstance(res, Deferred):
                while True:
                    out = res.poll()
                    if out is not None:
                        return out
                    if time.time() > res.deadline:
                        return {"error": "off-thread deferred timed out"}
                    time.sleep(0.001)
            return res
        finally:
            _offthread.active = False

# ---- RE toolkit add-ons (Tier A static analysis + par-dump labels + dynamic Tier B) -----------
try:
    import re_tools
    re_tools.bind(globals())
    COMMANDS.update(re_tools.RE_COMMANDS)
    _OFFTHREAD_COMMANDS |= re_tools.RE_OFFTHREAD          # all read-only -> paused-safe
    log_message("info", "re_tools (Tier A) loaded")
except Exception as e:
    log_message("error", f"re_tools load failed: {e}")
try:
    import re_tools_pardump as _pardump
    COMMANDS.update(_pardump.PAR_COMMANDS)
    log_message("info", "re_tools_pardump loaded")
except Exception as e:
    log_message("error", f"re_tools_pardump load failed: {e}")
try:
    import re_tools_dynamic as _rtd
    _rtd.bind(globals())
    COMMANDS.update(_rtd.RE_DYN_COMMANDS)                 # NOT off-thread (need the live tick)
    log_message("info", "re_tools_dynamic (Tier B) loaded")
except Exception as e:
    log_message("error", f"re_tools_dynamic load failed: {e}")

def handle_client(conn: socket.socket, addr):
    """Handle a single client connection (one request/response)."""''')

# ---- H: handle_client dispatch -> off-thread branch -------------------------------------------
E("H handle_client dispatch",
'''                    if command in COMMANDS:
                        work = enqueue_work(COMMANDS[command], params)
                        result, error = work.wait(timeout=_request_wait_timeout(command, params))
                        if error:''',
'''                    if command in COMMANDS:
                        if command in _OFFTHREAD_COMMANDS:
                            # Paused-safe: run on this thread, no dependence on the game tick.
                            try:
                                result, error = _run_offthread(COMMANDS[command], params), None
                            except Exception as e:
                                result, error = None, e
                        else:
                            work = enqueue_work(COMMANDS[command], params)
                            result, error = work.wait(timeout=_request_wait_timeout(command, params))
                        if error:''')

src = io.open(BRIDGE, encoding="utf-8").read()
for label, old, new in EDITS:
    c = src.count(old)
    assert c == 1, f"EDIT {label}: expected 1 match, found {c}"
    src = src.replace(old, new)
io.open(BRIDGE, "w", encoding="utf-8", newline="").write(src)
print(f"applied {len(EDITS)} edits OK")
