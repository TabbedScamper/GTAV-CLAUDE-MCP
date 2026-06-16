"""
scenario_gen.py — generate ENDLESS, GROUNDED things to do.

Composes scenarios for the mission engine (mission_runner.py / play_heist.py) from:
  * an OBJECTIVE-TYPE library where every type maps to REAL game natives (verified callable in
    native_db.json) — checkpoints, waypoints, mission flag, area tests, ped spawns — NOT modder hacks;
  * real LANDMARK coordinates (pyscript/world_data/landmarks.json) so locations are real places;
  * THEMES that sequence objective types into coherent missions (heist, assault, delivery, rampage, race...).

Result: Claude (or `--random`) can produce limitless varied scenarios that the runner plays "the game's
way." Each objective declares the native_marker/native it uses so the runner presents it natively.

Grounded = every objective ties to a real native + a real coord; nothing invented. Deterministic with --seed.
Stdlib only. `python scenario_gen.py --self-test` / `--theme heist --location "Fleeca Bank (Legion)"`.
"""
import argparse, json, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
LANDMARKS = os.path.join(PROJ, "pyscript", "world_data", "landmarks.json")
OUT_DIR = os.path.join(HERE, "scenarios")


def load_landmarks():
    if os.path.exists(LANDMARKS):
        with open(LANDMARKS, "r", encoding="utf-8") as f:
            return [lm for lm in json.load(f).get("landmarks", []) if "x" in lm]
    return []


# ---------------------------------------------------------------- objective-type library (REAL natives)
# Each builder returns a mission_runner objective dict. `native` lists the real natives the runner uses to
# present/judge it (all verified in native_db). `nearby(loc, d)` jitters a point near a landmark.
def _near(loc, d, rng):
    return [round(loc["x"] + rng.uniform(-d, d), 1), round(loc["y"] + rng.uniform(-d, d), 1), round(loc["z"], 1)]

def obj_goto(loc, rng, **k):
    return {"id": k.get("id", "travel"), "kind": "goto", "text": f"Go to ~y~{loc['name']}.",
            "coords": [round(loc["x"], 1), round(loc["y"], 1), round(loc["z"], 1)], "blip_colour": 66,
            "native_marker": "checkpoint", "native": ["ADD_BLIP_FOR_COORD", "SET_BLIP_ROUTE", "CREATE_CHECKPOINT", "IS_ENTITY_AT_COORD"],
            "complete": {"type": "reach", "radius": k.get("radius", 15.0)}}

def obj_breach(loc, rng, **k):
    return {"id": k.get("id", "breach"), "kind": "goto", "text": "Breach the ~g~objective.",
            "coords": _near(loc, 6, rng), "blip_colour": 2, "native_marker": "checkpoint",
            "native": ["CREATE_CHECKPOINT", "GET_DISTANCE_BETWEEN_COORDS"],
            "complete": {"type": "reach", "radius": k.get("radius", 3.0)}}

def obj_grab(loc, rng, **k):
    return {"id": k.get("id", "grab"), "kind": "wait", "text": "Grab the ~g~loot.", "blip_colour": 5,
            "native": ["PLAY_SOUND_FRONTEND"], "complete": {"type": "timer", "seconds": k.get("seconds", 10.0)}}

def obj_eliminate(loc, rng, **k):
    n = k.get("count", rng.randint(2, 5))
    return {"id": k.get("id", "eliminate"), "kind": "kill", "text": f"Eliminate the ~r~{n} hostiles.",
            "coords": _near(loc, 8, rng), "blip_colour": 1, "native_marker": "blip",
            "spawn": {"ped": "s_m_y_swat_01", "count": n, "hostile": True},
            "native": ["CREATE_PED", "ADD_BLIP_FOR_ENTITY", "TASK_COMBAT_HATED_TARGETS_AROUND_PED", "IS_PED_DEAD_OR_DYING"],
            "complete": {"type": "all_targets_dead"}}

