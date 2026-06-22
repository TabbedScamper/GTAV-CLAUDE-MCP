"""
auto_drive.py — an autonomous DRIVING system built on context steering (Game AI Pro 2, Andrew Fray):
fuse a DANGER map (what the car's raycast sensors see) with an INTEREST map (where we want to go —
toward an objective, or AWAY from the cops) and steer toward the best-scoring direction. Plus two
things the stock game AI never does: roll-over self-recovery, and cop-distance-maximizing evasion.

Three sensing/acting primitives + one fused tick, all called BY NAME through the allowlist:
  drive_sense    — cast N rays around the vehicle (front/diagonals/sides/rear) -> per-direction clearance
  drive_recover  — detect a flipped car and force it back onto its wheels (SET_VEHICLE_ON_GROUND_PROPERLY)
  evade_cops     — find the nearest police vehicle (native, no fragile pools) -> a flee-away target
  auto_drive     — ONE context-steering tick: recover + sense + decide (evade/goto/cruise) + drive

DESIGN / HONESTY:
  * The raycast uses START_EXPENSIVE_SYNCHRONOUS_SHAPE_TEST_LOS_PROBE + GET_SHAPE_TEST_RESULT, whose
    results are OUT-PARAMS. We run the FULL probe in one game-thread command (these handlers are NOT
    off-thread) using the inline on-thread caller, writing the out-params into a ctypes scratch buffer
    and reading them straight back — so the one-frame synchronous result is valid when we read it.
  * Pure geometry/scoring (context steering) is deterministic and self-tested below (python auto_drive.py).
  * Cop finding uses GET_CLOSEST_VEHICLE over police MODELS (robust) instead of memory-pool scans.
"""
import math, ctypes

_g = {}
def bind(bridge_globals):
    _g.clear(); _g.update(bridge_globals)

def _cns(name, *args, ret="int"):
    """Inline on-thread native call via the bridge's verified caller (same frame, same script ctx)."""
    f = _g.get("_call_native_safe")
    return f(name, *args, return_type=ret) if f else None
def _rdi(addr):
    f = _g.get("read_int");  return f(addr) if f else None
def _rdf(addr):
    f = _g.get("read_float"); return f(addr) if f else None

# ---- scratch buffer for shape-test out-params (hit BOOL*, endCoords V3*, normal V3*, entity*) ----
_BUF = None
def _buf_addr():
    global _BUF
    if _BUF is None:
        _BUF = ctypes.create_string_buffer(96)
    return ctypes.addressof(_BUF)

# ============================================================ PURE LOGIC (self-tested) ===============
def unit(dx, dy):
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n

def dir_from_forward(fwd, right, ang_deg):
    """World XY direction `ang_deg` clockwise from the vehicle's forward (0=front,90=right,180=rear)."""
    a = math.radians(ang_deg)
    c, s = math.cos(a), math.sin(a)
    return (fwd[0] * c + right[0] * s, fwd[1] * c + right[1] * s)

# ray ring: (angle_from_forward, label, range_m). Front sees furthest; rear shortest.
RAYS = [(0, "front", 45), (45, "front_right", 30), (90, "right", 18), (135, "rear_right", 14),
        (180, "rear", 18), (225, "rear_left", 14), (270, "left", 18), (315, "front_left", 30)]

def context_choose(desired_dir, sensors, fwd, right, hard_block=6.0):
    """Context steering: score 8 slots by alignment-with-desired minus danger(closeness). Returns
    (best_angle_from_forward, best_world_dir, scores). sensors: {label: clearance_m or None}."""
    best = None
    scores = {}
    for ang, label, rng in RAYS:
        wdir = dir_from_forward(fwd, right, ang)
        align = wdir[0] * desired_dir[0] + wdir[1] * desired_dir[1]      # -1..1 (1 = exactly desired way)
        clr = sensors.get(label)
        clr = rng if clr is None else clr                                # None = clear to full range
        danger = max(0.0, 1.0 - clr / rng)                               # 0 clear .. 1 touching
        blocked = clr <= hard_block
        score = align - 1.6 * danger - (5.0 if blocked else 0.0)         # avoid blocked, prefer desired
        scores[label] = round(score, 2)
        if best is None or score > best[0]:
            best = (score, ang, wdir)
    return best[1], best[2], scores

# ============================================================ IN-GAME (verify live) =================
_POLICE_MODELS = ("police", "police2", "police3", "police4", "policet", "sheriff", "sheriff2", "fbi", "fbi2")
_state = {"last_target": None}

