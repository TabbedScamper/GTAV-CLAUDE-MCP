"""
gtav_host.py - Headless Claude host for the in-game GTA V chat.

This replaces the old "interactive claude in a terminal + ConsoleTrigger mirror + 3rd-party
paste tool" chain with ONE always-on process:

    in-game F10  ->  bridge queue  ->  [host pulls]  ->  Claude (Agent SDK)  ->  reply
                                                                  |
                            transcript written to GTAV_Claude_Console shared memory
                                                                  v
                                                   C# ClaudeChatUI panel renders it

Why this is seamless:
  - The Claude Agent SDK reuses your existing `claude /login` (Claude subscription) - NO api key,
    NO terminal window, NO focus stealing, NO paste hack.
  - The host owns a persistent session (context persists across messages).
  - Claude gets ALL the in-game tools via the existing MCP server (mcp_server.server).
  - The host writes the conversation into the same shared-memory buffer the LemonUI panel
    already reads, so it DOUBLES AS the terminal mirror -> ConsoleTrigger.exe is retired.

Run:  python gtav_host.py        (or via run_host.bat, minimized)
Stop: Ctrl+C
Requires:  pip install -r requirements.txt   and   `claude /login` done once.
"""
import asyncio
import json
import mmap
import os
import socket
import struct
import subprocess
import sys
import time
import unicodedata

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 27015
SHARED_MEM_NAME = "GTAV_Claude_Console"   # the C# panel reads this (was ConsoleTrigger's)
SHM_SIZE = 64 * 1024
SHM_MAX_CONTENT = 60000                   # C# caps reads at 60000 bytes
# chat_history.txt: the C# panel logs your F10 inputs here ("ts|You|text"); the host appends
# its replies ("ts|Claude|text") so the file is a full two-way transcript readable off-process.
CHAT_HISTORY_FILE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "GTAV-Claude-MCP", "chat_history.txt")
# Event-driven input via long-poll: the bridge holds the request open and returns the instant
# you press Enter in-game (it checks the queue every frame). Re-issued every AWAIT_TIMEOUT_MS.
AWAIT_TIMEOUT_MS = 25000                   # how long each long-poll waits before re-issuing
AWAIT_SOCKET_TIMEOUT = 35.0               # host socket timeout (must exceed AWAIT_TIMEOUT_MS/1000)
MAX_TRANSCRIPT_LINES = 400

SYSTEM_PROMPT = (
    "You are Claude, embedded as an in-game assistant inside GTA V (single-player). "
    "The user talks to you from inside the game via an on-screen chat. "
    "Use the gtav memory-bridge tools to see and safely act in the game: call natives by name "
    "(call_native), spawn vehicles, set weather/time, give weapons, read/write wheel values, etc. "
    "ALWAYS prefer calling a native BY NAME (it is hash-verified and crash-safe) and never guess raw "
    "hashes. Keep replies SHORT and conversational (1-3 sentences) - they render in a small in-game "
    "panel. Use plain ASCII text only: NO emoji, checkmarks, bullets or special symbols, and use "
    "straight quotes (' and \") and a hyphen (-) instead of dashes - the in-game font cannot draw "
    "anything else and shows it as blank boxes. When you perform an action, confirm it briefly. "
    "GROUND GAME-DATA QUESTIONS WITH TOOLS - DO NOT ANSWER FROM MEMORY: for anything about how the "
    "game is DEFINED or what is IN it (radio stations/tracklists/song names, vehicle handling, mod "
    "kits/carvariations, what files or values exist), you MUST verify with the gtadata_* tools "
    "(gtadata_find -> gtadata_decode -> gtadata_read -> gtadata_resolve/gtadata_crack) before "
    "answering. Your training memory of GTA internals is frequently WRONG - read the actual extracted "
    "game files instead. Resolve the friendly name to its internal id first (e.g. FlyLo FM = "
    "RADIO_14_DANCE_02, Rebel Radio = RADIO_06_COUNTRY). If the tools cannot confirm it, say you could "
    "not verify it rather than guessing from memory. "
    "Single-player only; never touch GTA Online. "
    "Each user message is prefixed with a [Live game state: ...] line describing where the player is, "
    "their vehicle, time, weather, wanted level and health - use it to respond in context. Call the "
    "get_context tool if you need a fresh/fuller snapshot mid-task."
)