def obj_survive(loc, rng, **k):
    return {"id": k.get("id", "survive"), "kind": "wait", "text": "~r~Survive the assault.",
            "coords": _near(loc, 4, rng), "blip_colour": 1, "spawn": {"ped": "s_m_y_swat_01", "wave": True},
            "native": ["CREATE_PED", "SET_PED_COMBAT_ATTRIBUTES", "GET_GAME_TIMER"],
            "complete": {"type": "timer", "seconds": k.get("seconds", 60.0)}}

def obj_steal_vehicle(loc, rng, **k):
    return {"id": k.get("id", "steal"), "kind": "goto", "text": "Get to the ~b~target vehicle.",
            "coords": _near(loc, 10, rng), "blip_colour": 3, "native_marker": "checkpoint",
            "native": ["CREATE_CHECKPOINT", "IS_PED_IN_ANY_VEHICLE"],
            "complete": {"type": "reach", "radius": k.get("radius", 4.0)}}

def obj_deliver(loc, rng, **k):
    return {"id": k.get("id", "deliver"), "kind": "goto", "text": f"Deliver to ~g~{loc['name']}.",
            "coords": [round(loc["x"], 1), round(loc["y"], 1), round(loc["z"], 1)], "blip_colour": 5,
            "native_marker": "checkpoint", "native": ["ADD_BLIP_FOR_COORD", "SET_BLIP_ROUTE", "CREATE_CHECKPOINT"],
            "complete": {"type": "reach", "radius": k.get("radius", 10.0)}}

def obj_escape(loc, rng, **k):
    return {"id": k.get("id", "escape"), "kind": "escape", "text": "~y~Lose the cops and leave the area.",
            "coords": [round(loc["x"], 1), round(loc["y"], 1), round(loc["z"], 1)], "blip_colour": 66,
            "native_marker": "blip", "native": ["GET_PLAYER_WANTED_LEVEL", "GET_DISTANCE_BETWEEN_COORDS"],
            "complete": {"type": "left_area_and_clean", "radius": k.get("radius", 150.0)}}

def obj_checkpoints(loc, rng, **k):
    """A race leg — handled as a reach; the runner draws a real CREATE_CHECKPOINT."""
    return {"id": k.get("id", "cp"), "kind": "goto", "text": "Hit the ~y~checkpoint.",
            "coords": _near(loc, 60, rng), "blip_colour": 66, "native_marker": "checkpoint",
            "native": ["CREATE_CHECKPOINT"], "complete": {"type": "reach", "radius": 12.0}}

def obj_assassinate(loc, rng, **k):
    return {"id": k.get("id", "assassinate"), "kind": "kill", "text": "Assassinate the ~r~target.",
            "coords": _near(loc, 10, rng), "blip_colour": 1, "native_marker": "blip",
            "spawn": {"ped": "a_m_m_business_01", "count": 1, "hostile": False, "priority": True},
            "native": ["CREATE_PED", "SET_ENTITY_IS_TARGET_PRIORITY", "IS_PED_DEAD_OR_DYING", "HAS_ENTITY_BEEN_DAMAGED_BY_ENTITY"],
            "complete": {"type": "all_targets_dead"}}

def obj_defend(loc, rng, **k):
    return {"id": k.get("id", "defend"), "kind": "wait", "text": "~r~Defend the position.",
            "coords": _near(loc, 5, rng), "blip_colour": 1, "spawn": {"ped": "s_m_y_swat_01", "count": k.get("count", 4), "hostile": True},
            "native": ["CREATE_PED", "TASK_GUARD_CURRENT_POSITION", "IS_PED_DEAD_OR_DYING", "GET_GAME_TIMER"],
            "complete": {"type": "timer", "seconds": k.get("seconds", 45.0)}}

