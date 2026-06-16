# Security & Privacy — GTAV-Claude-MCP

This document explains what GTAV-Claude-MCP does to your system, what data leaves your machine, why
antivirus/EDR may flag or block it, and how to report a problem. Read it before installing.

## What it does (and why AV/EDR reacts)
GTAV-Claude-MCP lets Claude observe and modify a **running single-player GTA V** by embedding CPython
inside `GTA5.exe` (via the third-party PyLoaderV + ScriptHookV) and exposing the game over a local
socket. To do that, the in-game bridge (`pyscript/bridge.py`):

- **Reads and writes the game's process memory** — validated with `VirtualQuery` before every dereference
  (a bad address becomes an error, not a crash that a Python `try/except` can't catch).
- **Changes memory page protection** to write, then **restores** the original protection afterward.
- **Calls game functions ("natives")** — only **by name** through a verified allowlist
  (`native_db.json`, ~6700 entries) resolved to the correct hash for your edition. A name not on the
  allowlist is refused, so a wrong hash cannot be executed.
- **Pattern-scans** memory and can **patch bytes** (with an undo registry + write-ahead log so changes
  are reversible and forensically logged).

Process injection, RWX memory, `WriteProcessMemory`-style writes, code-patching, and memory scanning are
the textbook behaviors security products associate with malware. As a result:

- **Antivirus may flag the compiled `.dll`s** (`ClaudeChatUI.dll`, `ClaudeRadio.dll`) or `yt-dlp` as a
  false positive. `bridge.py` is plain source you can read.
- **Enterprise EDR (SentinelOne, CrowdStrike, etc.) will likely block it** — these stop process injection
  by design. Use a **personal** machine, not a managed/work one. **Do not** attempt to disable or bypass
  a managed EDR; that is a policy/security violation, not part of installing this tool.

These behaviors are intentional and documented; the project is open source so you can verify them.

## Data & privacy — what leaves your machine
Be aware that this tool is an AI bridge, so some data does go off-machine depending on which optional
parts you run:

- **The MCP bridge socket is local only** — `127.0.0.1:27015`, your PC talking to your own game. Nothing
  about the bridge itself goes to the internet.
- **Claude sees your game state.** When you chat with Claude (in the Claude client, or via the headless
  `gtav_host.py`), the game context it reads — location, vehicle, weather, etc. — and your messages are
  sent to **Anthropic** through your Claude client / the Claude Agent SDK, exactly like any normal Claude
  conversation. This is how Claude can respond about your game. Your **Claude login/subscription** is used
  for this; **no API key is stored in the repo**.
- **Claude FM downloads music** you request from YouTube/SoundCloud via `yt-dlp` to your local library.
- **No analytics or telemetry** are sent by this project itself.

If you are not comfortable with game state being sent to your AI provider, do not use the chat/host parts;
the tool cannot work without sending Claude what it needs to respond.

## Safety design
- **By-name native allowlist** — a wrong/unknown native is refused, not called.
- **Validate-before-deref** on every read/write; page protection restored after writes.
- **Write-ahead log + undo** — every write is journaled to `pyscript/crash_logs/` before execution and
  can be reverted; after a crash the bridge reports the last operation so it isn't repeated.
- **Single-player only.** Never use this with **GTA Online** — it risks a ban and is not supported.
- **Riskier tools** (`write_memory`, `patch_bytes`, `call_function`) can crash the game if misused; your
  single-player saves are not modified by in-memory edits. Decline an action if you're unsure what it does.

## No secrets in the repository
This repo must never contain API keys, tokens, or machine-specific private paths. Authentication is your
local `claude /login`; machine-specific data extraction paths live in a git-ignored `gtadata_local.json`.

## Reporting a vulnerability or concern
Please open a **private GitHub Security Advisory** on this repository for sensitive issues, or a regular
issue for non-sensitive reports. Include your GTA edition (Legacy/Enhanced), build, the tool version, and
steps to reproduce. Do **not** publish exploit details before they're addressed.
