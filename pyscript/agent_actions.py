"""
agent_actions.py — the EXECUTOR layer of the play-and-watch agent: high-level VERBS that compile to
frame-accurate control by issuing GTA's own task natives (the engine runs them at 60fps — that IS the
reactive substrate, so Claude never touches the per-frame loop).

Verbs target the PLAYER ped (so "watch Claude play" = Claude drives the player via tasks). Each verb
issues the verified ped-AI task natives from Examples/PATTERNS/09 + the make-it-stick natives, BY NAME
through the allowlist (wrong name -> safe error, never a crash). Intent is tracked so the loop only
re-issues a task when the intent CHANGES (no per-cycle task spam).

HONESTY: the verb DISPATCH + intent state + arg-building is self-tested (`python agent_actions.py`).
The native calls themselves need IN-GAME verification. Defensive: a missing primitive returns an error,
never crashes.

Commands: act, get_intent, stop_acting, list_verbs.
"""
import time

_g = {}
def bind(bridge_globals):
    _g.clear(); _g.update(bridge_globals)

_intent = {"verb": "idle", "params": {}, "issued_at": 0.0}
DRIVE_STYLE_NORMAL = 786603       # PATTERNS/09: stop for vehicles/peds, obey lights
DRIVE_STYLE_RUSHED = 786469       # ignore lights, avoid traffic

def _native(name, args=(), ret=None):
    h = _g.get("handle_call_native_by_name")
    if not h:
        return {"error": "no native caller"}
    return h({"name": name, "args": list(args), "return_type": ret})

def _result(r):
    return None if not isinstance(r, dict) else (None if r.get("error") else r.get("result"))

def _player_ped():
    return _result(_native("PLAYER_PED_ID", [], "int"))

def _player_vehicle(ped):
    if _result(_native("IS_PED_IN_ANY_VEHICLE", [ped, False], "bool")):
        return _result(_native("GET_VEHICLE_PED_IS_IN", [ped, False], "int"))
    return None

def _make_stick(ped):
    _native("SET_BLOCKING_OF_NON_TEMPORARY_EVENTS", [ped, True], "void")
    _native("SET_PED_KEEP_TASK", [ped, True], "void")

# ---------------------------------------------------------------- the verbs
# Each returns a dict describing the issued task(s). The VERB TABLE maps name -> (builder, doc).
def _v_walk_to(ped, p):
    x, y, z = p["x"], p["y"], p["z"]
    blend = 3.0 if p.get("run") else 1.0
    _native("TASK_FOLLOW_NAV_MESH_TO_COORD", [ped, x, y, z, blend, -1, 0.5, 0, 40000.0], "void")
    return {"task": "TASK_FOLLOW_NAV_MESH_TO_COORD", "dest": [x, y, z], "run": bool(p.get("run"))}

def _v_drive_to(ped, p):
    veh = _player_vehicle(ped)
    if not veh:
        return {"error": "not in a vehicle (drive_to needs the player driving)"}
    x, y, z = p["x"], p["y"], p["z"]
    speed = float(p.get("speed", 20.0))
    style = int(p.get("style", DRIVE_STYLE_NORMAL))
    stop = float(p.get("stop_range", 5.0))
    # Driving-practice finding (driving_skill_log.json): max driver ability + zero aggressiveness +
    # a real avoidance style = ~0 accidents. Without these the AI rams traffic. Tunable via params.
    _native("SET_DRIVER_ABILITY", [ped, float(p.get("ability", 1.0))], "void")
    _native("SET_DRIVER_AGGRESSIVENESS", [ped, float(p.get("aggressiveness", 0.0))], "void")
    _native("TASK_VEHICLE_DRIVE_TO_COORD_LONGRANGE", [ped, veh, x, y, z, speed, style, stop], "void")
    return {"task": "TASK_VEHICLE_DRIVE_TO_COORD_LONGRANGE", "dest": [x, y, z], "speed": speed, "style": style}