def obj_destroy(loc, rng, **k):
    return {"id": k.get("id", "destroy"), "kind": "kill", "text": "~r~Destroy the target vehicle.",
            "coords": _near(loc, 12, rng), "blip_colour": 1, "native_marker": "blip",
            "spawn": {"vehicle": "insurgent", "count": 1},
            "native": ["CREATE_VEHICLE", "ADD_BLIP_FOR_ENTITY", "IS_VEHICLE_DRIVEABLE", "GET_ENTITY_HEALTH", "ADD_EXPLOSION"],
            "complete": {"type": "all_targets_dead"}}

def obj_chase(loc, rng, **k):
    return {"id": k.get("id", "chase"), "kind": "goto", "text": "~r~Chase down the target.",
            "coords": _near(loc, 80, rng), "blip_colour": 1, "native_marker": "blip",
            "spawn": {"vehicle": "kuruma", "count": 1, "flee": True},
            "native": ["CREATE_VEHICLE", "TASK_VEHICLE_CHASE", "GET_DISTANCE_BETWEEN_COORDS"],
            "complete": {"type": "reach", "radius": k.get("radius", 10.0)}}

def obj_lose_tail(loc, rng, **k):
    return {"id": k.get("id", "lose_tail"), "kind": "escape", "text": "~y~Lose the cops.",
            "native": ["GET_PLAYER_WANTED_LEVEL", "CLEAR_PLAYER_WANTED_LEVEL"],
            "complete": {"type": "wanted_zero"}}

def obj_distract(loc, rng, **k):
    return {"id": k.get("id", "distract"), "kind": "wait", "text": "~r~Draw and HOLD a 3-star wanted level.",
            "native": ["SET_PLAYER_WANTED_LEVEL", "GET_PLAYER_WANTED_LEVEL"],
            "complete": {"type": "wanted_above", "threshold": k.get("threshold", 3), "seconds": k.get("seconds", 30.0)}}

def obj_collect(loc, rng, **k):
    n = k.get("count", rng.randint(3, 6))
    return {"id": k.get("id", "collect"), "kind": "wait", "text": f"Collect the ~g~{n} packages.",
            "coords": _near(loc, 15, rng), "blip_colour": 5, "spawn": {"pickup": "PICKUP_MONEY_CASE", "count": n},
            "native": ["CREATE_PICKUP_ROTATE", "HAS_PICKUP_BEEN_COLLECTED", "ADD_BLIP_FOR_COORD"],
            "complete": {"type": "collected", "count": n}}

def obj_capture(loc, rng, **k):
    return {"id": k.get("id", "capture"), "kind": "wait", "text": "~y~Hold the zone.",
            "coords": _near(loc, 4, rng), "blip_colour": 66,
            "native": ["IS_ENTITY_IN_ANGLED_AREA", "GET_GAME_TIMER", "ADD_BLIP_FOR_COORD"],
            "complete": {"type": "timer", "seconds": k.get("seconds", 40.0)}}

def obj_transport(loc, rng, **k):
    return {"id": k.get("id", "transport"), "kind": "goto", "text": f"Drop the passenger at ~g~{loc['name']}.",
            "coords": [round(loc["x"], 1), round(loc["y"], 1), round(loc["z"], 1)], "blip_colour": 5,
            "native_marker": "checkpoint", "spawn": {"ped": "a_m_y_business_01", "count": 1, "passenger": True},
            "native": ["CREATE_PED", "TASK_ENTER_VEHICLE", "IS_PED_IN_VEHICLE", "IS_ENTITY_AT_COORD"],
            "complete": {"type": "reach", "radius": k.get("radius", 12.0)}}

def obj_hunt(loc, rng, **k):
    return {"id": k.get("id", "hunt"), "kind": "kill", "text": "Track and kill the ~r~animal.",
            "coords": _near(loc, 40, rng), "blip_colour": 1, "spawn": {"ped": "a_c_deer", "count": 1, "hostile": False},
            "native": ["CREATE_PED", "IS_PED_DEAD_OR_DYING", "ADD_BLIP_FOR_COORD"],
            "complete": {"type": "all_targets_dead"}}

