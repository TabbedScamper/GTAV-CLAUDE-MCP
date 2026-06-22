"""Add the advanced-RE MCP tools to server.py (assert exact single-match anchor)."""
import io
SERVER = r"C:\Users\mwalton\Dropbox\Personal-Files\GTA 5 Mods\Projects\GTAV-Claude-MCP\mcp_server\server.py"

OLD = '''@mcp.tool()
def nearby_vehicles(num: int = 16) -> str:
    """Handles of vehicles near the player (GET_PED_NEARBY_VEHICLES)."""
    return json.dumps(_send_command("nearby_vehicles", {"num": num}), indent=2)

# =============================================================================
# Utility Tools
# ============================================================================='''

NEW = '''@mcp.tool()
def nearby_vehicles(num: int = 16) -> str:
    """Handles of vehicles near the player (GET_PED_NEARBY_VEHICLES)."""
    return json.dumps(_send_command("nearby_vehicles", {"num": num}), indent=2)

# =============================================================================
# Value scanning (find an address you can SEE but don't know - Cheat-Engine style)
# =============================================================================

@mcp.tool()
def scan_first(vtype: str = "i32", scan_type: str = "exact", value=None,
               lo=None, hi=None, start: str = None, size: int = None) -> str:
    """First scan to find an unknown address. vtype: i8/u8/i16/u16/i32/u32/i64/u64/f32/f64.
    scan_type: exact|unknown|bigger|smaller|between. Use 'unknown' then narrow with scan_next as you
    change the value in-game (take damage, spend cash). Optionally bound to a (start,size) region."""
    return json.dumps(_send_command("scan_first", {"vtype": vtype, "scan_type": scan_type,
        "value": value, "lo": lo, "hi": hi, "start": start, "size": size}), indent=2)

@mcp.tool()
def scan_next(scan_type: str = "unchanged", value=None) -> str:
    """Narrow the previous scan. scan_type: changed|unchanged|increased|decreased|exact|incby|decby
    (compared vs the previous scan's values). Repeat until a handful of addresses remain."""
    return json.dumps(_send_command("scan_next", {"scan_type": scan_type, "value": value}), indent=2)

@mcp.tool()
def scan_results(limit: int = 50) -> str:
    """Show the current narrowed scan survivors (address + value)."""
    return json.dumps(_send_command("scan_results", {"limit": limit}), indent=2)

@mcp.tool()
def scan_count() -> str:
    """How many addresses survive the current scan."""
    return json.dumps(_send_command("scan_count", {}), indent=2)

@mcp.tool()
def scan_undo() -> str:
    """Revert the last scan step (back to the previous survivor set)."""
    return json.dumps(_send_command("scan_undo", {}), indent=2)

@mcp.tool()
def scan_reset() -> str:
    """Clear the scan session."""
    return json.dumps(_send_command("scan_reset", {}), indent=2)

# =============================================================================
# Auto-signature + code modification + enum decode
# =============================================================================

@mcp.tool()
def make_signature(address: str, max_len: int = 64) -> str:
    """Generate a unique, patch-stable AOB signature for an address (reverse of scanning). Wildcards
    operand bytes that relocate; grows until exactly one module match. Store it so a finding survives
    game updates. Needs capstone."""
    return json.dumps(_send_command("make_signature", {"address": address, "max_len": max_len}), indent=2)

@mcp.tool()
def patch_bytes(address: str, bytes: str, allow_outside: bool = False) -> str:
    """Write bytes at an address (reversible - snapshot saved). `bytes` = hex like 'EB 1A' or '90 90'.
    Refuses non-executable/out-of-module targets unless allow_outside=true. Returns a patch id for
    restore_patch. Changes game CODE - confirm intent with the user."""
    return json.dumps(_send_command("patch_bytes", {"address": address, "bytes": bytes,
        "allow_outside": allow_outside}), indent=2)

@mcp.tool()
def nop(address: str, count: int = 1) -> str:
    """NOP out `count` whole instructions at address (length-aware - never cuts an instruction). Use
    to disable a check/call. Reversible via restore_patch. Needs capstone."""
    return json.dumps(_send_command("nop", {"address": address, "count": count}), indent=2)

@mcp.tool()
def restore_patch(id: int) -> str:
    """Undo a patch_bytes/nop by its id (writes the original bytes back)."""
    return json.dumps(_send_command("restore_patch", {"id": id}), indent=2)

@mcp.tool()
def list_patches() -> str:
    """List active patches (id + address)."""
    return json.dumps(_send_command("list_patches", {}), indent=2)

@mcp.tool()
def restore_all_patches() -> str:
    """Undo every active patch."""
    return json.dumps(_send_command("restore_all_patches", {}), indent=2)

@mcp.tool()
def alloc_cave(size: int = 4096, near: str = None) -> str:
    """Allocate executable memory within +/-2GB of `near` (default GTA5.exe base) so a 5-byte rel32
    jmp can reach it - for trampolines/injected logic."""
    return json.dumps(_send_command("alloc_cave", {"size": size, "near": near}), indent=2)

@mcp.tool()
def capture_stack(max_frames: int = 32) -> str:
    """Capture the current call stack (return addresses symbolized to GTA5.exe+offset). Use from a
    watchpoint/hook context to learn who called."""
    return json.dumps(_send_command("capture_stack", {"max_frames": max_frames}), indent=2)

@mcp.tool()
def enum_decode(enum: str, value) -> str:
    """Decode an int to its named enum member via the imported parser dump (e.g. eVehicleClass 6 ->
    VC_SPORT). Handles bitflag enums too. Needs a dump loaded."""
    return json.dumps(_send_command("enum_decode", {"enum": enum, "value": value}), indent=2)

@mcp.tool()
def par_struct(struct: str) -> str:
    """All fields (offset,name,type,size) of a parser-reflected struct from the imported dump."""
    return json.dumps(_send_command("par_struct", {"struct": struct}), indent=2)

# =============================================================================
# Utility Tools
# ============================================================================='''

src = io.open(SERVER, encoding="utf-8").read()
assert src.count(OLD) == 1, f"anchor count = {src.count(OLD)}"
io.open(SERVER, "w", encoding="utf-8", newline="").write(src.replace(OLD, NEW))
print("server.py: advanced-RE tools added")