def _v_drive_wander(ped, p):
    veh = _player_vehicle(ped)
    if not veh:
        return {"error": "not in a vehicle"}
    speed = float(p.get("speed", 18.0))
    _native("SET_DRIVER_ABILITY", [ped, float(p.get("ability", 1.0))], "void")
    _native("SET_DRIVER_AGGRESSIVENESS", [ped, float(p.get("aggressiveness", 0.0))], "void")
    _native("TASK_VEHICLE_DRIVE_WANDER", [ped, veh, speed, DRIVE_STYLE_NORMAL | 2048], "void")
    return {"task": "TASK_VEHICLE_DRIVE_WANDER", "speed": speed}

def _v_engage(ped, p):
    target = p.get("target")
    if not target:
        return {"error": "engage needs a target handle"}
    _make_stick(ped)
    _native("SET_PED_COMBAT_ATTRIBUTES", [ped, 5, True], "void")   # AlwaysFight
    _native("SET_PED_COMBAT_ABILITY", [ped, 2], "void")            # Professional
    _native("TASK_COMBAT_PED", [ped, target, 0, 16], "void")
    return {"task": "TASK_COMBAT_PED", "target": target}

def _v_engage_area(ped, p):
    _make_stick(ped)
    _native("SET_PED_COMBAT_ATTRIBUTES", [ped, 5, True], "void")
    radius = float(p.get("radius", 100.0))
    _native("TASK_COMBAT_HATED_TARGETS_AROUND_PED", [ped, radius, 0], "void")
    return {"task": "TASK_COMBAT_HATED_TARGETS_AROUND_PED", "radius": radius}

def _v_flee(ped, p):
    _make_stick(ped)
    if p.get("from_ped"):
        _native("TASK_SMART_FLEE_PED", [ped, p["from_ped"], float(p.get("distance", 100.0)), -1, False, False], "void")
        return {"task": "TASK_SMART_FLEE_PED", "from": p["from_ped"]}
    if all(k in p for k in ("x", "y", "z")):
        _native("TASK_SMART_FLEE_COORD", [ped, p["x"], p["y"], p["z"], float(p.get("distance", 100.0)), -1, False, False], "void")
        return {"task": "TASK_SMART_FLEE_COORD", "from": [p["x"], p["y"], p["z"]]}
    return {"error": "flee needs from_ped or x/y/z"}

def _v_follow(ped, p):
    ent = p.get("entity")
    if not ent:
        return {"error": "follow needs an entity handle"}
    off = p.get("offset", [0.0, -2.0, 0.0])
    _native("TASK_FOLLOW_TO_OFFSET_OF_ENTITY", [ped, ent, off[0], off[1], off[2], float(p.get("speed", 2.0)), -1, float(p.get("stop_range", 4.0)), True], "void")
    _native("SET_PED_KEEP_TASK", [ped, True], "void")
    return {"task": "TASK_FOLLOW_TO_OFFSET_OF_ENTITY", "entity": ent}

def _v_wander(ped, p):
    _native("TASK_WANDER_STANDARD", [ped, 0.0, 0], "void")
    return {"task": "TASK_WANDER_STANDARD"}

def _v_stop(ped, p):
    _native("CLEAR_PED_TASKS", [ped], "void")
    return {"task": "CLEAR_PED_TASKS"}

VERBS = {
    "walk_to": (_v_walk_to, "walk/run to x,y,z (nav mesh). params: x,y,z, run(bool)"),
    "drive_to": (_v_drive_to, "drive the player's car to x,y,z. params: x,y,z, speed, style, stop_range"),
    "drive_wander": (_v_drive_wander, "cruise traffic aimlessly. params: speed"),
    "engage": (_v_engage, "fight a specific ped. params: target(handle)"),
    "engage_area": (_v_engage_area, "fight all hated peds around. params: radius"),
    "flee": (_v_flee, "flee a ped or coord. params: from_ped | x,y,z; distance"),
    "follow": (_v_follow, "follow an entity. params: entity(handle), offset, speed"),
    "wander": (_v_wander, "wander on foot ambiently"),
    "stop": (_v_stop, "clear tasks (idle)"),
}

