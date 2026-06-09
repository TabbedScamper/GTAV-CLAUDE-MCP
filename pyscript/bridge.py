"""
GTA V In-Game Bridge for Claude MCP

This script runs inside GTA V via PyLoaderV 0.6.
It provides a socket server that the MCP server connects to for memory operations.

Architecture (from BUILD-BRIEF §7):
- Socket thread accepts connections, enqueues work items
- Game tick drains the queue and executes (natives/memory are only safe here)
- Never touch game memory from the socket thread

PyLoaderV 0.6 API (from Syntax_Guide):
- gta.invoke(hash, *args, return_type="int") - call native by hash
- gta.entity_address(handle) - get memory address from entity handle
- gta.read_float(addr) / gta.read_int(addr) - read memory
- gta.wait(ms) - yield script (only in on_tick)
- gta.notify(msg) - show notification
- gta.log(level, msg) - write to log
- from gta import player, world, ui - high-level wrappers
"""

import json
import socket
import struct
import threading
import queue
import time
import ctypes
import os
import faulthandler
from ctypes import wintypes
from typing import Any, Callable, Optional
from datetime import datetime

# =============================================================================
# Crash Diagnostics (§7b from BUILD-BRIEF)
# =============================================================================

# Enable Python faulthandler for traceback on crash
try:
    CRASH_LOG_DIR = os.path.join(os.path.dirname(__file__), "crash_logs")
    os.makedirs(CRASH_LOG_DIR, exist_ok=True)
    _fault_log_path = os.path.join(CRASH_LOG_DIR, f"faulthandler_{os.getpid()}.log")
    _fault_log = open(_fault_log_path, "w")
    faulthandler.enable(file=_fault_log, all_threads=True)
except Exception:
    CRASH_LOG_DIR = None
    _fault_log = None

# Write-ahead log - records each operation BEFORE execution
_wal_path = os.path.join(CRASH_LOG_DIR, "last_op.jsonl") if CRASH_LOG_DIR else None
_wal_seq = 0

def wal_write(operation: dict):
    """Write-ahead log: record operation BEFORE it executes."""
    global _wal_seq
    if not _wal_path:
        return
    _wal_seq += 1
    operation["seq"] = _wal_seq
    operation["timestamp"] = datetime.now().isoformat()
    operation["pid"] = os.getpid()
    try:
        with open(_wal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(operation) + "\n")
            f.flush()
            os.fsync(f.fileno())  # Ensure write to disk before operation
    except Exception:
        pass

def wal_clear():
    """Clear the WAL after successful operation."""
    if _wal_path and os.path.exists(_wal_path):
        try:
            os.remove(_wal_path)
        except Exception:
            pass

def write_crash_report(error_msg: str, context: dict = None):
    """Write a crash report with context."""
    if not CRASH_LOG_DIR:
        return
    report = {
        "timestamp": datetime.now().isoformat(),
        "pid": os.getpid(),
        "error": error_msg,
        "context": context or {},
    }
    # Include last WAL entry
    if _wal_path and os.path.exists(_wal_path):
        try:
            with open(_wal_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    report["last_operation"] = json.loads(lines[-1])
        except Exception:
            pass

    crash_file = os.path.join(CRASH_LOG_DIR, f"crash_{os.getpid()}_{int(time.time())}.json")
    try:
        with open(crash_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass

# =============================================================================
# Configuration
# =============================================================================

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 27015
MAX_WORK_PER_TICK = 32
INTERVAL_MS = 0  # Every frame for responsive socket handling

# =============================================================================
# PyLoaderV Detection
# =============================================================================

try:
    import gta
    from gta import player, world, ui
    from gta.natives.ped import IS_PED_IN_ANY_VEHICLE, GET_VEHICLE_PED_IS_IN
    from gta.natives.player import PLAYER_PED_ID
    PYLOADER_AVAILABLE = True
except ImportError:
    PYLOADER_AVAILABLE = False
    gta = None
    player = None
    world = None
    ui = None
    IS_PED_IN_ANY_VEHICLE = None
    GET_VEHICLE_PED_IS_IN = None
    PLAYER_PED_ID = None

# =============================================================================
# Known Wheel Offsets (Legacy - from BUILD-BRIEF §3a)
# These are VERIFIED from FiveM + IKT cross-check
# =============================================================================

WHEEL_OFFSETS = {
    "y_rotation": 0x008,      # Camber
    "inv_y_rotation": 0x010,  # Inverse camber
    "x_offset": 0x030,        # Track width
    "y_offset": 0x034,        # Y offset
    "z_offset": 0x038,        # Height offset
    "tyre_radius": 0x110,     # Visual + collision size
    "rim_radius": 0x114,      # Visual rim
    "tyre_width": 0x118,      # Visual + contact
}

# =============================================================================
# Visual Wheel Offsets (from FiveM VehicleExtraNatives.cpp)
# These control VISUAL wheel rendering, separate from CWheel physics
# =============================================================================

# FiveM patterns for finding these dynamically (for future pattern scanner)
FIVEM_PATTERNS = {
    # Pattern: "44 0F 2F 43 48 45 8D" -> DrawHandlerPtrOffset at +4 (uint8)
    "draw_handler": b"\x44\x0F\x2F\x43\x48\x45\x8D",
    # Pattern: "4C 8D 48 ? 80 E1 01" -> StreamRenderGfxPtrOffset at -4 (uint32)
    "stream_render_gfx": b"\x4C\x8D\x48",  # ? byte, then 80 E1 01
    # Pattern: "48 89 01 B8 00 00 80 3F 66 44 89 51" -> size at +20, width at +23
    "wheel_visual": b"\x48\x89\x01\xB8\x00\x00\x80\x3F\x66\x44\x89\x51",
    # Pattern: "E8 ? ? ? ? 48 63 87 ? ? ? ?" -> WheelsPtrOffset at +15
    "wheels_ptr": b"\xE8",  # Plus call displacement + 48 63 87
}

# Runtime-discovered offsets (filled by pattern scan or manual discovery)
_visual_wheel_offsets = {
    "draw_handler_ptr": None,       # vehicle + this -> DrawHandler
    "stream_render_gfx_ptr": None,  # DrawHandler + this -> StreamRenderGfx
    "wheel_size": None,             # StreamRenderGfx + this -> wheel size float
    "wheel_width": None,            # StreamRenderGfx + this -> wheel width float
}

# Field name aliases for user convenience
FIELD_ALIASES = {
    "camber": "y_rotation",
    "track_width": "x_offset",
    "radius": "tyre_radius",
    "size": "tyre_radius",
    "width": "tyre_width",
    "height": "z_offset",
}

# Wheel array offset candidates (will probe these)
# Different game versions use different offsets
WHEEL_ARRAY_OFFSETS = [
    # Found for game version 101 (Legacy)
    0xC30,
    # Common Legacy offsets
    0xC18, 0xC20, 0xC28, 0xC38, 0xC40, 0xC48, 0xC50,
    # Older versions
    0xB48, 0xB50, 0xB58, 0xB60, 0xB68, 0xB70, 0xB78, 0xB80,
    # Extended range
    0xC58, 0xC60, 0xC68, 0xC70, 0xC78, 0xC80, 0xC88, 0xC90,
    0xC98, 0xCA0, 0xCA8, 0xCB0, 0xCB8, 0xCC0, 0xCC8, 0xCD0,
    # Even more
    0xAE0, 0xAE8, 0xAF0, 0xAF8, 0xB00, 0xB08, 0xB10, 0xB18,
    0xB20, 0xB28, 0xB30, 0xB38, 0xB40,
]

# =============================================================================
# Memory Access (using ctypes for write, gta module for read when available)
# =============================================================================

# Windows API setup for VirtualQuery validation
kernel32 = ctypes.windll.kernel32

# Configure RtlMoveMemory for writing
_RtlMoveMemory = kernel32.RtlMoveMemory
_RtlMoveMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
_RtlMoveMemory.restype = None

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
READABLE_PROTECTIONS = 0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80
WRITABLE_PROTECTIONS = 0x04 | 0x08 | 0x40 | 0x80

def is_valid_address(address: int, size: int = 4, check_writable: bool = False) -> bool:
    """Validate memory region before access using VirtualQuery."""
    if address == 0 or size <= 0:
        return False

    required = WRITABLE_PROTECTIONS if check_writable else READABLE_PROTECTIONS
    mbi = MEMORY_BASIC_INFORMATION()

    result = kernel32.VirtualQuery(
        ctypes.c_void_p(address),
        ctypes.byref(mbi),
        ctypes.sizeof(mbi)
    )

    if result == 0:
        return False
    if mbi.State != MEM_COMMIT:
        return False
    if mbi.Protect & PAGE_GUARD:
        return False
    if not (mbi.Protect & required):
        return False
    # Ensure the WHOLE [address, address+size) span stays within this committed region,
    # so a read/write near a region edge can't spill into an adjacent unmapped page.
    region_base = mbi.BaseAddress or 0
    region_end = region_base + (mbi.RegionSize or 0)
    if address + size > region_end:
        return False

    return True

def read_float(addr: int) -> Optional[float]:
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
    return None

def read_ptr(addr: int) -> Optional[int]:
    """Read a 64-bit pointer from memory."""
    if not is_valid_address(addr, 8):
        return None
    try:
        return ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint64)).contents.value
    except:
        return None

def read_bytes(addr: int, size: int) -> Optional[bytes]:
    """Read raw bytes from memory."""
    if not is_valid_address(addr, size):
        return None
    try:
        buffer = (ctypes.c_ubyte * size)()
        ctypes.memmove(buffer, addr, size)
        return bytes(buffer)
    except:
        return None

def _write_raw(addr: int, data: bytes) -> bool:
    """
    Write `data` to `addr` safely: validate the region, temporarily unprotect ONLY if
    needed, write, then RESTORE the original page protection. Returns True on success.

    This is the single write path. Validate-before-deref avoids the in-process access
    violations that hard-crash GTA (a Python try/except cannot catch a hardware AV).
    """
    n = len(data)
    if addr == 0 or n <= 0:
        return False

    changed = False
    prev = ctypes.c_ulong(0)
    if not is_valid_address(addr, n, check_writable=True):
        # Not writable as-is: try to flip it RW (remember old protection to restore)
        if not kernel32.VirtualProtect(ctypes.c_void_p(addr), n, 0x40, ctypes.byref(prev)):
            return False
        changed = True
        # Re-validate after the protection change; if still bad, restore and bail.
        if not is_valid_address(addr, n, check_writable=True):
            tmp = ctypes.c_ulong(0)
            kernel32.VirtualProtect(ctypes.c_void_p(addr), n, prev.value, ctypes.byref(tmp))
            return False
    try:
        buf = ctypes.create_string_buffer(bytes(data), n)
        _RtlMoveMemory(ctypes.c_void_p(addr), buf, n)
        return True
    except Exception as e:
        log_message("error", f"_write_raw failed @0x{addr:X}: {e}")
        return False
    finally:
        if changed:
            tmp = ctypes.c_ulong(0)
            kernel32.VirtualProtect(ctypes.c_void_p(addr), n, prev.value, ctypes.byref(tmp))

def write_float(addr: int, value: float, wal: bool = True) -> Optional[float]:
    """Write a float to memory, return old value (or None on failure)."""
    if not is_valid_address(addr, 4):
        log_message("error", f"write_float: invalid address 0x{addr:X}")
        return None
    if wal:
        wal_write({"op": "write_float", "address": f"0x{addr:X}", "value": value})
    try:
        old = ctypes.cast(addr, ctypes.POINTER(ctypes.c_float)).contents.value
    except Exception as e:
        log_message("error", f"write_float read-old failed @0x{addr:X}: {e}")
        return None
    if not _write_raw(addr, struct.pack('<f', float(value))):
        return None
    return old

def write_int(addr: int, value: int, wal: bool = True) -> Optional[int]:
    """Write a 32-bit int to memory, return old value (or None on failure)."""
    if not is_valid_address(addr, 4):
        log_message("error", f"write_int: invalid address 0x{addr:X}")
        return None
    if wal:
        wal_write({"op": "write_int", "address": f"0x{addr:X}", "value": value})
    try:
        old = ctypes.cast(addr, ctypes.POINTER(ctypes.c_int32)).contents.value
    except Exception as e:
        log_message("error", f"write_int read-old failed @0x{addr:X}: {e}")
        return None
    if not _write_raw(addr, struct.pack('<i', int(value))):
        return None
    return old

