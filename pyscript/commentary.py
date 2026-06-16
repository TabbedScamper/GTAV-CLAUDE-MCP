"""
commentary.py — make Michael / Franklin / Trevor COMMENT on what's happening, natively.

It doesn't extract or play audio — it tells the game to play ITS OWN recorded line in the protagonist's
voice via PLAY_PED_AMBIENT_SPEECH_NATIVE (a real ped reacting). An event -> a fitting speech CONTEXT
(world_data/speech_contexts.json, grounded in R*'s decompiled scripts) -> the game speaks it. Wired to our
event bus: world_events (took_damage / new_threat / wanted_changed) and the mission engine (objective done,
timer, pass/fail) fire `comment(event)` so the protagonist reacts live during a generated heist.

Anti-spam: a cooldown + IS_AMBIENT_SPEECH_PLAYING check so it doesn't talk over itself. Voice is the
player's protagonist by default; pass voice="trevor"/"michael"/"franklin" to force one (SET_AMBIENT_VOICE_NAME).

HONESTY: the event->context selection + cooldown logic is self-tested (`python commentary.py`). The speech
natives are called by name through the allowlist (wrong name -> safe error, never a crash) and need in-game
verification (some contexts may not exist for a given voice — swap them in speech_contexts.json). Pure-data
status is paused-safe.

Commands: comment, say, commentary_status, commentary_set (cooldown/enable).
"""
import json, os, time

_g = {}
def bind(bridge_globals):
    _g.clear(); _g.update(bridge_globals)

_HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = {}            # event -> [contexts]
VOICES = {"michael": "MICHAEL", "franklin": "FRANKLIN", "trevor": "TREVOR"}
_state = {"last": -1e9, "cooldown": 4.0, "enabled": True, "rr": {}}   # rr = round-robin index per event

def _load():
    EVENTS.clear()
    p = os.path.join(_HERE, "world_data", "speech_contexts.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            EVENTS.update(json.load(f).get("events", {}))
    return len(EVENTS)

_N = _load()

# ---------------------------------------------------------------- pure logic (self-tested)
def pick_context(event, rotate=True):
    """Choose a context for an event; round-robin through the list for variety (deterministic, no RNG)."""
    opts = EVENTS.get(event)
    if not opts:
        return None
    if not rotate:
        return opts[0]
    i = _state["rr"].get(event, 0) % len(opts)
    _state["rr"][event] = i + 1
    return opts[i]

def can_speak(now, speaking=False):
    return _state["enabled"] and not speaking and (now - _state["last"]) >= _state["cooldown"]

# ---------------------------------------------------------------- native (verify in-game)
def _native(name, args=(), ret=None):
    h = _g.get("handle_call_native_by_name")
    if not h:
        return None
    r = h({"name": name, "args": list(args), "return_type": ret})
    return (None if (isinstance(r, dict) and r.get("error")) else (r.get("result") if isinstance(r, dict) else r))

def _player_ped():
    return _native("PLAYER_PED_ID", [], "int")

def say(context, voice=None, ped=None):
    """Play one speech context in the protagonist's voice (force-played). Returns what it issued."""
    ped = ped if ped is not None else _player_ped()
    if not ped:
        return {"error": "no ped"}
    if voice and voice.lower() in VOICES:
        _native("SET_AMBIENT_VOICE_NAME", [ped, VOICES[voice.lower()], True], "void")
    # PLAY_PED_AMBIENT_SPEECH_NATIVE(ped, speechName, speechParam, p3); FORCE param so it plays now.
    _native("PLAY_PED_AMBIENT_SPEECH_NATIVE", [ped, context, "SPEECH_PARAMS_FORCE_SHOUTED_CLEAR", 0], "void")
    return {"success": True, "context": context, "voice": voice}

def comment(event, voice=None, now=None):
    """Event -> fitting protagonist line, respecting the cooldown + not talking over speech."""
    now = now if now is not None else time.time()
    speaking = bool(_native("IS_AMBIENT_SPEECH_PLAYING", [_player_ped()], "bool")) if not _g.get("_test") else False
    if not can_speak(now, speaking):
        return {"success": True, "skipped": "cooldown_or_busy"}
    ctx = pick_context(event)
    if not ctx:
        return {"success": True, "skipped": f"no context for '{event}'"}
    _state["last"] = now
    return {"success": True, "event": event, **say(ctx, voice)}

# ---- handlers ----
def handle_comment(p): return comment(p.get("event"), p.get("voice"))
def handle_say(p):     return say(p.get("context"), p.get("voice"))
def handle_commentary_status(p):
    return {"success": True, "events": sorted(EVENTS.keys()), "voices": list(VOICES),
            "cooldown": _state["cooldown"], "enabled": _state["enabled"]}
def handle_commentary_set(p):
    if "cooldown" in p: _state["cooldown"] = float(p["cooldown"])
    if "enabled" in p: _state["enabled"] = bool(p["enabled"])
    return {"success": True, "cooldown": _state["cooldown"], "enabled": _state["enabled"]}

COMMENTARY_COMMANDS = {"comment": handle_comment, "say": handle_say,
                       "commentary_status": handle_commentary_status, "commentary_set": handle_commentary_set}
COMMENTARY_OFFTHREAD = {"commentary_status"}   # native-touching otherwise -> game thread

if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    _g["_test"] = True
    fired = []
    bind({"_test": True, "handle_call_native_by_name": lambda p: (fired.append((p["name"], p.get("args"))) or {"result": 2 if p["name"] == "PLAYER_PED_ID" else None})})
    # round-robin variety
    assert pick_context("combat_start") == EVENTS["combat_start"][0]
    assert pick_context("combat_start") == EVENTS["combat_start"][1]   # rotates
    assert pick_context("nonexistent_event") is None
    # cooldown gating
    _state["last"] = -1e9; _state["cooldown"] = 4.0
    r1 = comment("took_damage", now=100.0)
    assert r1.get("context"), r1
    r2 = comment("took_damage", now=101.0)               # within cooldown -> skipped
    assert r2.get("skipped"), r2
    r3 = comment("took_damage", now=105.0)               # past cooldown -> speaks
    assert r3.get("context"), r3
    # forcing a voice issues SET_AMBIENT_VOICE_NAME(TREVOR) then the speech native
    fired.clear(); _state["last"] = -1e9
    comment("mission_passed", voice="trevor", now=200.0)
    names = [n for n, a in fired]
    assert "SET_AMBIENT_VOICE_NAME" in names and "PLAY_PED_AMBIENT_SPEECH_NATIVE" in names, names
    tv = [a for n, a in fired if n == "SET_AMBIENT_VOICE_NAME"][0]
    assert tv[1] == "TREVOR", tv
    print(f"commentary self-test PASSED — event->context (round-robin), cooldown gating, voice-force all correct ({_N} events)")
    print("  (the speech natives play the game's real protagonist lines; verify contexts in-game)")