def _player_vehicle():
    ped = _cns("PLAYER_PED_ID", ret="int")
    if not ped or not _cns("IS_PED_IN_ANY_VEHICLE", ped, False, ret="bool"):
        return ped, 0
    return ped, _cns("GET_VEHICLE_PED_IS_IN", ped, False, ret="int")

def _vec3(name, *a):
    v = _cns(name, *a, ret="vector3")
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return (float(v[0]), float(v[1]), float(v[2]))
    if isinstance(v, dict):
        return (float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0)))
    return None

def _raycast(ox, oy, oz, ex, ey, ez, ignore):
    """One synchronous LOS probe -> (hit, hitcoord, entity). Full sequence in this one game-thread call."""
    base = _buf_addr(); ctypes.memset(base, 0, 96)
    # ScriptHookV Vector3 is 24 bytes (each float + 4 pad, 8-byte stride). Layout of the out-params:
    #   hit BOOL @ +0 | endCoords V3 @ +8 (x+8,y+16,z+24) | normal V3 @ +32 | entity @ +56
    HIT, CO, NO, ENT = base, base + 8, base + 32, base + 56
    h = _cns("START_EXPENSIVE_SYNCHRONOUS_SHAPE_TEST_LOS_PROBE", ox, oy, oz, ex, ey, ez, -1, ignore, 7, ret="int")
    status = _cns("GET_SHAPE_TEST_RESULT", h, HIT, CO, NO, ENT, ret="int")
    hit = _rdi(HIT) or 0
    hx, hy, hz = _rdf(CO), _rdf(CO + 8), _rdf(CO + 16)
    ent = _rdi(ENT) or 0
    return status, hit, (hx, hy, hz), ent

def _sense(veh, origin, fwd, right):
    """Cast the ray ring at TWO heights (low for bumpers/low cars, high for walls/peds), take the
    nearest hit per direction. Anchored to the GROUND so low vehicles aren't passed over."""
    hag = _cns("GET_ENTITY_HEIGHT_ABOVE_GROUND", veh, ret="float") or 0.0
    if not (0.0 <= hag <= 3.0):                                # airborne/garbage -> sane fallback
        hag = 0.4
    ground = origin[2] - hag
    z_lo, z_hi = ground + 0.35, ground + 0.95                  # bumper height, then window/ped height
    ox, oy = origin[0], origin[1]
    out = {}
    for ang, label, rng in RAYS:
        wd = dir_from_forward(fwd, right, ang)
        sx, sy = ox + wd[0] * 2.0, oy + wd[1] * 2.0            # start just outside our own bumper
        ex, ey = ox + wd[0] * rng, oy + wd[1] * rng
        best, bent = None, 0
        for z in (z_lo, z_hi):
            status, hit, hc, ent = _raycast(sx, sy, z, ex, ey, z, veh)
            if hit and hc[0] is not None:
                d = min(math.dist((sx, sy, z), hc) + 2.0, rng)
                if best is None or d < best:
                    best, bent = d, ent
        out[label] = {"clear": round(best, 1) if best is not None else None, "entity": bent}
    return out

def handle_drive_sense(p):
    ped, veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    origin = _vec3("GET_ENTITY_COORDS", veh, True); fwd3 = _vec3("GET_ENTITY_FORWARD_VECTOR", veh)
    if not origin or not fwd3:
        return {"error": "no vehicle pose"}
    fwd = unit(fwd3[0], fwd3[1]); right = (fwd[1], -fwd[0])
    sensors = _sense(veh, origin, fwd, right)
    return {"success": True, "sensors": sensors,
            "summary": "; ".join(f"{k} {('%.0fm'%v['clear']) if v['clear'] is not None else 'clear'}"
                                 for k, v in sensors.items())}