# =============================================================================
# Vehicle/Wheel Access
# =============================================================================

def get_player_vehicle_handle() -> Optional[int]:
    """Get the player's current vehicle handle."""
    if not PYLOADER_AVAILABLE:
        return None
    try:
        # Get player ped handle
        ped_handle = PLAYER_PED_ID()
        if not ped_handle:
            return None

        # Check if in vehicle
        if not IS_PED_IN_ANY_VEHICLE(ped_handle, False):
            return None

        # Get vehicle handle
        veh_handle = GET_VEHICLE_PED_IS_IN(ped_handle, False)
        return veh_handle if veh_handle and veh_handle != 0 else None
    except Exception as e:
        log_message("error", f"get_player_vehicle_handle: {e}")
        return None

def get_vehicle_address(handle: int) -> Optional[int]:
    """Get the memory address of a vehicle from its handle."""
    if not PYLOADER_AVAILABLE or not gta:
        return None
    try:
        addr = gta.entity_address(handle)
        return addr if addr and addr != 0 else None
    except Exception as e:
        log_message("error", f"get_vehicle_address: {e}")
        return None

def get_wheels_array(vehicle_address: int) -> Optional[tuple[int, int]]:
    """
    Get the wheels pointer array and count from a vehicle.
    Returns (wheels_ptr, wheel_count) or None.
    """
    if vehicle_address == 0:
        return None

    # Try known wheel array offsets
    for offset in WHEEL_ARRAY_OFFSETS:
        wheels_ptr = read_ptr(vehicle_address + offset)
        if wheels_ptr and wheels_ptr != 0 and is_valid_address(wheels_ptr, 32):
            # Validate first wheel pointer
            wheel0 = read_ptr(wheels_ptr)
            if wheel0 and wheel0 != 0 and is_valid_address(wheel0, 0x120):
                # Count valid wheel pointers
                count = 0
                for i in range(8):
                    wp = read_ptr(wheels_ptr + i * 8)
                    if wp and wp != 0 and is_valid_address(wp, 0x20):
                        count += 1
                    else:
                        break
                if count > 0:
                    log_message("debug", f"Found wheels at vehicle+0x{offset:X}: {count} wheels")
                    return (wheels_ptr, count)

    return None

# =============================================================================
# Logging
# =============================================================================

def log_message(level: str, message: str):
    """Log a message (to PyLoaderV log or stdout)."""
    if PYLOADER_AVAILABLE and gta:
        gta.log(level, f"[MCP Bridge] {message}")
    else:
        print(f"[{level.upper()}] [MCP Bridge] {message}")

def show_notification(message: str):
    """Show an in-game notification."""
    if PYLOADER_AVAILABLE and gta:
        gta.notify(message)
    else:
        print(f"[NOTIFY] {message}")

# =============================================================================
# Work Queue (thread-safe handoff from socket to game tick)
# =============================================================================

_work_queue: queue.Queue = queue.Queue()

class Deferred:
    """
    Returned by a handler that must complete across MULTIPLE game ticks WITHOUT
    blocking the game thread (e.g. waiting for a vehicle model to stream in).

    poll() runs once per frame ON THE GAME THREAD (no sleep):
      - return None  -> still pending; check again next frame
      - return dict  -> done; that dict becomes the command result
    The socket thread keeps waiting on the WorkItem (off the game thread), so the
    request stays synchronous to the caller while the game NEVER freezes.
    """

    def __init__(self, poll: Callable, timeout: float = 8.0):
        self.poll = poll
        self.deadline = time.time() + timeout

# Deferred ops in flight - advanced one poll-per-frame by _process_deferred()
_pending_deferred: list = []

class WorkItem:
    """A unit of work to execute on the game thread."""

    def __init__(self, func: Callable, args: tuple = (), kwargs: dict = None):
        self.func = func
        self.args = args
        self.kwargs = kwargs or {}
        self.result: Any = None
        self.error: Optional[Exception] = None
        self.done = threading.Event()

    def execute(self):
        """Execute on game thread. If the handler returns a Deferred, hand it to the
        tick loop to finish over future frames instead of blocking the game thread here."""
        try:
            res = self.func(*self.args, **self.kwargs)
        except Exception as e:
            self.error = e
            log_message("error", f"WorkItem error: {e}")
            self.done.set()
            return
        if isinstance(res, Deferred):
            _pending_deferred.append((self, res))  # completed later by _process_deferred()
            return
        self.result = res
        self.done.set()

    def wait(self, timeout: float = 20.0) -> tuple[Any, Optional[Exception]]:
        """Wait for completion and return result."""
        self.done.wait(timeout)
        return self.result, self.error

def _process_deferred():
    """Advance every in-flight Deferred by ONE non-blocking poll. Game thread only."""
    if not _pending_deferred:
        return
    now = time.time()
    still = []
    for item, d in _pending_deferred:
        try:
            res = d.poll()
        except Exception as e:
            item.error = e
            item.done.set()
            continue
        if res is not None:
            item.result = res
            item.done.set()
        elif now > d.deadline:
            item.result = {"error": "timeout waiting for deferred op (model load?)"}
            item.done.set()
        else:
            still.append((item, d))
    _pending_deferred[:] = still

def enqueue_work(func: Callable, *args, **kwargs) -> WorkItem:
    """Enqueue work for the game thread."""
    item = WorkItem(func, args, kwargs)
    _work_queue.put(item)
    return item

# =============================================================================
# Command Handlers
# =============================================================================

_overlay_text = ""
_overlay_state = "idle"
_snapshots: dict[str, dict] = {}
_undo_stack: list[dict] = []

# Continuous write storage - applied every frame
_continuous_writes: dict[str, dict] = {}  # key -> {wheel_index, field, value}

# In-game chat system
_chat_history: list[dict] = []  # {sender: "claude"|"user", text: str, timestamp: float}
_pending_user_input: threading.Event = threading.Event()
_user_input_result: str = ""
_keyboard_active: bool = False
_user_message_queue: list[dict] = []  # Messages from user waiting for Claude to read

# File-based chat input (workaround for PyLoaderV keyboard limitation)
CHAT_INPUT_FILE = os.path.join(os.path.dirname(__file__), "chat_input.txt")
_last_chat_file_mtime = 0

def handle_status(params: dict) -> dict:
    """Handle status command."""
    vehicle_handle = get_player_vehicle_handle()
    in_vehicle = vehicle_handle is not None

    result = {
        "connected": True,
        "pyloader_available": PYLOADER_AVAILABLE,
        "in_vehicle": in_vehicle,
        "vehicle_handle": vehicle_handle,
        "wheel_offsets": WHEEL_OFFSETS,
    }

    if PYLOADER_AVAILABLE and gta:
        try:
            result["game_version"] = gta.game_version()
        except:
            pass

    return result

def handle_read(params: dict) -> dict:
    """Handle memory read command."""
    address = params.get("address", "0")
    type_name = params.get("type", "float")
    count = params.get("count", 1)

    # Parse address
    try:
        if isinstance(address, str):
            addr = int(address, 16) if address.startswith("0x") else int(address)
        else:
            addr = int(address)
    except ValueError:
        return {"error": f"Invalid address: {address}"}

    values = []
    type_sizes = {"float": 4, "int32": 4, "int": 4, "ptr": 8, "uint64": 8}
    size = type_sizes.get(type_name, 4)

    for i in range(count):
        current_addr = addr + i * size
        if type_name in ("float",):
            val = read_float(current_addr)
        elif type_name in ("int32", "int"):
            val = read_int(current_addr)
        elif type_name in ("ptr", "uint64"):
            val = read_ptr(current_addr)
        else:
            val = read_int(current_addr)

        if val is None:
            return {"error": f"Failed to read at 0x{current_addr:X}"}
        values.append(val)

    return {
        "success": True,
        "address": f"0x{addr:X}",
        "type": type_name,
        "values": values if count > 1 else values[0]
    }

def handle_write(params: dict) -> dict:
    """Handle memory write command."""
    address = params.get("address", "0")
    value = params.get("value")
    type_name = params.get("type", "float")

    try:
        if isinstance(address, str):
            addr = int(address, 16) if address.startswith("0x") else int(address)
        else:
            addr = int(address)
    except ValueError:
        return {"error": f"Invalid address: {address}"}

    if type_name == "float":
        old_value = write_float(addr, float(value))
    else:
        old_value = write_int(addr, int(value))

    if old_value is None:
        return {"error": f"Failed to write at 0x{addr:X}"}

    # Track for undo
    _undo_stack.append({
        "address": f"0x{addr:X}",
        "old_value": old_value,
        "type": type_name,
        "timestamp": time.time()
    })

    return {
        "success": True,
        "address": f"0x{addr:X}",
        "old_value": old_value,
        "new_value": value
    }

def handle_snapshot(params: dict) -> dict:
    """Handle memory snapshot command."""
    address = params.get("address", "0")
    size = params.get("size", 256)
    label = params.get("label", f"snap_{len(_snapshots)}")

    try:
        if isinstance(address, str):
            addr = int(address, 16) if address.startswith("0x") else int(address)
        else:
            addr = int(address)
    except ValueError:
        return {"error": f"Invalid address: {address}"}

    data = read_bytes(addr, size)
    if data is None:
        return {"error": f"Failed to read {size} bytes at 0x{addr:X}"}

    _snapshots[label] = {
        "address": addr,
        "data": data.hex(),
        "timestamp": time.time()
    }

    return {
        "success": True,
        "label": label,
        "address": addr,
        "size": len(data),
        "data": data.hex()
    }

def handle_is_in_vehicle(params: dict) -> dict:
    """Check if player is in a vehicle."""
    handle = get_player_vehicle_handle()
    return {
        "in_vehicle": handle is not None,
        "vehicle_handle": handle
    }

def handle_get_vehicle_info(params: dict) -> dict:
    """Get detailed vehicle info."""
    handle = get_player_vehicle_handle()
    if handle is None:
        return {"error": "Not in a vehicle"}

    addr = get_vehicle_address(handle)

    result = {
        "handle": handle,
        "address": f"0x{addr:X}" if addr else None,
    }

    if addr:
        wheels_info = get_wheels_array(addr)
        if wheels_info:
            wheels_ptr, wheel_count = wheels_info
            result["wheels_ptr"] = f"0x{wheels_ptr:X}"
            result["wheel_count"] = wheel_count

            # Read first wheel's values as sample
            wheel0_ptr = read_ptr(wheels_ptr)
            if wheel0_ptr:
                result["wheel_0_address"] = f"0x{wheel0_ptr:X}"
                result["wheel_0_values"] = {}
                for name, offset in WHEEL_OFFSETS.items():
                    val = read_float(wheel0_ptr + offset)
                    if val is not None:
                        result["wheel_0_values"][name] = val

    return result

def handle_get_wheel_values(params: dict) -> dict:
    """Read values from a specific wheel."""
    wheel_index = params.get("wheel_index", 0)

    handle = get_player_vehicle_handle()
    if handle is None:
        return {"error": "Not in a vehicle"}

    addr = get_vehicle_address(handle)
    if addr is None:
        return {"error": "Could not get vehicle address"}

    wheels_info = get_wheels_array(addr)
    if wheels_info is None:
        return {"error": "Could not find wheels array"}

    wheels_ptr, wheel_count = wheels_info
    if wheel_index >= wheel_count:
        return {"error": f"Wheel index {wheel_index} out of range (max {wheel_count - 1})"}

    wheel_ptr = read_ptr(wheels_ptr + wheel_index * 8)
    if wheel_ptr is None:
        return {"error": f"Could not read wheel {wheel_index} pointer"}

    values = {}
    for name, offset in WHEEL_OFFSETS.items():
        val = read_float(wheel_ptr + offset)
        values[name] = val

    return {
        "success": True,
        "wheel_index": wheel_index,
        "wheel_address": f"0x{wheel_ptr:X}",
        "values": values
    }

