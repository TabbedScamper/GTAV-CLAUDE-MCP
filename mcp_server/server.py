"""
GTA V Memory MCP Server

Bridges Claude Code to a live GTA V instance via an in-game socket server.
Architecture: Claude Code <-> MCP Server (this) <-> Socket <-> In-Game Bridge (PyLoaderV)

Based on the action-prompted model:
- Agent asks user to perform an action ("enter a vehicle, say go")
- User confirms ("go" / "done")
- Agent samples/diffs memory
- User is the actuator + sync signal; bridge stays tiny

Tools follow §4b of BUILD-BRIEF:
- status, snapshot, diff, read, watch, write, revert
- mark (user-triggered capture)
- set_overlay, chat_post, await_user_message (in-game comms)
- State helpers: is_in_vehicle, get_vehicle_info
"""

import json
import socket
import struct
import time
from typing import Optional, Any
from dataclasses import dataclass, field
from mcp.server.fastmcp import FastMCP

# gtadata: fast deep-dive toolkit over the extracted/decrypted game files (manifest, decode, hashes)
try:
    from . import gtadata
except ImportError:  # when run as a loose script rather than a package
    import gtadata

# radio: Claude Radio - local library + yt-dlp fetch, plays in-game via ClaudeRadio.dll
try:
    from . import radio
except ImportError:
    import radio

# =============================================================================
# Configuration
# =============================================================================

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 27015  # ReClass.NET convention
SOCKET_TIMEOUT = 12.0  # generous ceiling so cold model-load spawns don't false-timeout

# =============================================================================
# MCP Server Instance
# =============================================================================

mcp = FastMCP("gtav-memory")

# =============================================================================
# Snapshot Storage (for diff operations)
# =============================================================================

@dataclass
class Snapshot:
    label: str
    address: int
    data: bytes
    timestamp: float = field(default_factory=time.time)

_snapshots: dict[str, Snapshot] = {}
# Undo is owned by the bridge (single source of truth). Server no longer keeps its own stack.

# =============================================================================
# Bridge Communication
# =============================================================================

def _send_command(command: str, params: dict | None = None, timeout: float | None = None) -> dict:
    """
    Send a command to the in-game bridge and wait for response.

    Protocol: 4-byte LE length header + JSON body

    timeout: socket read timeout. Defaults to SOCKET_TIMEOUT, but auto-extends for
    long-running commands that declare a `timeout_ms`/`duration_ms` (chat waits, watches,
    model loads) so the socket doesn't give up before the bridge finishes.
    """
    params = params or {}
    if timeout is None:
        declared_ms = params.get("timeout_ms") or params.get("duration_ms") or 0
        timeout = max(SOCKET_TIMEOUT, declared_ms / 1000.0 + 10.0) if declared_ms else SOCKET_TIMEOUT

    try:
        with socket.create_connection((BRIDGE_HOST, BRIDGE_PORT), timeout=timeout) as sock:
            # Build payload
            payload = json.dumps({
                "command": command,
                "params": params
            }).encode("utf-8")

            # Send: 4-byte LE length + payload
            sock.sendall(struct.pack("<I", len(payload)) + payload)

            # Receive: 4-byte LE length
            size_data = sock.recv(4)
            if len(size_data) < 4:
                return {"error": "Connection closed by bridge", "connected": False}

            size = struct.unpack("<I", size_data)[0]

            # Receive body
            body = b""
            while len(body) < size:
                chunk = sock.recv(min(4096, size - len(body)))
                if not chunk:
                    break
                body += chunk

            return json.loads(body.decode("utf-8"))

    except socket.timeout:
        return {"error": "Bridge timeout - is GTA V running with the bridge script?", "connected": False}
    except ConnectionRefusedError:
        return {"error": "Bridge not running - start GTA V and load the bridge script", "connected": False}
    except Exception as e:
        return {"error": f"Bridge communication error: {e}", "connected": False}

# =============================================================================
# Core Tools - Status & Connection
# =============================================================================

@mcp.tool()
def status() -> str:
    """
    Check connection to the GTA V bridge and get game state.

    Returns: Connection status, game version, current state (in menu, in world, etc.)
    """
    result = _send_command("status")
    return json.dumps(result, indent=2)

# =============================================================================
# Core Tools - Memory Operations
# =============================================================================

@mcp.tool()
def read_memory(address: str, type: str = "float", count: int = 1) -> str:
    """
    Read typed value(s) from a memory address.

    Args:
        address: Hex address (e.g., "0x1A2B3C4D") or expression (e.g., "vehicle+0x110")
        type: Data type - "byte", "int16", "int32", "int64", "float", "double", "ptr"
        count: Number of consecutive values to read (default 1)

    Returns: The value(s) read, or error if address is invalid/unreadable
    """
    result = _send_command("read", {
        "address": address,
        "type": type,
        "count": count
    })
    return json.dumps(result, indent=2)

@mcp.tool()
def write_memory(address: str, value: Any, type: str = "float") -> str:
    """
    Write a typed value to a memory address. Requires explicit consent.

    CAUTION: Writing to wrong addresses can crash the game.
    The bridge validates addresses and snapshots before writing for revert.

    Args:
        address: Hex address or expression
        value: Value to write (will be converted to specified type)
        type: Data type - "byte", "int16", "int32", "int64", "float", "double"

    Returns: Success/failure, old value (for manual revert reference)
    """
    # The bridge owns the single undo stack (it knows wheel-field vs raw-write semantics).
    result = _send_command("write", {
        "address": address,
        "value": value,
        "type": type
    })
    return json.dumps(result, indent=2)

