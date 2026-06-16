"""
world_sense.py — the WORLD-SENSING layer: "where am I + what's happening" as a compact semantic snapshot.

This is the foundation for real-time play (see docs/WORLD-SENSING.md). It fuses:
  * player state (pos/heading/health/armor/weapon/wanted/in-vehicle)  -- via memory + simple natives
  * nearby entities (peds/vehicles) annotated with distance + compass + ahead/behind  -- via the bridge's
    existing pool sensing, defensively (degrades if a lister isn't available)
  * location in words (nearest landmark + bearing; zone code if available)  -- landmarks.json + natives
into get_world_state() -> a small dict + a human "summary" string Claude can act on, plus world_events()
which diffs successive snapshots (took damage, wanted changed, entered/left vehicle, new threat...).

DESIGN / HONESTY:
  * The PURE spatial+summary+event logic (bearing/distance/compass, landmark lookup, snapshot format,
    event deltas) is self-tested below (`python world_sense.py`). That's the "summarizer brain".
  * The in-game GATHERING (memory reads at +0x90/+0x280, natives by name) follows the bridge's verified
    patterns and calls natives BY NAME through the allowlist (wrong name -> safe error, never a crash).
    It is DEFENSIVE: any missing primitive is skipped, not fatal. >>> Verify the gathered values in-game. <<<
  * Out-param natives (street name, closest-vehicle-node, shape-test result) need bridge out-param support;
    raycast()/nearest_road_node() are best-effort and marked. Location leans on landmark math (no out-params).

Commands: get_world_state, describe_location, world_events, raycast, nearest_road_node, world_sense_status.
"""
import json, math, os, time

_g = {}
def bind(bridge_globals):
    _g.clear(); _g.update(bridge_globals)

_HERE = os.path.dirname(os.path.abspath(__file__))
LANDMARKS = []   # [{name, x, y, z, kind}]
_last_snapshot = {"state": None}

def _load_landmarks():
    LANDMARKS.clear()
    p = os.path.join(_HERE, "world_data", "landmarks.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            for lm in json.load(f).get("landmarks", []):
                if all(k in lm for k in ("name", "x", "y")):
                    LANDMARKS.append(lm)
    return len(LANDMARKS)

_NLM = _load_landmarks()

# ============================================================ PURE LOGIC (self-tested) ===============
_COMPASS8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

def compass(bearing_deg):
    """0=N, 90=E, 180=S, 270=W -> 8-point compass."""
    return _COMPASS8[int((bearing_deg % 360) / 45.0 + 0.5) % 8]

def bearing_to(px, py, tx, ty):
    """World compass bearing (deg, 0=N,90=E) from player(px,py) to target(tx,ty). +Y=North, +X=East."""
    return math.degrees(math.atan2(tx - px, ty - py)) % 360.0

def dist3(ax, ay, az, bx, by, bz):
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)

def relative_dir(px, py, heading_deg, tx, ty):
    """ahead/behind/left/right of the player's FACING. GTA forward = (-sin h, cos h) (matches spawn formula)."""
    rad = math.radians(heading_deg)
    fwd = (-math.sin(rad), math.cos(rad))
    right = (fwd[1], -fwd[0])               # 90deg clockwise of forward
    dx, dy = tx - px, ty - py
    n = math.hypot(dx, dy) or 1.0
    dx, dy = dx / n, dy / n
    fdot = fwd[0] * dx + fwd[1] * dy        # +ahead / -behind
    rdot = right[0] * dx + right[1] * dy    # +right / -left
    fb = "ahead" if fdot >= 0 else "behind"
    lr = "right" if rdot >= 0 else "left"
    if abs(fdot) > 0.85:
        return "ahead" if fdot > 0 else "behind"
    if abs(rdot) > 0.85:
        return lr
    return f"{fb}-{lr}"

def annotate(px, py, pz, heading, entities):
    """Add distance/compass/relative to each entity {x,y,z,...}, sorted nearest-first."""
    out = []
    for e in entities:
        ex, ey, ez = e.get("x"), e.get("y"), e.get("z", pz)
        if ex is None or ey is None:
            continue
        d = dist3(px, py, pz, ex, ey, ez)
        out.append({**e, "dist": round(d, 1),
                    "compass": compass(bearing_to(px, py, ex, ey)),
                    "rel": relative_dir(px, py, heading, ex, ey)})
    out.sort(key=lambda e: e["dist"])
    return out

def nearest_landmark(px, py, landmarks=None):
    lms = landmarks if landmarks is not None else LANDMARKS
    best = None
    for lm in lms:
        d = math.hypot(lm["x"] - px, lm["y"] - py)
        if best is None or d < best["dist"]:
            best = {"name": lm["name"], "kind": lm.get("kind", "poi"), "dist": round(d, 1),
                    "compass": compass(bearing_to(px, py, lm["x"], lm["y"]))}
    return best