def handle_set_wheel_value(params: dict) -> dict:
    """Set a wheel field value."""
    wheel_index = params.get("wheel_index", 0)
    field = params.get("field", "")
    value = params.get("value", 0.0)

    # Resolve field alias
    field = FIELD_ALIASES.get(field, field)
    if field not in WHEEL_OFFSETS:
        return {"error": f"Unknown field: {field}. Valid: {list(WHEEL_OFFSETS.keys())}"}

    handle = get_player_vehicle_handle()
    if handle is None:
        return {"error": "Not in a vehicle"}

    addr = get_vehicle_address(handle)
    if addr is None:
        return {"error": "Could not get vehicle address"}

    wheels_info = get_wheels_array(addr)
    if wheels_info is None:
        return {"error": "Could not find wheels array"}

    wheels_ptr, wheel_count = wheels_info
    if wheel_index >= wheel_count:
        return {"error": f"Wheel index {wheel_index} out of range"}

    wheel_ptr = read_ptr(wheels_ptr + wheel_index * 8)
    if wheel_ptr is None:
        return {"error": f"Could not read wheel {wheel_index} pointer"}

    offset = WHEEL_OFFSETS[field]
    old_value = write_float(wheel_ptr + offset, float(value))

    if old_value is None:
        return {"error": f"Failed to write to wheel {wheel_index} + 0x{offset:X}"}

    # Handle inverse Y rotation for camber
    if field == "y_rotation":
        write_float(wheel_ptr + WHEEL_OFFSETS["inv_y_rotation"], -float(value))

    # Track for undo
    _undo_stack.append({
        "address": f"wheel[{wheel_index}]+{field}",
        "old_value": old_value,
        "type": "wheel_field",
        "wheel_index": wheel_index,
        "field": field,
        "timestamp": time.time()
    })

    return {
        "success": True,
        "wheel_index": wheel_index,
        "field": field,
        "old_value": old_value,
        "new_value": value
    }

def handle_set_overlay(params: dict) -> dict:
    """Set overlay text."""
    global _overlay_text, _overlay_state
    _overlay_text = params.get("text", "")
    _overlay_state = params.get("state", "idle")

    # Show via notification
    show_notification(_overlay_text)

    return {"success": True}

def handle_chat_post(params: dict) -> dict:
    """Post message to chat."""
    message = params.get("message", "")
    _chat_history.append({
        "sender": "claude",
        "text": message,
        "timestamp": time.time()
    })
    # Only show notification for status messages (yellow ~y~ prefix)
    # Regular chat messages go to panel only
    if message.startswith("~y~"):
        show_notification(message)
    return {"success": True}

def _pop_user_message():
    """Pop the oldest queued user message text, or None. Tolerates 'message'/'text' keys."""
    if not _user_message_queue:
        return None
    msg = _user_message_queue.pop(0)
    return msg.get("message") or msg.get("text") or ""

def handle_await_user_message(params: dict) -> dict:
    """
    Wait (across frames, NON-blocking) for the user to send an in-game chat message.
    Returns immediately if one is already queued. Uses Deferred so the game never freezes
    while waiting on human typing time.
    """
    timeout_ms = params.get("timeout_ms", 60000)

    existing = _pop_user_message()
    if existing is not None:
        return {"success": True, "message": existing, "from_queue": True}

    def poll():
        m = _pop_user_message()
        return {"success": True, "message": m} if m is not None else None

    return Deferred(poll, timeout=timeout_ms / 1000.0)

def handle_get_pending_messages(params: dict) -> dict:
    """
    Get all pending messages from the user (non-blocking).

    Use this to check if the user has sent any messages without blocking.
    Returns an empty list if no messages pending.
    """
    messages = list(_user_message_queue)
    _user_message_queue.clear()
    return {
        "success": True,
        "messages": messages,
        "count": len(messages)
    }

def handle_has_pending_messages(params: dict) -> dict:
    """
    Check if there are any pending messages from the user (non-blocking).
    """
    return {
        "has_messages": len(_user_message_queue) > 0,
        "count": len(_user_message_queue)
    }

def handle_reset_keyboard(params: dict) -> dict:
    """Reset the keyboard state if it gets stuck."""
    global _keyboard_active, _keyboard_shown
    _keyboard_active = False
    _keyboard_shown = False
    _pending_user_input.set()  # Unblock any waiting await
    return {"success": True, "message": "Keyboard state reset"}

def handle_get_chat_history(params: dict) -> dict:
    """Get recent chat history."""
    limit = params.get("limit", 20)
    return {
        "success": True,
        "history": _chat_history[-limit:]
    }

def handle_get_crash_logs(params: dict) -> dict:
    """Get recent crash logs."""
    if not CRASH_LOG_DIR or not os.path.exists(CRASH_LOG_DIR):
        return {"error": "No crash log directory"}

    logs = []
    for filename in os.listdir(CRASH_LOG_DIR):
        if filename.startswith("crash_") and filename.endswith(".json"):
            path = os.path.join(CRASH_LOG_DIR, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    logs.append(json.load(f))
            except Exception:
                pass

    return {
        "success": True,
        "crash_logs": sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]
    }

def handle_mark(params: dict) -> dict:
    """Mark/capture current state."""
    label = params.get("label", "mark")

    handle = get_player_vehicle_handle()
    if handle is None:
        return {"success": True, "label": label, "in_vehicle": False}

    addr = get_vehicle_address(handle)
    snapshots = {}

    if addr:
        wheels_info = get_wheels_array(addr)
        if wheels_info:
            wheels_ptr, wheel_count = wheels_info
            for i in range(wheel_count):
                wheel_ptr = read_ptr(wheels_ptr + i * 8)
                if wheel_ptr:
                    data = read_bytes(wheel_ptr, 0x120)
                    if data:
                        snapshots[f"wheel_{i}"] = {
                            "address": wheel_ptr,
                            "data": data.hex()
                        }

    return {
        "success": True,
        "label": label,
        "vehicle_address": f"0x{addr:X}" if addr else None,
        "snapshots": snapshots
    }

def handle_watch(params: dict) -> dict:
    """Watch addresses over time."""
    addresses = params.get("addresses", [])
    duration_ms = params.get("duration_ms", 2000)
    interval_ms = params.get("interval_ms", 100)

    # Parse addresses
    addrs = []
    for a in addresses:
        try:
            if isinstance(a, str):
                addrs.append(int(a, 16) if a.startswith("0x") else int(a))
            else:
                addrs.append(int(a))
        except ValueError:
            return {"error": f"Invalid address: {a}"}

    # Sample across frames via Deferred - NO sleep on the game thread (no freeze).
    samples = {addr: [] for addr in addrs}
    start = time.time()
    end = start + duration_ms / 1000.0
    state = {"next": start}

    def poll():
        now = time.time()
        if now >= state["next"]:
            for addr in addrs:
                val = read_float(addr)
                if val is not None:
                    samples[addr].append(val)
            state["next"] = now + interval_ms / 1000.0
        if now < end:
            return None  # keep sampling next frame
        # Done - build digest
        digest = {}
        for addr in addrs:
            vals = samples[addr]
            if vals:
                digest[f"0x{addr:X}"] = {
                    "samples": len(vals), "min": min(vals), "max": max(vals),
                    "first": vals[0], "last": vals[-1], "delta": vals[-1] - vals[0],
                    "changed": len(set(vals)) > 1,
                }
        return {"success": True, "duration_s": time.time() - start, "digest": digest}

    return Deferred(poll, timeout=(duration_ms / 1000.0) + 5.0)

def handle_revert_last(params: dict) -> dict:
    """Revert the last write."""
    if not _undo_stack:
        return {"error": "No writes to revert"}

    last = _undo_stack.pop()

    if last.get("type") == "wheel_field":
        # Revert wheel field write
        result = handle_set_wheel_value({
            "wheel_index": last["wheel_index"],
            "field": last["field"],
            "value": last["old_value"]
        })
        result["reverted"] = last
        return result
    else:
        # Revert direct memory write
        result = handle_write({
            "address": last["address"],
            "value": last["old_value"],
            "type": last.get("type", "float")
        })
        result["reverted"] = last
        return result