def obj_drill(loc, rng, **k):
    return {"id": k.get("id", "drill"), "kind": "wait", "text": "~g~Drill the safe.",
            "coords": _near(loc, 3, rng), "blip_colour": 2, "native_marker": "checkpoint",
            "native": ["REQUEST_ANIM_DICT", "TASK_PLAY_ANIM_ADVANCED", "IS_ENTITY_PLAYING_ANIM"],
            "complete": {"type": "timer", "seconds": k.get("seconds", 12.0)}}

def obj_recon(loc, rng, **k):
    return {"id": k.get("id", "recon"), "kind": "goto", "text": "Get eyes on the ~y~target.",
            "coords": _near(loc, 25, rng), "blip_colour": 66, "native_marker": "blip",
            "native": ["IS_ENTITY_ON_SCREEN", "IS_SPHERE_VISIBLE", "GET_DISTANCE_BETWEEN_COORDS"],
            "complete": {"type": "reach", "radius": k.get("radius", 25.0)}}

def obj_sabotage(loc, rng, **k):
    return {"id": k.get("id", "sabotage"), "kind": "wait", "text": "~g~Sabotage the target.",
            "coords": _near(loc, 6, rng), "blip_colour": 2, "native_marker": "checkpoint",
            "native": ["TASK_PLAY_ANIM", "HAS_ENTITY_BEEN_DAMAGED_BY_ENTITY"],
            "complete": {"type": "timer", "seconds": k.get("seconds", 8.0)}}

OBJECTIVE_TYPES = {
    "goto": obj_goto, "breach": obj_breach, "grab": obj_grab, "eliminate": obj_eliminate,
    "survive": obj_survive, "steal_vehicle": obj_steal_vehicle, "deliver": obj_deliver,
    "escape": obj_escape, "checkpoint": obj_checkpoints,
    # + R*-grounded set (PATTERNS/13)
    "assassinate": obj_assassinate, "defend": obj_defend, "destroy": obj_destroy, "chase": obj_chase,
    "lose_tail": obj_lose_tail, "distract": obj_distract, "collect": obj_collect, "capture": obj_capture,
    "transport": obj_transport, "hunt": obj_hunt, "drill": obj_drill, "recon": obj_recon, "sabotage": obj_sabotage,
}

# ---------------------------------------------------------------- themes (sequences of objective types)
# Each theme: list of (objective_type, location_role). location roles pull from the chosen + extra landmarks.
THEMES = {
    "heist":    {"name": "Heist", "seq": ["goto", "breach", "grab", "escape"], "reward": (120000, 250000), "timer": 300},
    "assault":  {"name": "Assault", "seq": ["goto", "eliminate", "escape"], "reward": (40000, 90000), "timer": 240},
    "rampage":  {"name": "Rampage", "seq": ["goto", "survive"], "reward": (30000, 70000), "timer": None},
    "delivery": {"name": "Delivery", "seq": ["steal_vehicle", "deliver"], "reward": (15000, 45000), "timer": 360},
    "stickup":  {"name": "Stick-up", "seq": ["goto", "grab", "escape"], "reward": (8000, 30000), "timer": 180},
    "race":     {"name": "Street Race", "seq": ["checkpoint", "checkpoint", "checkpoint", "checkpoint"], "reward": (10000, 25000), "timer": 200},
    "rescue":   {"name": "Rescue", "seq": ["goto", "eliminate", "deliver"], "reward": (35000, 80000), "timer": 300},
    "hit":      {"name": "Hit", "seq": ["recon", "assassinate", "lose_tail"], "reward": (50000, 110000), "timer": 240},
    "vaultjob": {"name": "Vault Job", "seq": ["goto", "drill", "grab", "defend", "escape"], "reward": (150000, 300000), "timer": 360},
    "sabotage": {"name": "Sabotage Op", "seq": ["goto", "sabotage", "destroy", "escape"], "reward": (45000, 95000), "timer": 240},
    "smash":    {"name": "Smash & Grab", "seq": ["chase", "destroy"], "reward": (20000, 50000), "timer": 180},
    "supply":   {"name": "Supply Run", "seq": ["goto", "collect", "deliver"], "reward": (18000, 50000), "timer": 360},
    "lastman":  {"name": "Last Man Standing", "seq": ["goto", "survive"], "reward": (30000, 70000), "timer": None},
    "kingpin":  {"name": "Turf War", "seq": ["goto", "capture"], "reward": (25000, 60000), "timer": None},
    "taxi":     {"name": "Cab Job", "seq": ["goto", "transport"], "reward": (5000, 15000), "timer": 300},
    "poach":    {"name": "Poacher", "seq": ["goto", "hunt"], "reward": (8000, 22000), "timer": None},
    "decoy":    {"name": "Decoy", "seq": ["goto", "distract"], "reward": (15000, 40000), "timer": None},
}


