# RE Toolkit — integration guide (merge into the home `bridge.py`)

New add-on modules that bolt the deep-RE capabilities onto the existing bridge. **Nothing in the
current `bridge.py`/`server.py` is modified** — these are additive files + a few lines of wiring.
Implementation rationale + sources: `RE-TOOLKIT.md`.

| File | Tier | Status |
|---|---|---|
| `pyscript/re_tools.py` | A — static analysis (scan/funcs/RTTI/pointers/disasm) | **TESTED off-game, 13/13** |
| `pyscript/test_re_tools.py` | A — self-test | run `python test_re_tools.py` to re-validate on any machine |
| `pyscript/re_tools_dynamic.py` | B — globals/pools/nearby | **UNTESTED** — logic per Menyoo/SHV; verify AOBs in-game |
| `watch_access` / `trace_func` | B — HW-breakpoint "what accesses X" | **not shipped** — build in-game (RE-TOOLKIT.md §3.1); a wrong offset crashes GTA |

---

## 1. Wire into `bridge.py`
Add **after** the `COMMANDS` dict and `_OFFTHREAD_COMMANDS` are defined (near the bottom, before the
socket server):
```python
# ---- RE toolkit add-ons --------------------------------------------------
try:
    import re_tools
    re_tools.bind(globals())                      # share bridge's ctypes primitives
    COMMANDS.update(re_tools.RE_COMMANDS)
    _OFFTHREAD_COMMANDS |= re_tools.RE_OFFTHREAD   # all read-only -> paused-safe
    log_message("info", "re_tools (Tier A) loaded")
except Exception as e:
    log_message("error", f"re_tools load failed: {e}")

try:
    import re_tools_dynamic as rtd
    rtd.bind(globals())
    COMMANDS.update(rtd.RE_DYN_COMMANDS)           # NOT off-thread (need the live tick)
    log_message("info", "re_tools_dynamic (Tier B) loaded")
except Exception as e:
    log_message("error", f"re_tools_dynamic load failed: {e}")
```
`bind(globals())` lets the modules reuse `read_bytes`, `is_valid_address`, `is_executable_address`,
`_get_main_module`, `read_ptr`, `_joaat`, `handle_call_function`, `handle_call_native_by_name`, `gta`,
`PYLOADER_AVAILABLE`, `log_message`, `note_finding`, `_FINDINGS`. No duplication.

**Optional speedup:** `re_tools.re_scan` is the regex-accelerated AOB scanner (C-speed vs the byte loop).
You can route the existing `scan_pattern` MCP tool to it, or just use `re_scan` directly. The masked-AOB
→ regex trick (`re.DOTALL`) is the same one worth retrofitting into the inline `handle_scan_pattern`.

**Dep for `disasm`:** `pip install capstone` into the bridge's Python (the embedded PyLoaderV one).
Everything else is pure ctypes; `disasm` degrades cleanly if capstone is absent.