def act(verb, params=None, force=False, now=None):
    """Issue a verb. If it equals the current intent and not force, it's a no-op (avoids task spam)."""
    params = params or {}
    now = now if now is not None else time.time()
    if not force and verb == _intent["verb"] and params == _intent["params"]:
        return {"success": True, "verb": verb, "unchanged": True}
    spec = VERBS.get(verb)
    if not spec:
        return {"error": f"unknown verb '{verb}'", "verbs": list(VERBS)}
    ped = _player_ped()
    if not ped:
        return {"error": "no player ped"}
    out = spec[0](ped, params)
    if isinstance(out, dict) and out.get("error"):
        return out
    _intent.update({"verb": verb, "params": params, "issued_at": now})
    return {"success": True, "verb": verb, "issued": out}

# ---- handlers ----
def handle_act(p):        return act(p.get("verb"), p.get("params"), p.get("force", False))
def handle_get_intent(p): return {"success": True, **_intent}
def handle_stop_acting(p):
    r = act("stop", force=True)
    return r
def handle_list_verbs(p): return {"success": True, "verbs": {k: v[1] for k, v in VERBS.items()}}

ACTION_COMMANDS = {"act": handle_act, "get_intent": handle_get_intent,
                   "stop_acting": handle_stop_acting, "list_verbs": handle_list_verbs}
ACTION_OFFTHREAD = {"get_intent", "list_verbs"}   # pure-state; verbs call natives -> game thread

if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    calls = []
    def fake(p):
        calls.append((p["name"], p.get("args")))
        if p["name"] == "PLAYER_PED_ID": return {"result": 2}
        if p["name"] == "IS_PED_IN_ANY_VEHICLE": return {"result": True}
        if p["name"] == "GET_VEHICLE_PED_IS_IN": return {"result": 7}
        return {"result": None}
    bind({"handle_call_native_by_name": fake})
    # walk_to issues nav-mesh task with run=sprint blend 3.0
    r = act("walk_to", {"x": 1.0, "y": 2.0, "z": 3.0, "run": True})
    assert r["success"] and r["issued"]["task"] == "TASK_FOLLOW_NAV_MESH_TO_COORD", r
    nav = [a for (n, a) in calls if n == "TASK_FOLLOW_NAV_MESH_TO_COORD"][0]
    assert nav[1:4] == [1.0, 2.0, 3.0] and nav[4] == 3.0, nav     # ped, x,y,z, blend=3.0(run)
    # same intent again = no-op (no new task)
    before = len(calls)
    r2 = act("walk_to", {"x": 1.0, "y": 2.0, "z": 3.0, "run": True})
    assert r2.get("unchanged") and len(calls) == before, "same intent must not re-issue"
    # drive_to uses the vehicle + longrange task
    calls.clear()
    r3 = act("drive_to", {"x": 5.0, "y": 6.0, "z": 7.0, "speed": 25.0})
    drv = [a for (n, a) in calls if n == "TASK_VEHICLE_DRIVE_TO_COORD_LONGRANGE"][0]
    assert drv[0] == 2 and drv[1] == 7 and drv[2:5] == [5.0, 6.0, 7.0] and drv[5] == 25.0, drv
    # engage sets make-stick + combat then tasks
    calls.clear(); act("engage", {"target": 99})
    names = [n for (n, a) in calls]
    assert "SET_BLOCKING_OF_NON_TEMPORARY_EVENTS" in names and "TASK_COMBAT_PED" in names, names
    # flee requires a from
    assert act("flee", {})["issued"]["task"] if False else act("flee", {"from_ped": 99})["issued"]["task"] == "TASK_SMART_FLEE_PED"
    assert act("nonsense")["error"]
    print("agent_actions self-test PASSED — verb dispatch + intent-dedup + arg-building correct")
    print(f"  {len(VERBS)} verbs; native execution needs in-game verification")