# ----------------------------------------------------------------------------
# ASCII folding for the in-game panel
# ----------------------------------------------------------------------------
# GTA V's text font (what the LemonUI panel draws with) only renders a limited
# character set (~ASCII + Latin-1). Anything outside it draws as a "tofu" box.
# Claude's replies routinely include smart quotes, em-dashes, ellipses, bullets,
# arrows, checkmarks and emoji -> all boxes. Fold everything to safe ASCII before
# it reaches shared memory so the panel renders clean text.
_TRANSLATE = {
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'",        # single quotes -> '
    0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"',        # double quotes -> "
    0x2013: "-", 0x2014: "-", 0x2015: "-", 0x2212: "-",        # en/em dash, minus -> -
    0x2026: "...",                                             # ellipsis -> ...
    0x2022: "-", 0x00B7: "-", 0x25CF: "-", 0x25AA: "-", 0x25E6: "-",  # bullets -> -
    0x2192: "->", 0x2190: "<-", 0x21D2: "=>", 0x21D0: "<=",    # arrows
    0x00A0: " ", 0x202F: " ", 0x2009: " ",                     # non-breaking / thin spaces
    0x2713: "v", 0x2714: "v", 0x2705: "[ok]", 0x2611: "[x]",   # check marks
    0x274C: "X", 0x2717: "x", 0x2718: "x",                     # cross marks
    0x2122: "(tm)", 0x00AE: "(r)", 0x00A9: "(c)",              # tm / (r) / (c)
    0x00B0: "deg", 0x00BD: "1/2", 0x00BC: "1/4", 0x00BE: "3/4",
}

def ascii_fold(text: str) -> str:
    """Map non-ASCII to a renderable ASCII equivalent (accented letters -> base
    letter via NFKD; unmapped symbols/emoji are dropped). Newlines are preserved."""
    out = []
    for ch in text:
        o = ord(ch)
        if o < 128:
            out.append(ch)
        elif o in _TRANSLATE:
            out.append(_TRANSLATE[o])
        else:
            # é -> e, ñ -> n, etc.; emoji and other symbols decompose to "" (dropped)
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii"))
    return "".join(out)


# ----------------------------------------------------------------------------
# Shared-memory transcript (what the in-game panel renders)
# ----------------------------------------------------------------------------
class Transcript:
    def __init__(self):
        self._lines = []
        try:
            self._mm = mmap.mmap(-1, SHM_SIZE, tagname=SHARED_MEM_NAME)
        except Exception as e:
            self._mm = None
            print(f"[host] WARNING: could not open shared memory '{SHARED_MEM_NAME}': {e}")
        self.add("Claude in-game host started. Press F10 in GTA to chat.")

    def add(self, line: str):
        for ln in str(line).split("\n"):
            self._lines.append(ln)
        if len(self._lines) > MAX_TRANSCRIPT_LINES:
            self._lines = self._lines[-MAX_TRANSCRIPT_LINES:]
        self._flush()

    def replace_last(self, line: str):
        """Replace the last line (for live-streaming Claude's reply)."""
        if self._lines:
            self._lines[-1] = str(line)
        else:
            self._lines.append(str(line))
        self._flush()

    def _flush(self):
        if not self._mm:
            return
        content = ascii_fold("\n".join(self._lines)).encode("utf-8")
        if len(content) > SHM_MAX_CONTENT:
            content = content[-SHM_MAX_CONTENT:]
        try:
            self._mm.seek(0)
            self._mm.write(struct.pack("<i", len(content)))
            self._mm.write(content)
        except Exception as e:
            print(f"[host] shm write failed: {e}")

# ----------------------------------------------------------------------------
# Bridge client (same wire protocol as mcp_server: 4-byte LE length + JSON)
# ----------------------------------------------------------------------------
def bridge_send(command: str, params: dict | None = None, timeout: float = 5.0) -> dict:
    params = params or {}
    try:
        with socket.create_connection((BRIDGE_HOST, BRIDGE_PORT), timeout=timeout) as s:
            payload = json.dumps({"command": command, "params": params}).encode("utf-8")
            s.sendall(struct.pack("<I", len(payload)) + payload)
            head = b""
            while len(head) < 4:
                chunk = s.recv(4 - len(head))
                if not chunk:
                    return {"error": "bridge closed"}
                head += chunk
            size = struct.unpack("<I", head)[0]
            body = b""
            while len(body) < size:
                chunk = s.recv(min(4096, size - len(body)))
                if not chunk:
                    break
                body += chunk
            return json.loads(body.decode("utf-8"))
    except (ConnectionRefusedError, socket.timeout):
        return {"error": "bridge not running"}
    except Exception as e:
        return {"error": f"bridge error: {e}"}

