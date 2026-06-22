"""
play_agent.py — "tell me to play and watch": the real-time agent LOOP.

Architecture (grounded in current real-time-LLM-agent research — Hierarchical Language Agent / HLA):
  * EXECUTOR (high frequency): GTA's own task system runs the current verb at 60fps. We never touch the
    per-frame loop — we just issue a verb (pyscript/agent_actions.py) and the engine executes it.
  * FAST MIND / reflex policy (every tick, ~2 Hz, NO LLM): RuleDecider picks a sensible verb from the
    world-state instantly — handles safety + continuity (flee when hurt, engage threats, head to the
    goal). This is the latency-hiding + cost-saving layer (cache/reflex idea: most ticks need no LLM).
  * SLOW MIND / strategist (on events or a cooldown, the LLM): ClaudeDecider sets the high-level GOAL/
    intent for novel situations. Called rarely (event-driven), so it never bottlenecks the action.

The loop: sense (get_world_state) -> events (world_events) -> reflex decides every tick -> escalate to
Claude on an event or cooldown -> act (issue verb; the bridge dedups unchanged intents so no task spam)
-> narrate ("watch") -> sleep. Event-driven + dedup + reflex-default = responsive without per-frame LLM.

HONESTY: the loop + reflex policy + decision wiring is self-tested offline (`--self-test`, mocked bridge).
The ClaudeDecider is scaffolded (wire your Agent SDK / gtav_host where marked). Acting for real needs
GTA + the bridge loaded (world_sense + agent_actions) and is gated behind `--go` (default is dry-run:
narrate decisions, issue nothing). Stdlib only.
"""
import argparse, json, math, socket, struct, sys, time

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 27015


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


# ---------------------------------------------------------------- FAST MIND: reflex policy (no LLM)
def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(min(len(a), len(b)))))

class RuleDecider:
    """Instant, deterministic verb from world-state + the current goal. Safety first, then goal, then idle."""
    LOW_HP = 30
    ARRIVE = 8.0

    def decide(self, state, events, goal):
        p = state.get("player", {})
        hp = p.get("health") or 100
        threats = state.get("threats", [])
        in_veh = bool(p.get("vehicle"))
        pos = [p.get("x") or 0, p.get("y") or 0, p.get("z") or 0]

        # 1) safety reflex: hurt + threatened -> flee the nearest threat
        if threats and hp <= self.LOW_HP:
            t = threats[0]
            return ("flee", {"from_ped": t["handle"]} if t.get("handle") else
                    {"x": pos[0], "y": pos[1] + 50, "z": pos[2]}, f"low HP ({hp}) + threat -> flee")
        # 2) combat reflex: threatened and healthy -> engage
        if threats and hp > self.LOW_HP:
            t = threats[0]
            if t.get("handle"):
                return ("engage", {"target": t["handle"]}, f"threat {t.get('dist')}m -> engage")
            return ("engage_area", {"radius": 80.0}, "threats near -> engage area")
        # 3) explicit goal: head to the waypoint
        if goal and goal.get("type") == "goto" and all(k in goal for k in ("x", "y", "z")):
            if _dist(pos, [goal["x"], goal["y"], goal["z"]]) > self.ARRIVE:
                verb = "drive_to" if in_veh else "walk_to"
                params = {"x": goal["x"], "y": goal["y"], "z": goal["z"]}
                if not in_veh:
                    params["run"] = True
                return (verb, params, f"goal -> {verb}")
            return ("stop", {}, "arrived at goal")
        # 3b) MISSION OBJECTIVE: pursue the game's current objective (sensed via blips) when free
        obj = state.get("objective")
        if obj:
            if obj.get("kind") == "enemy" and obj.get("entity"):
                return ("engage", {"target": obj["entity"]}, "objective enemy -> engage")
            c = obj.get("coords")
            if c and _dist(pos, c) > self.ARRIVE:
                verb = "drive_to" if in_veh else "walk_to"
                params = {"x": c[0], "y": c[1], "z": c[2]}
                if not in_veh:
                    params["run"] = True
                return (verb, params, f"objective ({obj.get('kind')}) -> {verb}")
        # 4) idle behavior: cruise or wander
        if in_veh:
            return ("drive_wander", {}, "no goal, in car -> cruise")
        return ("wander", {}, "no goal, on foot -> wander")


# ---------------------------------------------------------------- SLOW MIND: Claude strategist (scaffold)
class ClaudeDecider:
    """Sets the high-level GOAL on events/cooldown. >>> Wire your Claude Agent SDK here (see gtav_host.py).
    It should receive state['summary'] + the verb list + current goal, and return a new goal dict, e.g.
    {'type':'goto','x','y','z','note':'rob the bank'} or {'type':'freeplay'}.  Default: keep the goal."""
    def __init__(self, call=None):
        self.call = call   # call(summary, events, goal, verbs) -> goal dict
    def revise_goal(self, state, events, goal, verbs):
        if not self.call:
            return goal
        try:
            return self.call(state.get("summary", ""), events, goal, verbs) or goal
        except Exception:
            return goal


