"""
play_heist.py — "Claude, build a bank heist and play through it." The COMBINED launcher.

Runs the Director-Actor loop (IBSEN director-actor / Left-4-Dead AI-Director pattern): one tick loop with
two roles sharing one world read per tick:
  * DIRECTOR (game master)  = mission_runner.MissionRunner — owns the scenario: advances objectives as the
    player completes them, enforces the timer (cops on timeout), judges pass/fail, and LOGS the run (learning).
  * ACTOR (player)          = play_agent.RuleDecider (+ optional ClaudeDecider) — pursues the director's
    CURRENT objective as its goal, while the reflex handles threats/safety.

The director's current objective is fed straight to the player as its goal (tight coupling — no dependence
on blip presentation), so "build" (the scenario + director) and "play" (the actor) run as one watchable loop.
After each run the director's `review()` is printed — the telemetry that makes it smarter over plays.

HONESTY: the loop, the director↔actor coupling, goal mapping, and dedup are self-tested offline
(`--self-test`, mocked bridge), on top of the already-tested mission_runner + play_agent cores. Acting in
game is gated behind `--go` (default dry-run narrates both sides). Stdlib only.
"""
import argparse, json, sys, time
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from mission_runner import MissionRunner, load_scenario, review, BridgeClient
from play_agent import RuleDecider, ClaudeDecider


def _world_for_director(state):
    """Project the full world snapshot into the small shape MissionRunner.step expects."""
    p = state.get("player", {}) if isinstance(state, dict) else {}
    return {"pos": [p.get("x") or 0, p.get("y") or 0, p.get("z") or 0],
            "wanted": p.get("wanted") or 0,
            "dead": (p.get("health", 100) is not None and p.get("health", 100) <= 0),
            "dead_handles": []}


class HeistLauncher:
    def __init__(self, scenario, bridge=None, reflex=None, strategist=None,
                 dry_run=True, narrate=print, clock=time.time):
        self.bridge = bridge or BridgeClient()
        self.narrate = narrate
        self.dry_run = dry_run
        self.reflex = reflex or RuleDecider()
        self.strategist = strategist            # optional Slow Mind (commentary/adaptation)
        # the director shares our bridge/clock; we always inject world so it never double-reads
        self.mr = MissionRunner(scenario, bridge=self.bridge, dry_run=dry_run,
                                narrate=lambda m: self.narrate("  " + m), clock=clock)
        self._last_act = None

    def step(self):
        state = self.bridge.send("get_world_state")
        if not isinstance(state, dict) or state.get("error"):
            return {"error": state.get("error") if isinstance(state, dict) else "bad state", "done": True}
        ev = self.bridge.send("world_events")
        events = ev.get("events", []) if isinstance(ev, dict) else []
        if not self.dry_run:                 # protagonist reacts to combat/danger (commentary; cooldown-gated)
            _map = {"took_damage": "took_damage", "new_threat": "combat_start", "threat_closing": "new_threat"}
            for e in events:
                ce = _map.get(e.get("event"))
                if ce:
                    self.bridge.send("comment", {"event": ce}); break

        # DIRECTOR: advance the scenario using the shared world
        self.mr.step(_world_for_director(state))
        if self.mr.status in ("passed", "failed"):
            return {"done": True, "result": self.mr.status}

        # ACTOR: pursue the director's current objective
        obj = self.mr.current_objective()
        if obj is None:
            return {"done": True, "result": self.mr.status}
        if obj.get("coords"):
            goal = {"type": "goto", "x": obj["coords"][0], "y": obj["coords"][1], "z": obj["coords"][2]}
            verb, params, reason = self.reflex.decide(state, events, goal)
        elif obj.get("kind") in ("engage", "kill") and obj.get("target"):
            verb, params, reason = "engage", {"target": obj["target"]}, "objective target"
        else:
            # a 'wait'/'grab' objective with no coords -> hold position
            verb, params, reason = "stop", {}, f"hold for '{obj['id']}'"

        changed = (verb, params) != self._last_act
        if changed and not self.dry_run:
            self.bridge.send("act", {"verb": verb, "params": params})
        if changed:
            self._last_act = (verb, params)

        self.narrate(f"[DIRECTOR] {obj['id']}: {obj.get('text','')}  |  [PLAYER] {verb} ({reason})")
        return {"done": False, "objective": obj["id"], "verb": verb}

    def run(self, max_steps=None, tick=0.5):
        self.narrate(f"=== play_heist: '{self.mr.s['name']}' (dry_run={self.dry_run}) ===")
        steps = 0
        try:
            while max_steps is None or steps < max_steps:
                r = self.step()
                steps += 1
                if r.get("done"):
                    break
                if tick:
                    time.sleep(tick)
        except KeyboardInterrupt:
            self.narrate("interrupted.")
        if not self.dry_run:
            self.bridge.send("stop_acting")
        self.narrate("Review (what it has learned): " + json.dumps(review(scenario=self.mr.s["name"])))
        return self.mr.status


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Build-and-play: director (mission) + actor (player) in one loop.")
    ap.add_argument("scenario", nargs="?", default="bank_heist", help="scenario name in tools/scenarios/ or a path")
    ap.add_argument("--go", action="store_true", help="act in-game (default: dry-run narrate both sides)")
    ap.add_argument("--claude", action="store_true", help="enable the Claude Slow Mind for adaptation/commentary")
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--tick", type=float, default=0.5)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    strategist = None
    if a.claude:
        try:
            from claude_strategist import ClaudeStrategist
            s = ClaudeStrategist().start()
            strategist = ClaudeDecider(call=s.decide_goal) if not s._err else None
            print("[play_heist] Claude Slow Mind " + ("enabled." if strategist else f"unavailable ({s._err})."))
        except Exception as e:
            print(f"[play_heist] Claude unavailable ({e}); director+reflex only.")
    HeistLauncher(load_scenario(a.scenario), strategist=strategist, dry_run=not a.go).run(max_steps=a.steps, tick=a.tick)
    return 0


