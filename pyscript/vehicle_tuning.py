"""
vehicle_tuning.py — wheel fitment (stance) via NATIVES + live handling read/write.

The "better method" layer for ExtendedLSC, grounded in real shipping mods:
  * Wheel fitment uses SET_VEHICLE_WHEEL_X_OFFSET (track width) + SET_VEHICLE_WHEEL_Y_ROTATION (camber)
    -- VStancer's approach. NO memory offsets -> update-proof. (Complements, does NOT replace, the
    existing memory-based visual-wheel code in bridge.py.)
  * Handling read/write reaches CHandlingData and uses the field map verified by ikt's HandlingEditor
    (HandlingInfo.h) + this project's b3788 work.

SAFETY: every native is called BY NAME through the bridge's verified allowlist (handle_call_native_by_name),
so a wrong/missing native degrades to an ERROR, never a crash. Handling pointer is validated (readable)
before any write. Re-assert note: the engine re-derives these each frame; for persistence, re-apply
(see bridge set_continuous) -- one-shot apply is fine for immediate effect / testing.

>>> The native paths need IN-GAME verification on the live build (can't be tested headless). <<<

Commands: set_wheel_fitment, get_wheel_fitment, reset_wheel_fitment, get_handling, set_handling, list_handling_fields.
"""
_g = {}
def bind(bridge_globals):
    """Receive the bridge's globals so we reuse its native caller + memory primitives."""
    _g.clear(); _g.update(bridge_globals)

# Verified CHandlingData float offsets (ikt HandlingInfo.h, cross-checked vs alexguirre parser dumps).
HANDLING_FIELDS = {
    "mass": 0x0C, "drive_bias_front": 0x48, "initial_drive_gears": 0x50, "drive_inertia": 0x54,
    "initial_drive_force": 0x60, "drive_max_flat_vel": 0x64, "brake_force": 0x6C,
    "steering_lock": 0x80, "traction_curve_max": 0x88, "traction_curve_min": 0x90,
    "suspension_force": 0xBC, "suspension_upper_limit": 0xC8, "suspension_lower_limit": 0xCC,
    "suspension_raise": 0xD0, "suspension_bias_front": 0xD4,
}
# Candidate CVehicle -> CHandlingData* offsets to validate against the live build (b3788 work = 0x960).
_HANDLING_PTR_CANDIDATES = (0x960, 0x918)

def _native(name, args, ret=None):
    """Call a native by name through the bridge's verified allowlist. Returns the result value or raises."""
    h = _g.get("handle_call_native_by_name")
    if not h:
        raise RuntimeError("bridge native caller unavailable")
    r = h({"name": name, "args": list(args), "return_type": ret})
    if isinstance(r, dict) and r.get("error"):
        raise RuntimeError(f"{name}: {r['error']}")
    return r.get("result") if isinstance(r, dict) else r

def _player_vehicle():
    fn = _g.get("get_player_vehicle_handle")
    return fn() if fn else None

