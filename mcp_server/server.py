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
def get_context() -> str:
    """Live snapshot of player + world state for situational awareness: position, zone, health,
    armour, heading, wanted level, time of day, weather, and current vehicle (model/speed/health).
    Reads only — safe to call anytime. Fields that can't be read are omitted."""
    return json.dumps(_send_command("get_context"), indent=2)

# =============================================================================
# Entry Point
# =============================================================================

def main():
    """Entry point for the MCP server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
