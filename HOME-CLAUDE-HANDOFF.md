# ⚠️ READ FIRST — hand-off to the HOME-machine Claude

The work-machine Claude rebuilt `bridge.py`/`server.py` and added the RE toolkit. **The integration is
already DONE and tested — you do NOT need to merge anything.** Your job: **verify, deploy, and COMMIT so
it can never be lost again.**

## What the work machine already did (2026-06-10 PM)
The work machine's local `bridge.py`/`server.py` had been **deleted by a Dropbox sync-lock**. They were
restored from the **`0eb6599` (1:40 AM) commit** — the verified latest base — and **this session's work
was re-applied onto that base** with assertion-checked edits, then tested:
- `bridge.py` (now ~131 KB): `call_function`/`find_string`/`find_xrefs`/`reload_content_changeset`, the
  **paused-safe off-thread execution** (`_OFFTHREAD_COMMANDS`/`_run_offthread`, `_is_offthread`,
  region-aware `_next_readable_chunk` scanner), and the **RE-toolkit wiring** (imports re_tools /
  re_tools_pardump / re_tools_dynamic and registers their commands). **Integration test: 20/20 pass.**
- `server.py` (now ~52 KB): 19 new `@mcp.tool()`s (the engine-call tools + the full RE toolkit).
- Both **compile clean**; `re_tools` self-test **13/13**; par-dump importer **9/9**.

**Your steps (in order):**
1. **VERIFY** the rebuilt `bridge.py`/`server.py` match what you expect. They were rebuilt from `0eb6599`
   + this session's work; if your game-folder copy had ANY change beyond `0eb6599` that isn't in
   `0eb6599`, reconcile it (the base was confirmed to include your gtadata/radio additions).
2. **DEPLOY** `bridge.py` + the `re_tools*.py` files to the legacy game folder's `pyscript\`.
   `pip install capstone` into the bridge's Python (only `disasm` needs it).
3. **COMMIT + PUSH** (see the #1 rule below).
4. In-game validation order is in `RE-TOOLKIT-INTEGRATION.md §3`.

## 🔴 THE #1 RULE (why this note exists)
The work machine's Dropbox copy of `bridge.py` + `server.py` got **deleted by a Dropbox sync-lock**, and
because that session's edits were **never committed to git**, they only survived because the **deployed
copy in the legacy game folder** still had them. Do not repeat that. After you merge:

1. **COMMIT TO GIT.** From the project root:
   ```
   git add -A
   git commit -m "Merge RE toolkit + session work (call_function, off-thread pause-safe, scanner, docs)"
   ```
   (End the commit message with the Co-Authored-By line if your harness requires it.) Push if a remote
   is set up: `git push`.
2. **KEEP IT IN DROPBOX** — the project lives in `...Dropbox\...\GTAV-Claude-MCP\`. The merged
   `bridge.py`/`server.py` must be saved **both** in this Dropbox project folder **and** deployed to the
   legacy game folder's `pyscript\` (the run location). Treat the **game-folder deploy as the source of
   truth** and the Dropbox project + git as the backups — but keep all three in sync. **Never leave new
   work uncommitted.**

## What's new in this Dropbox project (from the work-machine session)
New files (additive — nothing in your `bridge.py`/`server.py` was modified by the work machine):
- `pyscript/re_tools.py` — Tier A static-analysis tools (TESTED 13/13): `re_scan`, `func_bounds`,
  `list_functions`, `identify` (RTTI→class), `dump_vtable`, `read_chain`, `find_pointers`, `disasm`.
- `pyscript/re_tools_dynamic.py` — Tier B (UNTESTED, verify in-game): `read_global`/`write_global`,
  `enumerate_entities`, `nearby_peds`/`nearby_vehicles`.
- `pyscript/re_tools_pardump.py` — par-dump importer (TESTED): auto-labels struct offsets with real
  field names. **Highest-value in-game step.**
- `pyscript/test_re_tools.py` — self-test for Tier A.
- Docs: `RE-TOOLKIT.md` (blueprint + deps + perf), `RE-TOOLKIT-INTEGRATION.md` (exact merge steps +
  ready-to-paste MCP tools), `RPF-REFERENCE.md`, `RPF-HOTRELOAD-RUNBOOK.md`, plus new §8 in
  `DISCOVERIES.md` (pause-menu behavior).

## How to merge (details in `RE-TOOLKIT-INTEGRATION.md`)
1. Copy the new `pyscript/re_tools*.py` next to the real `bridge.py` (Dropbox project AND game folder).
2. In `bridge.py`, after `COMMANDS`/`_OFFTHREAD_COMMANDS` are defined, add the wiring block:
   ```python
   import re_tools, re_tools_dynamic as rtd, re_tools_pardump as pardump
   re_tools.bind(globals()); COMMANDS.update(re_tools.RE_COMMANDS)
   _OFFTHREAD_COMMANDS |= re_tools.RE_OFFTHREAD
   rtd.bind(globals()); COMMANDS.update(rtd.RE_DYN_COMMANDS)   # NOT off-thread
   COMMANDS.update(pardump.PAR_COMMANDS)
   ```
3. Add the MCP tool stubs from `RE-TOOLKIT-INTEGRATION.md §2` to `mcp_server/server.py`.
4. `pip install capstone` into the bridge's Python (only needed for `disasm`; everything else is ctypes).
5. Optionally route `inspect` through `pardump.label(struct, offset)` so it shows real field names.

## First in-game validation (order)
1. `re_scan` / `list_functions` / `identify` (pure read-only — should just work; `identify(<vehicle ptr>)`
   should print `CVehicle`-ish).
2. Download alexguirre par-dump JSON for THIS GTA5.exe build, run
   `python re_tools_pardump.py par_gta5_<build>.json par_index.json`, wire `load_index`.
3. `read_global`, then `enumerate_entities("vehicle")` (re-derive the pool AOB with `re_scan` if empty).
4. Build `watch_access` (hardware-breakpoint "what accesses X") WITH the game up per `RE-TOOLKIT.md §3.1`
   — do NOT ship it blind; a wrong offset crashes GTA.

## Reminder about the rest of the session's work
Besides the RE toolkit, this session also added (already in your home `bridge.py` from the 1-2 AM work,
verify present): `call_function`/`find_string`/`find_xrefs`/`reload_content_changeset`, the **paused-safe
off-thread execution** (`_OFFTHREAD_COMMANDS`/`_run_offthread`, region-aware `_next_readable_chunk`
scanner). If any of those are missing on the home copy, cross-check against `DISCOVERIES.md` and the docs.

**After merging: commit to git + keep Dropbox in sync. Don't skip that.**
