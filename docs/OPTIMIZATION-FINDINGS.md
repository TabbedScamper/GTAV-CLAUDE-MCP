# Optimization findings (pre-release) — both projects

From a deep read of the actual source against best practices + the verified reference patterns
(SHVDN, VStancer, ikt). Verdict: **both ship as-is with a handful of small, low-risk fixes.** No risky
rewrites before the weekend. Priorities: **P1 = do before release**, P2 = nice, P3 = optional polish.

## GTAV-CLAUDE-MCP — `bridge.py` (mostly clean — "ship it")
- **[P1, low-risk] Page-protection leak** — `handle_write_visual_wheel` (≈L1452-1456) flips a page RW via
  `VirtualProtect` and never restores it; it's also redundant (the very next `write_float`/`_write_raw`
  already validates + restores). **Delete the manual flip.**
- **[P1, low-risk] ctypes argtypes** — set `kernel32.GetModuleHandleW/VirtualQuery/VirtualProtect/
  GetModuleFileNameW` restype/argtypes **once at module load** (next to `_RtlMoveMemory`), not per-call —
  avoids the Win64 pointer-truncation hazard the code already documents.
- **[P2, low-risk] Dead state** — remove `_keyboard_shown`/`_keyboard_active`, `CHAT_INPUT_FILE`,
  `_last_chat_file_mtime`, `_user_input_result` (write-only/vestigial), and `"diff"` from
  `_OFFTHREAD_COMMANDS` (no handler).
- **[P3] Quick wins** — route the repeated `int(addr,16)`-style parsing through the existing `_resolve_addr`;
  hoist `import math` out of the spawn poll; `except Exception:` instead of bare `except:`.
- The memory primitives are genuinely careful (validate-before-deref, restore protection, paused-safe split,
  native allowlist). **No structural risk.**

## GTAV-CLAUDE-MCP — `server.py` (good — surgical)
- **[P1, low-risk] Duplicate tool bug** — `par_struct` is defined twice (≈L634 and ≈L762); FastMCP may warn/
  clobber. **Delete the second copy.**
- **[P1, low-risk] Experimental tools** — the held agent/mission/commentary block is one contiguous slice
  (≈L1404-1500: get_world_state, world_events, describe_location, nearest_road_node, raycast, act,
  list_verbs, get_intent, get_objective, get_objectives, is_on_mission, comment, say). **Cut as a unit.**
- Keep `anim_search`/`anim_get` (catalog reads — part of the shipping catalogs).
- **`_send_command` framing/timeout is sound — ship unchanged** (the timeout-extends-on-declared-duration
  heuristic is a genuinely good touch).

## ExtendedLSC (shippable on its memory path — two trivial fixes)
- **[P1, trivial, highest-value] Debug logging is ON by default — turn it OFF.** Both `ExtendedLSC.ini`
  `[Debug] EnableLogging=true` AND the code default `ModSettings.DebugLogging = true`. Flip both to `false`
  (continuous disk I/O + growing logfile on every user's machine otherwise).
- **[P1, low-risk-to-ship / HIGH-risk-to-change] Wheel fitment uses MEMORY (hardcoded b3788 offsets), not
  the update-proof native path; `WheelFitmentNative.cs` is dead.** The `WheelMemory.IsValid()` VirtualQuery
  guard makes it **crash-safe** (a future patch makes fitment silently no-op, not crash). **Ship on memory;
  file the native migration (`SET_VEHICLE_WHEEL_X_OFFSET`/`_Y_ROTATION`, L/R parity) as the #1 post-release
  hardening item — do NOT attempt the swap this weekend.**
- **[P3, low-risk] Dead files (~2,600 lines)** — `WheelFitmentNative.cs`, `PatternScanner.cs`, `WheelBones.cs`,
  `TransmissionHUD.cs` are all unreferenced (only `.Log` setters). Exclude from compile (`<Compile Remove>`)
  or delete; stop the `.Log` assignment + `transmissionHUD` construction in Main.cs first.
- **[P2, low-risk] Dev harnesses compiled in** — `ELSCTransmission.HandleAutorun` (autorun.txt runway tester)
  + `DragHUD.DrawPreviewIfRequested` (ui_preview.txt) run each tick (inert without the trigger files). Optional:
  `#if DEBUG`-gate the trigger checks. Also `Main.cs` ~L7627/7682/7709 write `ExtendedLSC_debug.log` bypassing
  the DebugLogging gate — route through `Log()` or confirm unreachable.
- **`.csproj` deps are correctly pinned** (SHVDN3 3.6.0, LemonUI 2.1.1, Newtonsoft 13.0.3); delete the
  misleading "works on Enhanced" comment (it's the Legacy package, and the offsets are Legacy-b3788).

## The pre-release fix list (in order)
1. ExtendedLSC: logging off (ini + ModSettings default). ← do first, biggest UX win
2. server.py: delete duplicate `par_struct`.
3. bridge.py: delete the page-protection flip + hoist ctypes argtypes + drop dead state.
4. Lean restructure: move the held experimental modules to `experimental/`; strip the server experimental
   tools block (1404-1500) + bridge experimental import blocks.
5. ExtendedLSC: exclude the 4 dead files; slim the package (drop the 330 MB of backups/integrated_mods).
6. Install + usage docs for both.