@mcp.tool()
def revert_last_write() -> str:
    """
    Revert the last memory write. Routes to the bridge's single undo stack, which
    correctly reverts both raw writes and wheel-field writes.
    """
    return json.dumps(_send_command("revert_last"), indent=2)

@mcp.tool()
def snapshot(label: str, address: str, size: int = 256) -> str:
    """
    Capture a memory region for later comparison.

    Args:
        label: Name for this snapshot (e.g., "before_shift", "in_1st_gear")
        address: Starting address (hex or expression)
        size: Number of bytes to capture (default 256)

    Returns: Confirmation with snapshot metadata
    """
    result = _send_command("snapshot", {
        "address": address,
        "size": size
    })

    if result.get("success") and "data" in result:
        _snapshots[label] = Snapshot(
            label=label,
            address=result["address"],
            data=bytes.fromhex(result["data"]),
            timestamp=time.time()
        )
        return json.dumps({
            "success": True,
            "label": label,
            "address": hex(result["address"]),
            "size": len(_snapshots[label].data),
            "timestamp": _snapshots[label].timestamp
        }, indent=2)

    return json.dumps(result, indent=2)

@mcp.tool()
def diff(label_a: str, label_b: str) -> str:
    """
    Compare two snapshots and report differences.

    This is the core discovery tool: take snapshot A, user performs action,
    take snapshot B, diff shows which bytes changed.

    Args:
        label_a: First snapshot label (e.g., "before")
        label_b: Second snapshot label (e.g., "after")

    Returns: List of changed offsets with old/new values, interpreted as various types
    """
    if label_a not in _snapshots:
        return json.dumps({"error": f"Snapshot '{label_a}' not found"})
    if label_b not in _snapshots:
        return json.dumps({"error": f"Snapshot '{label_b}' not found"})

    snap_a = _snapshots[label_a]
    snap_b = _snapshots[label_b]

    if snap_a.address != snap_b.address:
        return json.dumps({"error": "Snapshots are from different addresses"})

    changes = []
    min_len = min(len(snap_a.data), len(snap_b.data))

    # Find changed bytes and group into likely fields (4-byte aligned)
    i = 0
    while i < min_len:
        if snap_a.data[i] != snap_b.data[i]:
            # Found a change - read 4 bytes for float/int interpretation
            offset = i
            if i + 4 <= min_len:
                old_bytes = snap_a.data[i:i+4]
                new_bytes = snap_b.data[i:i+4]

                old_float = struct.unpack("<f", old_bytes)[0]
                new_float = struct.unpack("<f", new_bytes)[0]
                old_int = struct.unpack("<i", old_bytes)[0]
                new_int = struct.unpack("<i", new_bytes)[0]

                changes.append({
                    "offset": f"+0x{offset:X}",
                    "address": f"0x{snap_a.address + offset:X}",
                    "old_bytes": old_bytes.hex(),
                    "new_bytes": new_bytes.hex(),
                    "as_float": {"old": old_float, "new": new_float, "delta": new_float - old_float},
                    "as_int": {"old": old_int, "new": new_int, "delta": new_int - old_int}
                })
                i += 4  # Skip ahead since we consumed 4 bytes
            else:
                changes.append({
                    "offset": f"+0x{offset:X}",
                    "old": snap_a.data[i],
                    "new": snap_b.data[i]
                })
                i += 1
        else:
            i += 1

    return json.dumps({
        "snapshot_a": label_a,
        "snapshot_b": label_b,
        "base_address": hex(snap_a.address),
        "time_delta": snap_b.timestamp - snap_a.timestamp,
        "changes": changes,
        "total_changes": len(changes)
    }, indent=2)

@mcp.tool()
def watch(addresses: list[str], duration_seconds: float = 2.0, interval_ms: int = 100) -> str:
    """
    Watch multiple addresses over time and report value changes.

    Useful for finding which value tracks a behavior (e.g., which float ramps
    when you floor the throttle = RPM).

    Args:
        addresses: List of addresses to watch (hex or expressions)
        duration_seconds: How long to watch (default 2s)
        interval_ms: Sample interval in milliseconds (default 100ms)

    Returns: Per-address digest showing min/max/delta and change pattern
    """
    result = _send_command("watch", {
        "addresses": addresses,
        "duration_ms": int(duration_seconds * 1000),
        "interval_ms": interval_ms
    })
    return json.dumps(result, indent=2)

# =============================================================================
# Core Tools - User Sync (Action-Prompted Model)
# =============================================================================

@mcp.tool()
def mark(label: str) -> str:
    """
    User-triggered capture point. Call this when user says "go" or "done".

    Captures current vehicle/wheel state with the given label.
    This is the sync signal in the action-prompted model.

    Args:
        label: Descriptive label (e.g., "user_ready", "shift_complete")
    """
    result = _send_command("mark", {"label": label})

    # Also store any snapshot data returned
    if result.get("success") and "snapshots" in result:
        for name, data in result["snapshots"].items():
            _snapshots[f"{label}_{name}"] = Snapshot(
                label=f"{label}_{name}",
                address=data["address"],
                data=bytes.fromhex(data["data"]),
                timestamp=time.time()
            )

    return json.dumps(result, indent=2)

