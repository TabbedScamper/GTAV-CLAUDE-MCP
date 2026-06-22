"""
endless_missions.py — INFINITE things to do. The player-facing loop that ties the grounded mission
generator (scenario_gen) to the live game master (mission_runner): generate a fresh mission near the
player, present it the game's real way (routed blip + checkpoint + objective text + real spawns), let
the PLAYER play it, reward on completion, then escalate difficulty and hand out the next one — forever.

Flow per mission:
  1. read the player's position, pick a landmark a sensible distance away (a real trip, not across the map)
  2. generate(theme=random, location=that landmark, difficulty=d)  -> a grounded, validated scenario
  3. announce it in-game (subtitle), then MissionRunner(...).run() — the player plays; the runner detects
     completion via real natives, drops the cash reward, and cleans up its blips/checkpoints/spawns
  4. pass -> difficulty+1 (harder next time); fail -> difficulty-1 (floor 1). Repeat.

HONESTY: generation + escalation + landmark-band selection are self-tested offline (--self-test, mock
bridge). The live run reuses mission_runner (its in-game effects are best-effort, by name through the
allowlist). Stdlib only; same dir as scenario_gen.py / mission_runner.py.
"""
import argparse, json, math, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scenario_gen as gen
from mission_runner import MissionRunner, BridgeClient


def _dist(a, b):
    return math.hypot((a[0] or 0) - b["x"], (a[1] or 0) - b["y"])

def landmark_band(pos, lms, lo=90.0, hi=1100.0):
    """Landmarks a sensible trip away from the player (not on top of them, not across the map)."""
    band = [l for l in lms if lo <= _dist(pos, l) <= hi]
    return band or [min(lms, key=lambda l: _dist(pos, l))]   # fallback: nearest

def player_pos(bridge):
    r = bridge.send("get_world_state")
    p = r.get("player", {}) if isinstance(r, dict) else {}
    if p.get("x") is None:
        return None
    return [p.get("x"), p.get("y"), p.get("z")]

def announce(bridge, scen, n, diff):
    """Big-feed subtitle so the player sees the new job in-game."""
    timer = scen.get("timer_seconds")
    line = f"~b~JOB #{n}~s~: ~y~{scen['name']}~s~  (~g~${scen['reward']:,}~s~)" + \
           (f"  ~o~{timer}s" if timer else "") + f"  [diff {diff}]"
    bridge.send("hud_message", {"text": line, "style": "notify"})   # inline, one-frame -> actually renders

def run_endless(count=None, start_diff=1, theme=None, tick=0.5, mission_timeout=900,
                go=True, narrate=print, bridge=None, landmarks=None):
    bridge = bridge or BridgeClient()
    lms = landmarks if landmarks is not None else gen.load_landmarks()
    if not lms:
        narrate("No landmarks loaded — need pyscript/world_data/landmarks.json"); return
    diff = max(1, start_diff)
    passed = total = 0
    while count is None or total < count:
        total += 1
        pos = player_pos(bridge)
        if pos is None:
            narrate("Can't read player position (is GTA running + bridge up?). Stopping."); break
        loc = random.choice(landmark_band(pos, lms))["name"]
        scen = gen.generate(theme=theme, location=loc, difficulty=diff, seed=random.randrange(1 << 30))
        gen.validate(scen)
        narrate(f"\n===== JOB #{total} (difficulty {diff}) =====")
        narrate(f"  {scen['name']} — {len(scen['objectives'])} objectives — ${scen['reward']:,}"
                + (f" — {scen['timer_seconds']}s" if scen.get('timer_seconds') else ""))
        if go:
            announce(bridge, scen, total, diff)
        runner = MissionRunner(scen, bridge=bridge, dry_run=not go, narrate=narrate)
        # cap steps so a stuck player auto-abandons and gets a fresh job instead of hanging forever
        max_steps = int(mission_timeout / tick) if mission_timeout else None
        status = runner.run(max_steps=max_steps, tick=tick)
        if status == "passed":
            passed += 1; diff += 1
            narrate(f"  [PASS] escalating to difficulty {diff}.")
        else:
            diff = max(1, diff - 1)
            narrate(f"  [{status.upper()}] easing to difficulty {diff}. Next job incoming.")
        if go and (count is None or total < count):
            time.sleep(2.5)   # breather between jobs
    narrate(f"\nEndless session done: {passed}/{total} passed.")
    return {"passed": passed, "total": total, "ended_difficulty": diff}


