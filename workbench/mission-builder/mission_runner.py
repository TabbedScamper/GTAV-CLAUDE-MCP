"""
mission_runner.py — the GAME MASTER: run a declarative mission SCENARIO as a live state machine, react
to the player completing objectives, enforce a timer, and LEARN from each playthrough.

This is the engine behind "give me a bank heist scenario" and "5 minutes to get in and out or cops are
called." It mirrors the real M8T heist framework (Examples/PATTERNS/12): one tick loop, an ordered list of
objectives, set a routed blip + objective text per stage, detect completion (reach / left-area / wanted==0
/ timer / target-dead) via the world-sensing layer, fire reactions (raise wanted = cops), reward + clean up
on pass. It composes with the play-and-watch agent: the objective blips it sets are read by mission_sense,
so play_agent can PLAY the mission this runner builds.

LEARNING: every run appends an outcome record to mission_log.jsonl (objectives reached, time, fail reason,
timed-out). `review()` summarizes it (success rate, where runs fail, time-per-objective) — the seed of
"gets smarter as it plays": Claude reads the review and adjusts the scenario / its play strategy.

HONESTY: the scenario state machine, completion predicates, timer/reaction firing, pass/fail, and the
learning log are self-tested offline (`--self-test`, mocked bridge). The in-game effects (blip/objective
text, set-wanted reaction, reward) go through the bridge by name (safe; wrong name -> error, never crash)
and the cops-on-timeout + completion-by-position parts are reliable; blip/text/reward are best-effort and
marked. Acting is gated behind `--go` (default dry-run narrates). Stdlib only.
"""
import argparse, json, math, os, socket, struct, sys, time

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 27015
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mission_log.jsonl")
SCEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")


# ---------------------------------------------------------------- bridge client
class BridgeClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=20.0):
        self.host, self.port, self.timeout = host, port, timeout
    def send(self, command, params=None):
        payload = json.dumps({"command": command, "params": params or {}}).encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(struct.pack("<I", len(payload)) + payload)
                head = s.recv(4)
                if len(head) < 4:
                    return {"error": "bridge closed"}
                size = struct.unpack("<I", head)[0]
                body = b""
                while len(body) < size:
                    c = s.recv(min(4096, size - len(body)))
                    if not c:
                        break
                    body += c
                return json.loads(body.decode("utf-8"))
        except Exception as e:
            return {"error": f"bridge: {e}", "connected": False}


# ---------------------------------------------------------------- pure logic (self-tested)
def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))

def _dist2d(a, b):
    """Horizontal distance. 'reach'/'left_area' use this because the marker is a vertical CYLINDER and
    the completion z can be ground-snapped to a DIFFERENT level (e.g. an interior streamed below a plaza),
    which would make a 3D test impossible to satisfy while standing right on the marker."""
    return math.hypot(a[0] - b[0], a[1] - b[1])

def objective_complete(obj, player_pos, wanted, stage_elapsed, world):
    """Pure predicate: is the current objective satisfied? (M8T completion tests, PATTERNS/12.)"""
    c = obj.get("complete", {})
    t = c.get("type")
    coords = obj.get("coords")
    if t == "reach" and coords:
        # 2D (cylinder) + a generous vertical band so multi-level coords still pass when you're on the spot
        return _dist2d(player_pos, coords) <= c.get("radius", 8.0) and abs(player_pos[2] - coords[2]) <= c.get("z_tol", 20.0)
    if t == "left_area" and coords:
        return _dist2d(player_pos, coords) >= c.get("radius", 120.0)
    if t == "left_area_and_clean" and coords:
        return _dist2d(player_pos, coords) >= c.get("radius", 120.0) and wanted == 0
    if t == "wanted_zero":
        return wanted == 0
    if t == "timer":
        return stage_elapsed >= c.get("seconds", 5.0)
    if t == "target_dead":
        return obj.get("target") in (world.get("dead_handles") or [])
    if t == "all_targets_dead":
        # the runner tracks spawned target handles -> world["targets_alive"] is the live count
        return (world.get("targets_alive") if world.get("targets_alive") is not None else 0) == 0
    if t == "wanted_above":
        return wanted >= c.get("threshold", 3) and stage_elapsed >= c.get("seconds", 30.0)
    if t == "collected":
        return (world.get("collected_count") or 0) >= c.get("count", 1)
    if t == "in_target_vehicle":
        return bool(world.get("in_target_vehicle"))
    return False