def pull_user_messages() -> list[str]:
    """Drain queued in-game F10 messages from the bridge."""
    r = bridge_send("get_pending_messages")
    out = []
    for m in r.get("messages", []) or []:
        text = m.get("message") or m.get("text")
        if text:
            out.append(text)
    return out

def notify(text: str):
    """Optional yellow in-game toast (the panel shows the transcript regardless)."""
    bridge_send("chat_post", {"message": "~y~" + text[:80]})

def log_reply(text: str):
    """Append Claude's reply to chat_history.txt as 'ts|Claude|text', matching the C# panel's
    'ts|You|text' input lines, so the file holds the full two-way conversation. Newlines are
    flattened to keep one entry per line (the file is line-delimited)."""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = ts + "|Claude|" + " ".join(text.split()) + "\n"
        with open(CHAT_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[host] chat-history log skipped: {e}")

def gta_is_running() -> bool:
    """True if the GTA process is alive. Used to tell a PAUSE (bridge unresponsive but game running)
    from a real crash/close (process gone) - so we never report a benign pause as a crash."""
    try:
        out = subprocess.run(["tasklist", "/NH"], capture_output=True, text=True, timeout=5).stdout.lower()
        return "gta5.exe" in out or "gta5_enhanced.exe" in out
    except Exception:
        return True  # if we can't tell, assume running - never false-alarm a crash


def read_last_op_from_disk() -> str | None:
    """Read the bridge's write-ahead log directly from disk (survives a GTA crash since the
    bridge fsyncs it before each op). Lets the host report the last op even when GTA is dead."""
    wal = os.path.join(HERE, "pyscript", "crash_logs", "last_op.jsonl")
    try:
        if not os.path.exists(wal):
            return None
        with open(wal, "r", encoding="utf-8-sig") as f:  # tolerate a BOM
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            return None
        op = json.loads(lines[-1])
        return f"last op before crash: {op.get('op')} {op.get('address') or op.get('name') or ''} " \
               f"= {op.get('value', op.get('args', ''))} (seq {op.get('seq')}, {op.get('timestamp','')})"
    except Exception:
        return None

# ----------------------------------------------------------------------------
# Claude Agent SDK
# ----------------------------------------------------------------------------
try:
    from claude_agent_sdk import (
        ClaudeSDKClient, ClaudeAgentOptions,
        AssistantMessage, ResultMessage, SystemMessage,
    )
    SDK_OK = True
    SDK_ERR = None
except Exception as e:  # not installed yet
    SDK_OK = False
    SDK_ERR = str(e)

def _build_options():
    return ClaudeAgentOptions(
        cwd=HERE,
        system_prompt=SYSTEM_PROMPT,
        setting_sources=["project"],          # loads this project's CLAUDE.md
        permission_mode="bypassPermissions",  # headless: no tool-approval prompts
        allowed_tools=["mcp__gtav__*"],        # all game-bridge tools
        mcp_servers={
            "gtav": {
                "command": sys.executable,
                "args": ["-m", "mcp_server.server"],
            }
        },
    )

def _extract_text(message) -> str:
    """Pull display text out of an AssistantMessage across SDK content shapes."""
    parts = []
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    for block in content or []:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
    return "".join(parts)

# Keyword groups that decide which detail blocks get attached to a message.
_KW_VEHICLE = ("drive", "car", "vehicle", "race", "speed", "fast", "engine", "fix", "repair",
               "fuel", "crash", "wheel", "tyre", "tire", "ride", "bike", "boat", "plane", "heli")
_KW_COMBAT = ("shoot", "gun", "weapon", "kill", "fight", "ammo", "reload", "aim", "combat",
              "enemy", "attack", "armour", "armor")
_KW_WANTED = ("cop", "police", "wanted", "star", "escape", "lose", "heat", "busted", "arrest")
_KW_WHERE = ("where", "location", "area", "here", "place", "map", "coord", "position", "nearby")

# Activity flags worth surfacing even when unprompted (high-signal situations).
_NOTABLE_ACTIVITY = {"in_air", "swimming", "underwater", "ragdoll", "in_cover", "falling",
                     "climbing", "diving", "parachuting"}

def _format_context(ctx: dict, msg: str = "") -> str:
    """Lean core + message-relevant expansion. Full snapshot is pulled (cheap) but only the
    parts that matter for THIS message are injected, so history stays small and fresh."""
    if not ctx:
        return ""
    m = (msg or "").lower()
    has = lambda words: any(w in m for w in words)
    p = ctx.get("player") or {}
    veh = ctx.get("vehicle")
    parts = []

    # --- always-on core ---
    if veh:
        s = "in a " + veh.get("model", "vehicle")
        if "speed_mph" in veh:
            s += f" ({veh['speed_mph']:.0f} mph)"
        parts.append(s)
    elif "vehicle" in ctx:
        parts.append("on foot")
    if ctx.get("zone"):
        parts.append("in " + ctx["zone"])
    if ctx.get("time"):
        parts.append(ctx["time"])
    if ctx.get("weather"):
        parts.append(ctx["weather"])
    if "wanted_level" in p:
        parts.append(f"wanted {p['wanted_level']}*")
    if "health" in p:
        parts.append("hp " + str(p["health"]) + (f"/{p['max_health']}" if "max_health" in p else ""))

    # --- message-relevant expansion ---
    if veh and has(_KW_VEHICLE):
        if veh.get("class"):
            parts.append(veh["class"])
        if "engine_health" in veh:
            parts.append(f"engine {veh['engine_health']}")
        if "body_health" in veh:
            parts.append(f"body {veh['body_health']}")
        if veh.get("plate"):
            parts.append(f"plate {veh['plate']}")
    if has(_KW_COMBAT):
        w = ctx.get("weapon") or {}
        if w.get("name"):
            parts.append("weapon " + w["name"] + (f" x{w['ammo']}" if "ammo" in w else ""))
        if "armour" in p:
            parts.append(f"armour {p['armour']}")
    if has(_KW_WANTED) and ctx.get("game_state"):
        parts.append("[" + ", ".join(ctx["game_state"]) + "]")
    if has(_KW_WHERE) and p.get("position"):
        pos = p["position"]
        parts.append(f"@({pos['x']:.0f},{pos['y']:.0f},{pos['z']:.0f})")

    # --- notable activity (always) + full activity when fighting ---
    activity = ctx.get("activity") or []
    shown = activity if has(_KW_COMBAT) else [a for a in activity if a in _NOTABLE_ACTIVITY]
    if shown:
        parts.append("(" + ", ".join(shown) + ")")

    return ", ".join(parts)

async def handle_message(client, user_text: str, transcript: Transcript):
    transcript.add(f"> {user_text}")
    transcript.add("Claude: ...")
    # Auto-feed live game state so Claude is situationally aware (best-effort, non-fatal).
    # Full pull (cheap - one round-trip), then trim to what's relevant to THIS message.
    prompt = user_text
    try:
        r = await asyncio.to_thread(bridge_send, "get_context", {"detail": "full"})
        ctx_line = _format_context(r.get("context") or {}, user_text) if r.get("success") else ""
        if ctx_line:
            prompt = f"[Live game state: {ctx_line}]\n\n{user_text}"
    except Exception:
        pass
    reply = ""
    try:
        await client.query(prompt)
        async for message in client.receive_response():
            if SystemMessage and isinstance(message, SystemMessage):
                continue
            if AssistantMessage and isinstance(message, AssistantMessage):
                txt = _extract_text(message)
                if txt:
                    reply += txt
                    transcript.replace_last("Claude: " + reply)
            elif ResultMessage and isinstance(message, ResultMessage):
                break
    except Exception as e:
        transcript.replace_last(f"Claude: [error: {e}]")
        print(f"[host] query error: {e}")
        return
    if reply.strip():
        transcript.replace_last("Claude: " + reply.strip())
        notify(reply.strip())
        log_reply(reply.strip())
    else:
        transcript.replace_last("Claude: (done)")

def _self_register():
    """Write this host's launcher path where the in-game C# UI can find it, so after the
    first manual run the DLL can auto-launch us (no hardcoded per-machine path)."""
    try:
        cfg_dir = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "GTAV-Claude-MCP")
        os.makedirs(cfg_dir, exist_ok=True)
        bat = os.path.join(HERE, "run_host.bat")
        with open(os.path.join(cfg_dir, "host_path.txt"), "w", encoding="utf-8") as f:
            f.write(bat)
    except Exception as e:
        print(f"[host] self-register skipped: {e}")