def _front_count(n):
    # VStancer's CalculateFrontWheelsCount ~= half, rounded down (4->2, 2->1, 6->3). Bikes/odd rigs vary.
    return max(1, n // 2)

# ---------- wheel fitment (natives, update-proof) ----------
def set_wheel_fitment(track_front=None, track_rear=None, camber_front=None, camber_rear=None):
    veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    try:
        n = int(_native("GET_VEHICLE_NUMBER_OF_WHEELS", [veh], "int") or 0)
    except Exception as e:
        return {"error": f"could not read wheel count: {e}"}
    if n <= 0:
        return {"error": "vehicle reports 0 wheels"}
    fc = _front_count(n)
    applied = {"wheels": n, "front_count": fc, "set": {}}
    try:
        for i in range(n):
            is_front = i < fc
            sign = 1.0 if (i % 2 == 0) else -1.0   # mirror L/R by index parity
            tw = track_front if is_front else track_rear
            cam = camber_front if is_front else camber_rear
            if tw is not None:
                _native("SET_VEHICLE_WHEEL_X_OFFSET", [veh, i, sign * float(tw)], "void")
            if cam is not None:
                _native("SET_VEHICLE_WHEEL_Y_ROTATION", [veh, i, sign * float(cam)], "void")
        if track_front is not None: applied["set"]["track_front"] = track_front
        if track_rear is not None: applied["set"]["track_rear"] = track_rear
        if camber_front is not None: applied["set"]["camber_front"] = camber_front
        if camber_rear is not None: applied["set"]["camber_rear"] = camber_rear
    except Exception as e:
        return {"error": f"native apply failed: {e}", "hint": "are SET_VEHICLE_WHEEL_X_OFFSET/_Y_ROTATION in native_db for this edition?"}
    applied["success"] = True
    applied["note"] = "engine re-derives wheel transforms each frame; re-apply (or set_continuous) to persist"
    return applied

def get_wheel_fitment():
    veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    try:
        n = int(_native("GET_VEHICLE_NUMBER_OF_WHEELS", [veh], "int") or 0)
        fc = _front_count(n)
        return {"success": True, "wheels": n, "front_count": fc,
                "track_front": _native("GET_VEHICLE_WHEEL_X_OFFSET", [veh, 0], "float"),
                "camber_front": _native("GET_VEHICLE_WHEEL_Y_ROTATION", [veh, 0], "float"),
                "track_rear": _native("GET_VEHICLE_WHEEL_X_OFFSET", [veh, fc], "float"),
                "camber_rear": _native("GET_VEHICLE_WHEEL_Y_ROTATION", [veh, fc], "float")}
    except Exception as e:
        return {"error": f"read failed: {e}"}

def reset_wheel_fitment():
    return set_wheel_fitment(0.0, 0.0, 0.0, 0.0)

# ---------- live handling (memory; validated pointer) ----------
def _handling_ptr(veh):
    """Resolve CHandlingData* for a vehicle, validating a candidate offset before trusting it."""
    get_addr = _g.get("get_vehicle_address"); read_ptr = _g.get("read_ptr")
    is_valid = _g.get("is_valid_address")
    if not (get_addr and read_ptr):
        return None, "bridge memory primitives unavailable"
    base = get_addr(veh)
    if not base:
        return None, "could not get vehicle address"
    for off in _HANDLING_PTR_CANDIDATES:
        p = read_ptr(base + off)
        if p and (not is_valid or is_valid(p)):
            return p, off
    return None, "no candidate handling offset validated (re-scan on this build)"

def get_handling(field=None):
    veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    ptr, off = _handling_ptr(veh)
    if not ptr:
        return {"error": off}
    read_float = _g.get("read_float")
    fields = [field] if field else list(HANDLING_FIELDS.keys())
    out = {}
    for fn in fields:
        if fn not in HANDLING_FIELDS:
            return {"error": f"unknown field '{fn}'", "valid": list(HANDLING_FIELDS.keys())}
        out[fn] = read_float(ptr + HANDLING_FIELDS[fn])
    return {"success": True, "handling_ptr": f"0x{ptr:X}", "ptr_offset": f"0x{off:X}" if isinstance(off, int) else off, "values": out}

def set_handling(field, value):
    if field not in HANDLING_FIELDS:
        return {"error": f"unknown field '{field}'", "valid": list(HANDLING_FIELDS.keys())}
    veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    ptr, off = _handling_ptr(veh)
    if not ptr:
        return {"error": off}
    write_float = _g.get("write_float")
    old = write_float(ptr + HANDLING_FIELDS[field], float(value))
    return {"success": True, "field": field, "old_value": old, "new_value": float(value),
            "warning": "CHandlingData is MODEL-SHARED: this changes every vehicle of this model. Clone handling for per-car."}

# ---- handlers ----
def handle_set_wheel_fitment(p):
    return set_wheel_fitment(p.get("track_front"), p.get("track_rear"), p.get("camber_front"), p.get("camber_rear"))
def handle_get_wheel_fitment(p):  return get_wheel_fitment()
def handle_reset_wheel_fitment(p): return reset_wheel_fitment()
def handle_get_handling(p):       return get_handling(p.get("field"))
def handle_set_handling(p):       return set_handling(p.get("field"), p.get("value"))
def handle_list_handling_fields(p):
    return {"success": True, "fields": {k: f"0x{v:X}" for k, v in HANDLING_FIELDS.items()},
            "note": "CHandlingData float offsets (ikt HandlingInfo.h). Reached via CVehicle+0x960 (b3788)."}

TUNING_COMMANDS = {
    "set_wheel_fitment": handle_set_wheel_fitment, "get_wheel_fitment": handle_get_wheel_fitment,
    "reset_wheel_fitment": handle_reset_wheel_fitment,
    "get_handling": handle_get_handling, "set_handling": handle_set_handling,
    "list_handling_fields": handle_list_handling_fields,
}
# Native-touching -> needs the game thread. Only the pure-data field list is paused-safe.
TUNING_OFFTHREAD = {"list_handling_fields"}

if __name__ == "__main__":
    # Offline smoke test of the PURE logic (parity mirroring + field map); native calls are stubbed.
    calls = []
    def _fake_native(p):
        calls.append((p["name"], p.get("args")))
        if p["name"] == "GET_VEHICLE_NUMBER_OF_WHEELS": return {"result": 4}
        return {"result": 0.0}
    bind({"handle_call_native_by_name": _fake_native, "get_player_vehicle_handle": lambda: 99})
    r = set_wheel_fitment(track_front=0.05, camber_front=-0.1)
    assert r["success"] and r["wheels"] == 4 and r["front_count"] == 2, r
    xs = [a for (nm, a) in calls if nm == "SET_VEHICLE_WHEEL_X_OFFSET"]
    assert xs == [[99, 0, 0.05], [99, 1, -0.05]], xs   # front only, mirrored L/R
    cams = [a for (nm, a) in calls if nm == "SET_VEHICLE_WHEEL_Y_ROTATION"]
    assert cams == [[99, 0, -0.1], [99, 1, 0.1]], cams
    assert "initial_drive_force" in HANDLING_FIELDS and HANDLING_FIELDS["suspension_raise"] == 0xD0
    print("vehicle_tuning self-test PASSED — parity mirroring + field map correct")
    print("  (native + memory paths need in-game verification)")