def generate(theme=None, location=None, difficulty=1, seed=None, landmarks=None):
    """Produce one grounded scenario dict (mission_runner-compatible)."""
    rng = random.Random(seed)
    lms = landmarks if landmarks is not None else load_landmarks()
    if not lms:
        raise RuntimeError("no landmarks loaded (need pyscript/world_data/landmarks.json)")
    theme = theme or rng.choice(list(THEMES))
    if theme not in THEMES:
        raise ValueError(f"unknown theme '{theme}' (have {list(THEMES)})")
    t = THEMES[theme]
    # pick the primary location
    if location:
        primary = next((l for l in lms if location.lower() in l["name"].lower()), None) or rng.choice(lms)
    else:
        primary = rng.choice(lms)
    objectives, used = [], {}
    for i, otype in enumerate(t["seq"]):
        # deliver/transport go to a DIFFERENT landmark (a dropoff). escape stays anchored to the mission
        # area (left_area_and_clean = move AWAY from HERE + lose cops), so its marker isn't across the map.
        if otype in ("deliver", "transport"):
            loc = rng.choice([l for l in lms if l is not primary] or [primary])
        else:
            loc = primary
        n = used.get(otype, 0); used[otype] = n + 1
        obj = OBJECTIVE_TYPES[otype](loc, rng, id=otype + (str(n + 1) if n else ""))
        # difficulty scales counts/timers
        if "count" in obj.get("spawn", {}):
            obj["spawn"]["count"] = obj["spawn"].get("count", 3) + (difficulty - 1)
        if obj.get("complete", {}).get("type") == "all_targets_dead":
            obj.setdefault("spawn", {})["count"] = obj["spawn"].get("count", 3) + (difficulty - 1)
        objectives.append(obj)
    lo, hi = t["reward"]
    reward = int(rng.randint(lo, hi) * (1 + 0.25 * (difficulty - 1)))
    timer = t["timer"] if t["timer"] is None else max(60, int(t["timer"] * (1.1 - 0.15 * (difficulty - 1))))
    name = f"{primary['name']} {t['name']}"
    scen = {"$generated": True, "theme": theme, "difficulty": difficulty,
            "name": name, "reward": reward, "objectives": objectives,
            "native_presentation": True}
    if timer:
        scen["timer_seconds"] = timer
        scen["on_timeout"] = {"wanted": min(5, 2 + difficulty), "subtitle": "~r~Out of time - cops called!"}
    return scen