def summarize_log(records):
    """Turn raw run records into the 'getting smarter' review."""
    if not records:
        return {"runs": 0}
    n = len(records)
    passed = [r for r in records if r.get("result") == "passed"]
    fails = [r for r in records if r.get("result") != "passed"]
    # where do failures happen? count fail objective index
    fail_at = {}
    for r in fails:
        k = r.get("failed_objective", "?")
        fail_at[k] = fail_at.get(k, 0) + 1
    reasons = {}
    for r in fails:
        reasons[r.get("fail_reason", "?")] = reasons.get(r.get("fail_reason", "?"), 0) + 1
    avg_time = round(sum(r.get("seconds", 0) for r in passed) / len(passed), 1) if passed else None
    return {"runs": n, "passed": len(passed), "success_rate": round(len(passed) / n, 2),
            "avg_pass_time_s": avg_time, "fails_by_objective": fail_at, "fail_reasons": reasons,
            "lesson": _lesson(fail_at, reasons)}

def _lesson(fail_at, reasons):
    if not fail_at and not reasons:
        return "No failures yet."
    worst = max(fail_at.items(), key=lambda kv: kv[1])[0] if fail_at else None
    top = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else None
    bits = []
    if worst is not None:
        bits.append(f"most runs fail at objective '{worst}'")
    if top:
        bits.append(f"top cause: {top}")
    return "; ".join(bits) + " -> adjust strategy/scenario there."