def handle_drive_sense_debug(p):
    """Cast ONE forward ray and dump the raw out-param buffer (to confirm offsets live)."""
    ped, veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    origin = _vec3("GET_ENTITY_COORDS", veh, True); fwd3 = _vec3("GET_ENTITY_FORWARD_VECTOR", veh)
    fwd = unit(fwd3[0], fwd3[1])
    hag = _cns("GET_ENTITY_HEIGHT_ABOVE_GROUND", veh, ret="float") or 0.0
    if not (0.0 <= hag <= 3.0): hag = 0.4
    ox, oy, oz = origin[0], origin[1], origin[2] - hag + 0.35   # ground + bumper height
    rng = float(p.get("range", 45.0))
    sx, sy = ox + fwd[0] * 2.0, oy + fwd[1] * 2.0
    ex, ey = ox + fwd[0] * rng, oy + fwd[1] * rng
    base = _buf_addr(); ctypes.memset(base, 0, 96)
    HIT, CO, NO, ENT = base, base + 8, base + 32, base + 56
    h = _cns("START_EXPENSIVE_SYNCHRONOUS_SHAPE_TEST_LOS_PROBE", sx, sy, oz, ex, ey, oz, -1, veh, 7, ret="int")
    status = _cns("GET_SHAPE_TEST_RESULT", h, HIT, CO, NO, ENT, ret="int")
    words_i = [_rdi(base + 4 * i) for i in range(16)]
    words_f = [round(_rdf(base + 4 * i), 2) if _rdf(base + 4 * i) is not None else None for i in range(16)]
    hit = _rdi(HIT); hx, hy, hz = _rdf(CO), _rdf(CO + 8), _rdf(CO + 16); ent = _rdi(ENT)
    d = math.dist((sx, sy, oz), (hx or 0, hy or 0, hz or 0)) + 2.0 if hit else None
    return {"success": True, "status": status, "handle": h, "hit": hit, "entity": ent,
            "hit_xyz": [hx, hy, hz], "origin_xy": [round(ox, 1), round(oy, 1)],
            "computed_front_m": round(d, 1) if d else None,
            "raw_int32": words_i, "raw_float": words_f}

def handle_drive_recover(p):
    ped, veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    on_wheels = _cns("IS_VEHICLE_ON_ALL_WHEELS", veh, ret="bool")
    roll = abs(_cns("GET_ENTITY_ROLL", veh, ret="float") or 0.0)
    upside = _cns("IS_ENTITY_UPSIDEDOWN", veh, ret="bool")
    flipped = (not on_wheels and roll > 75.0) or bool(upside)
    recovered = False
    if flipped:
        _cns("SET_VEHICLE_ON_GROUND_PROPERLY", veh, 5.0, ret="bool")
        recovered = bool(_cns("IS_VEHICLE_ON_ALL_WHEELS", veh, ret="bool"))
    return {"success": True, "flipped": flipped, "recovered": recovered,
            "roll": round(roll, 1), "on_wheels": bool(on_wheels)}

def _nearest_cop(px, py, pz, radius=130.0):
    """Nearest police vehicle by MODEL (no memory pools). Returns (handle, pos, dist) or None."""
    best = None
    for m in _POLICE_MODELS:
        mh = _cns("GET_HASH_KEY", m, ret="int")
        v = _cns("GET_CLOSEST_VEHICLE", px, py, pz, radius, mh, 70, ret="int")
        if v and _cns("DOES_ENTITY_EXIST", v, ret="bool"):
            cp = _vec3("GET_ENTITY_COORDS", v, True)
            if cp:
                d = math.dist((px, py, pz), cp)
                if best is None or d < best[2]:
                    best = (v, cp, d)
    return best

def handle_evade_cops(p):
    ped, veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    pos = _vec3("GET_ENTITY_COORDS", veh, True)
    cop = _nearest_cop(*pos)
    if not cop:
        return {"success": True, "cop": None, "note": "no police vehicle nearby"}
    fdx, fdy = unit(pos[0] - cop[1][0], pos[1] - cop[1][1])         # away from the cop
    tgt = [pos[0] + fdx * 150.0, pos[1] + fdy * 150.0, pos[2]]
    return {"success": True, "cop_dist": round(cop[2], 1), "flee_target": [round(t, 1) for t in tgt],
            "flee_dir": [round(fdx, 2), round(fdy, 2)]}

