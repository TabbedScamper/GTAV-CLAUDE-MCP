"""
mission_sense.py — the MISSION-OBJECTIVE sensor: "what should I be doing right now?"

Turns "wanders the map" into "pursues the current objective". It reads the game's BLIPS to find where
the player is being directed: the GPS-routed objective, the user waypoint, hostile target blips, etc.
All via verified natives (confirmed present in native_db.json):
  GET_STANDARD_BLIP_ENUM_ID / GET_WAYPOINT_BLIP_ENUM_ID  -> the sprite "buckets" to enumerate
  GET_FIRST_BLIP_INFO_ID(sprite) / GET_NEXT_BLIP_INFO_ID(sprite) / DOES_BLIP_EXIST  -> walk the blips
  GET_BLIP_COORDS (Vector3) / GET_BLIP_SPRITE / GET_BLIP_COLOUR / GET_BLIP_INFO_ID_ENTITY_INDEX
  DOES_BLIP_HAVE_GPS_ROUTE  -> the blip the game is ROUTING to == the objective destination (the prize)
  IS_BLIP_FLASHING / IS_BLIP_SHORT_RANGE / GET_MISSION_FLAG / IS_WAYPOINT_ACTIVE

This unlocks the SIMPLE story objectives (go-to / reach-the-marker / kill-the-target) for the play-agent:
the strategist/reflex gets `objective = {coords, kind, entity}` and pursues it. It does NOT read objective
TEXT (that needs HUD/vision) or handle timed/stealth/QTE beats — see docs/PLAY-AND-WATCH.md for the limits.

HONESTY: the classify/pick-primary LOGIC is pure + self-tested (`python mission_sense.py`). The blip
enumeration calls natives by name through the allowlist (wrong name -> safe error, never a crash) and the
Vector3 coord return shape needs IN-GAME verification (normalized defensively here).

Commands: get_objective, get_objectives, is_on_mission.
"""
_g = {}
def bind(bridge_globals):
    _g.clear(); _g.update(bridge_globals)

_MAX_BLIPS = 200   # safety cap on enumeration

# Blip colour ids (R* standard): 1=red(enemy), 2=green(friend), 5=yellow(objective), 3=blue(player/route)
COL_RED, COL_GREEN, COL_YELLOW = 1, 2, 5

def _native(name, args=(), ret=None):
    h = _g.get("handle_call_native_by_name")
    if not h:
        return None
    r = h({"name": name, "args": list(args), "return_type": ret})
    if isinstance(r, dict):
        return None if r.get("error") else r.get("result")
    return r