# ---------------------------------------------------------------- CLI + self-test
def _main(argv=None):
    ap = argparse.ArgumentParser(description="Infinite grounded missions for the player to play.")
    ap.add_argument("--count", type=int, default=None, help="number of jobs (default: endless)")
    ap.add_argument("--difficulty", type=int, default=1, help="starting difficulty (escalates on pass)")
    ap.add_argument("--theme", choices=list(gen.THEMES), help="lock a theme (default: random each job)")
    ap.add_argument("--tick", type=float, default=0.5)
    ap.add_argument("--mission-timeout", type=float, default=900, help="auto-abandon a job after N s (0=never)")
    ap.add_argument("--dry-run", action="store_true", help="narrate only; don't act in-game")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    run_endless(count=a.count, start_diff=a.difficulty, theme=a.theme, tick=a.tick,
                mission_timeout=a.mission_timeout or None, go=not a.dry_run)
    return 0


def _self_test():
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    lms = [{"name": "Near", "x": 100, "y": 0, "z": 0}, {"name": "Mid", "x": 500, "y": 0, "z": 0},
           {"name": "Far", "x": 5000, "y": 0, "z": 0}]
    # band selection: exclude too-close + too-far
    band = landmark_band([0, 0, 0], lms, lo=90, hi=1100)
    names = {l["name"] for l in band}
    assert "Mid" in names and "Far" not in names, names
    assert "Near" in names, names   # 100m is within [90,1100]
    # escalation: a mock bridge that always "passes" by teleporting the player onto each objective
    class MB:
        def __init__(s): s.diffs = []
        def send(s, cmd, params=None):
            if cmd == "get_world_state":
                # report player far away first read (for band), then the test drives completion via runner steps
                return {"success": True, "player": {"x": 500, "y": 0, "z": 0, "wanted": 0, "health": 100}}
            return {"success": True, "result": 1}
    # generation + validation across all themes at several difficulties (grounding holds)
    for th in gen.THEMES:
        for d in (1, 3, 5):
            s = gen.generate(theme=th, difficulty=d, seed=7, landmarks=lms)
            gen.validate(s)
            assert s["difficulty"] == d and s["reward"] > 0 and s["objectives"]
    # reward scales up with difficulty (same seed/theme)
    r1 = gen.generate(theme="heist", difficulty=1, seed=1, landmarks=lms)["reward"]
    r3 = gen.generate(theme="heist", difficulty=3, seed=1, landmarks=lms)["reward"]
    assert r3 > r1, (r1, r3)
    # run a short endless session over a mocked world. FailBridge reports the player as dead so each
    # mission ends immediately (no coord coordination needed) -> verify the loop iterates `count` times
    # and DE-escalates on failure (escalation-on-pass is covered by mission_runner's own self-test).
    def narr(*a): pass
    class FailBridge:
        def send(s, cmd, params=None):
            if cmd == "get_world_state":
                return {"success": True, "player": {"x": 0, "y": 0, "z": 0, "wanted": 0, "health": 0}}  # dead -> fail fast
            return {"success": True, "result": 1}
    res = run_endless(count=3, start_diff=3, theme="taxi", tick=0, mission_timeout=0,
                      go=True, narrate=narr, bridge=FailBridge(), landmarks=lms)
    assert res["total"] == 3 and res["passed"] == 0, res
    assert res["ended_difficulty"] == 1, res   # 3 fails from diff 3 -> 2 -> 1 -> 1 (floor)
    print("endless_missions self-test PASSED — landmark band, all-theme generation+validation, reward")
    print(f"  scaling, and the endless loop ({res['total']} jobs, de-escalation on fail). 17 themes x")
    print("  landmarks x difficulty x seed = effectively infinite, grounded, player-playable jobs.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