def handle_list_snapshots(params: dict) -> dict:
    """List all stored snapshots."""
    return {
        "snapshots": [
            {"label": k, "address": f"0x{v['address']:X}", "size": len(v['data'])//2}
            for k, v in _snapshots.items()
        ]
    }

def handle_get_undo_stack(params: dict) -> dict:
    """Get undo stack."""
    return {
        "undo_stack": _undo_stack[-10:],
        "total": len(_undo_stack)
    }

def handle_set_continuous(params: dict) -> dict:
    """Set a wheel value to be applied continuously (every frame)."""
    wheel_index = params.get("wheel_index", 0)
    field = params.get("field", "")
    value = params.get("value", 0.0)
    enabled = params.get("enabled", True)

    # Resolve field alias
    field = FIELD_ALIASES.get(field, field)
    if field not in WHEEL_OFFSETS:
        return {"error": f"Unknown field: {field}"}

    key = f"wheel_{wheel_index}_{field}"

    if enabled:
        _continuous_writes[key] = {
            "wheel_index": wheel_index,
            "field": field,
            "value": float(value),
            "vehicle_handle": get_player_vehicle_handle(),  # stamp: only apply to THIS car
        }
        return {"success": True, "key": key, "enabled": True, "value": value}
    else:
        if key in _continuous_writes:
            del _continuous_writes[key]
        return {"success": True, "key": key, "enabled": False}

def handle_clear_continuous(params: dict) -> dict:
    """Clear all continuous writes."""
    count = len(_continuous_writes)
    _continuous_writes.clear()
    return {"success": True, "cleared": count}

def handle_list_continuous(params: dict) -> dict:
    """List all active continuous writes."""
    return {"continuous_writes": list(_continuous_writes.values())}

# Per-frame wheel-resolution cache: avoid re-brute-scanning the wheels array every frame.
_wheels_cache = {"handle": None, "wheels_ptr": None, "wheel_count": 0}

def _resolve_wheels_cached():
    """Return (wheels_ptr, wheel_count) for the current vehicle, caching per handle."""
    handle = get_player_vehicle_handle()
    if handle is None:
        _wheels_cache["handle"] = None
        return None, None
    if handle == _wheels_cache["handle"] and _wheels_cache["wheels_ptr"]:
        return _wheels_cache["wheels_ptr"], _wheels_cache["wheel_count"]
    addr = get_vehicle_address(handle)
    info = get_wheels_array(addr) if addr else None
    if not info:
        _wheels_cache.update({"handle": handle, "wheels_ptr": None, "wheel_count": 0})
        return None, None
    _wheels_cache.update({"handle": handle, "wheels_ptr": info[0], "wheel_count": info[1]})
    return info[0], info[1]

def apply_continuous_writes():
    """Apply all continuous writes - called every frame (game thread). No WAL/fsync here."""
    if not _continuous_writes:
        return

    handle = get_player_vehicle_handle()
    if handle is None:
        return

    wheels_ptr, wheel_count = _resolve_wheels_cached()
    if not wheels_ptr:
        return

    for key, data in _continuous_writes.items():
        # Stale-handle guard: only re-assert on the vehicle the write was created for.
        if data.get("vehicle_handle") not in (None, handle):
            continue
        wheel_index = data["wheel_index"]
        field = data["field"]
        value = data["value"]

        if wheel_index >= wheel_count:
            continue

        wheel_ptr = read_ptr(wheels_ptr + wheel_index * 8)
        if wheel_ptr is None:
            continue

        offset = WHEEL_OFFSETS[field]
        write_float(wheel_ptr + offset, value, wal=False)  # per-frame: no WAL/fsync storm

        # Handle inverse for camber
        if field == "y_rotation":
            write_float(wheel_ptr + WHEEL_OFFSETS["inv_y_rotation"], -value, wal=False)

def handle_probe_wheels(params: dict) -> dict:
    """Probe vehicle memory to find wheel array offset."""
    handle = get_player_vehicle_handle()
    if handle is None:
        return {"error": "Not in a vehicle"}

    addr = get_vehicle_address(handle)
    if addr is None:
        return {"error": "Could not get vehicle address"}

    # Probe a wide range of offsets looking for pointer arrays
    candidates = []

    for offset in range(0xA00, 0xE00, 0x8):
        ptr = read_ptr(addr + offset)
        if ptr and ptr != 0 and is_valid_address(ptr, 8):
            # Check if this looks like a wheel array (4+ valid pointers)
            wheel_count = 0
            for i in range(8):
                wp = read_ptr(ptr + i * 8)
                if wp and wp != 0 and is_valid_address(wp, 0x20):
                    # Check if first float at wp+8 is in camber range (-1 to 1)
                    val = read_float(wp + 0x8)
                    if val is not None and -1.0 <= val <= 1.0:
                        wheel_count += 1
                else:
                    break

            if wheel_count >= 2:
                candidates.append({
                    "offset": f"0x{offset:X}",
                    "ptr": f"0x{ptr:X}",
                    "wheel_count": wheel_count
                })

    return {
        "vehicle_address": f"0x{addr:X}",
        "candidates": candidates
    }

def handle_probe_drawhandler(params: dict) -> dict:
    """
    Probe vehicle memory looking for DrawHandler -> StreamRenderGfx chain.
    FiveM uses this path for visual wheel size/width.
    """
    handle = get_player_vehicle_handle()
    if handle is None:
        return {"error": "Not in a vehicle"}

    addr = get_vehicle_address(handle)
    if addr is None:
        return {"error": "Could not get vehicle address"}

    candidates = []

    # Scan vehicle offsets 0x20-0x200 for potential DrawHandler pointer
    for veh_offset in range(0x20, 0x200, 0x8):
        ptr1 = read_ptr(addr + veh_offset)
        if not ptr1 or ptr1 == 0 or not is_valid_address(ptr1, 0x40):
            continue

        # Try offsets 0x8, 0x10, 0x18, 0x20, 0x28 for StreamRenderGfx
        for ptr2_offset in [0x8, 0x10, 0x18, 0x20, 0x28, 0x30]:
            ptr2 = read_ptr(ptr1 + ptr2_offset)
            if not ptr2 or ptr2 == 0 or not is_valid_address(ptr2, 0x100):
                continue

            # Scan for float values in wheel dimension range (0.1 - 2.0)
            wheel_floats = []
            for float_offset in range(0, 0x100, 4):
                val = read_float(ptr2 + float_offset)
                if val is not None and 0.1 < val < 2.0:
                    wheel_floats.append({
                        "offset": f"0x{float_offset:X}",
                        "value": round(val, 4)
                    })

            if len(wheel_floats) >= 2:  # At least 2 wheel-like values
                candidates.append({
                    "veh_offset": f"0x{veh_offset:X}",
                    "ptr1": f"0x{ptr1:X}",
                    "ptr2_offset": f"0x{ptr2_offset:X}",
                    "ptr2": f"0x{ptr2:X}",
                    "floats": wheel_floats[:10]  # First 10
                })

    return {
        "vehicle_address": f"0x{addr:X}",
        "candidates": candidates[:20]  # Limit results
    }

def handle_find_wheel_visual_offsets(params: dict) -> dict:
    """
    Try to find visual wheel size/width by comparing CWheel values with
    values in potential StreamRenderGfx structures.

    The FiveM approach stores visual wheel size/width in StreamRenderGfx.
    We look for floats that match or are close to tyre_radius/tyre_width.
    """
    handle = get_player_vehicle_handle()
    if handle is None:
        return {"error": "Not in a vehicle"}

    addr = get_vehicle_address(handle)
    if addr is None:
        return {"error": "Could not get vehicle address"}

    # First, get the CWheel values for reference
    wheels_info = get_wheels_array(addr)
    if wheels_info is None:
        return {"error": "Could not find wheels array"}

    wheels_ptr, wheel_count = wheels_info
    wheel0_ptr = read_ptr(wheels_ptr)
    if wheel0_ptr is None:
        return {"error": "Could not read wheel 0 pointer"}

    # Get wheel dimensions from CWheel
    tyre_radius = read_float(wheel0_ptr + WHEEL_OFFSETS["tyre_radius"])
    rim_radius = read_float(wheel0_ptr + WHEEL_OFFSETS["rim_radius"])
    tyre_width = read_float(wheel0_ptr + WHEEL_OFFSETS["tyre_width"])

    reference = {
        "tyre_radius": tyre_radius,
        "rim_radius": rim_radius,
        "tyre_width": tyre_width,
    }

    # Now search for these values in potential StreamRenderGfx structures
    matches = []

    # Known VStancer/FiveM DrawHandler offsets to try
    draw_handler_offsets = [0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78, 0x80, 0xA8, 0xB0]
    srg_offsets = [0x8, 0x18, 0x20, 0x28]

    for dh_offset in draw_handler_offsets:
        ptr1 = read_ptr(addr + dh_offset)
        if not ptr1 or ptr1 == 0 or not is_valid_address(ptr1, 0x40):
            continue

        for srg_offset in srg_offsets:
            ptr2 = read_ptr(ptr1 + srg_offset)
            if not ptr2 or ptr2 == 0 or not is_valid_address(ptr2, 0x200):
                continue

            # Scan for floats matching our wheel dimensions
            found_values = []
            for offset in range(0, 0x200, 4):
                val = read_float(ptr2 + offset)
                if val is None:
                    continue

                # Check if this matches any wheel dimension (within 0.01 tolerance)
                for name, ref_val in reference.items():
                    if ref_val is not None and abs(val - ref_val) < 0.01:
                        found_values.append({
                            "offset": f"0x{offset:X}",
                            "value": round(val, 6),
                            "matches": name,
                            "ref_value": round(ref_val, 6)
                        })

            if found_values:
                matches.append({
                    "dh_offset": f"0x{dh_offset:X}",
                    "srg_offset": f"0x{srg_offset:X}",
                    "ptr1": f"0x{ptr1:X}",
                    "ptr2": f"0x{ptr2:X}",
                    "found": found_values
                })

    return {
        "vehicle_address": f"0x{addr:X}",
        "reference_values": {k: round(v, 6) if v else None for k, v in reference.items()},
        "matches": matches
    }

def handle_scan_structure(params: dict) -> dict:
    """
    Scan a memory address for float values (for finding visual wheel offsets).
    """
    address = params.get("address", "0")
    size = params.get("size", 0x200)
    min_val = params.get("min", 0.0)
    max_val = params.get("max", 10.0)

    try:
        if isinstance(address, str):
            addr = int(address, 16) if address.startswith("0x") else int(address)
        else:
            addr = int(address)
    except ValueError:
        return {"error": f"Invalid address: {address}"}

    if not is_valid_address(addr, size):
        return {"error": f"Invalid address range: 0x{addr:X}"}

    floats = []
    for offset in range(0, size, 4):
        val = read_float(addr + offset)
        if val is not None:
            if min_val <= val <= max_val:
                floats.append({
                    "offset": f"0x{offset:X}",
                    "value": round(val, 6)
                })

    return {
        "address": f"0x{addr:X}",
        "size": size,
        "floats_in_range": floats
    }

def handle_write_visual_wheel(params: dict) -> dict:
    """
    Try to write to visual wheel size/width via the DrawHandler->StreamRenderGfx path.

    CAUTION: This can crash if the offsets are wrong. Use find_wheel_visual_offsets first.

    Params:
        veh_offset: Offset from vehicle to DrawHandler (e.g., 0x48)
        srg_offset: Offset from DrawHandler to StreamRenderGfx (e.g., 0x8)
        value_offset: Offset in StreamRenderGfx for the value (e.g., 0x2C)
        value: Float value to write
    """
    veh_offset = params.get("veh_offset", 0)
    srg_offset = params.get("srg_offset", 0x8)
    value_offset = params.get("value_offset", 0)
    value = params.get("value", 1.0)

    # Parse offsets if strings
    if isinstance(veh_offset, str):
        veh_offset = int(veh_offset, 16) if veh_offset.startswith("0x") else int(veh_offset)
    if isinstance(srg_offset, str):
        srg_offset = int(srg_offset, 16) if srg_offset.startswith("0x") else int(srg_offset)
    if isinstance(value_offset, str):
        value_offset = int(value_offset, 16) if value_offset.startswith("0x") else int(value_offset)

    # Write-ahead log for crash diagnosis
    wal_write({
        "op": "write_visual_wheel",
        "veh_offset": f"0x{veh_offset:X}",
        "srg_offset": f"0x{srg_offset:X}",
        "value_offset": f"0x{value_offset:X}",
        "value": value,
    })

    handle = get_player_vehicle_handle()
    if handle is None:
        return {"error": "Not in a vehicle"}

    addr = get_vehicle_address(handle)
    if addr is None:
        return {"error": "Could not get vehicle address"}

    # Follow pointer chain with validation
    ptr1 = read_ptr(addr + veh_offset)
    if not ptr1 or ptr1 == 0 or not is_valid_address(ptr1, 0x40):
        return {"error": f"Invalid pointer at vehicle+0x{veh_offset:X}"}

    ptr2 = read_ptr(ptr1 + srg_offset)
    if not ptr2 or ptr2 == 0 or not is_valid_address(ptr2, value_offset + 4):
        return {"error": f"Invalid pointer at drawhandler+0x{srg_offset:X}"}

    target_addr = ptr2 + value_offset

    # Validate target address before write
    if not is_valid_address(target_addr, 4, check_writable=True):
        # Try to make it writable
        old_protect = ctypes.c_ulong()
        if not kernel32.VirtualProtect(ctypes.c_void_p(target_addr), 4, 0x40, ctypes.byref(old_protect)):
            return {"error": f"Cannot write to 0x{target_addr:X}"}

    # Read old value and write new
    old_value = read_float(target_addr)
    write_result = write_float(target_addr, float(value))
    new_value = read_float(target_addr)

    return {
        "success": True,
        "path": f"vehicle+0x{veh_offset:X} -> +0x{srg_offset:X} -> +0x{value_offset:X}",
        "target_address": f"0x{target_addr:X}",
        "old_value": old_value,
        "wrote": value,
        "read_back": new_value
    }

def handle_set_visual_wheel_size(params: dict) -> dict:
    """
    Set the visual wheel size using discovered offsets.

    This uses the FiveM-style StreamRenderGfx path.
    Offsets must be discovered first via find_wheel_visual_offsets or probe_drawhandler.

    Params:
        size: New wheel size (float, typically 0.3-1.5)
        dh_offset: DrawHandler offset from vehicle (if known)
        srg_offset: StreamRenderGfx offset from DrawHandler (if known)
        size_offset: Size offset in StreamRenderGfx (if known)
    """
    size = params.get("size", 1.0)

    # Use provided offsets or cached ones
    dh_offset = params.get("dh_offset", _visual_wheel_offsets.get("draw_handler_ptr"))
    srg_offset = params.get("srg_offset", _visual_wheel_offsets.get("stream_render_gfx_ptr"))
    size_offset = params.get("size_offset", _visual_wheel_offsets.get("wheel_size"))

    if dh_offset is None or srg_offset is None or size_offset is None:
        return {"error": "Visual wheel offsets not discovered. Run find_wheel_visual_offsets first."}

    return handle_write_visual_wheel({
        "veh_offset": dh_offset,
        "srg_offset": srg_offset,
        "value_offset": size_offset,
        "value": size
    })

def handle_set_visual_wheel_width(params: dict) -> dict:
    """
    Set the visual wheel width using discovered offsets.

    This uses the FiveM-style StreamRenderGfx path.
    Offsets must be discovered first via find_wheel_visual_offsets or probe_drawhandler.

    Params:
        width: New wheel width (float, typically 0.1-0.5)
        dh_offset: DrawHandler offset from vehicle (if known)
        srg_offset: StreamRenderGfx offset from DrawHandler (if known)
        width_offset: Width offset in StreamRenderGfx (if known)
    """
    width = params.get("width", 0.3)

    # Use provided offsets or cached ones
    dh_offset = params.get("dh_offset", _visual_wheel_offsets.get("draw_handler_ptr"))
    srg_offset = params.get("srg_offset", _visual_wheel_offsets.get("stream_render_gfx_ptr"))
    width_offset = params.get("width_offset", _visual_wheel_offsets.get("wheel_width"))

    if dh_offset is None or srg_offset is None or width_offset is None:
        return {"error": "Visual wheel offsets not discovered. Run find_wheel_visual_offsets first."}

    return handle_write_visual_wheel({
        "veh_offset": dh_offset,
        "srg_offset": srg_offset,
        "value_offset": width_offset,
        "value": width
    })

def handle_cache_visual_offsets(params: dict) -> dict:
    """
    Cache the discovered visual wheel offsets for later use.

    Params:
        dh_offset: DrawHandler offset from vehicle
        srg_offset: StreamRenderGfx offset from DrawHandler
        size_offset: Wheel size offset in StreamRenderGfx
        width_offset: Wheel width offset in StreamRenderGfx
    """
    _visual_wheel_offsets["draw_handler_ptr"] = params.get("dh_offset")
    _visual_wheel_offsets["stream_render_gfx_ptr"] = params.get("srg_offset")
    _visual_wheel_offsets["wheel_size"] = params.get("size_offset")
    _visual_wheel_offsets["wheel_width"] = params.get("width_offset")

    return {
        "success": True,
        "cached_offsets": _visual_wheel_offsets
    }

def handle_queue_user_message(params: dict) -> dict:
    """
    Queue a user message (from C# UI companion).

    Params:
        message: The message text
    """
    global _user_input_result

    message = params.get("message", "")
    if not message:
        return {"error": "No message provided"}

    msg_entry = {
        "timestamp": datetime.now().isoformat(),
        "from": "user",
        "message": message
    }

    _user_message_queue.append(msg_entry)
    _chat_history.append(msg_entry)
    _pending_user_input.set()
    _user_input_result = message

    log_message("info", f"Queued user message: {message}")

    return {"success": True, "message": message}

def handle_call_native(params: dict) -> dict:
    """
    Call any GTA V native by hash.

    This is the key to Tier 1 capabilities - spawning, teleporting, weather, etc.

    Params:
        hash: Native hash as hex string (e.g., "0xAF35D0D2583051B0") or int
        args: List of arguments to pass
        return_type: Expected return type - "int", "float", "bool", "string", "void" (default "int")
    """
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    native_hash = params.get("hash")
    args = params.get("args", [])
    return_type = params.get("return_type", "int")

    if native_hash is None:
        return {"error": "Missing 'hash' parameter"}

    # Parse hash
    try:
        if isinstance(native_hash, str):
            h = int(native_hash, 16) if native_hash.startswith("0x") else int(native_hash)
        else:
            h = int(native_hash)
    except ValueError:
        return {"error": f"Invalid hash: {native_hash}"}

    # Allowlist gate: refuse hashes not in the verified DB (prevents bad-hash crashes).
    # FAIL CLOSED: if the DB didn't load, refuse ALL raw-hash calls rather than waving them through.
    h_str = f"0x{h:X}"
    if not NATIVE_DB.loaded:
        return {"error": "Native DB not loaded - refusing raw-hash call (fail closed)."}
    if not NATIVE_DB.is_callable(h_str):
        return {"error": f"Hash {h_str} is not on the verified allowlist - refusing (would risk a "
                         f"crash). Use call_native_by_name with a native name instead.",
                "known_bad": h_str.lower() in NATIVE_DB.known_bad}

    # Recover the DB signature (via hash->name) so we can coerce arg types here too.
    info = NATIVE_DB.info(NATIVE_DB.name_for(h_str))
    coerced, coerce_note = _coerce_args(args, info)

    wal_write({"op": "call_native", "hash": h_str, "args": coerced, "return_type": return_type})

    try:
        result = gta.invoke(h, *coerced, return_type=return_type)
        out = {"success": True, "hash": h_str, "result": result}
        if coerce_note:
            out["warning"] = coerce_note
        return out
    except Exception as e:
        log_message("error", f"Native call failed: {e}")
        return {"error": f"Native call failed: {e}"}

def handle_spawn_vehicle(params: dict) -> dict:
    """
    Spawn a vehicle in front of the player.

    This is a convenience wrapper around the native calls needed to spawn.

    Params:
        model: Vehicle model name (e.g., "adder", "zentorno") or hash
        distance: Distance in front of player (default 5.0)
    """
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    model = params.get("model", "adder")
    distance = params.get("distance", 5.0)

    try:
        if isinstance(model, str):
            model_hash = gta.invoke(0xD24D37CC275948CC, model, return_type="int")  # GET_HASH_KEY
        else:
            model_hash = int(model)
    except Exception as e:
        return {"error": f"Bad model '{model}': {e}"}

    # Start the async stream load, then DEFER - do NOT block the game thread.
    gta.invoke(0x963D27A58DF860AC, model_hash, return_type="void")  # REQUEST_MODEL
    log_message("info", f"Spawn requested: {model} (0x{model_hash:X})")

    def poll():
        # Runs once per frame on the game thread (NO sleep, NO freeze).
        if not gta.invoke(0x98A4EB5D89A0C952, model_hash, return_type="bool"):  # HAS_MODEL_LOADED
            return None  # still streaming - check again next frame

        # Loaded -> compute spawn point from CURRENT player pos/heading, then create.
        ped = gta.invoke(0xD80958FC74E988A6, return_type="int")  # PLAYER_PED_ID
        in_vehicle = gta.invoke(0x997ABD671D25CA0B, ped, False, return_type="bool")  # IS_PED_IN_ANY_VEHICLE
        ref = gta.invoke(0x9A9112A0FE9A4713, ped, False, return_type="int") if in_vehicle else ped  # GET_VEHICLE_PED_IS_IN
        entity_addr = gta.entity_address(ref)
        heading = gta.invoke(0xE83D4F9BA2A38914, ref, return_type="float")  # GET_ENTITY_HEADING
        if not entity_addr:
            return {"error": "Could not get entity address"}
        pos_x = gta.read_float(entity_addr + 0x90)
        pos_y = gta.read_float(entity_addr + 0x94)
        pos_z = gta.read_float(entity_addr + 0x98)
        if pos_x is None:
            return {"error": "Could not read position"}

        import math
        rad = math.radians(heading)
        # VERIFIED: forward = (-sin, +cos)
        spawn_x = pos_x - math.sin(rad) * distance
        spawn_y = pos_y + math.cos(rad) * distance
        spawn_z = pos_z + 0.5

        vehicle = gta.invoke(
            0xAF35D0D2583051B0,  # CREATE_VEHICLE
            model_hash, spawn_x, spawn_y, spawn_z, heading,
            True, False, return_type="int")
        gta.invoke(0xE532F5D78798DAAB, model_hash, return_type="void")  # SET_MODEL_AS_NO_LONGER_NEEDED
        if not vehicle or vehicle == 0:
            return {"error": "CREATE_VEHICLE returned 0"}
        log_message("info", f"Spawned {model} -> handle {vehicle} at ({spawn_x:.1f},{spawn_y:.1f},{spawn_z:.1f})")
        return {
            "success": True,
            "model": model,
            "model_hash": f"0x{model_hash:X}",
            "vehicle_handle": vehicle,
            "position": [spawn_x, spawn_y, spawn_z],
        }

    return Deferred(poll, timeout=8.0)

def handle_preload_model(params: dict) -> dict:
    """
    Pre-load a model so spawning is instant later.

    Params:
        model: Model name or hash to preload
        wait: If True (default), wait for load. If False, fire-and-forget.
    """
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    model = params.get("model", "adder")
    wait = params.get("wait", True)

    try:
        if isinstance(model, str):
            model_hash = gta.invoke(0xD24D37CC275948CC, model, return_type="int")  # GET_HASH_KEY
        else:
            model_hash = int(model)
    except Exception as e:
        return {"error": f"Bad model '{model}': {e}"}

    gta.invoke(0x963D27A58DF860AC, model_hash, return_type="void")  # REQUEST_MODEL

    if not wait:
        # Fire-and-forget - return immediately
        return {"success": True, "model": model, "model_hash": f"0x{model_hash:X}", "status": "loading"}

    def poll():
        # One non-blocking check per frame on the game thread (NO sleep).
        if not gta.invoke(0x98A4EB5D89A0C952, model_hash, return_type="bool"):  # HAS_MODEL_LOADED
            return None
        log_message("info", f"Model {model} (0x{model_hash:X}) preloaded")
        return {"success": True, "model": model, "model_hash": f"0x{model_hash:X}", "status": "loaded"}

    return Deferred(poll, timeout=10.0)

def handle_teleport(params: dict) -> dict:
    """
    Teleport the player to coordinates.

    Params:
        x, y, z: Coordinates
        heading: Optional heading (degrees)
    """
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    x = params.get("x", 0.0)
    y = params.get("y", 0.0)
    z = params.get("z", 0.0)
    heading = params.get("heading")

    try:
        ped = gta.invoke(0xD80958FC74E988A6, return_type="int")  # PLAYER_PED_ID

        # Check if in vehicle
        in_vehicle = gta.invoke(0x997ABD671D25CA0B, ped, False, return_type="bool")  # IS_PED_IN_ANY_VEHICLE

        if in_vehicle:
            vehicle = gta.invoke(0x9A9112A0FE9A4713, ped, False, return_type="int")  # GET_VEHICLE_PED_IS_IN
            entity = vehicle
        else:
            entity = ped

        # SET_ENTITY_COORDS
        gta.invoke(0x06843DA7060A026B, entity, x, y, z, False, False, False, True, return_type="void")

        if heading is not None:
            # SET_ENTITY_HEADING
            gta.invoke(0x8E2530AA8ADA980E, entity, float(heading), return_type="void")

        # Notification disabled

        return {
            "success": True,
            "position": [x, y, z],
            "heading": heading
        }

    except Exception as e:
        log_message("error", f"Teleport failed: {e}")
        return {"error": f"Teleport failed: {e}"}

def handle_set_weather(params: dict) -> dict:
    """
    Set the weather.

    Params:
        weather: Weather name (e.g., "CLEAR", "RAIN", "THUNDER", "SNOW", "FOGGY")
    """
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    weather = params.get("weather", "CLEAR").upper()

    try:
        # SET_WEATHER_TYPE_NOW
        gta.invoke(0xED712CA327900C8A, weather, return_type="void")

        # Notification disabled

        return {"success": True, "weather": weather}

    except Exception as e:
        log_message("error", f"Set weather failed: {e}")
        return {"error": f"Set weather failed: {e}"}

def handle_set_time(params: dict) -> dict:
    """
    Set the game time.

    Params:
        hour: Hour (0-23)
        minute: Minute (0-59, optional)
        second: Second (0-59, optional)
    """
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    hour = params.get("hour", 12)
    minute = params.get("minute", 0)
    second = params.get("second", 0)

    try:
        # SET_CLOCK_TIME
        gta.invoke(0x47C3B5848C3E45D8, int(hour), int(minute), int(second), return_type="void")

        # Notification disabled

        return {"success": True, "time": f"{hour:02d}:{minute:02d}:{second:02d}"}

    except Exception as e:
        log_message("error", f"Set time failed: {e}")
        return {"error": f"Set time failed: {e}"}

def handle_give_weapon(params: dict) -> dict:
    """
    Give the player a weapon.

    Params:
        weapon: Weapon name (e.g., "WEAPON_PISTOL", "WEAPON_MINIGUN", "WEAPON_RPG")
        ammo: Ammo count (default 9999)
    """
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    weapon = params.get("weapon", "WEAPON_PISTOL").upper()
    if not weapon.startswith("WEAPON_"):
        weapon = f"WEAPON_{weapon}"

    ammo = params.get("ammo", 9999)

    try:
        ped = gta.invoke(0xD80958FC74E988A6, return_type="int")  # PLAYER_PED_ID
        weapon_hash = gta.invoke(0xD24D37CC275948CC, weapon, return_type="int")  # GET_HASH_KEY

        # GIVE_WEAPON_TO_PED
        gta.invoke(0xBF0FD6E56C964FCB, ped, weapon_hash, ammo, False, True, return_type="void")

        # Notification disabled

        return {"success": True, "weapon": weapon, "ammo": ammo}

    except Exception as e:
        log_message("error", f"Give weapon failed: {e}")
        return {"error": f"Give weapon failed: {e}"}

def handle_repair_vehicle(params: dict) -> dict:
    """Repair the current vehicle."""
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}

    try:
        ped = gta.invoke(0xD80958FC74E988A6, return_type="int")  # PLAYER_PED_ID

        if not gta.invoke(0x796D90EFB19AA332, ped, False, return_type="bool"):  # IS_PED_IN_ANY_VEHICLE
            return {"error": "Not in a vehicle"}

        vehicle = gta.invoke(0x9A9112A0FE9A4713, ped, False, return_type="int")  # GET_VEHICLE_PED_IS_IN

        # SET_VEHICLE_FIXED
        gta.invoke(0x115722B1B9C14C1C, vehicle, return_type="void")

        # SET_VEHICLE_DIRT_LEVEL (clean it too)
        gta.invoke(0x79D3B596FE44EE8B, vehicle, 0.0, return_type="void")

        # Notification disabled

        return {"success": True}

    except Exception as e:
        log_message("error", f"Repair failed: {e}")
        return {"error": f"Repair failed: {e}"}