## 2. Add the MCP tools (`mcp_server/server.py`)
Paste these next to the other `@mcp.tool()`s (they just forward to the bridge over the socket):
```python
@mcp.tool()
def disasm(address: str, count: int = 32) -> str:
    """Disassemble N instructions at an address (x86-64). Shows [reg+disp] struct offsets + call
    targets. Needs capstone in the bridge env. Pairs with find_xrefs/func_bounds."""
    return json.dumps(_send_command("disasm", {"address": address, "count": count}), indent=2)

@mcp.tool()
def func_bounds(address: str) -> str:
    """Function (start,end) containing an address, via the in-memory PE .pdata table. O(log n)."""
    return json.dumps(_send_command("func_bounds", {"address": address}), indent=2)

@mcp.tool()
def list_functions(offset: int = 0, limit: int = 50) -> str:
    """Enumerate the module's functions (RUNTIME_FUNCTION exception directory)."""
    return json.dumps(_send_command("list_functions", {"offset": offset, "limit": limit}), indent=2)

@mcp.tool()
def identify(address: str) -> str:
    """Object pointer -> C++ class name via MSVC RTTI (e.g. 'CVehicle','CPed'). The 'what is this
    object' tool. Pair with inspect."""
    return json.dumps(_send_command("identify", {"address": address}), indent=2)

@mcp.tool()
def dump_vtable(vtable: str) -> str:
    """List a class vtable's virtual-method addresses (cross-ref with disasm/func_bounds)."""
    return json.dumps(_send_command("dump_vtable", {"vtable": vtable}), indent=2)

@mcp.tool()
def read_chain(base: str, offsets: list) -> str:
    """Resolve a multi-level pointer path: [base] +off1 -> +off2 -> ... -> final address. Validates
    every hop (a bad deref would crash the game)."""
    return json.dumps(_send_command("read_chain", {"base": base, "offsets": offsets}), indent=2)

@mcp.tool()
def find_pointers(target: str, start: str = None, size: int = None, limit: int = 64) -> str:
    """Find 8-byte-aligned slots whose value == target (pointer-scan primitive). Optionally bound to
    a (start,size) region."""
    return json.dumps(_send_command("find_pointers", {"target": target, "start": start,
                                                       "size": size, "limit": limit}), indent=2)

@mcp.tool()
def re_scan(pattern: str, limit: int = 16) -> str:
    """Regex-accelerated AOB scan of the main module (C-speed). Pattern: hex bytes + ?? wildcards."""
    return json.dumps(_send_command("re_scan", {"pattern": pattern, "limit": limit}), indent=2)

# --- Tier B (dynamic; need the live game) ---
@mcp.tool()
def read_global(index: int, kind: str = "int") -> str:
    """Read a script-VM Global_<index> (gameplay/mission state) via ScriptHookV. kind: int|uint|float|hex."""
    return json.dumps(_send_command("read_global", {"index": index, "kind": kind}), indent=2)

@mcp.tool()
def write_global(index: int, value, kind: str = "int") -> str:
    """Write a script-VM Global_<index>. Confirm with the user first - globals drive gameplay state."""
    return json.dumps(_send_command("write_global", {"index": index, "value": value, "kind": kind}), indent=2)

@mcp.tool()
def enumerate_entities(kind: str = "ped", limit: int = 128, with_handles: bool = False) -> str:
    """Walk the ped/vehicle/object pool -> entity pointers (and optionally script handles). Lets Claude
    operate on the whole world, not just the player. UNTESTED - verify the pool AOB in-game."""
    return json.dumps(_send_command("enumerate_entities",
                      {"kind": kind, "limit": limit, "with_handles": with_handles}), indent=2)

@mcp.tool()
def nearby_peds(num: int = 16) -> str:
    """Handles of peds near the player (GET_PED_NEARBY_PEDS, size-prefixed buffer)."""
    return json.dumps(_send_command("nearby_peds", {"num": num}), indent=2)

@mcp.tool()
def nearby_vehicles(num: int = 16) -> str:
    """Handles of vehicles near the player (GET_PED_NEARBY_VEHICLES)."""
    return json.dumps(_send_command("nearby_vehicles", {"num": num}), indent=2)
```

## 3. First in-game validation pass (in priority order)
1. **`re_scan`/`func_bounds`/`list_functions`/`identify`** — pure read-only, should "just work":
   `re_scan("48 8B 05 ?? ?? ?? ??")`, `list_functions()` (expect thousands), `identify(<a vehicle ptr>)`
   should print `CVehicle`-ish. If `identify` returns null on a known entity, the RTTI walk needs a
   tweak for the build.
2. **Import the par-dumps** for your exact build (see RE-TOOLKIT.md §4.1) → `inspect` labels real field
   names. Biggest payoff; needs your `GTA5.exe` build number.
3. **`read_global`** — read a known global; confirm ScriptHookV.dll's `getGlobalPtr` resolves.
4. **`enumerate_entities("vehicle")`** — if empty, re-derive the pool AOB (the patterns in
   `re_tools_dynamic.py` are Menyoo's, version-specific). Use `re_scan` to find the current pattern.
5. **`watch_access`** (HW breakpoints) — build this WITH the game up, following RE-TOOLKIT.md §3.1
   (x64 CONTEXT `_pack_=16`, DR7 bits, VEH ring-buffer, off-thread WAL). Don't ship it blind.

## 4. Notes
- All Tier A commands are registered as **off-thread** → they work **during the pause menu** too
  (read/scan/inspect while paused), matching the paused-safe path in DISCOVERIES.md §8.
- Tier B commands call natives/engine functions → they need the live tick → they will wait for unpause.
- Cache discovered addresses with `note_finding` so repeat ops are instant and survive F9 reloads.