# ---------------------------------------------------------------- the loop
SIGNIFICANT = {"took_damage", "wanted_changed", "new_threat", "threat_closing", "exited_vehicle"}

class PlayAgent:
    def __init__(self, bridge=None, reflex=None, strategist=None, goal=None,
                 tick=0.5, claude_cooldown=8.0, dry_run=True, narrate=print, clock=time.time):
        self.bridge = bridge or BridgeClient()
        self.reflex = reflex or RuleDecider()
        self.strategist = strategist or ClaudeDecider()
        self.goal = goal
        self.tick = tick
        self.claude_cooldown = claude_cooldown
        self.dry_run = dry_run
        self.narrate = narrate
        self.clock = clock
        self._last_claude = -1e9
        self._verbs = None
        self._last_act = None    # client-side dedup: skip the bridge round-trip when the verb is unchanged

    def _verb_list(self):
        if self._verbs is None:
            r = self.bridge.send("list_verbs")
            self._verbs = r.get("verbs", {}) if isinstance(r, dict) else {}
        return self._verbs

    def step(self):
        state = self.bridge.send("get_world_state")
        if not isinstance(state, dict) or state.get("error"):
            return {"error": state.get("error") if isinstance(state, dict) else "bad state"}
        ev = self.bridge.send("world_events")
        events = ev.get("events", []) if isinstance(ev, dict) else []
        names = {e.get("event") for e in events}

        # SLOW MIND: revise the goal on a significant event or after cooldown
        now = self.clock()
        if (names & SIGNIFICANT) or (now - self._last_claude >= self.claude_cooldown):
            self.goal = self.strategist.revise_goal(state, events, self.goal, self._verb_list())
            self._last_claude = now

        # FAST MIND: pick the verb to execute now (instant, no LLM)
        verb, params, reason = self.reflex.decide(state, events, self.goal)

        # EXECUTOR: issue it only when the intent CHANGED (client-side dedup saves a round-trip/tick;
        # the bridge dedups again as a backstop). The engine keeps running the standing task meanwhile.
        acted = {"unchanged": True}
        changed = (verb, params) != self._last_act
        if changed and not self.dry_run:
            acted = self.bridge.send("act", {"verb": verb, "params": params})
        if changed:
            self._last_act = (verb, params)
        self.narrate(f"[{verb}] {reason} | {state.get('summary','')[:120]}"
                     + (f" | events: {','.join(names)}" if names else ""))
        return {"state": state, "events": events, "verb": verb, "params": params,
                "reason": reason, "goal": self.goal, "acted": acted}

    def run(self, max_steps=None):
        self.narrate(f"play_agent starting (goal={self.goal}, dry_run={self.dry_run})")
        steps = 0
        try:
            while max_steps is None or steps < max_steps:
                r = self.step()
                if r.get("error"):
                    self.narrate(f"  stop: {r['error']}")
                    break
                steps += 1
                if self.tick:
                    time.sleep(self.tick)
        except KeyboardInterrupt:
            self.narrate("  interrupted; clearing tasks")
        if not self.dry_run:
            self.bridge.send("stop_acting")
        return steps


# ---------------------------------------------------------------- CLI + self-test
def _main(argv=None):
    ap = argparse.ArgumentParser(description="Real-time 'play and watch' agent loop.")
    ap.add_argument("--goto", nargs=3, type=float, metavar=("X", "Y", "Z"), help="goal: drive/walk to a coord")
    ap.add_argument("--tick", type=float, default=0.5, help="reactive tick seconds")
    ap.add_argument("--cooldown", type=float, default=8.0, help="min seconds between strategist (Claude) calls")
    ap.add_argument("--steps", type=int, default=None, help="max steps (default: run until Ctrl+C)")
    ap.add_argument("--go", action="store_true", help="actually act in-game (default: dry-run, narrate only)")
    ap.add_argument("--claude", action="store_true",
                    help="enable the Claude Slow Mind (strategist) via the Agent SDK; default is reflex-only")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    goal = {"type": "goto", "x": a.goto[0], "y": a.goto[1], "z": a.goto[2]} if a.goto else {"type": "freeplay"}
    strategist = ClaudeDecider()
    if a.claude:
        try:
            from claude_strategist import ClaudeStrategist
            s = ClaudeStrategist().start()
            if s._err:
                print(f"[play_agent] Claude strategist unavailable ({s._err}); running reflex-only.")
            else:
                strategist = ClaudeDecider(call=s.decide_goal)
                print("[play_agent] Claude Slow Mind enabled (sets goals on events/cooldown).")
        except Exception as e:
            print(f"[play_agent] could not start Claude strategist ({e}); running reflex-only.")
    PlayAgent(goal=goal, strategist=strategist, tick=a.tick, claude_cooldown=a.cooldown,
              dry_run=not a.go).run(max_steps=a.steps)
    return 0