# =============================================================================
# Native Database (verified-hash allowlist) - prevents bad-hash CRASHES
# A wrong 64-bit hash crashes the game instantly. The alloc8or hashes are the
# AUTHORITATIVE canonical hashes, so "present in the DB" == safe to call.
# This is what lets Claude call ANY of ~6700 natives without guessing/crashing.
# =============================================================================

_NATIVE_DB_PATH = os.path.join(os.path.dirname(__file__), "native_db.json")

def _map_return_type(rt):
    """alloc8or return-type enum -> gta.invoke return_type string."""
    if not rt:
        return "int"
    rt = rt.strip()
    if rt == "void":
        return "void"
    if rt == "BOOL":
        return "bool"
    if rt == "float":
        return "float"
    if rt in ("char*", "const char*", "char *", "string"):
        return "string"
    if rt in ("Vector3", "Vector3*"):
        return "vector3"  # GET_ENTITY_COORDS/ROTATION/VELOCITY etc. (see _coerce note)
    return "int"  # int/Hash/Entity/Ped/Vehicle/Player/Object/Cam/Blip/Any/etc.

# Param-type buckets for arg coercion. GTA bit-reinterprets native arg slots, so a Python
# int passed where a float is expected becomes a near-zero denormal (entities spawn at origin,
# headings snap to 0, sizes collapse). We coerce each arg by the DB's declared param type.
_FLOAT_TYPES = {"float"}
_BOOL_TYPES = {"BOOL"}
_STR_TYPES = {"char*", "const char*", "char *", "string"}