def format_summary(state):
    """One-line-ish human summary Claude reads each think-cycle."""
    p = state.get("player", {})
    parts = []
    loc = state.get("location", {})
    where = loc.get("zone") or ""
    lm = loc.get("nearest_landmark")
    if lm:
        where = (where + " " if where else "") + f"~{lm['dist']:.0f}m {lm['compass']} of {lm['name']}"
    veh = p.get("vehicle")
    mode = f"driving {veh}" if veh else "on foot"
    hp = p.get("health"); wl = p.get("wanted")
    head = f"{mode}, {hp}hp" + (f", {p['armor']} armor" if p.get("armor") else "")
    if p.get("weapon"):
        head += f", {p['weapon']}"
    if wl:
        head += f", {wl} star{'s' if wl != 1 else ''}"
    parts.append(f"{head}. {where}".strip())
    obj = state.get("objective")
    if obj:
        oc = f"{obj.get('kind','objective')}"
        if obj.get("dist") is not None:
            oc += f" {obj['dist']:.0f}m {obj.get('compass','')}"
        parts.append(f"Objective: {oc}" + (" [on mission]" if state.get("on_mission") else "") + ".")
    th = state.get("threats", [])
    if th:
        ts = "; ".join(f"{t.get('label','ped')} {t['dist']:.0f}m {t['compass']} ({t['rel']})" for t in th[:4])
        parts.append(f"Threats: {ts}.")
    nv = state.get("nearby_vehicles", [])
    if nv:
        parts.append(f"{len(nv)} vehicle(s) near; closest {nv[0]['dist']:.0f}m {nv[0]['compass']}.")
    return " ".join(parts)

def diff_events(prev, cur):
    """Compare two snapshots -> list of notable events for event-driven re-planning."""
    ev = []
    if not prev:
        return [{"event": "first_snapshot"}]
    pp, cp = prev.get("player", {}), cur.get("player", {})
    if pp.get("health") is not None and cp.get("health") is not None:
        d = cp["health"] - pp["health"]
        if d <= -5:
            ev.append({"event": "took_damage", "amount": -d, "health": cp["health"]})
        elif d >= 20:
            ev.append({"event": "healed", "health": cp["health"]})
    if cp.get("wanted", 0) != pp.get("wanted", 0):
        ev.append({"event": "wanted_changed", "from": pp.get("wanted", 0), "to": cp.get("wanted", 0)})
    if bool(cp.get("vehicle")) != bool(pp.get("vehicle")):
        ev.append({"event": "entered_vehicle" if cp.get("vehicle") else "exited_vehicle",
                   "vehicle": cp.get("vehicle")})
    pth, cth = prev.get("threats", []), cur.get("threats", [])
    if len(cth) > len(pth):
        ev.append({"event": "new_threat", "count": len(cth)})
    elif len(cth) < len(pth):
        ev.append({"event": "threat_cleared", "count": len(cth)})
    if cth and (not pth or cth[0]["dist"] < (pth[0]["dist"] if pth else 1e9) - 8):
        ev.append({"event": "threat_closing", "dist": cth[0]["dist"], "compass": cth[0]["compass"]})
    return ev

# ============================================================ IN-GAME GATHERING (verify live) ========
def _native(name, args=(), ret=None):
    h = _g.get("handle_call_native_by_name")
    if not h:
        return None
    r = h({"name": name, "args": list(args), "return_type": ret})
    if isinstance(r, dict):
        return None if r.get("error") else r.get("result")
    return r

def _player_ped():
    return _native("PLAYER_PED_ID", [], "int")

def _entity_pos(handle):
    """Position for an entity handle. Primary: GET_ENTITY_COORDS (vector3) — the bridge's own proven
    player-coords path. The memory read below only works ON the game thread (gta.entity_address needs
    game-thread context), so it can't be the primary path; it's a fallback when the native is unavailable."""
    if not handle:
        return None
    v = _native("GET_ENTITY_COORDS", [handle, True], "vector3")
    if isinstance(v, (list, tuple)) and len(v) >= 3 and v[0] is not None:
        return (v[0], v[1], v[2])
    # Fallback: memory read via whatever handle->address resolver the bridge exposes (game-thread only).
    ea = _g.get("entity_address") or _g.get("get_vehicle_address"); rf = _g.get("read_float")
    if ea and rf:
        base = ea(handle)
        if base:
            x, y, z = rf(base + 0x90), rf(base + 0x94), rf(base + 0x98)
            if x is not None:
                return (x, y, z)
    return None