def _coords(v):
    """Normalize a Vector3 native return (list/tuple/dict) -> (x,y,z) or None."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return (float(v[0]), float(v[1]), float(v[2]))
    if isinstance(v, dict):
        try:
            return (float(v["x"]), float(v["y"]), float(v["z"]))
        except (KeyError, TypeError, ValueError):
            return None
    return None

# ---------------------------------------------------------------- pure logic (self-tested)
def classify(blip):
    """Categorize one gathered blip dict -> kind. Order matters (most-specific first)."""
    if blip.get("is_waypoint"):
        return "waypoint"
    if blip.get("has_route"):
        return "route_destination"
    col = blip.get("colour")
    if blip.get("entity") and col == COL_RED:
        return "enemy"
    if col == COL_RED:
        return "enemy"
    if col == COL_GREEN:
        return "friend"
    if blip.get("flashing") or col == COL_YELLOW:
        return "objective"
    return "marker"

_PRIORITY = {"route_destination": 0, "waypoint": 1, "enemy": 2, "objective": 3, "friend": 4, "marker": 5}

def pick_primary(blips, player_pos=None):
    """Pick the blip the agent should act on: routed destination > waypoint > nearest enemy/objective."""
    if not blips:
        return None
    def key(b):
        pr = _PRIORITY.get(b.get("kind", "marker"), 9)
        d = 0.0
        if player_pos and b.get("coords"):
            d = sum((player_pos[i] - b["coords"][i]) ** 2 for i in range(3))
        return (pr, d)
    return sorted(blips, key=key)[0]

# ---------------------------------------------------------------- in-game gathering (verify live)
def _enumerate_sprite(sprite, is_waypoint=False, out=None, seen=None):
    out = out if out is not None else []
    seen = seen if seen is not None else set()
    blip = _native("GET_FIRST_BLIP_INFO_ID", [sprite], "int")
    guard = 0
    while blip and guard < _MAX_BLIPS:
        guard += 1
        if _native("DOES_BLIP_EXIST", [blip], "bool") and blip not in seen:
            seen.add(blip)
            ent = _native("GET_BLIP_INFO_ID_ENTITY_INDEX", [blip], "int") or 0
            b = {
                "handle": blip,
                "coords": _coords(_native("GET_BLIP_COORDS", [blip], "vector3")),
                "sprite": _native("GET_BLIP_SPRITE", [blip], "int"),
                "colour": _native("GET_BLIP_COLOUR", [blip], "int"),
                "has_route": bool(_native("DOES_BLIP_HAVE_GPS_ROUTE", [blip], "bool")),
                "flashing": bool(_native("IS_BLIP_FLASHING", [blip], "bool")),
                "entity": ent if ent and ent > 0 else None,
                "is_waypoint": is_waypoint,
            }
            b["kind"] = classify(b)
            out.append(b)
        blip = _native("GET_NEXT_BLIP_INFO_ID", [sprite], "int")
    return out

def gather_blips():
    std = _native("GET_STANDARD_BLIP_ENUM_ID", [], "int")
    wp = _native("GET_WAYPOINT_BLIP_ENUM_ID", [], "int")
    out, seen = [], set()
    if std:
        _enumerate_sprite(std, False, out, seen)
    if wp:
        _enumerate_sprite(wp, True, out, seen)
    return out

def get_objectives(player_pos=None):
    blips = gather_blips()
    primary = pick_primary(blips, player_pos)
    return {"success": True,
            "on_mission": bool(_native("GET_MISSION_FLAG", [], "bool")),
            "waypoint_active": bool(_native("IS_WAYPOINT_ACTIVE", [], "bool")),
            "count": len(blips),
            "primary": primary,
            "blips": blips}

def get_objective(player_pos=None):
    """The compact primary objective for the agent loop: kind + coords (+ target entity)."""
    blips = gather_blips()
    primary = pick_primary(blips, player_pos)
    on_mission = bool(_native("GET_MISSION_FLAG", [], "bool"))
    if not primary:
        return {"success": True, "on_mission": on_mission, "objective": None}
    return {"success": True, "on_mission": on_mission,
            "objective": {"kind": primary.get("kind"), "coords": primary.get("coords"),
                          "entity": primary.get("entity"), "has_route": primary.get("has_route")}}

# ---- handlers ----
def handle_get_objective(p):  return get_objective(p.get("player_pos"))
def handle_get_objectives(p): return get_objectives(p.get("player_pos"))
def handle_is_on_mission(p):
    return {"success": True, "on_mission": bool(_native("GET_MISSION_FLAG", [], "bool")),
            "waypoint_active": bool(_native("IS_WAYPOINT_ACTIVE", [], "bool"))}

MISSION_COMMANDS = {"get_objective": handle_get_objective, "get_objectives": handle_get_objectives,
                    "is_on_mission": handle_is_on_mission}
MISSION_OFFTHREAD = set()   # all call natives -> game thread

if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    # classify
    assert classify({"has_route": True}) == "route_destination"
    assert classify({"is_waypoint": True}) == "waypoint"
    assert classify({"colour": 1, "entity": 7}) == "enemy"
    assert classify({"colour": 2}) == "friend"
    assert classify({"colour": 5}) == "objective"
    assert classify({"flashing": True}) == "objective"
    assert classify({"colour": 0}) == "marker"
    # pick_primary: route beats waypoint beats enemy; ties broken by distance
    blips = [
        {"kind": "marker", "coords": (0, 0, 0)},
        {"kind": "enemy", "coords": (100, 0, 0)},
        {"kind": "route_destination", "coords": (500, 0, 0)},
        {"kind": "waypoint", "coords": (10, 0, 0)},
    ]
    assert pick_primary(blips)["kind"] == "route_destination"
    assert pick_primary([b for b in blips if b["kind"] != "route_destination"])["kind"] == "waypoint"
    twoenemies = [{"kind": "enemy", "coords": (100, 0, 0)}, {"kind": "enemy", "coords": (10, 0, 0)}]
    assert pick_primary(twoenemies, player_pos=(0, 0, 0))["coords"] == (10, 0, 0)  # nearer enemy
    # coords normalization
    assert _coords([1, 2, 3]) == (1.0, 2.0, 3.0) and _coords({"x": 1, "y": 2, "z": 3}) == (1.0, 2.0, 3.0)
    assert _coords(None) is None
    print("mission_sense self-test PASSED — classify + pick_primary + coord-normalize correct")
    print("  (blip enumeration + Vector3 coord return need in-game verification)")