# ---------------------------------------------------------------- the runner (game master)
class MissionRunner:
    def __init__(self, scenario, bridge=None, dry_run=True, narrate=print, clock=time.time, log_path=LOG_PATH):
        self.s = scenario
        self.bridge = bridge or BridgeClient()
        self.dry_run = dry_run
        self.narrate = narrate
        self.clock = clock
        self.log_path = log_path
        self.idx = 0
        self.started = None
        self.stage_started = None
        self.timed_out = False
        self.status = "pending"
        self._blip_set_for = None
        self.native = bool(scenario.get("native_presentation"))   # use REAL game systems (checkpoint/mission flag/print)
        self._checkpoint = None
        self._obj_blip = None   # the objective blip (colored + GPS route) — the real mission marker
        self._spawned = []      # all entities we spawned (for cleanup)
        self._targets = []      # [(handle, kind)] completion-relevant spawns (peds/vehicles)
        self._spawn_done_for = None
        self._collected = 0
        self._scratch = None    # alloc_cave cell for out-param natives (REMOVE_BLIP / STAT_GET_INT / ground-z)
        self._marker_refined_for = None   # idx whose checkpoint we've ground-snapped
        self._text_shown_at = 0           # last time we (re)showed the objective subtitle

    # --- pointer-correct blip removal: REMOVE_BLIP takes a Blip* (pointer to the handle), so passing
    #     the handle BY VALUE silently no-ops (that's the leftover-blip bug). Write the handle into a
    #     scratch cell and pass its ADDRESS. (Recipe from the out-param work; verified in-game.) ---
    def _scratch_cell(self):
        if self._scratch is None and not self.dry_run:
            r = self.bridge.send("alloc_cave", {"size": 8})
            a = r.get("address") if isinstance(r, dict) else None
            self._scratch = int(a, 16) if a else None
        return self._scratch

    def _remove_blip(self, handle):
        if self.dry_run or not handle:
            return
        cell = self._scratch_cell()
        self._nat("SET_BLIP_ROUTE", [handle, False], "void")
        if cell:
            self.bridge.send("write", {"address": hex(cell), "type": "int32", "value": int(handle)})
            self._nat("REMOVE_BLIP", [cell], "void")
        else:
            self._nat("REMOVE_BLIP", [handle], "void")   # fallback (best-effort)

    # --- REAL native presentation (the game's own systems; best-effort, verify in-game) ---
    def _nat(self, name, args, ret=None):
        if self.dry_run:
            return None
        return self.bridge.send("call_native_by_name", {"name": name, "args": list(args), "return_type": ret})

    def _mission_flag(self, on):
        if self.native:
            self._nat("SET_MISSION_FLAG", [bool(on)], "void")

    def _say_event(self, event):
        """Fire a protagonist voice reaction for a mission event (commentary module; best-effort)."""
        if not self.dry_run:
            try:
                self.bridge.send("comment", {"event": event})
            except Exception:
                pass

    def _present_native(self, obj):
        """Objective presented the GAME'S way: a COLORED objective blip + GPS route + 3D checkpoint
        + the subtitle print system. NOT SET_NEW_WAYPOINT — that's the player's own click-waypoint
        (white, user-owned), not a mission marker. Real missions use a blip with a colored route."""
        if not self.native:
            return
        c = obj.get("coords")
        # clear the previous objective marker (blip + checkpoint) before drawing the new one
        if self._obj_blip is not None:
            self._remove_blip(self._obj_blip); self._obj_blip = None
        if self._checkpoint is not None:
            self._nat("DELETE_CHECKPOINT", [self._checkpoint], "void"); self._checkpoint = None
        if c and obj.get("native_marker") == "checkpoint":
            colour = int(obj.get("blip_colour", 5))   # 5 = mission yellow; each objective carries its own
            r = self._nat("ADD_BLIP_FOR_COORD", [c[0], c[1], c[2]], "int")
            blip = r.get("result") if isinstance(r, dict) else None
            if isinstance(blip, int):
                self._obj_blip = blip
                self._nat("SET_BLIP_SPRITE", [blip, 1], "void")          # standard objective dot
                self._nat("SET_BLIP_COLOUR", [blip, colour], "void")
                self._nat("SET_BLIP_AS_SHORT_RANGE", [blip, False], "void")
                self._nat("SET_BLIP_ROUTE", [blip, True], "void")         # the colored GPS route line
                self._nat("SET_BLIP_ROUTE_COLOUR", [blip, colour], "void")
            gz = self._ground_z(c[0], c[1], c[2])    # snap to real ground if the area is loaded
            cz = gz if gz is not None else c[2]
            if gz is not None:
                c[2] = gz
                self._marker_refined_for = self.idx
            # type 47 = plain mission cylinder (NOT type 4 = checkered race-finish flag).
            # radius 4m = a sensible marker size (the 15m COMPLETION radius made it a massive cylinder).
            r = self._nat("CREATE_CHECKPOINT", [47, c[0], c[1], cz, c[0], c[1], cz,
                          4.0, 255, 180, 0, 150, 0], "int")
            self._checkpoint = r.get("result") if isinstance(r, dict) else None
            if isinstance(self._checkpoint, int):
                self._nat("SET_CHECKPOINT_CYLINDER_HEIGHT", [self._checkpoint, 3.0, 3.0, 4.0], "void")
        txt = obj.get("text")
        if txt:
            self._print_subtitle("~y~New objective:~s~ " + txt)   # notification pop (above minimap)
            self._set_hud(txt)                                    # + persistent bottom-center text

    # --- spawn/track/cleanup execution (the per-type "wire it into the code"; best-effort, verify in-game) ---
    def _resolve_hash(self, name):
        r = self.bridge.send("resolve", {"name": name})
        return r.get("hash") if isinstance(r, dict) else None

    def _enter_spawns(self, obj):
        """Spawn this objective's entities (peds/vehicles/pickups) and track handles for completion + cleanup."""
        if self.dry_run or self._spawn_done_for == self.idx:
            self._spawn_done_for = self.idx
            return
        self._spawn_done_for = self.idx
        spec = obj.get("spawn") or {}
        c = obj.get("coords") or [0, 0, 0]
        if "vehicle" in spec:
            r = self.bridge.send("spawn_vehicle", {"model": spec["vehicle"], "distance": 18})   # bridge convenience (model load handled)
            h = r.get("vehicle_handle") if isinstance(r, dict) else None
            if h:
                self._targets.append((h, "vehicle")); self._spawned.append((h, "vehicle"))
        if "ped" in spec:
            mh = self._resolve_hash(spec["ped"])
            if mh:
                self._nat("REQUEST_MODEL", [mh], "void")
                cnt = int(spec.get("count", 1))
                for i in range(cnt):
                    # spread around the area (12-22m, varied angle) + ground-snap, so they don't all pop
                    # in clustered in the player's face — feels like they were already there.
                    ang = (2 * math.pi / max(cnt, 1)) * i + 0.6
                    rad = 12.0 + 5.0 * (i % 3)
                    px, py = round(c[0] + math.cos(ang) * rad, 2), round(c[1] + math.sin(ang) * rad, 2)
                    pz = self._ground_z(px, py, c[2]) or c[2]
                    heading = round((math.degrees(ang) + 180) % 360, 1)
                    rr = self._nat("CREATE_PED", [26, mh, px, py, pz, heading, True, False], "int")
                    h = rr.get("result") if isinstance(rr, dict) else None
                    if h:
                        if spec.get("hostile"):
                            self._nat("SET_PED_COMBAT_ATTRIBUTES", [h, 46, True], "void")   # always fight, no flee
                            self._nat("SET_PED_COMBAT_ATTRIBUTES", [h, 5, True], "void")
                            self._nat("TASK_COMBAT_HATED_TARGETS_AROUND_PED", [h, 200.0, 0], "void")
                        self._targets.append((h, "ped")); self._spawned.append((h, "ped"))
        if "pickup" in spec:
            mh = self._resolve_hash(spec.get("pickup_model", "prop_cash_pile_01")) or 0
            for i in range(int(spec.get("count", 1))):
                self._nat("CREATE_PICKUP_ROTATE", [self._resolve_hash(spec["pickup"]) or 0, c[0] + i, c[1], c[2], 0, 0, 0, 512, 0, True, mh], "int")

    def _live_state(self):
        """Query tracked spawns -> the counts the completion predicates need. Best-effort; in dry-run, inert."""
        out = {}
        if self.dry_run:
            return out
        if self._targets:
            alive = 0
            for h, kind in self._targets:
                if kind == "vehicle":
                    r = self._nat("IS_VEHICLE_DRIVEABLE", [h, False], "bool")
                else:
                    r = self._nat("IS_ENTITY_DEAD", [h, False], "bool")
                ok = (r.get("result") if isinstance(r, dict) else None)
                alive += 1 if (ok if kind == "vehicle" else (not ok)) else 0
            out["targets_alive"] = alive
        return out

    def _release_entity(self, h):
        """Release a spawned entity to the game's population so it despawns NATURALLY. Uses the pointer
        recipe — SET_ENTITY_AS_NO_LONGER_NEEDED takes an Entity* out-param. (Passing a handle BY VALUE to
        the pointer native DELETE_ENTITY is what crashed the game on cleanup after the kills.)"""
        if self.dry_run or not h:
            return
        cell = self._scratch_cell()
        if cell:
            self.bridge.send("write", {"address": hex(cell), "type": "int32", "value": int(h)})
            self._nat("SET_ENTITY_AS_NO_LONGER_NEEDED", [cell], "void")

    def _cleanup_spawns(self):
        if self.dry_run:
            self._targets = []; self._spawned = []; return
        for h, kind in self._spawned:
            if kind == "blip":
                self._remove_blip(h)
            else:
                self._release_entity(h)   # natural despawn (the game cleans up dead bodies), no crash
        self._targets = []; self._spawned = []

    # --- in-game effects (best-effort, by name through the allowlist) ---
    def _set_objective_blip(self, obj):
        if self._blip_set_for == self.idx:
            return
        if self.native:                      # the correct way: real waypoint + checkpoint + print
            self._present_native(obj)
        self._enter_spawns(obj)              # spawn this objective's peds/vehicles/pickups (self-guards dry-run)
        if self.dry_run:
            self._blip_set_for = self.idx
            return
        # non-native scenarios still get a routed objective blip; native ones already have one from
        # _present_native (don't add a second, untracked blip — that was the leftover-blip leak).
        c = obj.get("coords")
        if c and not self.native:
            r = self.bridge.send("call_native_by_name", {"name": "ADD_BLIP_FOR_COORD",
                                                         "args": [c[0], c[1], c[2]], "return_type": "int"})
            blip = r.get("result") if isinstance(r, dict) else None
            if blip:
                self._obj_blip = blip   # track it so it's removed on advance/finish
                self.bridge.send("call_native_by_name", {"name": "SET_BLIP_COLOUR",
                                                         "args": [blip, obj.get("blip_colour", 66)], "return_type": "void"})
                self.bridge.send("call_native_by_name", {"name": "SET_BLIP_ROUTE",
                                                         "args": [blip, True], "return_type": "void"})
        self._blip_set_for = self.idx

    def _call_cops(self, level, text):
        self.narrate(f"  [GAME MASTER] timer expired -> {text} (wanted {level})")
        if not self.dry_run:
            self.bridge.send("set_wanted_level", {"level": level})   # the reliable cops-on-timeout reaction

    def _print_subtitle(self, text, dur=5000, style="notify"):
        """On-screen objective/mission text via the bridge's inline `hud_message`. Default style=notify
        (the feed above the minimap) — verified to render in free-roam; PRINT subtitles/help text are
        suppressed unless the player has the Subtitles setting on, so they're unreliable for objectives."""
        if self.dry_run:
            return
        self.bridge.send("hud_message", {"text": text, "style": style, "duration": dur})

    def _set_hud(self, text):
        """Persistent bottom-center objective text (drawn every frame -> ignores the Subtitles setting).
        Short auto-expiry; the step loop refreshes it, so it clears on its own if the runner stops."""
        if not self.dry_run:
            self.bridge.send("set_hud_text", {"text": text, "duration": 12})

    def _clear_hud(self):
        if not self.dry_run:
            self.bridge.send("set_hud_text", {"text": "", "duration": 0})

    def _nat_result(self, name, args, ret):
        r = self._nat(name, args, ret)
        return r.get("result") if isinstance(r, dict) else None

    def _ground_z(self, x, y, z):
        """Real ground height at (x,y) via GET_GROUND_Z_FOR_3D_COORD (float* out-param). Needs the area
        loaded (near the player) -> returns None otherwise, so callers fall back to the coord z."""
        if self.dry_run:
            return None
        cell = self._scratch_cell()
        if not cell:
            return None
        self.bridge.send("write", {"address": hex(cell), "type": "float", "value": 0.0})
        ok = self._nat_result("GET_GROUND_Z_FOR_3D_COORD", [x, y, z + 8.0, cell, False], "bool")
        if not ok:
            return None
        gz = self.bridge.send("read", {"address": hex(cell), "type": "float"}).get("values")
        return gz if isinstance(gz, (int, float)) and gz != 0.0 else None

    def _maybe_refine_marker(self, obj, player_pos):
        """Once the player is near enough that the area is loaded, snap the checkpoint to the real ground
        (fixes the ~10ft-floating markers — landmark Z sits high)."""
        if self.dry_run or not self.native or self._checkpoint is None:
            return
        if self._marker_refined_for == self.idx:
            return
        c = obj.get("coords")
        if not c or _dist(player_pos, c) > 170.0:
            return
        gz = self._ground_z(c[0], c[1], c[2])
        if gz is not None and abs(gz - c[2]) > 0.7:
            self._nat("DELETE_CHECKPOINT", [self._checkpoint], "void")
            self._checkpoint = self._nat_result("CREATE_CHECKPOINT",
                [47, c[0], c[1], gz, c[0], c[1], gz, 4.0, 255, 180, 0, 150, 0], "int")
            if isinstance(self._checkpoint, int):
                self._nat("SET_CHECKPOINT_CYLINDER_HEIGHT", [self._checkpoint, 3.0, 3.0, 4.0], "void")
            c[2] = gz   # snap completion z too
        self._marker_refined_for = self.idx

    def _reward(self, amount):
        """Pay the player DIRECTLY into story-mode cash — no physical pickup, no leaving the car.
        Detect the active character, read SPx_TOTAL_CASH (STAT_GET_INT out-param) and add the reward."""
        if self.dry_run or not amount:
            return
        ped = self._nat_result("PLAYER_PED_ID", [], "int")
        model = self._nat_result("GET_ENTITY_MODEL", [ped], "int") if ped else None
        idx = 0
        for i, nm in ((0, "player_zero"), (1, "player_one"), (2, "player_two")):
            if self._nat_result("GET_HASH_KEY", [nm], "int") == model:
                idx = i; break
        stat = self._nat_result("GET_HASH_KEY", [f"SP{idx}_TOTAL_CASH"], "int")
        cell = self._scratch_cell()
        if stat and cell:
            self.bridge.send("write", {"address": hex(cell), "type": "int32", "value": 0})
            self._nat("STAT_GET_INT", [stat, cell, -1], "bool")
            cur = self.bridge.send("read", {"address": hex(cell), "type": "int32"}).get("values")
            if isinstance(cur, int):
                self._nat("STAT_SET_INT", [stat, cur + int(amount), True], "void")
        self._print_subtitle(f"~g~+${amount:,}~s~ earned.", 5000)

    def _world(self):
        r = self.bridge.send("get_world_state")
        if not isinstance(r, dict) or r.get("error"):
            return None
        p = r.get("player", {})
        return {"pos": [p.get("x") or 0, p.get("y") or 0, p.get("z") or 0],
                "wanted": p.get("wanted") or 0, "dead": p.get("health", 100) is not None and p.get("health", 100) <= 0,
                "dead_handles": []}

    def current_objective(self):
        """The objective now active (for a director/launcher to feed the player), or None if done."""
        return self.s["objectives"][self.idx] if self.idx < len(self.s["objectives"]) else None

    def step(self, world=None):
        """Advance the game master. `world` may be injected (so a combined loop reads world once and
        shares it with the player); if None, the runner reads it itself."""
        now = self.clock()
        if self.status == "pending":
            self.status = "running"; self.started = now; self.stage_started = now
            self._mission_flag(True)         # tell the GAME a mission is active (real system)
            self._say_event("mission_start")
            self.narrate(f"[MISSION] '{self.s['name']}' started. {len(self.s['objectives'])} objectives, "
                         f"{self.s.get('timer_seconds','no')}s limit." + (" [native]" if self.native else ""))
        w = world if world is not None else self._world()
        if w is None:
            return {"status": self.status, "note": "no world state"}

        # fail: player died
        if w["dead"]:
            return self._finish("failed", "player_died", now)
        # global timer -> reaction (cops). Fires once.
        tl = self.s.get("timer_seconds")
        if tl and not self.timed_out and (now - self.started) > tl:
            self.timed_out = True
            ot = self.s.get("on_timeout", {})
            self._call_cops(ot.get("wanted", 4), ot.get("subtitle", "Out of time - cops called!"))
            self._say_event("wanted_gained")

        obj = self.s["objectives"][self.idx]
        self._set_objective_blip(obj)
        self._maybe_refine_marker(obj, w["pos"])     # ground-snap the floating marker once near
        # keep the persistent bottom-center objective text alive (refresh well within its 12s expiry)
        if not self.dry_run and obj.get("text") and (now - self._text_shown_at) > 5.0:
            self._set_hud(obj["text"])
            self._text_shown_at = now
        if self._collected:
            w = {**w, "collected_count": self._collected}
        w = {**w, **self._live_state()}      # inject targets_alive etc. from tracked spawns
        stage_elapsed = now - self.stage_started
        if objective_complete(obj, w["pos"], w["wanted"], stage_elapsed, w):
            self.narrate(f"  [done] objective '{obj['id']}' ({obj.get('text','')})")
            self._say_event("objective_done")
            self._cleanup_spawns(); self._spawn_done_for = None
            self.idx += 1
            self.stage_started = now
            self._blip_set_for = None
            self._text_shown_at = 0     # show the next objective's text immediately
            if self.idx >= len(self.s["objectives"]):
                return self._finish("passed", None, now)
        else:
            self.narrate(f"  -> objective '{obj['id']}': {obj.get('text','')}"
                         + (f"  [timer {int(tl-(now-self.started))}s]" if tl and not self.timed_out else ""))
        return {"status": self.status, "objective": obj["id"], "index": self.idx,
                "elapsed": round(now - self.started, 1), "timed_out": self.timed_out}

    def _finish(self, result, reason, now):
        self.status = result
        secs = round(now - self.started, 1) if self.started else 0
        self._say_event("mission_passed" if result == "passed" else "mission_failed")
        self._cleanup_spawns()               # remove any spawned mission entities
        self._clear_hud()                    # remove the persistent objective text
        if self.native:                      # tear down the real systems
            if self._obj_blip is not None:
                self._remove_blip(self._obj_blip); self._obj_blip = None
            if self._checkpoint is not None:
                self._nat("DELETE_CHECKPOINT", [self._checkpoint], "void"); self._checkpoint = None
            self._mission_flag(False)
            if result == "passed":
                self._nat("PLAY_MISSION_COMPLETE_AUDIO", ["FRANKLIN_BIG_01"], "void")
        if result == "passed":
            reward = self.s.get("reward", 0)
            self.narrate(f"[MISSION PASSED] '{self.s['name']}' in {secs}s. Reward ${reward}.")
            self._reward(reward)
        else:
            self.narrate(f"[MISSION FAILED] '{self.s['name']}' ({reason}) at objective "
                         f"'{self.s['objectives'][min(self.idx,len(self.s['objectives'])-1)]['id']}' after {secs}s.")
        rec = {"scenario": self.s["name"], "result": result, "seconds": secs,
               "objectives_completed": self.idx, "total_objectives": len(self.s["objectives"]),
               "failed_objective": None if result == "passed" else self.s["objectives"][min(self.idx, len(self.s["objectives"])-1)]["id"],
               "fail_reason": reason, "timed_out": self.timed_out}
        self._log(rec)
        return {"status": result, "record": rec}

    def _log(self, rec):
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def run(self, max_steps=None, tick=0.5):
        steps = 0
        try:
            while self.status in ("pending", "running") and (max_steps is None or steps < max_steps):
                self.step(); steps += 1
                if tick:
                    time.sleep(tick)
        except KeyboardInterrupt:
            self.narrate("  interrupted.")
        return self.status