_SINGLE_INSTANCE_PORT = 27099
_instance_lock = None

def acquire_single_instance() -> bool:
    """Cross-process single-instance lock: bind a localhost port. If another host already holds it,
    return False so we exit. Prevents the C# UI's Insert auto-launch from spawning DUPLICATE hosts that
    would fight over the in-game message queue (which manifests as Claude 'stuck' in the panel)."""
    global _instance_lock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))  # no SO_REUSEADDR - we WANT bind to fail if taken
        s.listen(1)
        _instance_lock = s  # keep alive for the process lifetime
        return True
    except OSError:
        s.close()
        return False


async def main():
    _self_register()
    transcript = Transcript()

    if not SDK_OK:
        msg = (f"Claude Agent SDK not installed ({SDK_ERR}). Run: pip install -r requirements.txt")
        transcript.add(msg)
        print("[host] " + msg)
        return

    print(f"[host] starting (cwd={HERE}); waiting for in-game messages...")
    # Warn (non-fatal) if the bridge isn't up yet.
    st = bridge_send("status")
    if st.get("error"):
        transcript.add(f"(bridge: {st['error']} - start GTA V + load bridge.py with F9)")

    options = _build_options()
    while True:  # outer loop: recover the session if the SDK subprocess dies
        try:
            async with ClaudeSDKClient(options=options) as client:
                transcript.add("Connected to Claude. Ready.")
                bridge_up = True
                while True:
                    # LONG-POLL (event-driven): the bridge's await_user_message blocks (checking the
                    # queue every game frame ~16ms) and returns the instant you press Enter in-game.
                    # It returns a timeout error after AWAIT_TIMEOUT_MS if you didn't type - we just
                    # re-issue (that also serves as the GTA liveness heartbeat).
                    r = await asyncio.to_thread(
                        bridge_send, "await_user_message",
                        {"timeout_ms": AWAIT_TIMEOUT_MS}, AWAIT_SOCKET_TIMEOUT)
                    err = str(r.get("error", "")).lower()

                    if r.get("success") and r.get("message"):
                        if not bridge_up:
                            bridge_up = True
                            transcript.add("(GTA bridge back online.)")
                        await handle_message(client, r["message"], transcript)
                    elif "timeout" in err or "deferred" in err:
                        # No message in the window - bridge is alive, just idle. Loop instantly.
                        if not bridge_up:
                            bridge_up = True
                            transcript.add("(GTA bridge back online.)")
                    else:
                        # Bridge unreachable. This happens on a real crash/close BUT ALSO on a benign
                        # PAUSE (the bridge freezes with the game in any menu) or a loading screen.
                        # Only call it a crash if GTA5.exe is actually GONE - otherwise it's just paused,
                        # and we must NOT read the write-ahead log or blame the last native.
                        if bridge_up:
                            bridge_up = False
                            if await asyncio.to_thread(gta_is_running):
                                transcript.add("(GTA bridge paused - game still running, e.g. pause menu. "
                                               "Not a crash.)")
                            else:
                                note = read_last_op_from_disk()
                                transcript.add("(GTA bridge offline - game closed or crashed."
                                               + (f" {note})" if note else ")"))
                        await asyncio.sleep(2.0)  # back off before retrying
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("[host] shutting down.")
            return
        except Exception as e:
            transcript.add(f"(host session error: {e} - reconnecting in 5s)")
            print(f"[host] session error: {e}; reconnecting in 5s")
            await asyncio.sleep(5.0)

if __name__ == "__main__":
    if not acquire_single_instance():
        print("[host] another Claude host is already running - exiting (single instance).")
        sys.exit(0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