@mcp.tool()
def await_user_message() -> str:
    """
    Block until the user types a message in the in-game chat.

    This is how the user communicates from inside the game to Claude.
    Returns when user submits text, or times out.

    Returns: The user's message text
    """
    result = _send_command("await_user_message", {"timeout_ms": 60000})
    return json.dumps(result, indent=2)

# =============================================================================
# Core Tools - In-Game Display
# =============================================================================

@mcp.tool()
def set_overlay(text: str, state: str = "searching") -> str:
    """
    Update the in-game overlay to show Claude's current status/intent.

    This keeps the user informed without alt-tabbing.

    Args:
        text: Status text to display (e.g., "Finding RPM offset...", "Found! RPM @ +0x824")
        state: Overlay state - "searching", "found", "waiting", "error"
    """
    result = _send_command("set_overlay", {"text": text, "state": state})
    return json.dumps(result, indent=2)

@mcp.tool()
def chat_post(message: str) -> str:
    """
    Post a message to the in-game chat panel (Claude -> User).

    Use this to ask questions, report findings, or request actions.

    Args:
        message: Text to display in the in-game chat
    """
    result = _send_command("chat_post", {"message": message})
    return json.dumps(result, indent=2)

@mcp.tool()
def ask_in_game(question: str, timeout_seconds: int = 60) -> str:
    """
    Ask a question in-game and wait for the user's response.

    Use this instead of terminal confirmations when the user is playing.
    Shows the question via notification, opens keyboard, waits for response.

    Common pattern for confirmations:
        result = ask_in_game("Write camber=0.1 to wheel 0? Type yes/no")
        if "yes" in result.lower():
            # proceed with write

    Args:
        question: The question to display in-game
        timeout_seconds: How long to wait for response (default 60)

    Returns: The user's typed response
    """
    # Post the question
    _send_command("chat_post", {"message": question})

    # Wait for response
    result = _send_command("await_user_message", {"timeout_ms": timeout_seconds * 1000})
    return json.dumps(result, indent=2)

@mcp.tool()
def get_pending_messages() -> str:
    """
    Check for any messages the user typed in-game (non-blocking).

    The user can press F10 anytime to send a message. Use this to check
    if they've sent anything without blocking/waiting.

    Returns: List of pending messages (empty if none)
    """
    result = _send_command("get_pending_messages")
    return json.dumps(result, indent=2)

@mcp.tool()
def has_pending_messages() -> str:
    """
    Quick check if user has sent any messages in-game (non-blocking).

    Returns: True/False and count
    """
    result = _send_command("has_pending_messages")
    return json.dumps(result, indent=2)

# =============================================================================
# State Helpers - Vehicle Info (read-only, for auto-detect-with-confirm)
# =============================================================================

@mcp.tool()
def is_in_vehicle() -> str:
    """
    Check if the player is currently in a vehicle.

    Returns: True/False and basic vehicle info if in one
    """
    result = _send_command("is_in_vehicle")
    return json.dumps(result, indent=2)

@mcp.tool()
def get_vehicle_info() -> str:
    """
    Get detailed info about the current vehicle.

    Returns: Model hash, display name, plate text, wheel count,
             wheel pointers, and current offset values for known fields
    """
    result = _send_command("get_vehicle_info")
    return json.dumps(result, indent=2)

@mcp.tool()
def get_wheel_values(wheel_index: int = 0) -> str:
    """
    Read current values from a specific wheel using known Legacy offsets.

    This uses the verified offsets from FiveM/IKT:
    - 0x008: Y rotation (camber)
    - 0x010: Inverse Y rotation
    - 0x030: X offset (track width)
    - 0x110: Tyre radius
    - 0x114: Rim radius
    - 0x118: Tyre width

    Args:
        wheel_index: Which wheel (0=FL, 1=FR, 2=RL, 3=RR typically)

    Returns: Current values for all known wheel fields
    """
    result = _send_command("get_wheel_values", {"wheel_index": wheel_index})
    return json.dumps(result, indent=2)

@mcp.tool()
def set_wheel_value(wheel_index: int, field: str, value: float) -> str:
    """
    Set a wheel field value using known offsets.

    Args:
        wheel_index: Which wheel (0-3)
        field: Field name - "camber", "track_width", "tyre_radius", "rim_radius", "tyre_width"
        value: New value (float)

    Returns: Success/failure and old value for reference
    """
    # Bridge records the undo entry (it handles wheel-field revert semantics).
    result = _send_command("set_wheel_value", {
        "wheel_index": wheel_index,
        "field": field,
        "value": value
    })
    return json.dumps(result, indent=2)

# =============================================================================
# Discovery Tools - Pattern Scanning
# =============================================================================

@mcp.tool()
def scan_pattern(pattern: str, module: str = "GTA5.exe") -> str:
    """
    Search for a byte pattern in game memory.

    Pattern format: hex bytes with ?? wildcards (e.g., "48 8B 05 ?? ?? ?? ?? 45")

    Args:
        pattern: AOB pattern to search for
        module: Module to search in (default "GTA5.exe")

    Returns: List of matching addresses
    """
    result = _send_command("scan_pattern", {
        "pattern": pattern,
        "module": module
    })
    return json.dumps(result, indent=2)