def load_scenario(name_or_path):
    p = name_or_path if os.path.exists(name_or_path) else os.path.join(SCEN_DIR, name_or_path + ".json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def review(log_path=LOG_PATH, scenario=None):
    recs = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        r = json.loads(ln)
                        if scenario is None or r.get("scenario") == scenario:
                            recs.append(r)
                    except ValueError:
                        pass
    return summarize_log(recs)


# ---------------------------------------------------------------- CLI + self-test
def _main(argv=None):
    ap = argparse.ArgumentParser(description="Mission game-master: run a scenario, enforce timer, learn.")
    ap.add_argument("scenario", nargs="?", help="scenario name (in tools/scenarios/) or path")
    ap.add_argument("--go", action="store_true", help="act in-game (default: dry-run narrate)")
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--tick", type=float, default=0.5)
    ap.add_argument("--review", action="store_true", help="summarize past runs (the 'what have I learned')")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.review:
        print(json.dumps(review(scenario=a.scenario and load_scenario(a.scenario)["name"] if a.scenario else None), indent=2)); return 0
    if not a.scenario:
        ap.error("scenario required (or --review / --self-test)")
    MissionRunner(load_scenario(a.scenario), dry_run=not a.go, clock=time.time).run(max_steps=a.steps, tick=a.tick)
    print("\nReview so far:", json.dumps(review(scenario=load_scenario(a.scenario)["name"]), indent=2))
    return 0


def _self_test():
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    # completion predicates
    o_reach = {"complete": {"type": "reach", "radius": 5}, "coords": [0, 0, 0]}
    assert objective_complete(o_reach, [3, 0, 0], 0, 0, {})
    assert not objective_complete(o_reach, [10, 0, 0], 0, 0, {})
    o_clean = {"complete": {"type": "left_area_and_clean", "radius": 100}, "coords": [0, 0, 0]}
    assert objective_complete(o_clean, [200, 0, 0], 0, 0, {})           # far + clean
    assert not objective_complete(o_clean, [200, 0, 0], 3, 0, {})       # far but wanted -> not done
    assert objective_complete({"complete": {"type": "timer", "seconds": 5}}, [0, 0, 0], 0, 6, {})

    # run a scenario over a scripted world; verify stage advance, timer->cops, pass, and logging
    scen = {"name": "Test Heist", "reward": 100000, "timer_seconds": 10,
            "on_timeout": {"wanted": 4, "subtitle": "cops!"},
            "objectives": [
                {"id": "travel", "text": "go to bank", "coords": [100, 0, 0], "complete": {"type": "reach", "radius": 5}},
                {"id": "grab", "text": "grab loot", "complete": {"type": "timer", "seconds": 3}},
                {"id": "escape", "text": "escape clean", "coords": [0, 0, 0], "complete": {"type": "left_area_and_clean", "radius": 100}},
            ]}
    # world script: t0 at bank (travel done) -> grab timer -> still wanted (no escape) past 10s (cops) -> clean+far (pass)
    world_seq = [
        {"player": {"x": 102, "y": 0, "z": 0, "wanted": 0, "health": 100}},   # at bank -> travel done
        {"player": {"x": 102, "y": 0, "z": 0, "wanted": 2, "health": 100}},   # grabbing (t+? )
        {"player": {"x": 102, "y": 0, "z": 0, "wanted": 2, "health": 100}},   # still grabbing
        {"player": {"x": 300, "y": 0, "z": 0, "wanted": 2, "health": 100}},   # far but wanted -> escape NOT done; timer expires here
        {"player": {"x": 300, "y": 0, "z": 0, "wanted": 0, "health": 100}},   # far + clean -> pass
    ]
    set_wanted = []
    times = [0, 4, 6, 11, 13]   # step 3 is at t=11 > 10s timer -> cops fire
    idx = {"i": 0}
    class MB:
        def send(self, cmd, params=None):
            if cmd == "get_world_state": return {"success": True, **world_seq[min(idx["i"], len(world_seq)-1)]}
            if cmd == "set_wanted_level": set_wanted.append(params["level"]); return {"success": True}
            return {"success": True, "result": 1}
    import tempfile
    logf = os.path.join(tempfile.gettempdir(), "mr_selftest.jsonl")
    if os.path.exists(logf): os.remove(logf)
    mr = MissionRunner(scen, bridge=MB(), dry_run=False, narrate=lambda *a: None,
                       clock=lambda: times[min(idx["i"], len(times)-1)], log_path=logf)
    results = []
    for i in range(5):
        idx["i"] = i
        results.append(mr.step())
        if mr.status in ("passed", "failed"):
            break
    assert mr.status == "passed", mr.status
    assert set_wanted == [4], f"cops should be called once on timeout: {set_wanted}"
    # log + review
    rev = review(log_path=logf)
    assert rev["runs"] == 1 and rev["passed"] == 1 and rev["success_rate"] == 1.0, rev
    # a failing run records the fail objective + reason
    idx["i"] = 0
    mr2 = MissionRunner(scen, bridge=type("B", (), {"send": lambda s, c, p=None: {"success": True, **{"player": {"x":0,"y":0,"z":0,"wanted":0,"health":0}}} if c=="get_world_state" else {"success": True}})(),
                        dry_run=False, narrate=lambda *a: None, clock=lambda: 1.0, log_path=logf)
    r = mr2.step()
    assert r["status"] == "failed" and r["record"]["fail_reason"] == "player_died", r
    rev2 = review(log_path=logf)
    assert rev2["runs"] == 2 and rev2["fail_reasons"].get("player_died") == 1, rev2

    # --- spawn/eliminate execution: spawn 2 hostiles, they die -> all_targets_dead completes ---
    elim_scen = {"name": "Elim Test", "objectives": [
        {"id": "kill", "kind": "kill", "coords": [10, 0, 0], "spawn": {"ped": "s_m_y_swat_01", "count": 2, "hostile": True},
         "complete": {"type": "all_targets_dead"}}]}
    dead = {"flag": False}
    spawned_calls = {"create_ped": 0}
    class MBspawn:
        def send(self, cmd, params=None):
            if cmd == "get_world_state": return {"success": True, "player": {"x": 10, "y": 0, "z": 0, "wanted": 0, "health": 100}}
            if cmd == "resolve": return {"hash": 1234}
            if cmd == "call_native_by_name":
                n = params.get("name")
                if n == "CREATE_PED": spawned_calls["create_ped"] += 1; return {"result": 100 + spawned_calls["create_ped"]}
                if n == "IS_ENTITY_DEAD": return {"result": dead["flag"]}
                return {"result": None}
            return {"success": True}
    mr3 = MissionRunner(elim_scen, bridge=MBspawn(), dry_run=False, narrate=lambda *a: None, clock=lambda: 0.0, log_path=logf)
    mr3.step()                                   # enter -> spawns 2 peds, both alive -> not complete
    assert spawned_calls["create_ped"] == 2 and len(mr3._targets) == 2, (spawned_calls, mr3._targets)
    assert mr3.status == "running"
    dead["flag"] = True                          # both die
    mr3.step()                                   # targets_alive 0 -> objective complete -> mission passed
    assert mr3.status == "passed", mr3.status
    assert mr3._targets == [], "spawns cleaned up on completion"
    print("mission_runner self-test PASSED — completion predicates + state machine + timer->cops + pass/fail + learning log + spawn/track/cleanup all correct")
    print("  (in-game blip/text/reward are best-effort and need verification; cops-on-timeout + completion-by-position are reliable)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