def _coerce_arg(value, ptype):
    """Coerce one JSON-decoded arg to the type the native expects (best-effort)."""
    if ptype in _FLOAT_TYPES:
        return float(value)
    if ptype in _BOOL_TYPES:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if ptype in _STR_TYPES:
        return str(value)
    if ptype and ptype.endswith("*"):
        return value  # output/pointer param - left as-is (see native_info flag)
    # int / Hash / Entity / Ped / Vehicle / Player / Object / Cam / Blip / Any / enums
    try:
        return int(value)
    except (TypeError, ValueError):
        return value

def _coerce_args(args, info):
    """Coerce a positional arg list against a native's DB param signature.
    Returns (coerced_args, note). Only coerces when arg count matches param count."""
    if not info:
        return args, None
    params = info.get("params") or []
    if len(args) != len(params):
        return args, f"arg-count {len(args)} != expected {len(params)} - passed without coercion"
    out = [_coerce_arg(a, (params[i].get("type") or "")) for i, a in enumerate(args)]
    return out, None

class NativeDB:
    """Loads native_db.json and serves a verified-hash allowlist for the edition."""

    def __init__(self, path, edition="legacy"):
        self.edition = edition
        self.natives = {}
        self.known_bad = set()
        self._by_name = {}      # NAME -> record (callable on this edition)
        self._allow = set()     # lowercased callable hashes
        self._hash_to_name = {}
        self.loaded = False
        self.load_error = None
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                doc = json.load(f)
            self.natives = doc.get("natives", {})
            self.known_bad = {h.lower() for h in doc.get("known_bad", {})}
            for name, e in self.natives.items():
                h = (e.get("hashes", {}) or {}).get(edition) or ""
                if not h:
                    continue
                hl = h.lower()
                if hl in self.known_bad:
                    continue
                self._by_name[name] = e
                self._allow.add(hl)
                self._hash_to_name[hl] = name
            self.loaded = True
        except Exception as ex:
            self.load_error = str(ex)

    def lookup(self, name):
        e = self._by_name.get(name)
        return e["hashes"][self.edition] if e else None

    def is_callable(self, hash_str):
        h = hash_str.lower()
        return h in self._allow and h not in self.known_bad

    def name_for(self, hash_str):
        return self._hash_to_name.get(hash_str.lower())

    def info(self, name):
        return self.natives.get(name)

def detect_edition():
    """Enhanced ships GTA5_Enhanced.exe; Legacy ships GTA5.exe."""
    try:
        buf = ctypes.create_unicode_buffer(520)
        kernel32.GetModuleFileNameW(None, buf, 520)
        exe = os.path.basename(buf.value).lower()
        return "enhanced" if "enhanced" in exe else "legacy"
    except Exception:
        return "legacy"

_EDITION = detect_edition()
NATIVE_DB = NativeDB(_NATIVE_DB_PATH, _EDITION)
if NATIVE_DB.loaded:
    log_message("info", f"NativeDB loaded: {len(NATIVE_DB._allow)} callable natives ({_EDITION})")
else:
    log_message("error", f"NativeDB FAILED to load: {NATIVE_DB.load_error}")

def handle_native_db_status(params: dict) -> dict:
    return {
        "loaded": NATIVE_DB.loaded,
        "edition": NATIVE_DB.edition,
        "callable_count": len(NATIVE_DB._allow),
        "total_natives": len(NATIVE_DB.natives),
        "known_bad": sorted(NATIVE_DB.known_bad),
        "load_error": NATIVE_DB.load_error,
    }

def handle_call_native_by_name(params: dict) -> dict:
    """Call a native by NAME (safe default). Resolves to the edition's verified hash."""
    if not PYLOADER_AVAILABLE or not gta:
        return {"error": "PyLoaderV not available"}
    name = params.get("name")
    args = params.get("args", [])
    return_type = params.get("return_type")
    if not name:
        return {"error": "Missing 'name'"}

    if not NATIVE_DB.loaded:
        return {"error": "Native DB not loaded - refusing native calls (fail closed)."}

    info = NATIVE_DB.info(name)
    h = NATIVE_DB.lookup(name)
    if not h:
        if info:
            return {"error": f"Native '{name}' is not available on {NATIVE_DB.edition} edition",
                    "flags": info.get("flags", [])}
        return {"error": f"Unknown native '{name}' - not in DB (refusing to guess a hash). "
                         f"Use search_natives to find the correct name."}

    # Coerce args to the declared param types (prevents int-as-float garbage).
    coerced, coerce_note = _coerce_args(args, info)

    if return_type is None:
        return_type = _map_return_type(info.get("return_type")) if info else "int"

    h_int = int(h, 16)
    wal_write({"op": "call_native_by_name", "name": name, "hash": h, "args": coerced, "return_type": return_type})
    try:
        result = gta.invoke(h_int, *coerced, return_type=return_type)
        out = {"success": True, "name": name, "hash": h, "return_type": return_type, "result": result}
        if coerce_note:
            out["warning"] = coerce_note
        return out
    except Exception as e:
        log_message("error", f"call_native_by_name {name} failed: {e}")
        return {"error": f"Native call failed: {e}", "name": name, "hash": h,
                "hint": "If return_type=vector3 is unsupported by this loader, retry with an explicit return_type."}

def handle_native_info(params: dict) -> dict:
    """Get params/return/comment/flags for a native (so the caller uses it correctly)."""
    name = params.get("name")
    info = NATIVE_DB.info(name)
    if not info:
        return {"error": f"Unknown native '{name}'"}
    ptypes = [(p.get("type") or "") for p in (info.get("params") or [])]
    rt = info.get("return_type") or ""
    usage = []
    if any(t.endswith("*") for t in ptypes):
        usage.append("Has OUTPUT/pointer params (type ending in '*') - result is written to a buffer, "
                     "not returned; special handling required.")
    if rt in ("Vector3", "Vector3*"):
        usage.append("Returns Vector3 (3 floats) - bridge requests return_type=vector3; "
                     "if your loader returns wrong data, this native needs special handling.")
    return {
        "success": True,
        "name": name,
        "edition": NATIVE_DB.edition,
        "callable": NATIVE_DB.lookup(name) is not None,
        "namespace": info.get("namespace"),
        "return_type": rt,
        "params": info.get("params"),
        "hashes": info.get("hashes"),
        "flags": info.get("flags"),
        "comment": info.get("comment"),
        "usage_notes": usage,
    }

def handle_search_natives(params: dict) -> dict:
    """Search natives by name/comment/namespace - how Claude discovers what to call."""
    query = (params.get("query") or "").lower()
    namespace = params.get("namespace")
    limit = int(params.get("limit", 30))
    results = []
    for name, e in NATIVE_DB.natives.items():
        if namespace and (e.get("namespace", "").lower() != namespace.lower()):
            continue
        if query and query not in name.lower() and query not in (e.get("comment", "") or "").lower():
            continue
        results.append({
            "name": name,
            "namespace": e.get("namespace"),
            "return_type": e.get("return_type"),
            "params": [p.get("name") for p in e.get("params", [])],
            "callable": ((e.get("hashes", {}) or {}).get(NATIVE_DB.edition) is not None),
        })
        if len(results) >= limit:
            break
    return {"success": True, "query": query, "edition": NATIVE_DB.edition,
            "count": len(results), "natives": results}

def handle_list_namespaces(params: dict) -> dict:
    """List native namespaces + counts (for search_natives' namespace filter)."""
    counts = {}
    for e in NATIVE_DB.natives.values():
        ns = e.get("namespace", "?")
        counts[ns] = counts.get(ns, 0) + 1
    return {"success": True, "namespaces": dict(sorted(counts.items()))}

# =============================================================================
# get_context - a live snapshot of player/world state so Claude is "situationally
# aware" in-game (inspired by TalkToMeV's provider pattern). Reads only, via the
# verified-allowlist native path, every field best-effort (a failed read is omitted).
# =============================================================================

def _joaat(s: str) -> int:
    """Jenkins one-at-a-time hash (GTA's GET_HASH_KEY), lower-cased like the game does."""
    h = 0
    for c in s.lower().encode("utf-8"):
        h = (h + c) & 0xFFFFFFFF
        h = (h + ((h << 10) & 0xFFFFFFFF)) & 0xFFFFFFFF
        h ^= (h >> 6)
    h = (h + ((h << 3) & 0xFFFFFFFF)) & 0xFFFFFFFF
    h ^= (h >> 11)
    h = (h + ((h << 15) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return h & 0xFFFFFFFF

_WEATHER_NAMES = ["EXTRASUNNY", "CLEAR", "CLOUDS", "SMOG", "FOGGY", "OVERCAST", "RAIN",
                  "THUNDER", "CLEARING", "NEUTRAL", "SNOW", "BLIZZARD", "SNOWLIGHT",
                  "XMAS", "HALLOWEEN"]
_WEATHER_BY_HASH = {_joaat(n): n for n in _WEATHER_NAMES}

_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_VEH_CLASSES = ["Compact", "Sedan", "SUV", "Coupe", "Muscle", "Sports Classic", "Sports",
                "Super", "Motorcycle", "Off-road", "Industrial", "Utility", "Van", "Cycle",
                "Boat", "Helicopter", "Plane", "Service", "Emergency", "Military",
                "Commercial", "Train", "Open Wheel"]
_WEAPON_NAMES = ["WEAPON_UNARMED", "WEAPON_KNIFE", "WEAPON_NIGHTSTICK", "WEAPON_BAT",
    "WEAPON_CROWBAR", "WEAPON_HAMMER", "WEAPON_MACHETE", "WEAPON_PISTOL", "WEAPON_COMBATPISTOL",
    "WEAPON_APPISTOL", "WEAPON_PISTOL50", "WEAPON_SNSPISTOL", "WEAPON_HEAVYPISTOL", "WEAPON_REVOLVER",
    "WEAPON_MICROSMG", "WEAPON_SMG", "WEAPON_ASSAULTSMG", "WEAPON_COMBATPDW", "WEAPON_MACHINEPISTOL",
    "WEAPON_MG", "WEAPON_COMBATMG", "WEAPON_ASSAULTRIFLE", "WEAPON_CARBINERIFLE",
    "WEAPON_ADVANCEDRIFLE", "WEAPON_SPECIALCARBINE", "WEAPON_BULLPUPRIFLE", "WEAPON_COMPACTRIFLE",
    "WEAPON_PUMPSHOTGUN", "WEAPON_SAWNOFFSHOTGUN", "WEAPON_ASSAULTSHOTGUN", "WEAPON_HEAVYSHOTGUN",
    "WEAPON_DBSHOTGUN", "WEAPON_AUTOSHOTGUN", "WEAPON_SNIPERRIFLE", "WEAPON_HEAVYSNIPER",
    "WEAPON_MARKSMANRIFLE", "WEAPON_GRENADELAUNCHER", "WEAPON_RPG", "WEAPON_MINIGUN",
    "WEAPON_HOMINGLAUNCHER", "WEAPON_RAILGUN", "WEAPON_GRENADE", "WEAPON_STICKYBOMB",
    "WEAPON_SMOKEGRENADE", "WEAPON_MOLOTOV", "WEAPON_PROXMINE", "WEAPON_PIPEBOMB",
    "WEAPON_FIREEXTINGUISHER", "WEAPON_PETROLCAN", "WEAPON_FLARE", "WEAPON_FLAREGUN",
    "WEAPON_KNUCKLE", "WEAPON_SWITCHBLADE", "WEAPON_PARACHUTE", "WEAPON_STUNGUN"]
_WEAPON_BY_HASH = {_joaat(n): n.replace("WEAPON_", "") for n in _WEAPON_NAMES}

_PED_FLAGS = [  # each takes (ped) and returns BOOL; only the true ones are reported
    ("sprinting", "IS_PED_SPRINTING"), ("running", "IS_PED_RUNNING"),
    ("walking", "IS_PED_WALKING"), ("shooting", "IS_PED_SHOOTING"),
    ("reloading", "IS_PED_RELOADING"), ("jumping", "IS_PED_JUMPING"),
    ("climbing", "IS_PED_CLIMBING"), ("falling", "IS_PED_FALLING"),
    ("ragdoll", "IS_PED_RAGDOLL"), ("swimming", "IS_PED_SWIMMING"),
    ("underwater", "IS_PED_SWIMMING_UNDER_WATER"), ("melee", "IS_PED_IN_MELEE_COMBAT"),
    ("diving", "IS_PED_DIVING")]

def _call_native_safe(name, *args, return_type="int"):
    """Call a native BY NAME via the verified allowlist; return its result or None on ANY
    failure. No WAL (these are reads). Never raises - so get_context degrades gracefully."""
    try:
        if not (PYLOADER_AVAILABLE and gta and NATIVE_DB.loaded):
            return None
        h = NATIVE_DB.lookup(name)
        if not h:
            return None
        coerced, _ = _coerce_args(list(args), NATIVE_DB.info(name))
        return gta.invoke(int(h, 16), *coerced, return_type=return_type)
    except Exception:
        return None

def _as_vec3(v):
    """Normalize a Vector3-ish native return (tuple/list/dict/obj) into (x, y, z) or None."""
    try:
        if v is None:
            return None
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return (float(v[0]), float(v[1]), float(v[2]))
        if isinstance(v, dict):
            return (float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0)))
        if hasattr(v, "x") and hasattr(v, "y") and hasattr(v, "z"):
            return (float(v.x), float(v.y), float(v.z))
    except Exception:
        return None
    return None