def gather_player():
    ped = _player_ped()
    if not ped:
        return {"error": "no player ped"}
    pos = _entity_pos(ped) or (None, None, None)
    in_veh = _native("IS_PED_IN_ANY_VEHICLE", [ped, False], "bool")
    veh_name = None
    if in_veh:
        veh = _native("GET_VEHICLE_PED_IS_IN", [ped, False], "int")
        model = _native("GET_ENTITY_MODEL", [veh], "int") if veh else None
        veh_name = (_g.get("REVERSE", {}).get("vehicles", {}) if False else {}).get(model) or (f"0x{model:X}" if model else "vehicle")
    st = {
        "x": pos[0], "y": pos[1], "z": pos[2],
        "heading": _native("GET_ENTITY_HEADING", [ped], "float"),
        "health": _native("GET_ENTITY_HEALTH", [ped], "int"),
        "armor": _native("GET_PED_ARMOUR", [ped], "int"),
        "wanted": _native("GET_PLAYER_WANTED_LEVEL", [_native("PLAYER_ID", [], "int")], "int"),
        "vehicle": veh_name,
    }
    return st

def _gather_nearby(kind):
    """Use whatever pool-lister the bridge exposes (re_tools_dynamic nearby_*). Defensive."""
    cmd = {"vehicles": "nearby_vehicles", "peds": "nearby_peds"}.get(kind)
    cmds = _g.get("COMMANDS", {})
    fn = cmds.get(cmd) if isinstance(cmds, dict) else None
    if not fn:
        return []
    try:
        r = fn({})
    except Exception:
        return []
    items = r.get("results") or r.get(kind) or r.get("entities") or [] if isinstance(r, dict) else []
    norm = []
    for it in items if isinstance(items, list) else []:
        x = it.get("x") if isinstance(it, dict) else None
        if x is None and isinstance(it, dict) and isinstance(it.get("position"), (list, tuple)):
            x, y, z = it["position"][:3]
            it = {**it, "x": x, "y": y, "z": z}
        if isinstance(it, dict) and it.get("x") is not None:
            norm.append(it)
    return norm

def get_world_state():
    player = gather_player()
    if "error" in player:
        return player
    px, py, pz, heading = player.get("x"), player.get("y"), player.get("z"), player.get("heading") or 0.0
    state = {"player": player, "t": None}
    if px is not None:
        state["location"] = describe_location_xyz(px, py, pz)
        peds = annotate(px, py, pz, heading, _gather_nearby("peds"))
        vehs = annotate(px, py, pz, heading, _gather_nearby("vehicles"))
        # a "threat" heuristic: peds flagged armed/hostile/in-combat, else just nearest peds within 60m
        threats = [p for p in peds if p.get("armed") or p.get("hostile") or p.get("in_combat")] or \
                  [p for p in peds if p["dist"] <= 60.0][:5]
        state["threats"] = threats
        state["nearby_vehicles"] = vehs[:6]
        state["nearby_peds"] = peds[:8]
        # objective (if mission_sense is loaded): annotate with distance/compass from the player
        cmds = _g.get("COMMANDS", {})
        obj_fn = cmds.get("get_objective") if isinstance(cmds, dict) else None
        if obj_fn:
            try:
                obj = obj_fn({"player_pos": [px, py, pz]})
                o = obj.get("objective") if isinstance(obj, dict) else None
                if o and o.get("coords"):
                    c = o["coords"]
                    o = {**o, "dist": round(dist3(px, py, pz, c[0], c[1], c[2]), 1),
                         "compass": compass(bearing_to(px, py, c[0], c[1]))}
                if o:
                    state["objective"] = o
                if isinstance(obj, dict) and obj.get("on_mission"):
                    state["on_mission"] = True
            except Exception:
                pass
    state["summary"] = format_summary(state)
    _last_snapshot["state"] = state
    return {"success": True, **state}

def describe_location_xyz(x, y, z):
    loc = {"coords": [round(x, 1), round(y, 1), round(z, 1)]}
    lm = nearest_landmark(x, y)
    if lm:
        loc["nearest_landmark"] = lm
    # zone code (GET_NAME_OF_ZONE returns a string; only if the bridge supports string returns)
    zone = _native("GET_NAME_OF_ZONE", [x, y, z], "string")
    if isinstance(zone, str) and zone:
        loc["zone"] = zone
    return loc

# ---- handlers ----
def handle_get_world_state(p):
    return get_world_state()
def handle_describe_location(p):
    x, y, z = p.get("x"), p.get("y"), p.get("z")
    if x is None:
        ped = _player_ped(); pos = _entity_pos(ped) if ped else None
        if not pos:
            return {"error": "no coords and no player ped"}
        x, y, z = pos
    return {"success": True, **describe_location_xyz(x, y, z)}
