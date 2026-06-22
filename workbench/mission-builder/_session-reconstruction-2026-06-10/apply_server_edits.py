"""Apply this session's server.py MCP-tool additions onto the 0eb6599 baseline (assert exact match)."""
import io
SERVER = r"C:\Users\mwalton\Dropbox\Personal-Files\GTA 5 Mods\Projects\GTAV-Claude-MCP\mcp_server\server.py"

OLD = '''    result = _send_command("resolve_rip_relative", {
        "address": address,
        "offset_position": offset_position,
        "instruction_size": instruction_size
    })
    return json.dumps(result, indent=2)

# =============================================================================
# Utility Tools
# ============================================================================='''

NEW = '''    result = _send_command("resolve_rip_relative", {
        "address": address,
        "offset_position": offset_position,
        "instruction_size": instruction_size
    })
    return json.dumps(result, indent=2)

# =============================================================================
# Engine-function calling + runtime DLC (rpf) mount/reload
# =============================================================================

@mcp.tool()
def call_function(address: str, arg_types: list = None, args: list = None,
                  return_type: str = "int64", allow_outside_module: bool = False) -> str:
    """Call an arbitrary ENGINE function (not a script native) with a typed signature - e.g.
    fiPackfile::OpenPackfile/Mount to mount an rpf, or parManager::LoadFileIntoStructure to re-parse
    an edited handling.meta. DANGER: a wrong signature crashes the game; discover the address first
    (find_string + find_xrefs), then call. arg_types: int,uint,int64,uint64/ptr,float,bool,string."""
    return json.dumps(_send_command("call_function", {
        "address": address, "arg_types": arg_types or [], "args": args or [],
        "return_type": return_type, "allow_outside_module": allow_outside_module}), indent=2)

@mcp.tool()
def find_string(text: str, encoding: str = "ascii", limit: int = 16) -> str:
    """Find where a literal string lives in GTA5.exe (discovery anchor for engine functions). Feed
    hits to find_xrefs. encoding: ascii|utf16."""
    return json.dumps(_send_command("find_string", {"text": text, "encoding": encoding, "limit": limit}), indent=2)

@mcp.tool()
def find_xrefs(address: str, limit: int = 24) -> str:
    """Find code references (RIP-relative LEA + rel32 CALL/JMP) to an address. Walk string->func->callers."""
    return json.dumps(_send_command("find_xrefs", {"address": address, "limit": limit}), indent=2)

@mcp.tool()
def reload_content_changeset(group: str) -> str:
    """Re-apply a DLC content changeset group (REVERT+EXECUTE, verified natives). group e.g.
    'GROUP_STARTUP' or a pack's '<NAME>_AUTOGEN'."""
    return json.dumps(_send_command("reload_content_changeset", {"group": group}), indent=2)

# =============================================================================
# RE toolkit (static analysis + knowledge layer + dynamic)
# =============================================================================

@mcp.tool()
def re_scan(pattern: str, limit: int = 16) -> str:
    """Regex-accelerated AOB scan of the main module (C-speed). Pattern: hex bytes + ?? wildcards."""
    return json.dumps(_send_command("re_scan", {"pattern": pattern, "limit": limit}), indent=2)

@mcp.tool()
def disasm(address: str, count: int = 32) -> str:
    """Disassemble N x86-64 instructions; shows [reg+disp] struct offsets + call targets. Needs capstone."""
    return json.dumps(_send_command("disasm", {"address": address, "count": count}), indent=2)

@mcp.tool()
def func_bounds(address: str) -> str:
    """Function (start,end) containing an address, via the in-memory PE .pdata table (O(log n))."""
    return json.dumps(_send_command("func_bounds", {"address": address}), indent=2)

@mcp.tool()
def list_functions(offset: int = 0, limit: int = 50) -> str:
    """Enumerate the module's functions (RUNTIME_FUNCTION exception directory)."""
    return json.dumps(_send_command("list_functions", {"offset": offset, "limit": limit}), indent=2)

@mcp.tool()
def identify(address: str) -> str:
    """Object pointer -> C++ class name via MSVC RTTI (e.g. CVehicle, CPed). The 'what is this object'."""
    return json.dumps(_send_command("identify", {"address": address}), indent=2)

@mcp.tool()
def dump_vtable(vtable: str) -> str:
    """List a class vtable's virtual-method addresses (cross-ref with disasm/func_bounds)."""
    return json.dumps(_send_command("dump_vtable", {"vtable": vtable}), indent=2)

@mcp.tool()
def read_chain(base: str, offsets: list) -> str:
    """Resolve a multi-level pointer path: [base] +off1 -> +off2 -> ... -> final address (validates hops)."""
    return json.dumps(_send_command("read_chain", {"base": base, "offsets": offsets}), indent=2)

@mcp.tool()
def find_pointers(target: str, start: str = None, size: int = None, limit: int = 64) -> str:
    """Find 8-byte-aligned slots whose value == target (pointer-scan). Optionally bound to (start,size)."""
    return json.dumps(_send_command("find_pointers", {"target": target, "start": start,
                                                       "size": size, "limit": limit}), indent=2)

@mcp.tool()
def par_label(struct: str, offset: str) -> str:
    """Field name at (struct hash|name, offset) from the imported parser-dump. Auto-labels struct slots."""
    return json.dumps(_send_command("par_label", {"struct": struct, "offset": offset}), indent=2)

@mcp.tool()
def par_struct(struct: str) -> str:
    """All fields (offset,name,type,size) of a parser-reflected struct from the imported dump."""
    return json.dumps(_send_command("par_struct", {"struct": struct}), indent=2)

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
    """Walk the ped/vehicle/object pool -> entity pointers (+ optional handles). UNTESTED: verify pool AOB."""
    return json.dumps(_send_command("enumerate_entities",
                      {"kind": kind, "limit": limit, "with_handles": with_handles}), indent=2)

@mcp.tool()
def nearby_peds(num: int = 16) -> str:
    """Handles of peds near the player (GET_PED_NEARBY_PEDS)."""
    return json.dumps(_send_command("nearby_peds", {"num": num}), indent=2)

@mcp.tool()
def nearby_vehicles(num: int = 16) -> str:
    """Handles of vehicles near the player (GET_PED_NEARBY_VEHICLES)."""
    return json.dumps(_send_command("nearby_vehicles", {"num": num}), indent=2)

# =============================================================================
# Utility Tools
# ============================================================================='''

src = io.open(SERVER, encoding="utf-8").read()
assert src.count(OLD) == 1, f"anchor match count = {src.count(OLD)} (expected 1)"
io.open(SERVER, "w", encoding="utf-8", newline="").write(src.replace(OLD, NEW))
print("server.py: 19 MCP tools added")