def handle_get_context(params: dict) -> dict:
    """Live player/world snapshot for situational awareness. Reads only; never crashes.
    detail='lite' (default) = core fields; detail='full' = + activity flags, weapon,
    vehicle class/plate/extras, day, next weather, game-state flags, interior."""
    if not (PYLOADER_AVAILABLE and gta and NATIVE_DB.loaded):
        return {"error": "Native calls unavailable (PyLoaderV/DB not ready)"}

    full = params.get("detail") == "full"
    ctx = {}
    ped = _call_native_safe("PLAYER_PED_ID", return_type="int")
    pid = _call_native_safe("PLAYER_ID", return_type="int")

    # --- Player ---
    player = {}
    if ped:
        for key, nat in (("health", "GET_ENTITY_HEALTH"), ("max_health", "GET_PED_MAX_HEALTH"),
                         ("armour", "GET_PED_ARMOUR")):
            val = _call_native_safe(nat, ped, return_type="int")
            if val is not None:
                player[key] = val
        heading = _call_native_safe("GET_ENTITY_HEADING", ped, return_type="float")
        if heading is not None:
            player["heading"] = round(heading, 1)
        coords = _as_vec3(_call_native_safe("GET_ENTITY_COORDS", ped, True, return_type="vector3"))
        if coords:
            player["position"] = {"x": round(coords[0], 2), "y": round(coords[1], 2), "z": round(coords[2], 2)}
            zone = _call_native_safe("GET_NAME_OF_ZONE", coords[0], coords[1], coords[2], return_type="string")
            if zone:
                ctx["zone"] = zone
    if pid is not None:
        wl = _call_native_safe("GET_PLAYER_WANTED_LEVEL", pid, return_type="int")
        if wl is not None:
            player["wanted_level"] = wl
    if player:
        ctx["player"] = player

    # --- Time + weather ---
    hours = _call_native_safe("GET_CLOCK_HOURS", return_type="int")
    mins = _call_native_safe("GET_CLOCK_MINUTES", return_type="int")
    if hours is not None and mins is not None:
        ctx["time"] = f"{hours:02d}:{mins:02d}"
    wh = _call_native_safe("GET_PREV_WEATHER_TYPE_HASH_NAME", return_type="int")
    if wh is not None:
        wh32 = int(wh) & 0xFFFFFFFF
        ctx["weather"] = _WEATHER_BY_HASH.get(wh32, f"0x{wh32:X}")

    # --- Vehicle (None = on foot) ---
    veh = None
    if ped:
        in_veh = _call_native_safe("IS_PED_IN_ANY_VEHICLE", ped, False, return_type="bool")
        veh = _call_native_safe("GET_VEHICLE_PED_IS_IN", ped, False, return_type="int") if in_veh else None
        if veh:
            vehicle = {}
            model = _call_native_safe("GET_ENTITY_MODEL", veh, return_type="int")
            if model is not None:
                disp = _call_native_safe("GET_DISPLAY_NAME_FROM_VEHICLE_MODEL", model, return_type="string")
                if disp:
                    vehicle["model"] = disp
            spd = _call_native_safe("GET_ENTITY_SPEED", veh, return_type="float")
            if spd is not None:
                vehicle["speed_mph"] = round(spd * 2.2369, 1)
            for key, nat in (("body_health", "GET_VEHICLE_BODY_HEALTH"),
                            ("engine_health", "GET_VEHICLE_ENGINE_HEALTH")):
                val = _call_native_safe(nat, veh, return_type="float")
                if val is not None:
                    vehicle[key] = round(val)
            ctx["vehicle"] = vehicle
        else:
            ctx["vehicle"] = None

    if not full:
        return {"success": True, "context": ctx}

    # ===== FULL detail (still one round-trip; all reads are microseconds) =====
    if ped:
        active = [name for name, nat in _PED_FLAGS if _call_native_safe(nat, ped, return_type="bool")]
        if _call_native_safe("IS_PED_IN_COVER", ped, False, return_type="bool"):
            active.append("in_cover")
        if _call_native_safe("IS_ENTITY_IN_AIR", ped, return_type="bool"):
            active.append("in_air")
        if active:
            ctx["activity"] = active
        w = _call_native_safe("GET_SELECTED_PED_WEAPON", ped, return_type="int")
        if w is not None:
            wh2 = int(w) & 0xFFFFFFFF
            weapon = {"name": _WEAPON_BY_HASH.get(wh2, f"0x{wh2:X}")}
            ammo = _call_native_safe("GET_AMMO_IN_PED_WEAPON", ped, w, return_type="int")
            if ammo is not None:
                weapon["ammo"] = ammo
            ctx["weapon"] = weapon

    # World extras
    dow = _call_native_safe("GET_CLOCK_DAY_OF_WEEK", return_type="int")
    if dow is not None and 0 <= dow < 7:
        ctx["day"] = _DAYS[dow]
    nw = _call_native_safe("GET_NEXT_WEATHER_TYPE_HASH_NAME", return_type="int")
    if nw is not None:
        nw32 = int(nw) & 0xFFFFFFFF
        ctx["weather_next"] = _WEATHER_BY_HASH.get(nw32, f"0x{nw32:X}")

    # Game-state flags
    gs = []
    if _call_native_safe("IS_SCREEN_FADED_OUT", return_type="bool"):
        gs.append("screen_faded")
    if _call_native_safe("IS_CUTSCENE_PLAYING", return_type="bool"):
        gs.append("cutscene")
    if pid is not None:
        if _call_native_safe("IS_PLAYER_DEAD", pid, return_type="bool"):
            gs.append("dead")
        if _call_native_safe("IS_PLAYER_FREE_AIMING", pid, return_type="bool"):
            gs.append("free_aiming")
    if gs:
        ctx["game_state"] = gs

    # Interior
    if ped:
        interior = _call_native_safe("GET_INTERIOR_FROM_ENTITY", ped, return_type="int")
        if interior:
            ctx["interior_id"] = interior

    # Vehicle extras (augment the vehicle dict built above)
    if veh and isinstance(ctx.get("vehicle"), dict):
        v = ctx["vehicle"]
        cls = _call_native_safe("GET_VEHICLE_CLASS", veh, return_type="int")
        if cls is not None and 0 <= cls < len(_VEH_CLASSES):
            v["class"] = _VEH_CLASSES[cls]
        plate = _call_native_safe("GET_VEHICLE_NUMBER_PLATE_TEXT", veh, return_type="string")
        if plate:
            v["plate"] = plate.strip()
        tank = _call_native_safe("GET_VEHICLE_PETROL_TANK_HEALTH", veh, return_type="float")
        if tank is not None:
            v["tank_health"] = round(tank)
        if _call_native_safe("GET_IS_VEHICLE_ENGINE_RUNNING", veh, return_type="bool"):
            v["engine_on"] = True
        if _call_native_safe("IS_VEHICLE_ON_ALL_WHEELS", veh, return_type="bool"):
            v["all_wheels"] = True

    return {"success": True, "context": ctx}

# =============================================================================
# Pattern scanning (AOB) - the offset-discovery primitive (was a dead tool)
# Chunked via Deferred so a multi-MB module scan never freezes the game thread.
# =============================================================================

def _get_main_module():
    """Return (base_addr, image_size) of GTA5.exe, or (None, None)."""
    try:
        base = kernel32.GetModuleHandleW(None)
        if not base:
            return None, None
        # PE header: e_lfanew @ base+0x3C -> NT headers; SizeOfImage @ OptionalHeader+0x38
        e_lfanew = ctypes.cast(base + 0x3C, ctypes.POINTER(ctypes.c_uint32)).contents.value
        nt = base + e_lfanew
        size_of_image = ctypes.cast(nt + 0x18 + 0x38, ctypes.POINTER(ctypes.c_uint32)).contents.value
        return base, size_of_image
    except Exception as e:
        log_message("error", f"_get_main_module failed: {e}")
        return None, None

def _parse_pattern(pattern: str):
    """'48 8B 05 ?? ?? ?? ?? 45' -> ([bytes...], [mask bools]). ? / ?? = wildcard."""
    toks = pattern.replace(",", " ").split()
    pat, mask = [], []
    for t in toks:
        if t in ("?", "??", "*"):
            pat.append(0); mask.append(False)
        else:
            pat.append(int(t, 16)); mask.append(True)
    return pat, mask

def handle_scan_pattern(params: dict) -> dict:
    """Find an AOB pattern in GTA5.exe. Returns up to `limit` match addresses.
    Chunked across frames (Deferred) to avoid freezing the game."""
    pattern = params.get("pattern", "")
    limit = int(params.get("limit", 16))
    try:
        pat, mask = _parse_pattern(pattern)
    except Exception as e:
        return {"error": f"Bad pattern: {e}"}
    if not pat:
        return {"error": "Empty pattern"}

    base, size = _get_main_module()
    if not base:
        return {"error": "Could not locate GTA5.exe module"}

    plen = len(pat)
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

    return Deferred(poll, timeout=60.0)

def handle_resolve_rip_relative(params: dict) -> dict:
    """Resolve a RIP-relative reference: absolute = instr_addr + instr_size + disp32(@offset)."""
    address = params.get("address")
    offset_position = int(params.get("offset_position", 3))
    instruction_size = int(params.get("instruction_size", 7))
    try:
        addr = int(address, 16) if isinstance(address, str) and address.startswith("0x") else int(address)
    except (TypeError, ValueError):
        return {"error": f"Invalid address: {address}"}
    disp_addr = addr + offset_position
    if not is_valid_address(disp_addr, 4):
        return {"error": f"Cannot read displacement at 0x{disp_addr:X}"}
    disp = ctypes.cast(disp_addr, ctypes.POINTER(ctypes.c_int32)).contents.value
    absolute = addr + instruction_size + disp
    return {"success": True, "instruction": f"0x{addr:X}", "displacement": disp,
            "resolved": f"0x{absolute:X}"}

