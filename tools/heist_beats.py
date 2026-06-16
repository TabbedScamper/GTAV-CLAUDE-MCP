"""
heist_beats.py — the Python ORCHESTRATION layer for the C# heist-interaction module (HeistInteractions.cs).

The C# side runs the player-driven, animated beats that PyLoaderV's Python can't (synchronized scenes +
text builder need a real SHVDN script-thread context — see memory gtav-interactive-beats-need-csharp). This
module is the other half: it sequences beats by writing ONE current beat to heist_beat.json and waiting for
the C# to report state==done (matching seq) in heist_status.json, then advances. File-based IPC over
the LOCALAPPDATA/GTAV-Claude-MCP dir — both processes share it, so no bridge round-trip is needed for beats.

Beat kinds the C# understands (HeistInteractions.cs):
  goto        {marker:[x,y,z], radius}                       — walk to a spot (no key)
  press_anim  {marker, radius, anchor?, heading?, prompt,    — walk up + Press E -> aligned synced-scene anim
               dict, ped_clip, props:[{model,clip}], hold_seconds}
  open_vault  {at:[x,y,z]}                                   — open the real vault door via interior entity set
  unlock_door {model, at:[x,y,z]}                            — door-system auto-unlock (walk-in)
  clear       {}                                             — stop + restore control

`objective` on a beat is written to objective.txt (the C# bottom-center mission text).
Self-tested offline with a mock channel; live run drives the deployed DLL. Stdlib only.
"""
import argparse, json, os, sys, time

DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "GTAV-Claude-MCP")
BEAT_FILE = os.path.join(DIR, "heist_beat.json")
STATUS_FILE = os.path.join(DIR, "heist_status.json")
OBJ_FILE = os.path.join(DIR, "objective.txt")


class BeatChannel:
    """Writes beats + objective text, reads C# status. A mock can replace the file IO for tests."""
    def __init__(self, beat_file=BEAT_FILE, status_file=STATUS_FILE, obj_file=OBJ_FILE):
        self.beat_file, self.status_file, self.obj_file = beat_file, status_file, obj_file
        os.makedirs(os.path.dirname(beat_file), exist_ok=True)

    def write_beat(self, beat):
        with open(self.beat_file, "w", encoding="utf-8") as f:
            json.dump(beat, f)

    def write_objective(self, text):
        with open(self.obj_file, "w", encoding="utf-8") as f:
            f.write(text or "")

    def read_status(self):
        try:
            with open(self.status_file, "r", encoding="utf-8") as f:
                t = f.read().strip()
            return json.loads(t) if t else {}
        except (OSError, ValueError):
            return {}

    def clear(self):
        self.write_beat("")          # empty file => C# stops
        self.write_objective("")


def run_beats(beats, channel=None, narrate=print, poll=0.4, timeout_per_beat=600, clock=time.time, sleep=time.sleep):
    """Drive a list of beat dicts through the C# module, in order. Each beat:
       - gets a fresh incrementing seq, its objective text is shown,
       - we wait until the C# reports {seq, state:'done'} for THAT seq (or time out),
       - then advance. Returns the list of completed beat ids.
    Beats with kind in (open_vault, unlock_door, clear) are fire-and-confirm (C# marks done immediately)."""
    channel = channel or BeatChannel()
    done_ids = []
    seq = int(clock()) & 0x7fffffff      # unique-ish start so a stale status file can't false-match
    for i, beat in enumerate(beats):
        seq += 1
        b = dict(beat); b["seq"] = seq
        channel.write_objective(b.get("objective", ""))
        channel.write_beat(b)
        bid = b.get("id", f"beat{i}")
        narrate(f"[BEAT {i+1}/{len(beats)}] {bid} ({b.get('kind')}) -> {b.get('objective','')}")
        t0 = clock()
        while True:
            st = channel.read_status()
            if st.get("seq") == seq and st.get("state") == "done":
                narrate(f"  [done] {bid}")
                done_ids.append(bid)
                break
            if clock() - t0 > timeout_per_beat:
                narrate(f"  [timeout] {bid} after {timeout_per_beat}s — stopping")
                channel.clear()
                return done_ids
            sleep(poll)
    channel.clear()
    narrate(f"[HEIST COMPLETE] {len(done_ids)}/{len(beats)} beats")
    return done_ids


# ---------------------------------------------------------------- self-test (mock channel, no game)
def _self_test():
    class Mock(BeatChannel):
        def __init__(self): self.cur = None; self.obj = ""; self.cleared = 0
        def write_beat(self, beat): self.cur = beat
        def write_objective(self, text): self.obj = text
        def clear(self): self.cleared += 1; self.cur = ""
        def read_status(self):
            # simulate the C# completing whatever beat is current
            if isinstance(self.cur, dict):
                return {"seq": self.cur["seq"], "state": "done"}
            return {}
    beats = [
        {"id": "approach", "kind": "goto", "marker": [1, 2, 3], "objective": "Get to the bank"},
        {"id": "drill", "kind": "press_anim", "marker": [1, 2, 3], "dict": "d", "ped_clip": "action",
         "props": [{"model": "m", "clip": "c"}], "objective": "Drill the vault"},
        {"id": "open", "kind": "open_vault", "at": [1, 2, 3], "objective": "Crack it"},
    ]
    m = Mock()
    done = run_beats(beats, channel=m, narrate=lambda s: None, poll=0, sleep=lambda s: None)
    assert done == ["approach", "drill", "open"], done
    assert m.cleared >= 1
    # timeout path
    class Never(Mock):
        def read_status(self): return {}
    t = {"n": 0}
    done2 = run_beats(beats, channel=Never(), narrate=lambda s: None, poll=0,
                      clock=lambda: t.__setitem__("n", t["n"] + 1) or t["n"], timeout_per_beat=5, sleep=lambda s: None)
    assert done2 == [], done2
    print("heist_beats self-test OK: sequencing, completion match, clear, timeout")


def main():
    ap = argparse.ArgumentParser(description="Run a heist beat sequence through the C# interaction module")
    ap.add_argument("scenario", nargs="?", help="path to a JSON file: {\"beats\": [...]}")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        _self_test(); return
    if not args.scenario:
        ap.error("need a scenario json (or --self-test)")
    with open(args.scenario, "r", encoding="utf-8") as f:
        data = json.load(f)
    run_beats(data["beats"])


if __name__ == "__main__":
    main()
