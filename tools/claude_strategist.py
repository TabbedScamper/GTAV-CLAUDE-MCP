"""
claude_strategist.py — the SLOW MIND: wires play_agent's ClaudeDecider to the Claude Agent SDK
(the same mechanism gtav_host.py uses), so Claude sets the high-level GOAL while the reflex layer
executes moment-to-moment.

It owns a persistent ClaudeSDKClient on a background asyncio loop, and exposes a SYNCHRONOUS
`decide_goal(summary, events, goal, verbs) -> goal_dict` that play_agent calls on events/cooldown.
Claude gets READ-ONLY sensing tools (it can look closer) but NOT the action tools — acting stays solely
in the loop's Executor, so the strategist can only steer, never fight the reflex layer.

HONESTY: `build_prompt` and `parse_goal` are pure + self-tested (`python claude_strategist.py`). The SDK
runner reuses the exact ClaudeSDKClient/query/receive_response calls from gtav_host.py; it needs the
Agent SDK installed + `claude /login` (home machine). Import is guarded so the pure parts test without it.
"""
import asyncio, json, os, re, sys, threading

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

STRATEGIST_PROMPT = (
    "You are the STRATEGIST (Slow Mind) for an autonomous agent playing GTA V single-player free-roam. "
    "A fast reflex layer already handles moment-to-moment safety and combat (it flees when hurt, fights "
    "threats, drives toward the goal). Your ONLY job is to choose the next high-level GOAL given the live "
    "situation. Respond with NOTHING but a single JSON object, one of:\n"
    '  {"type":"goto","x":<f>,"y":<f>,"z":<f>,"note":"why"}   - head to a place (use resolve()/describe_location for coords)\n'
    '  {"type":"freeplay","note":"why"}                        - no fixed destination; cruise/wander and react\n'
    "Pick goals that make for fun, sensible open-world play (visit a landmark, cruise the coast, escape a "
    "chase by heading out of the city). Do NOT call action tools - the reflex layer acts; you only steer. "
    "Single-player only. Reply with the JSON object only, no prose."
)

# Read-only sensing tools the strategist may use to look closer (NOT the action verbs).
STRATEGIST_TOOLS = [
    "mcp__gtav__get_world_state", "mcp__gtav__world_events", "mcp__gtav__describe_location",
    "mcp__gtav__resolve", "mcp__gtav__catalog_search", "mcp__gtav__recipe_search",
]

# ----------------------------------------------------------------- pure logic (self-tested)
def build_prompt(summary, events, goal, verbs):
    ev = ", ".join(e.get("event", "?") for e in (events or [])) or "none"
    g = json.dumps(goal) if goal else "none"
    vs = ", ".join(verbs.keys()) if isinstance(verbs, dict) else ", ".join(verbs or [])
    return (f"Live situation: {summary}\n"
            f"Recent events: {ev}\n"
            f"Current goal: {g}\n"
            f"Reflex verbs available to the executor: {vs}\n\n"
            "Choose the next GOAL. Reply with the JSON object only.")

def parse_goal(reply_text, fallback=None):
    """Extract a goal JSON object from Claude's reply; fall back on anything unparseable/invalid."""
    if not reply_text:
        return fallback
    m = re.search(r"\{.*\}", reply_text, re.DOTALL)   # first {...} (tolerates ```json fences/prose)
    if not m:
        return fallback
    try:
        g = json.loads(m.group(0))
    except ValueError:
        return fallback
    t = g.get("type")
    if t == "goto" and all(k in g for k in ("x", "y", "z")):
        try:
            return {"type": "goto", "x": float(g["x"]), "y": float(g["y"]), "z": float(g["z"]),
                    "note": str(g.get("note", ""))[:200]}
        except (TypeError, ValueError):
            return fallback
    if t == "freeplay":
        return {"type": "freeplay", "note": str(g.get("note", ""))[:200]}
    return fallback