# Command dispatch table
COMMANDS = {
    "status": handle_status,
    "read": handle_read,
    "write": handle_write,
    "snapshot": handle_snapshot,
    "is_in_vehicle": handle_is_in_vehicle,
    "get_vehicle_info": handle_get_vehicle_info,
    "get_wheel_values": handle_get_wheel_values,
    "set_wheel_value": handle_set_wheel_value,
    "set_overlay": handle_set_overlay,
    "chat_post": handle_chat_post,
    "mark": handle_mark,
    "watch": handle_watch,
    "revert_last": handle_revert_last,
    "list_snapshots": handle_list_snapshots,
    "get_undo_stack": handle_get_undo_stack,
    "probe_wheels": handle_probe_wheels,
    "set_continuous": handle_set_continuous,
    "clear_continuous": handle_clear_continuous,
    "list_continuous": handle_list_continuous,
    # Visual wheel probing (DrawHandler/StreamRenderGfx path)
    "probe_drawhandler": handle_probe_drawhandler,
    "scan_structure": handle_scan_structure,
    "write_visual_wheel": handle_write_visual_wheel,
    "find_wheel_visual_offsets": handle_find_wheel_visual_offsets,
    # FiveM-style visual wheel natives (port)
    "set_visual_wheel_size": handle_set_visual_wheel_size,
    "set_visual_wheel_width": handle_set_visual_wheel_width,
    "cache_visual_offsets": handle_cache_visual_offsets,
    # In-game chat
    "await_user_message": handle_await_user_message,
    "get_chat_history": handle_get_chat_history,
    "get_crash_logs": handle_get_crash_logs,
    "get_pending_messages": handle_get_pending_messages,
    "has_pending_messages": handle_has_pending_messages,
    "reset_keyboard": handle_reset_keyboard,
    "queue_user_message": handle_queue_user_message,
    # Native DB (verified-hash allowlist) - lets Claude call ANY native safely
    "native_db_status": handle_native_db_status,
    "call_native_by_name": handle_call_native_by_name,
    "native_info": handle_native_info,
    "search_natives": handle_search_natives,
    "list_namespaces": handle_list_namespaces,
    "get_context": handle_get_context,
    # Pattern scanning (offset discovery)
    "scan_pattern": handle_scan_pattern,
    "resolve_rip_relative": handle_resolve_rip_relative,
    # Tier 1: Native calling
    "call_native": handle_call_native,
    "spawn_vehicle": handle_spawn_vehicle,
    "preload_model": handle_preload_model,
    "teleport": handle_teleport,
    "set_weather": handle_set_weather,
    "set_time": handle_set_time,
    "give_weapon": handle_give_weapon,
    "repair_vehicle": handle_repair_vehicle,
}

# =============================================================================
# Socket Server
# =============================================================================

def _recvall(conn: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes, looping over TCP segmentation. None if peer closed early."""
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(min(4096, n - len(buf)))
        if not chunk:
            return None
        buf += chunk
    return buf

def _request_wait_timeout(command: str, params: dict) -> float:
    """How long the socket thread should wait for the game thread to finish this command.
    Long-running deferred ops (chat waits, watches, model loads) declare their own budget."""
    base = 20.0  # covers deferred model loads (8-10s)
    ms = params.get("timeout_ms") or params.get("duration_ms") or 0
    if ms:
        return max(base, ms / 1000.0 + 8.0)
    return base

def handle_client(conn: socket.socket, addr):
    """Handle a single client connection (one request/response)."""
    response = None
    try:
        size_data = _recvall(conn, 4)
        if size_data is None:
            return
        size = struct.unpack("<I", size_data)[0]
        if size <= 0 or size > 1024 * 1024:  # 1MB max
            response = {"error": "Bad message size"}
        else:
            body = _recvall(conn, size)
            if body is None:
                response = {"error": "Truncated message"}
            else:
                try:
                    request = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    response = {"error": "Invalid JSON"}
                else:
                    command = request.get("command", "")
                    params = request.get("params", {})
                    if command in COMMANDS:
                        work = enqueue_work(COMMANDS[command], params)
                        result, error = work.wait(timeout=_request_wait_timeout(command, params))
                        if error:
                            response = {"error": str(error)}
                        elif result is not None:
                            response = result
                        else:
                            response = {"error": "No result (handler returned None)"}
                    else:
                        response = {"error": f"Unknown command: {command}"}
    except Exception as e:
        log_message("error", f"Client error: {e}")
        response = {"error": f"Bridge client error: {e}"}

    # Always try to send a response so the caller never hangs.
    try:
        if response is None:
            response = {"error": "No response produced"}
        response_bytes = json.dumps(response).encode("utf-8")
        conn.sendall(struct.pack("<I", len(response_bytes)) + response_bytes)
    except Exception as e:
        log_message("error", f"Response send failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

_server_running = False
_server_thread = None

def socket_server_thread():
    """Run the socket server in a background thread."""
    global _server_running

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind((BRIDGE_HOST, BRIDGE_PORT))
    except OSError as e:
        log_message("error", f"Failed to bind to {BRIDGE_HOST}:{BRIDGE_PORT}: {e}")
        return

    server.listen(5)
    server.settimeout(1.0)

    log_message("info", f"Socket server listening on {BRIDGE_HOST}:{BRIDGE_PORT}")

    while _server_running:
        try:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except socket.timeout:
            continue
        except Exception as e:
            if _server_running:
                log_message("error", f"Server error: {e}")
            break

    server.close()
    log_message("info", "Socket server stopped")

# =============================================================================
# PyLoaderV Script Callbacks
# =============================================================================

def on_start():
    """Called when script loads."""
    global _server_running, _server_thread

    log_message("info", "GTA V Claude MCP Bridge starting...")
    show_notification("~g~Claude MCP Bridge~w~ starting...")

    _server_running = True
    _server_thread = threading.Thread(target=socket_server_thread, daemon=True)
    _server_thread.start()

    log_message("info", f"Ready - MCP server can connect to {BRIDGE_HOST}:{BRIDGE_PORT}")

# =============================================================================
# Key-Capture Input System (bypasses string return limitation)
# =============================================================================

_input_mode = False
_input_buffer = ""
_input_last_display = 0

# Virtual key code to character mapping
VK_TO_CHAR = {
    # Numbers
    0x30: ('0', ')'), 0x31: ('1', '!'), 0x32: ('2', '@'), 0x33: ('3', '#'),
    0x34: ('4', '$'), 0x35: ('5', '%'), 0x36: ('6', '^'), 0x37: ('7', '&'),
    0x38: ('8', '*'), 0x39: ('9', '('),
    # Letters (lowercase, uppercase with shift)
    0x41: ('a', 'A'), 0x42: ('b', 'B'), 0x43: ('c', 'C'), 0x44: ('d', 'D'),
    0x45: ('e', 'E'), 0x46: ('f', 'F'), 0x47: ('g', 'G'), 0x48: ('h', 'H'),
    0x49: ('i', 'I'), 0x4A: ('j', 'J'), 0x4B: ('k', 'K'), 0x4C: ('l', 'L'),
    0x4D: ('m', 'M'), 0x4E: ('n', 'N'), 0x4F: ('o', 'O'), 0x50: ('p', 'P'),
    0x51: ('q', 'Q'), 0x52: ('r', 'R'), 0x53: ('s', 'S'), 0x54: ('t', 'T'),
    0x55: ('u', 'U'), 0x56: ('v', 'V'), 0x57: ('w', 'W'), 0x58: ('x', 'X'),
    0x59: ('y', 'Y'), 0x5A: ('z', 'Z'),
    # Special keys
    0x20: (' ', ' '),  # Space
    0xBE: ('.', '>'),  # Period
    0xBC: (',', '<'),  # Comma
    0xBD: ('-', '_'),  # Minus
    0xBB: ('=', '+'),  # Equals
    0xBA: (';', ':'),  # Semicolon
    0xDE: ("'", '"'),  # Quote
    0xBF: ('/', '?'),  # Slash
    0xDC: ('\\', '|'), # Backslash
    0xDB: ('[', '{'),  # Left bracket
    0xDD: (']', '}'),  # Right bracket
    0xC0: ('`', '~'),  # Backtick
}

def display_input_prompt():
    """Display the current input buffer on screen."""
    global _input_last_display

    if not PYLOADER_AVAILABLE or not gta:
        return

    # Use help text display which stays on screen
    # BEGIN_TEXT_COMMAND_DISPLAY_HELP, ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME, END_TEXT_COMMAND_DISPLAY_HELP
    try:
        display_text = f"Type message: {_input_buffer}_"
        if len(display_text) > 99:
            display_text = f"...{_input_buffer[-90:]}_"

        # Use HUD text command for persistent display
        gta.invoke(0x8509B634FBE7DA11, "STRING", return_type="void")  # BEGIN_TEXT_COMMAND_DISPLAY_HELP
        gta.invoke(0x6C188BE134E074AA, display_text, return_type="void")  # ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME
        gta.invoke(0x238FFE5C7B0498A6, 0, False, False, -1, return_type="void")  # END_TEXT_COMMAND_DISPLAY_HELP
    except Exception as e:
        log_message("error", f"Display error: {e}")

def on_tick():
    """Called every INTERVAL_MS (every frame if 0)."""
    global _input_mode

    # Process pending work from socket thread
    for _ in range(MAX_WORK_PER_TICK):
        try:
            item = _work_queue.get_nowait()
            item.execute()
        except queue.Empty:
            break

    # Advance multi-frame deferred ops (spawns, preloads) - one non-blocking poll each
    _process_deferred()

    # Apply continuous writes every frame
    apply_continuous_writes()

    # Display input prompt every frame when in input mode
    if _input_mode:
        display_input_prompt()

def on_key_down(vk: int, down: bool, shift: bool, ctrl: bool, alt: bool):
    """Handle key events."""
    global _input_mode, _input_buffer, _user_input_result

    # Only process key down events
    if not down:
        return

    # F8 = show status (always works)
    if vk == 0x77:  # F8
        handle = get_player_vehicle_handle()
        if handle:
            addr = get_vehicle_address(handle)
            show_notification(f"~b~Vehicle:~w~ 0x{addr:X}" if addr else "~r~No address")
        else:
            show_notification("~y~Not in vehicle")
        return

    # F10 = toggle input mode
    if vk == 0x79:  # F10
        if not _input_mode:
            _input_mode = True
            _input_buffer = ""
            show_notification("~g~Chat open~w~ - Type message, ENTER to send, ESC to cancel")
            log_message("info", "Input mode activated")
        else:
            show_notification("~y~Already typing...")
        return

    # If not in input mode, ignore other keys
    if not _input_mode:
        return

    # Handle special keys when in input mode
    if vk == 0x1B:  # Escape - cancel input
        _input_mode = False
        _input_buffer = ""
        show_notification("~r~Message cancelled")
        log_message("info", "Input cancelled")
        return

    if vk == 0x0D:  # Enter - submit input
        if _input_buffer.strip():
            _user_input_result = _input_buffer.strip()
            _user_message_queue.append({
                "timestamp": datetime.now().isoformat(),
                "from": "user",
                "message": _user_input_result
            })
            _chat_history.append({
                "timestamp": datetime.now().isoformat(),
                "from": "user",
                "message": _user_input_result
            })
            _pending_user_input.set()
            show_notification(f"~g~Sent:~w~ {_user_input_result[:50]}{'...' if len(_user_input_result) > 50 else ''}")
            log_message("info", f"User message: {_user_input_result}")
        else:
            show_notification("~y~Empty message - not sent")
        _input_mode = False
        _input_buffer = ""
        return

    if vk == 0x08:  # Backspace - delete last character
        if _input_buffer:
            _input_buffer = _input_buffer[:-1]
        return

    # Map virtual key to character
    if vk in VK_TO_CHAR:
        char_pair = VK_TO_CHAR[vk]
        char = char_pair[1] if shift else char_pair[0]
        _input_buffer += char

def on_aborted():
    """Called when script unloads."""
    global _server_running

    log_message("info", "Stopping...")
    _server_running = False

    if _server_thread:
        _server_thread.join(timeout=2.0)

    log_message("info", "Stopped")

# =============================================================================
# Standalone Mode (for testing without PyLoaderV)
# =============================================================================

if __name__ == "__main__":
    print("[Bridge] Running in standalone test mode (no PyLoaderV)")
    print(f"[Bridge] Socket server will listen on {BRIDGE_HOST}:{BRIDGE_PORT}")

    _server_running = True
    _server_thread = threading.Thread(target=socket_server_thread, daemon=True)
    _server_thread.start()

    print("[Bridge] Press Ctrl+C to stop")

    try:
        while True:
            # Mirror on_tick: drain work queue AND advance deferred ops (await/watch/spawn).
            for _ in range(MAX_WORK_PER_TICK):
                try:
                    item = _work_queue.get_nowait()
                    item.execute()
                except queue.Empty:
                    break
            _process_deferred()
            time.sleep(0.016)  # ~60fps
    except KeyboardInterrupt:
        print("\n[Bridge] Shutting down...")
        _server_running = False
        _server_thread.join(timeout=2.0)
        print("[Bridge] Done")