def handle_world_events(p):
    prev = _last_snapshot.get("state")
    cur = get_world_state()
    if "error" in cur:
        return cur
    return {"success": True, "events": diff_events(prev, cur), "summary": cur.get("summary")}
def handle_raycast(p):
    """Shape-test ray. NOTE: GET_SHAPE_TEST_RESULT uses OUT-PARAMS -> needs bridge out-param support;
    best-effort + marked. Returns the raw native results for in-game verification."""
    sx, sy, sz = p.get("from", [0, 0, 0])[:3]
    ex, ey, ez = p.get("to", [0, 0, 0])[:3]
    flags = p.get("flags", -1); ignore = p.get("ignore_entity", 0)
    h = _native("START_SHAPE_TEST_RAY", [sx, sy, sz, ex, ey, ez, flags, ignore, 7], "int")
    return {"note": "GET_SHAPE_TEST_RESULT needs out-param support; verify in-game", "shape_test_handle": h}
def handle_nearest_road_node(p):
    """GET_CLOSEST_VEHICLE_NODE -> out-param (node pos). Best-effort; needs out-param support. Use for
    on-road spawn placement (the getaway-car fix)."""
    x, y, z = p.get("x"), p.get("y"), p.get("z")
    if x is None:
        ped = _player_ped(); pos = _entity_pos(ped) if ped else None
        if pos: x, y, z = pos
    return {"note": "GET_CLOSEST_VEHICLE_NODE_WITH_HEADING needs out-param support; verify in-game",
            "queried": [x, y, z]}
def handle_world_sense_status(p):
    return {"success": True, "landmarks": len(LANDMARKS),
            "note": "memory-based player+entity sensing is paused-safe; native location/raycast/road-node need in-game verify"}

WORLD_COMMANDS = {
    "get_world_state": handle_get_world_state, "describe_location": handle_describe_location,
    "world_events": handle_world_events, "raycast": handle_raycast,
    "nearest_road_node": handle_nearest_road_node, "world_sense_status": handle_world_sense_status,
}
# Pure-data status is off-thread; the rest call natives -> game thread.
WORLD_OFFTHREAD = {"world_sense_status"}

# ============================================================ SELF-TEST ===============================
if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    # compass + bearing
    assert compass(0) == "N" and compass(90) == "E" and compass(180) == "S" and compass(270) == "W"
    assert compass(45) == "NE" and compass(315) == "NW"
    assert abs(bearing_to(0, 0, 0, 10) - 0) < 1e-6      # due north
    assert abs(bearing_to(0, 0, 10, 0) - 90) < 1e-6     # due east
    # relative direction: player at origin facing North (heading 0): forward=(-sin0,cos0)=(0,1)=+Y
    assert relative_dir(0, 0, 0, 0, 10) == "ahead"      # target north = ahead
    assert relative_dir(0, 0, 0, 0, -10) == "behind"
    # landmark lookup
    lms = [{"name": "Maze Bank", "x": 0, "y": 0}, {"name": "Pier", "x": 100, "y": 0}]
    nl = nearest_landmark(5, 0, lms)
    assert nl["name"] == "Maze Bank" and nl["compass"] in ("E", "W"), nl
    # annotate sorts nearest-first + adds fields
    ents = [{"x": 0, "y": 50, "label": "far"}, {"x": 0, "y": 10, "label": "near"}]
    ann = annotate(0, 0, 0, 0, ents)
    assert ann[0]["label"] == "near" and ann[0]["compass"] == "N" and ann[0]["rel"] == "ahead", ann
    # summary
    state = {"player": {"health": 90, "wanted": 2, "weapon": "pistol", "vehicle": None},
             "location": {"zone": "VINE", "nearest_landmark": {"name": "Cinema", "dist": 40, "compass": "N"}},
             "threats": [{"label": "cop", "dist": 15, "compass": "NE", "rel": "ahead-right"}],
             "nearby_vehicles": [{"dist": 40, "compass": "N"}]}
    s = format_summary(state)
    assert "90hp" in s and "2 stars" in s and "Cinema" in s and "cop 15m NE" in s, s
    # events
    prev = {"player": {"health": 100, "wanted": 0, "vehicle": None}, "threats": []}
    cur = {"player": {"health": 80, "wanted": 2, "vehicle": "police"}, "threats": [{"dist": 12, "compass": "N"}]}
    evs = {e["event"] for e in diff_events(prev, cur)}
    assert {"took_damage", "wanted_changed", "entered_vehicle", "new_threat"} <= evs, evs
    print("world_sense self-test PASSED — compass/bearing/relative/landmark/annotate/summary/events all correct")
    print(f"  landmarks loaded: {_NLM}  (gathering layer needs in-game verification)")
