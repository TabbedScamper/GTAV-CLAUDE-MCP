"""
lifeinvader_breakin.py — the first NATURAL, player-driven heist slice, built on confirmed-stable ground
(inside the Lifeinvader interior the Enable-All-Interiors mod opens). Proves the full loop with NO teleport:
walk to a marker -> Press E -> frame-perfect synced-scene animation (C# HeistInteractions.cs) -> grab ->
walk out. Beats are anchored relative to the player's live position so they land on real floor.

Run with the player already standing inside the interior. Uses tools/heist_beats.run_beats (file IPC to the
C# module) + the bridge for player pos and the cash reward.
"""
import sys, os, math, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mission_runner import BridgeClient
import heist_beats

DRILL_DICT = "anim_heist@hs3f@ig10_lockbox_drill@pattern_01@lockbox_01@male@"  # validated in-game
REWARD = 95000


def fwd(h, d):
    r = math.radians(h)
    return (-math.sin(r) * d, math.cos(r) * d)


def main(go=True):
    b = BridgeClient(timeout=8)
    p = b.send("get_world_state").get("player", {})
    px, py, pz, ph = p["x"], p["y"], p["z"], p.get("heading", 0.0)
    fx, fy = fwd(ph, 2.6)            # a spot ~2.6m ahead (the server rack) — still on the interior floor
    bx, by = fwd(ph, -3.5)          # a spot behind (back toward the entrance) for the exit
    hack = [round(px + fx, 2), round(py + fy, 2), round(pz, 2)]
    exit_spot = [round(px + bx, 2), round(py + by, 2), round(pz, 2)]

    beats = [
        {"id": "approach", "kind": "goto", "marker": hack, "radius": 1.8,
         "objective": "Find the ~y~server terminal"},
        {"id": "hack", "kind": "press_anim", "marker": hack, "anchor": hack, "heading": ph,
         "prompt": "Press ~INPUT_CONTEXT~ to breach the ~g~server",
         "dict": DRILL_DICT, "ped_clip": "action",
         "props": [{"model": "ch_prop_vault_drill_01a", "clip": "action_ch_prop_vault_drill_01a"}],
         "hold_seconds": 8.0, "objective": "Breach the ~g~server rack"},
        {"id": "grab", "kind": "press_anim", "marker": hack, "anchor": hack, "heading": ph,
         "prompt": "Press ~INPUT_CONTEXT~ to grab the ~g~data drive",
         "dict": DRILL_DICT, "ped_clip": "action", "props": [], "hold_seconds": 3.0,
         "objective": "Grab the ~g~data drive"},
        {"id": "escape", "kind": "goto", "marker": exit_spot, "radius": 3.0,
         "objective": "~y~Get out of the building"},
    ]
    print(f"Lifeinvader break-in: hack @ {hack}, exit @ {exit_spot}")
    if not go:
        print(json.dumps(beats, indent=1)); return

    done = heist_beats.run_beats(beats)
    if "escape" in done:
        b.send("call_native_by_name", {"name": "PLAY_SOUND_FRONTEND",
               "args": [-1, "Hack_Success", "DLC_HEIST_BIOLAB_PREP_HACKING_SOUNDS", True], "return_type": "void"})
        # reward
        cur = b.send("call_native_by_name", {"name": "STAT_GET_INT", "args": [], "return_type": "int"})
        b.send("set_hud_text", {"text": f"~g~Break-in complete!  +${REWARD:,}", "duration": 8})
        print(f"COMPLETE — reward ${REWARD:,} (grant via your money system)")
    else:
        print("incomplete:", done)


if __name__ == "__main__":
    main(go=("--go" in sys.argv))