def _self_test():
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    # reflex policy unit checks
    R = RuleDecider()
    assert R.decide({"player": {"health": 20, "vehicle": None, "x": 0, "y": 0, "z": 0},
                     "threats": [{"handle": 9, "dist": 10}]}, [], None)[0] == "flee"
    assert R.decide({"player": {"health": 90}, "threats": [{"handle": 9, "dist": 10}]}, [], None)[0] == "engage"
    assert R.decide({"player": {"health": 100, "vehicle": "adder", "x": 0, "y": 0, "z": 0}, "threats": []},
                    [], {"type": "goto", "x": 100, "y": 0, "z": 0})[0] == "drive_to"
    assert R.decide({"player": {"health": 100, "vehicle": None, "x": 0, "y": 0, "z": 0}, "threats": []},
                    [], {"type": "goto", "x": 100, "y": 0, "z": 0})[0] == "walk_to"
    assert R.decide({"player": {"health": 100, "vehicle": None}, "threats": []}, [], None)[0] == "wander"
    # objective pursuit (no explicit goal): destination -> drive_to; enemy objective -> engage
    assert R.decide({"player": {"health": 100, "vehicle": "adder", "x": 0, "y": 0, "z": 0}, "threats": [],
                     "objective": {"kind": "route_destination", "coords": (200, 0, 0)}}, [], {"type": "freeplay"})[0] == "drive_to"
    assert R.decide({"player": {"health": 100, "vehicle": None, "x": 0, "y": 0, "z": 0}, "threats": [],
                     "objective": {"kind": "enemy", "entity": 42, "coords": (5, 0, 0)}}, [], None)[0] == "engage"

    # loop over scripted states with a mock bridge; verify verbs chosen + acting + dedup + Claude cadence
    scripted = [
        {"player": {"health": 100, "vehicle": "adder", "x": 0, "y": 0, "z": 0}, "threats": [], "summary": "cruising"},
        {"player": {"health": 100, "vehicle": "adder", "x": 0, "y": 0, "z": 0}, "threats": [], "summary": "cruising"},
        {"player": {"health": 25, "vehicle": None, "x": 0, "y": 0, "z": 0}, "threats": [{"handle": 9, "dist": 8}], "summary": "ambushed"},
        {"player": {"health": 80, "vehicle": None, "x": 0, "y": 0, "z": 0}, "threats": [{"handle": 9, "dist": 8}], "summary": "fighting"},
    ]
    ev_seq = [[], [], [{"event": "took_damage"}], []]
    acts, claude_calls = [], []
    idx = {"i": 0}
    class MockBridge:
        def send(self, cmd, params=None):
            if cmd == "get_world_state": return {"success": True, **scripted[min(idx["i"], len(scripted) - 1)]}
            if cmd == "world_events":    return {"events": ev_seq[min(idx["i"], len(ev_seq) - 1)]}
            if cmd == "list_verbs":      return {"verbs": {"drive_wander": "", "flee": "", "engage": ""}}
            if cmd == "act":             acts.append((params["verb"], params.get("params"))); return {"success": True}
            if cmd == "stop_acting":     return {"success": True}
            return {}
    def strat_call(summary, events, goal, verbs):
        claude_calls.append(summary); return goal
    clk = {"t": 0.0}
    ag = PlayAgent(bridge=MockBridge(), strategist=ClaudeDecider(call=strat_call), goal={"type": "freeplay"},
                   tick=0, claude_cooldown=8.0, dry_run=False, narrate=lambda *a: None, clock=lambda: clk["t"])
    chosen = []
    for i in range(len(scripted)):
        idx["i"] = i; clk["t"] = i * 1.0
        chosen.append(ag.step()["verb"])
    assert chosen == ["drive_wander", "drive_wander", "flee", "engage"], chosen
    # acting dedups: drive_wander issued once (steps 0,1 identical), then flee, then engage = 3 acts
    assert [v for v, _ in acts] == ["drive_wander", "flee", "engage"], acts
    # Claude (strategist) called step0 (cooldown) and step2 (took_damage event) only — NOT every tick
    assert len(claude_calls) == 2, claude_calls
    print("play_agent self-test PASSED — reflex policy + loop + act-dedup + event-driven Claude cadence correct")
    print("  (Executor verbs + world-sensing run in-game; wire ClaudeDecider.call to gtav_host for the Slow Mind.)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