def handle_auto_drive(p):
    """One context-steering tick. mode: 'evade' (flee cops, maximize distance), 'goto' (params x,y,z),
    or 'cruise'. Always runs rollover recovery + the sensor ring first."""
    ped, veh = _player_vehicle()
    if not veh:
        return {"error": "not in a vehicle"}
    rec = handle_drive_recover({})
    pos = _vec3("GET_ENTITY_COORDS", veh, True); fwd3 = _vec3("GET_ENTITY_FORWARD_VECTOR", veh)
    if not pos or not fwd3:
        return {"error": "no vehicle pose"}
    fwd = unit(fwd3[0], fwd3[1]); right = (fwd[1], -fwd[0])
    sensors = _sense(veh, pos, fwd, right)
    clr = {k: v["clear"] for k, v in sensors.items()}

    mode = p.get("mode", "cruise"); speed = float(p.get("speed", 22.0)); style = int(p.get("style", 786469))
    cop_dist = None; desired = fwd
    if mode == "evade":
        cop = _nearest_cop(*pos)
        if cop:
            cop_dist = round(cop[2], 1)
            desired = unit(pos[0] - cop[1][0], pos[1] - cop[1][1])      # interest = away from heat
            speed = float(p.get("speed", 38.0)); style = 786469          # fast getaway
            _cns("SET_DRIVER_AGGRESSIVENESS", ped, 1.0, ret="void")
        else:
            desired = fwd
    elif mode == "goto":
        tx, ty = float(p.get("x", pos[0])), float(p.get("y", pos[1]))
        desired = unit(tx - pos[0], ty - pos[1])
        _cns("SET_DRIVER_AGGRESSIVENESS", ped, float(p.get("aggressiveness", 0.0)), ret="void")
    _cns("SET_DRIVER_ABILITY", ped, 1.0, ret="void")

    ang, wdir, scores = context_choose(desired, clr, fwd, right)
    target = [pos[0] + wdir[0] * 70.0, pos[1] + wdir[1] * 70.0, pos[2]]
    # dry_run: perceive + decide but DON'T drive (safe validation — no risk of driving into anything)
    if p.get("dry_run"):
        return {"success": True, "mode": mode, "dry_run": True, "recover": rec, "cop_dist": cop_dist,
                "chosen_deg_from_fwd": ang, "sensors": clr, "scores": scores,
                "summary": f"[dry] mode={mode} would steer {ang}deg off-fwd"}
    # only re-task when the chosen target moved enough (avoid per-tick stutter)
    lt = _state.get("last_target")
    if lt is None or math.dist(lt[:2], target[:2]) > 18.0:
        _cns("TASK_VEHICLE_DRIVE_TO_COORD_LONGRANGE", ped, veh,
             target[0], target[1], target[2], speed, style, 10.0, ret="void")
        _state["last_target"] = target
        retasked = True
    else:
        retasked = False
    return {"success": True, "mode": mode, "recover": rec, "cop_dist": cop_dist,
            "chosen_deg_from_fwd": ang, "retasked": retasked,
            "sensors": clr, "scores": scores,
            "summary": (f"mode={mode}" + (f" cop@{cop_dist}m" if cop_dist is not None else "") +
                        f" steer {ang}° off-fwd, speed {speed:.0f}" +
                        (f", RECOVERED" if rec.get("recovered") else ""))}

AUTO_DRIVE_COMMANDS = {
    "drive_sense": handle_drive_sense, "drive_sense_debug": handle_drive_sense_debug,
    "drive_recover": handle_drive_recover,
    "evade_cops": handle_evade_cops, "auto_drive": handle_auto_drive,
}
AUTO_DRIVE_OFFTHREAD = set()   # all touch natives -> game thread

# ============================================================ SELF-TEST =============================
if __name__ == "__main__":
    # forward=North (0,1), right=East (1,0)
    fwd, right = (0.0, 1.0), (1.0, 0.0)
    assert dir_from_forward(fwd, right, 0) == (0.0, 1.0)              # front = North
    d90 = dir_from_forward(fwd, right, 90)
    assert abs(d90[0] - 1.0) < 1e-9 and abs(d90[1]) < 1e-9, d90       # right = East
    # context steering: want to go North, front blocked at 3m, front-left clear -> steer left-ish
    sensors = {"front": 3.0, "front_left": None, "front_right": None,
               "left": None, "right": None, "rear": None, "rear_left": None, "rear_right": None}
    ang, wdir, scores = context_choose((0.0, 1.0), sensors, fwd, right)
    assert ang != 0, ("should not pick blocked front", scores)
    assert scores["front"] < scores["front_left"], scores            # blocked front scored below clear
    # want North, everything clear -> pick front
    clear = {k: None for k, _, _ in RAYS}
    ang2, _, _ = context_choose((0.0, 1.0), clear, fwd, right)
    assert ang2 == 0, ang2
    print("auto_drive self-test PASSED — dir geometry + context steering (block-avoid, desired-seek) correct")