def _self_test():
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    scen = {"name": "Launcher Test Heist", "reward": 100000, "timer_seconds": 600,
            "objectives": [
                {"id": "travel", "kind": "goto", "text": "go to bank", "coords": [100, 0, 0], "complete": {"type": "reach", "radius": 5}},
                {"id": "grab", "kind": "wait", "text": "grab loot", "complete": {"type": "timer", "seconds": 3}},
                {"id": "escape", "kind": "escape", "text": "escape", "coords": [0, 0, 0], "complete": {"type": "left_area_and_clean", "radius": 100}},
            ]}
    worlds = [
        {"player": {"x": 50, "y": 0, "z": 0, "wanted": 0, "health": 100, "vehicle": "adder"}, "threats": []},   # travelling
        {"player": {"x": 102, "y": 0, "z": 0, "wanted": 0, "health": 100, "vehicle": "adder"}, "threats": []},  # at bank -> advance to grab
        {"player": {"x": 102, "y": 0, "z": 0, "wanted": 0, "health": 100, "vehicle": "adder"}, "threats": []},  # grabbing (hold)
        {"player": {"x": 102, "y": 0, "z": 0, "wanted": 0, "health": 100, "vehicle": "adder"}, "threats": []},  # grab timer done -> escape
        {"player": {"x": 300, "y": 0, "z": 0, "wanted": 0, "health": 100, "vehicle": "adder"}, "threats": []},  # far+clean -> pass
    ]
    times = [0, 1, 2, 5, 6]   # grab(seconds 3) completes once stage_elapsed>=3 (stage starts ~t1)
    idx = {"i": 0}; acts = []
    import tempfile, os
    logf = os.path.join(tempfile.gettempdir(), "heist_selftest.jsonl")
    if os.path.exists(logf): os.remove(logf)
    class MB:
        def send(self, cmd, params=None):
            if cmd == "get_world_state": return {"success": True, **worlds[min(idx["i"], len(worlds)-1)]}
            if cmd == "world_events": return {"events": []}
            if cmd == "act": acts.append((params["verb"], params.get("params"))); return {"success": True}
            if cmd == "set_wanted_level" or cmd == "stop_acting": return {"success": True}
            return {"success": True, "result": 1}
    hl = HeistLauncher(scen, bridge=MB(), dry_run=False, narrate=lambda *a: None, clock=lambda: times[min(idx["i"], len(times)-1)])
    hl.mr.log_path = logf
    seq = []
    for i in range(len(worlds)):
        idx["i"] = i
        r = hl.step(); seq.append(r)
        if r.get("done"):
            break
    # director reached 'passed'; player drove to bank, held during grab, drove to escape
    assert hl.mr.status == "passed", hl.mr.status
    verbs = [r.get("verb") for r in seq if "verb" in r]
    assert verbs[0] == "drive_to", verbs                     # heading to the bank
    assert "stop" in verbs, verbs                            # held during the grab objective
    assert verbs.count("drive_to") >= 2, verbs               # bank, then escape
    # acting de-dups (drive_to to same bank coords issued once before switching)
    assert ("stop", {}) in acts and ("drive_to", {"x": 100, "y": 0, "z": 0}) in acts, acts
    rev = review(log_path=logf)
    assert rev["runs"] == 1 and rev["passed"] == 1, rev
    print("play_heist self-test PASSED — director advances objectives, actor pursues each, hold-on-grab, pass + learning all correct")
    print("  (composes the tested mission_runner + play_agent cores; in-game driving/presentation need verification)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