def validate(scen):
    """Grounding check: every objective has a real completion type + (coords or a timer/dead test)."""
    ok_complete = {"reach", "left_area", "left_area_and_clean", "wanted_zero", "wanted_above",
                   "timer", "all_targets_dead", "target_dead", "collected", "in_target_vehicle"}
    assert scen.get("name") and scen.get("objectives"), "scenario needs name + objectives"
    for o in scen["objectives"]:
        c = o.get("complete", {})
        assert c.get("type") in ok_complete, f"objective '{o.get('id')}' bad completion {c}"
        if c["type"] in ("reach", "left_area", "left_area_and_clean"):
            assert o.get("coords") and len(o["coords"]) == 3, f"objective '{o.get('id')}' needs coords"
        assert o.get("native"), f"objective '{o.get('id')}' must declare its real natives (grounding)"
    return True


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Generate endless grounded mission scenarios.")
    ap.add_argument("--theme", choices=list(THEMES), help="mission theme (default: random)")
    ap.add_argument("--location", help="landmark name to center on (default: random)")
    ap.add_argument("--difficulty", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--save", help="write to tools/scenarios/<name>.json and print the path")
    ap.add_argument("--list", action="store_true", help="list themes + objective types")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.list:
        print(json.dumps({"themes": {k: v["seq"] for k, v in THEMES.items()},
                          "objective_types": list(OBJECTIVE_TYPES)}, indent=2)); return 0
    scen = generate(a.theme, a.location, a.difficulty, a.seed)
    validate(scen)
    if a.save:
        os.makedirs(OUT_DIR, exist_ok=True)
        fn = a.save if a.save.endswith(".json") else os.path.join(OUT_DIR, a.save + ".json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(scen, f, indent=2)
        print(fn)
    else:
        print(json.dumps(scen, indent=2))
    return 0


def _self_test():
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    lms = [{"name": "Fleeca Bank", "x": 147.0, "y": -1044.0, "z": 29.0, "kind": "bank"},
           {"name": "Legion Square", "x": 195.0, "y": -934.0, "z": 30.0, "kind": "landmark"},
           {"name": "Sandy Shores", "x": 1960.0, "y": 3740.0, "z": 32.0, "kind": "town"}]
    # every theme generates a valid, grounded, runner-compatible scenario
    def near_any_landmark(coords, maxd=120.0):
        return any(((coords[0]-l["x"])**2 + (coords[1]-l["y"])**2) ** 0.5 <= maxd for l in lms)
    for th in THEMES:
        s = generate(theme=th, seed=1, landmarks=lms)
        validate(s)
        for o in s["objectives"]:
            assert o["native"], o                               # grounded in REAL natives
            if o.get("coords"):
                assert near_any_landmark(o["coords"]), (th, o["id"], o["coords"])   # grounded NEAR a real place
    # deterministic with seed; varied across seeds
    a = generate(theme="heist", seed=7, landmarks=lms)
    b = generate(theme="heist", seed=7, landmarks=lms)
    c = generate(theme="heist", seed=8, landmarks=lms)
    assert a == b, "same seed must be deterministic"
    assert a["name"] or c["name"]
    # heist has the right objective spine + a real escape to a DIFFERENT landmark
    h = generate(theme="heist", location="Fleeca", seed=3, landmarks=lms)
    ids = [o["id"] for o in h["objectives"]]
    assert ids == ["goto", "breach", "grab", "escape"], ids
    esc = h["objectives"][-1]
    assert esc["complete"]["type"] == "left_area_and_clean" and esc["coords"] != h["objectives"][0]["coords"]
    # difficulty scales reward + enemy count
    e1 = generate(theme="assault", seed=2, difficulty=1, landmarks=lms)
    e3 = generate(theme="assault", seed=2, difficulty=3, landmarks=lms)
    assert e3["reward"] > e1["reward"]
    elim1 = next(o for o in e1["objectives"] if o["id"] == "eliminate")
    elim3 = next(o for o in e3["objectives"] if o["id"] == "eliminate")
    assert elim3["spawn"]["count"] > elim1["spawn"]["count"], (elim1["spawn"], elim3["spawn"])
    # it's mission_runner-compatible (the completion predicate accepts the generated objectives)
    sys.path.insert(0, HERE)
    import mission_runner as mr
    assert mr.objective_complete(h["objectives"][0], [147, -1044, 29], 0, 0, {}) is True  # at the bank -> reach done
    print("scenario_gen self-test PASSED — all themes generate valid, grounded, runner-compatible scenarios;")
    print(f"  deterministic-by-seed; difficulty scales; {len(THEMES)} themes x landmarks x seeds = endless content.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