@mcp.tool()
def resolve_rip_relative(address: str, offset_position: int, instruction_size: int) -> str:
    """
    Resolve a RIP-relative address from an instruction.

    Many GTA patterns use RIP-relative addressing. This reads the displacement
    at offset_position and computes the absolute address.

    Formula: absolute = instruction_address + instruction_size + displacement

    Args:
        address: Address of the instruction (hex)
        offset_position: Byte offset where the 4-byte displacement starts
        instruction_size: Total instruction length

    Returns: The resolved absolute address
    """
    result = _send_command("resolve_rip_relative", {
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
# =============================================================================

@mcp.tool()
def list_snapshots() -> str:
    """List all stored snapshots with their metadata."""
    return json.dumps({
        "snapshots": [
            {
                "label": s.label,
                "address": hex(s.address),
                "size": len(s.data),
                "timestamp": s.timestamp
            }
            for s in _snapshots.values()
        ]
    }, indent=2)

@mcp.tool()
def clear_snapshots() -> str:
    """Clear all stored snapshots."""
    count = len(_snapshots)
    _snapshots.clear()
    return json.dumps({"cleared": count})

@mcp.tool()
def get_undo_stack() -> str:
    """View the bridge's undo stack of recent writes (single source of truth)."""
    return json.dumps(_send_command("get_undo_stack"), indent=2)

@mcp.tool()
def get_crash_logs() -> str:
    """
    Get recent crash logs from the bridge.

    If the game crashed after a write operation, this shows:
    - The last operation before crash (from write-ahead log)
    - Crash timestamp and context
    - Python traceback if available

    Use this to diagnose what caused a crash.
    """
    result = _send_command("get_crash_logs")
    return json.dumps(result, indent=2)

@mcp.tool()
def get_chat_history(limit: int = 20) -> str:
    """
    Get recent chat history between Claude and the user in-game.

    Args:
        limit: Maximum number of messages to return (default 20)

    Returns: List of messages with sender, text, and timestamp
    """
    result = _send_command("get_chat_history", {"limit": limit})
    return json.dumps(result, indent=2)

@mcp.tool()
def probe_wheels() -> str:
    """
    Probe vehicle memory to find wheel array offset.

    Different game versions use different offsets for the wheel array.
    This scans a range of offsets to find where the wheel pointers are.
    """
    result = _send_command("probe_wheels")
    return json.dumps(result, indent=2)

@mcp.tool()
def probe_drawhandler() -> str:
    """
    Probe for DrawHandler -> StreamRenderGfx pointer chain.

    FiveM uses this path for visual wheel size/width modifications.
    This scans vehicle memory looking for pointer chains that lead to
    float values in the wheel dimension range (0.1 - 2.0).
    """
    result = _send_command("probe_drawhandler")
    return json.dumps(result, indent=2)

@mcp.tool()
def find_wheel_visual_offsets() -> str:
    """
    Find visual wheel size/width offsets by matching CWheel values.

    Compares values from the physics CWheel structure (tyre_radius, etc.)
    with values found in potential StreamRenderGfx structures to identify
    the correct visual wheel offsets.
    """
    result = _send_command("find_wheel_visual_offsets")
    return json.dumps(result, indent=2)

@mcp.tool()
def set_continuous(wheel_index: int, field: str, value: float, enabled: bool = True) -> str:
    """
    Set a wheel value to be applied continuously (every frame).

    GTA V's physics can reset wheel values each tick, so for some modifications
    to persist visually, they need to be reapplied every frame.

    Args:
        wheel_index: Which wheel (0-3)
        field: Field name - "camber", "track_width", etc.
        value: Value to continuously apply
        enabled: True to enable, False to disable

    Returns: Confirmation of continuous write status
    """
    result = _send_command("set_continuous", {
        "wheel_index": wheel_index,
        "field": field,
        "value": value,
        "enabled": enabled
    })
    return json.dumps(result, indent=2)

@mcp.tool()
def clear_continuous() -> str:
    """Clear all continuous wheel writes."""
    result = _send_command("clear_continuous")
    return json.dumps(result, indent=2)

@mcp.tool()
def list_continuous() -> str:
    """List all active continuous wheel writes."""
    result = _send_command("list_continuous")
    return json.dumps(result, indent=2)

# =============================================================================
# Visual Wheel Tools (FiveM-style StreamRenderGfx path)
# =============================================================================

@mcp.tool()
def set_visual_wheel_size(size: float, dh_offset: int = None, srg_offset: int = None, size_offset: int = None) -> str:
    """
    Set the visual wheel size using the FiveM-style StreamRenderGfx path.

    This modifies the rendered wheel size, separate from the physics CWheel values.
    Offsets must be discovered first via find_wheel_visual_offsets.

    Args:
        size: New wheel size (typically 0.3-1.5)
        dh_offset: Optional DrawHandler offset from vehicle (use cached if None)
        srg_offset: Optional StreamRenderGfx offset (use cached if None)
        size_offset: Optional size value offset (use cached if None)
    """
    params = {"size": size}
    if dh_offset is not None:
        params["dh_offset"] = dh_offset
    if srg_offset is not None:
        params["srg_offset"] = srg_offset
    if size_offset is not None:
        params["size_offset"] = size_offset

    result = _send_command("set_visual_wheel_size", params)
    return json.dumps(result, indent=2)

@mcp.tool()
def set_visual_wheel_width(width: float, dh_offset: int = None, srg_offset: int = None, width_offset: int = None) -> str:
    """
    Set the visual wheel width using the FiveM-style StreamRenderGfx path.

    This modifies the rendered wheel width, separate from the physics CWheel values.
    Offsets must be discovered first via find_wheel_visual_offsets.

    Args:
        width: New wheel width (typically 0.1-0.5)
        dh_offset: Optional DrawHandler offset from vehicle (use cached if None)
        srg_offset: Optional StreamRenderGfx offset (use cached if None)
        width_offset: Optional width value offset (use cached if None)
    """
    params = {"width": width}
    if dh_offset is not None:
        params["dh_offset"] = dh_offset
    if srg_offset is not None:
        params["srg_offset"] = srg_offset
    if width_offset is not None:
        params["width_offset"] = width_offset

    result = _send_command("set_visual_wheel_width", params)
    return json.dumps(result, indent=2)

@mcp.tool()
def cache_visual_offsets(dh_offset: int, srg_offset: int, size_offset: int, width_offset: int) -> str:
    """
    Cache discovered visual wheel offsets for later use.

    After using find_wheel_visual_offsets or probe_drawhandler to discover the
    correct offsets, use this to cache them so set_visual_wheel_size/width
    can use them without re-specifying.

    Args:
        dh_offset: DrawHandler offset from vehicle base
        srg_offset: StreamRenderGfx offset from DrawHandler
        size_offset: Wheel size offset in StreamRenderGfx
        width_offset: Wheel width offset in StreamRenderGfx
    """
    result = _send_command("cache_visual_offsets", {
        "dh_offset": dh_offset,
        "srg_offset": srg_offset,
        "size_offset": size_offset,
        "width_offset": width_offset
    })
    return json.dumps(result, indent=2)

@mcp.tool()
def scan_structure(address: str, size: int = 512, min_val: float = 0.0, max_val: float = 10.0) -> str:
    """
    Scan a memory structure for float values in a specified range.

    Useful for exploring discovered pointer chains to find wheel visual offsets.

    Args:
        address: Hex address to scan from
        size: Number of bytes to scan (default 512)
        min_val: Minimum float value to include (default 0.0)
        max_val: Maximum float value to include (default 10.0)

    Returns: List of offsets and values within the range
    """
    result = _send_command("scan_structure", {
        "address": address,
        "size": size,
        "min": min_val,
        "max": max_val
    })
    return json.dumps(result, indent=2)

# =============================================================================
# Native Library - call ANY of ~6700 natives safely (verified allowlist)
# =============================================================================

@mcp.tool()
def call_native(name: str, args: list = None, return_type: str = None) -> str:
    """
    Call a GTA V native function BY NAME - the primary way to do ANYTHING in-game.

    Resolves the name to its verified canonical hash and refuses unknown/guessed
    hashes (a wrong hash crashes the game). ~6700 natives available; works on
    Legacy + Enhanced. Discover names with search_natives(); check args with native_info().

    Args:
        name: Native name, e.g. "CREATE_VEHICLE", "SET_ENTITY_INVINCIBLE",
              "ADD_EXPLOSION", "SET_PED_MOVE_RATE_OVERRIDE".
        args: Arguments in order (see native_info for order/types). Entity/ped/vehicle
              args are handles (ints). Get the player ped via PLAYER_PED_ID first.
        return_type: Optional override ("int","float","bool","string","void");
                     omit to use the native's declared return type.
    """
    result = _send_command("call_native_by_name", {
        "name": name, "args": args or [], "return_type": return_type,
    })
    return json.dumps(result, indent=2)

@mcp.tool()
def call_native_by_hash(hash: str, args: list = None, return_type: str = "int") -> str:
    """
    Call a native by raw hash (power-user escape hatch). ALLOWLIST-GATED: refuses any
    hash not in the verified DB. Prefer call_native(name) instead.
    """
    result = _send_command("call_native", {
        "hash": hash, "args": args or [], "return_type": return_type,
    })
    return json.dumps(result, indent=2)

@mcp.tool()
def search_natives(query: str = "", namespace: str = None, limit: int = 30) -> str:
    """
    Search the ~6700-native library to discover what you can do.

    Args:
        query: substring in the native name OR its doc comment
               (e.g. "explosion", "wanted", "invincible", "vehicle_mod", "ptfx").
        namespace: optional filter (e.g. "VEHICLE","PLAYER","PED","WEAPON","ENTITY","MISC","CAM","FIRE","GRAPHICS").
        limit: max results (default 30).
    """
    params = {"query": query, "limit": limit}
    if namespace:
        params["namespace"] = namespace
    return json.dumps(_send_command("search_natives", params), indent=2)

@mcp.tool()
def native_info(name: str) -> str:
    """Get a native's params (order + types), return type, namespace, flags, and doc comment."""
    return json.dumps(_send_command("native_info", {"name": name}), indent=2)

@mcp.tool()
def native_db_status() -> str:
    """Native DB status: edition (legacy/enhanced), how many natives are callable, known-bad list."""
    return json.dumps(_send_command("native_db_status"), indent=2)

# =============================================================================
# Tier-1 convenience wrappers (the fun layer) - thin shells over verified natives
# =============================================================================

@mcp.tool()
def spawn_vehicle(model: str, distance: float = 5.0) -> str:
    """Spawn a vehicle in front of the player. model = e.g. "adder","hydra","rhino","insurgent2","buzzard". Preload big models first."""
    return json.dumps(_send_command("spawn_vehicle", {"model": model, "distance": distance}), indent=2)

@mcp.tool()
def preload_model(model: str, wait: bool = True) -> str:
    """Preload a model so a later spawn is instant (avoids the streaming lag). wait=False to fire-and-forget."""
    return json.dumps(_send_command("preload_model", {"model": model, "wait": wait}), indent=2)

@mcp.tool()
def teleport(x: float, y: float, z: float, heading: float = None) -> str:
    """Teleport the player (and current vehicle) to coordinates."""
    p = {"x": x, "y": y, "z": z}
    if heading is not None:
        p["heading"] = heading
    return json.dumps(_send_command("teleport", p), indent=2)

@mcp.tool()
def set_weather(weather: str) -> str:
    """Set weather instantly: CLEAR, EXTRASUNNY, CLOUDS, OVERCAST, RAIN, THUNDER, CLEARING, SMOG, FOGGY, SNOW, BLIZZARD, XMAS, HALLOWEEN."""
    return json.dumps(_send_command("set_weather", {"weather": weather}), indent=2)

@mcp.tool()
def set_time(hour: int, minute: int = 0, second: int = 0) -> str:
    """Set the in-game clock (24h)."""
    return json.dumps(_send_command("set_time", {"hour": hour, "minute": minute, "second": second}), indent=2)

@mcp.tool()
def give_weapon(weapon: str, ammo: int = 9999) -> str:
    """Give the player a weapon: e.g. WEAPON_PISTOL, WEAPON_MINIGUN, WEAPON_RPG, WEAPON_RAILGUN, WEAPON_STICKYBOMB, WEAPON_COMBATMG. "WEAPON_" optional."""
    return json.dumps(_send_command("give_weapon", {"weapon": weapon, "ammo": ammo}), indent=2)

@mcp.tool()
def repair_vehicle() -> str:
    """Repair and clean the player's current vehicle."""
    return json.dumps(_send_command("repair_vehicle"), indent=2)

# =============================================================================
# Player convenience wrappers - correct native + arg order baked in (no chains to fumble)
# =============================================================================

def _call(name: str, args: list = None, return_type: str = None) -> dict:
    """Internal: invoke a native by name through the verified-allowlist path."""
    return _send_command("call_native_by_name",
                         {"name": name, "args": args or [], "return_type": return_type})

@mcp.tool()
def get_player_ped() -> str:
    """Get the player's ped handle (PLAYER_PED_ID). Most native chains start here."""
    return json.dumps(_call("PLAYER_PED_ID", [], "int"), indent=2)

@mcp.tool()
def set_invincible(enabled: bool = True) -> str:
    """Toggle player invincibility (god mode). Fetches a fresh ped handle first."""
    ped = _call("PLAYER_PED_ID", [], "int")
    h = ped.get("result")
    if not h:
        return json.dumps({"error": "Could not get player ped", "detail": ped}, indent=2)
    return json.dumps(_call("SET_ENTITY_INVINCIBLE", [h, bool(enabled)], "void"), indent=2)

@mcp.tool()
def set_health(health: int = 200) -> str:
    """Set the player's health (max ~200 by default). Fetches a fresh ped handle first."""
    ped = _call("PLAYER_PED_ID", [], "int")
    h = ped.get("result")
    if not h:
        return json.dumps({"error": "Could not get player ped", "detail": ped}, indent=2)
    return json.dumps(_call("SET_ENTITY_HEALTH", [h, int(health)], "void"), indent=2)

@mcp.tool()
def set_wanted_level(level: int = 0) -> str:
    """Set the player's wanted level (0-5) and apply it. Use 0 to clear stars."""
    r1 = _call("SET_PLAYER_WANTED_LEVEL", [0, int(level), False], "void")
    r2 = _call("SET_PLAYER_WANTED_LEVEL_NOW", [0, False], "void")
    return json.dumps({"set": r1, "applied": r2}, indent=2)

@mcp.tool()
def list_namespaces() -> str:
    """List the native namespaces available (for use with search_natives's namespace filter)."""
    return json.dumps(_send_command("list_namespaces"), indent=2)

@mcp.tool()
def get_context(detail: str = "lite") -> str:
    """Live snapshot of player + world state for situational awareness. Reads only, safe anytime.
    detail='lite' (default): position, zone, health/armour, heading, wanted level, time, weather,
    current vehicle (model/speed/health).
    detail='full': also activity flags (sprinting/shooting/swimming/in_cover/in_air/...), current
    weapon + ammo, vehicle class/plate/tank/engine, day of week, next weather, game-state flags
    (cutscene/screen_faded/dead/free_aiming), interior id.
    Fields that can't be read are omitted."""
    return json.dumps(_send_command("get_context", {"detail": detail}), indent=2)

# --- Debugging workbench ---

@mcp.tool()
def get_environment() -> str:
    """Ground truth for debugging: GTA edition (Legacy/Enhanced), exe, module base+size,
    native-DB stats, and which offsets are verified for THIS build. Check this before relying
    on offsets — wheel offsets are Legacy-verified and may differ on Enhanced."""
    return json.dumps(_send_command("get_environment"), indent=2)

@mcp.tool()
def inspect(handle: int | None = None, address: str | None = None, size: int = 256,
            struct: str | None = None) -> str:
    """Universal 'what am I looking at': decode an entity handle OR a raw memory address into
    labeled, typed slots (float / int / pointer / zero / raw), with entity type + model and any
    known/finding labels applied. Follow pointer slots with another inspect on their value.
    Args: handle (entity) or address (hex/int); size bytes (8-2048, default 256);
    struct="wheel" to apply known wheel-field labels."""
    params = {"size": size}
    if handle is not None:
        params["handle"] = handle
    if address is not None:
        params["address"] = address
    if struct is not None:
        params["struct"] = struct
    return json.dumps(_send_command("inspect", params), indent=2)

@mcp.tool()
def set_goal(goal: str) -> str:
    """Record what you're building/debugging this session (persists across game/script reloads)."""
    return json.dumps(_send_command("set_goal", {"goal": goal}), indent=2)

@mcp.tool()
def note_finding(name: str, offset: str | None = None, base: str | None = None,
                 value: str | None = None, kind: str | None = None, note: str | None = None,
                 verified: bool | None = None) -> str:
    """Record a discovered offset/label/struct so it survives reloads and auto-labels future
    inspect() calls. e.g. note_finding('camber', offset='0x08', base='0x7FF...', kind='float',
    verified=True). Use base+offset so inspect can label that exact address."""
    params = {"name": name}
    for k, v in (("offset", offset), ("base", base), ("value", value), ("kind", kind),
                 ("note", note), ("verified", verified)):
        if v is not None:
            params[k] = v
    return json.dumps(_send_command("note_finding", params), indent=2)

@mcp.tool()
def get_findings() -> str:
    """Everything discovered/declared this session: the goal, named offsets, and notes.
    Check this at the start of a task so you don't re-derive what's already known."""
    return json.dumps(_send_command("get_findings"), indent=2)

@mcp.tool()
def clear_findings() -> str:
    """Reset the session findings (goal + offsets + notes)."""
    return json.dumps(_send_command("clear_findings"), indent=2)

# =============================================================================
# Deep-dive tools - the EXTRACTED/DECRYPTED game files on disk (offline data, not live memory)
# =============================================================================
# Use these to look up how the game is DEFINED (handling, mod kits, radio/audio, vehicles.meta,
# model file locations) - distinct from the live-memory tools above. Fast: a prebuilt manifest +
# decoded-XML cache + a growing JOAAT name dictionary, so repeat lookups are near-instant.
# Workflow: gtadata_find (locate) -> gtadata_decode (if binary .rel) -> gtadata_read (grep) ->
# gtadata_resolve/gtadata_crack (turn hashes into names; cracked names are remembered).

@mcp.tool()
def gtadata_find(pattern: str, limit: int = 50, regex: bool = False) -> str:
    """
    Find file paths in the extracted GTA V game tree (379k files) by substring (default) or regex.
    Instant - greps a prebuilt manifest, no filesystem scan (recursive scans time out, don't try them).

    Args:
        pattern: substring (e.g. "handling.meta", "radio_06") or regex if regex=True
        limit: max results (default 50)
        regex: treat pattern as a regex
    Returns: JSON list of full file paths. Tip: resolve a friendly name to its INTERNAL id first
             (e.g. "Rebel Radio" -> "radio_06_country") - grepping the friendly name finds the wrong thing.
    """
    return json.dumps(gtadata.find(pattern, limit=limit, regex=regex), indent=2)


@mcp.tool()
def gtadata_read(path: str, pattern: Optional[str] = None, limit: int = 200, context: int = 0) -> str:
    """
    Read a TEXT game file (.meta/.xml/...). With `pattern`, returns matching lines (grep) instead.

    Args:
        path: full path (from gtadata_find), or the cached .xml from gtadata_decode
        pattern: optional regex - return only matching lines (with `context` lines around each)
        limit: max lines (or max matches when pattern is set)
        context: lines of context around each match
    Returns: file text or matching lines.
    """
    return gtadata.read_text(path, pattern=pattern, limit=limit, context=context)


@mcp.tool()
def gtadata_decode(path: str, force: bool = False) -> str:
    """
    Make a game file readable. Text files (.meta/.xml) are returned as-is; binary AUDIO metadata
    (.rel / dat54 / dat151 - radio stations, track lists, sounds) is decoded to XML via CodeWalker
    and CACHED. Then gtadata_read the returned xml_path. (Model/texture .ydr/.yft/.ytd are binary -
    those still need the CodeWalker GUI.)

    Args:
        path: full path to a .rel/.meta from gtadata_find
        force: re-decode even if a cached XML exists
    Returns: JSON {ok, kind, xml_path|path, note}.
    """
    return json.dumps(gtadata.decode(path, force=force), indent=2)


@mcp.tool()
def gtadata_resolve(hash: str) -> str:
    """
    Resolve a JOAAT hash (e.g. "hash_26F5A0D9", "0x26F5A0D9") to a readable name using the known-name
    dictionary. Use it to turn the `hash_XXXXXXXX` refs in decoded audio XML into real names.

    Returns: JSON {hash, name|null}.
    """
    name = gtadata.resolve(hash)
    return json.dumps({"hash": str(hash), "name": name, "hex": f"{gtadata._as_hash(hash):08X}"}, indent=2)


@mcp.tool()
def gtadata_crack(target_hashes: list[str], candidates: list[str], learn: bool = True) -> str:
    """
    Crack unknown JOAAT hashes by testing candidate NAMES against them (the names in the game files
    are joaat hashes of internal strings). Generate `candidates` from a known list/convention - e.g.
    for Rebel Radio songs: ["RADIO_06_COUNTRY_CONVOY", "RADIO_06_COUNTRY_WHISKEY_RIVER", ...].
    Convention seen: radio track sounds are "RADIO_<NN>_<GENRE>_<TITLE_WORDS>".

    Args:
        target_hashes: the unknown hashes (e.g. SoundRef values from decoded XML)
        candidates: full candidate name strings to test
        learn: remember any hits (append to the name dictionary) so future decodes auto-resolve them
    Returns: JSON {hexhash: name} for matches.
    """
    return json.dumps(gtadata.crack(target_hashes, candidates, learn=learn), indent=2)


@mcp.tool()
def gtadata_learn(name: str) -> str:
    """Permanently add a confirmed internal name to the dictionary (so its hash resolves from now on)."""
    return json.dumps({"name": name, "hex": f"{gtadata.joaat(name):08X}", "new": gtadata.add_name(name)}, indent=2)


@mcp.tool()
def gtadata_joaat(name: str) -> str:
    """Compute the JOAAT hash of a name (lowercased), the hash GTA/CodeWalker use for internal names."""
    return json.dumps({"name": name, "hash": f"0x{gtadata.joaat(name):08X}", "hash_fmt": f"hash_{gtadata.joaat(name):08X}"})


# =============================================================================
# Claude Radio - play any song in-game (local library + on-demand yt-dlp fetch)
# =============================================================================
# A real in-game radio experience with NO Spotify/Premium/DRM. Songs are local files; new ones are
# fetched from YouTube and cached, then played in-game by ClaudeRadio.dll (NAudio). Library folder is
# native to the mod (%LOCALAPPDATA%\GTAV-Claude-MCP\music by default) and user-configurable.

@mcp.tool()
def play_song(query: str) -> str:
    """
    Play a song in-game on Claude Radio. Finds it in the local library (instant) or fetches it from
    YouTube (~few seconds), then plays it. Use a natural query: "Smokin and Ridin", "Convoy CW McCall".

    Returns: JSON {playing, artist, fetched, file}. If fetched=true it was just downloaded.
    Tip: tell the user "fetching..." may take a few seconds for a brand-new song; cached songs are instant.
    """
    try:
        return json.dumps(radio.play(query), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def queue_song(query: str) -> str:
    """Add a song to the Claude Radio queue (find-or-fetch, then append). Returns JSON {queued, fetched}."""
    try:
        return json.dumps(radio.queue(query), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def radio_control(action: str, volume: int = 100) -> str:
    """
    Transport control for Claude Radio. action one of: "pause", "resume", "skip", "stop", "volume".
    For "volume", pass volume=0..100.
    """
    a = action.lower().strip()
    fn = {"pause": radio.pause, "resume": radio.resume, "skip": radio.skip, "stop": radio.stop}.get(a)
    if fn:
        return json.dumps(fn())
    if a == "volume":
        return json.dumps(radio.set_volume(volume))
    return json.dumps({"error": f"unknown action '{action}' (pause|resume|skip|stop|volume)"})


@mcp.tool()
def radio_now_playing() -> str:
    """What's currently on Claude Radio (state, title, position) - read from the in-game player."""
    return json.dumps(radio.read_status(), indent=2)


@mcp.tool()
def radio_library(search: str = "") -> str:
    """List/search the local Claude Radio music library (downloaded + user-dropped songs)."""
    return json.dumps(radio.library_list(search), indent=2)


@mcp.tool()
def radio_set_music_folder(path: str) -> str:
    """Point Claude Radio's library at a folder (e.g. the user's own music collection), then index it."""
    try:
        return json.dumps(radio.set_music_folder(path), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def radio_rescan() -> str:
    """Re-index the music folder so songs the user dropped in by hand become playable. Returns counts."""
    try:
        return json.dumps(radio.rescan(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def radio_clear_library() -> str:
    """Delete ALL downloaded songs and reset the library (stops playback first). Reversible - songs
    re-download on demand. Confirm with the user before calling; this empties their music folder."""
    try:
        return json.dumps(radio.clear_library(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def reload_scripts() -> str:
    """Reload the in-game ScriptHookVDotNet C# scripts (ClaudeRadio, ClaudeChatUI) by simulating the
    Insert key - so a freshly-built/deployed DLL loads with NO manual keypress. Use after deploying a
    new ClaudeRadio.dll. (Key map: Insert = SHVDN/C# scripts; F9 = the PyLoaderV bridge itself.)"""
    return json.dumps(_send_command("reload_scripts"), indent=2)


# =============================================================================
# Entry Point
# =============================================================================

def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