# ----------------------------------------------------------------- the SDK runner (needs Agent SDK)
try:
    from claude_agent_sdk import (ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, ResultMessage,
                                  SystemMessage)
    SDK_OK, SDK_ERR = True, None
except Exception as e:
    SDK_OK, SDK_ERR = False, str(e)


def _build_options():
    # Mirrors gtav_host._build_options but with the strategist prompt + read-only tools.
    return ClaudeAgentOptions(
        cwd=PROJ,
        system_prompt=STRATEGIST_PROMPT,
        setting_sources=["project"],
        permission_mode="bypassPermissions",
        allowed_tools=STRATEGIST_TOOLS,
        mcp_servers={"gtav": {"command": sys.executable, "args": ["-m", "mcp_server.server"]}},
    )


def _extract_text(message):
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return "".join(getattr(b, "text", "") or "" for b in (content or []))


class ClaudeStrategist:
    """Persistent Claude session on a background loop; sync decide_goal() for play_agent."""
    def __init__(self, timeout=45.0):
        self.timeout = timeout
        self._loop = None
        self._client = None
        self._thread = None
        self._ready = threading.Event()
        self._err = None

    def start(self):
        if not SDK_OK:
            self._err = f"Claude Agent SDK not installed: {SDK_ERR}"
            return self
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30.0)
        return self

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._open())
        self._ready.set()
        self._loop.run_forever()

    async def _open(self):
        self._client = ClaudeSDKClient(options=_build_options())
        await self._client.__aenter__()

    async def _ask(self, prompt):
        await self._client.query(prompt)
        out = ""
        async for message in self._client.receive_response():
            if SystemMessage and isinstance(message, SystemMessage):
                continue
            if AssistantMessage and isinstance(message, AssistantMessage):
                out += _extract_text(message)
            elif ResultMessage and isinstance(message, ResultMessage):
                break
        return out

    def decide_goal(self, summary, events, goal, verbs):
        """Synchronous entry point for ClaudeDecider.call. Returns a goal dict (falls back to `goal`)."""
        if not SDK_OK or self._loop is None:
            return goal
        prompt = build_prompt(summary, events, goal, verbs)
        try:
            fut = asyncio.run_coroutine_threadsafe(self._ask(prompt), self._loop)
            reply = fut.result(timeout=self.timeout)
            return parse_goal(reply, fallback=goal)
        except Exception:
            return goal   # never let a strategist hiccup stall the loop; reflex keeps playing


def make_decider():
    """Convenience: a started strategist's decide_goal, ready to pass as ClaudeDecider(call=...)."""
    return ClaudeStrategist().start().decide_goal


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    # build_prompt includes the situational bits
    p = build_prompt("on foot, 90hp, ~40m N of Cinema", [{"event": "took_damage"}],
                     {"type": "freeplay"}, {"walk_to": "", "drive_to": ""})
    assert "90hp" in p and "took_damage" in p and "walk_to" in p, p
    # parse_goal: clean, fenced, prose-wrapped, invalid, missing-coords
    assert parse_goal('{"type":"goto","x":1,"y":2,"z":3,"note":"bank"}')["x"] == 1.0
    assert parse_goal('```json\n{"type":"freeplay","note":"cruise"}\n```')["type"] == "freeplay"
    assert parse_goal('Sure! {"type":"goto","x":-75,"y":-818,"z":44}')["y"] == -818.0
    assert parse_goal("no json here", fallback={"type": "freeplay"})["type"] == "freeplay"
    assert parse_goal('{"type":"goto","x":1}', fallback={"type": "freeplay"})["type"] == "freeplay"  # missing y,z
    assert parse_goal('{"type":"bogus"}', fallback=None) is None
    print("claude_strategist self-test PASSED — prompt build + goal parsing (clean/fenced/prose/invalid) correct")
    print(f"  SDK available: {SDK_OK}" + ("" if SDK_OK else f" ({SDK_ERR}) - install on the home machine for the live Slow Mind"))
