# Release Roadmap — GTAV-Claude-MCP + ExtendedLSC (3-day window)

> Internal planning doc (not part of the public release). Synthesizes the deep-dive of both projects:
> what they are, what's optimized, and what's missing to be "the best free modding companion."
> Companion docs: `RELEASE-AUDIT.md` (subsystem classification), `OPTIMIZATION-FINDINGS.md` (raw findings).

## TL;DR
Both products are **close to shippable**. The gap is **not code quality** — it's onboarding ergonomics
(MCP) and two unbuilt platform features (ExtendedLSC). Verdict from the deep-dive:
- **GTAV-Claude-MCP**: safety engineering is genuinely strong (by-name native allowlist, validate-before-
  deref, WAL, undo). The #1 launch risk is the **install chain** + docs. Mostly fixable with docs + a
  setup/doctor script.
- **ExtendedLSC**: Pillars 2 & 3 (data-driven categories, extra-buys) are real. **Pillar 1 (unlock LSC
  restrictions) is absent** and **Pillar 4 (modder-extensibility) is half-built** — custom JSON categories
  render but their items do nothing (no apply/purchase dispatch). That's the headline gap for the "platform."

## ✅ Applied today (safe, pre-verified — already in the tree)
| Project | Change | File |
|---|---|---|
| MCP | Deleted page-protection leak (left page RWX); `write_float`→`_write_raw` already flips+restores | `pyscript/bridge.py` ~1449 |
| MCP | `TCP_NODELAY` on both socket ends — kills ~40ms Nagle delay per RPC | `bridge.py` ~3246, `mcp_server/server.py` ~89 |
| MCP | README venv step now matches INSTALL/SETUP (venv **outside** project) — resolved the contradiction | `README.md` |
| MCP | New front-door docs | `INSTALL.md`, `USAGE.md` |
| ELSC | Guarded `UpdateManualTransmission()` + `stanceManager.Update()` in OnTick — an exception there used to **unload the whole script mid-session** | `src/Main.cs` ~6455 |
| ELSC | New front-door docs | `INSTALL.md`, `USAGE.md` |
| ELSC | **Build verified: 0 errors** (`dotnet build -c Release`) — fresh DLL ready to test | — |

## ⚠️ NOT applied (and why) — corrections to the raw findings
- **ctypes argtypes hoist (MCP):** the finding said set argtypes on `ctypes.windll.kernel32` — but that's a
  *shared* handle; doing so pollutes every caller (the project's own `re-safety` rule forbids it). The
  correct fix is a **private `ctypes.WinDLL('kernel32')` instance** with argtypes/restype set once, then
  use it for pointer-returning calls (`GetModuleHandleW` is the real Win64-truncation risk). Careful change
  — do it *after* live testing, not before.
- **"Dead state" removal (MCP):** `_user_input_result` is **not** dead — it's used at bridge.py 1559/1574/
  3386/3427-3440. Only `CHAT_INPUT_FILE`/`_last_chat_file_mtime` are truly unused; trivial, near-zero
  benefit — skipped to avoid any risk before testing.

---

## How they work (clarity)

### GTAV-Claude-MCP
`Claude client → MCP server (mcp_server/server.py) → socket 127.0.0.1:27015 → bridge.py (in GTA, F9) → game`.
The bridge embeds CPython in GTA5.exe (PyLoaderV). Pure-memory commands run on the socket thread (work
while paused); native calls marshal to the game thread. Natives are called **by name** through a verified
allowlist (`native_db.json`, ~6700) so a wrong hash is refused, not executed. Every write validates the
address, flips page protection RW, writes, restores protection, and journals to a WAL for crash forensics +
undo. `deploy.ps1` copies runtime files into the GTA install. The `.claude/` skills (`gta-genius`,
`re-safety`) + commands (`/make`, `/endless`, `/harvest`, `/crash-audit`) ship with the repo.

### ExtendedLSC
SHVDN3 `Main : Script` (partial, ~8300 lines). Menu via LemonUI. Two menu-build paths:
**(1) hardcoded** standard LSC categories, each gated on `GET_NUM_VEHICLE_MODS(idx) > 0`; **(2) data-driven**
— folders under `<GTA>\ExtendedLSC\Vehicles\Default\` each with an `items.json` (`MenuConfig.cs`) rendered
by `BuildCategoryMenu`. Ownership/purchases save to `ExtendedLSC\SaveData.json` (`VehicleSaveData.cs`).
Fitment/stance writes CWheel/StreamRenderGfx fields by **hardcoded b3788 offsets**, every deref
`VirtualQuery`-guarded (bad ptr → no-op, not a crash). Manual transmission finds gear/RPM/clutch offsets by
**pattern scan** (version-resilient) and NOPs the auto-shift sites. Drag HUD loads PNGs from
`<GTA>\ExtendedLSC\hud\`.

---

## The two ExtendedLSC headline gaps (the "platform" promise)

### Gap A — Pillar 4: make modder JSON items actually *do* something
**Today:** `BuildCategoryMenu`'s item `Activated` handler only calls `Log(...)`. The functional path
(`CreateMenuFromConfig` — TryPurchase + apply + ownership) is hand-wired to exactly 4 built-in categories
via hardcoded C# delegates. So a third-party `items.json` category appears with prices but selecting an item
is inert. The save layer (`SetCustomItemOwned`, arbitrary `categoryPath`) is **already generic** — only the
apply/dispatch is missing.

**Fix (design):** give each `MenuItem` a `Type` (the schema already has `Value`, `R/G/B`). Add ONE generic
dispatcher that routes on `Type` and wraps `TryPurchase` + `SetCustomItemOwned`:
| `Type` | Action |
|---|---|
| `vehiclemod` | `SET_VEHICLE_MOD(veh, ModType, Value)` (+ `TOGGLE_VEHICLE_MOD_KIT`) |
| `toggle` | `TOGGLE_VEHICLE_MOD(veh, ModType, true)` |
| `color`/`paint` | respray native from `R/G/B` or paint index |
| `neon` | neon enable + `SET_VEHICLE_NEON_LIGHTS_COLOUR` |
| `extra` | `SET_VEHICLE_EXTRA(veh, Value, false)` |
| `livery` | `SET_VEHICLE_LIVERY` |
| `native` (advanced) | escape hatch: hash + typed args |
Then `BuildCategoryMenu` routes its `Activated` through that dispatcher. Ship one **example category** +
a documented `items.json` schema. This is what turns "bigger menu" into "platform."

### Gap B — Pillar 1: unlock stock LSC restrictions
**Today:** the mod only calls `SET_VEHICLE_MOD_KIT(veh, 0)` (standard enable); every category is gated on
`GET_NUM_VEHICLE_MODS > 0`, so if the stock kit exposes nothing the category is empty. No mod-kit swapping
or availability injection exists.
**Fix (research, then build):** investigate mod-kit assignment/cycling so vehicles can expose categories
their default kit hides. Needs live testing (which kit indices are valid per vehicle). Lower confidence than
Gap A — **scope as a stretch goal**; validate on the home machine before promising it in the release notes.

---

## Prioritized 3-day plan

### Day 1 — onboarding + stability (highest adoption leverage)
- [ ] **MCP `setup.ps1`**: create venv outside project → `pip install -r requirements.txt` → `setx
      GTAV_MCP_PYTHON/_VENV` → call `deploy.ps1` → print "now: claude /login, F9, run_host.bat". One command
      instead of ~8.
- [ ] **MCP `doctor.ps1`**: PASS/FAIL the chain — venv+deps, env vars, `claude` logged in, GTA path+DLLs
      (reuse deploy.ps1's checklist), TCP connect + `status` round-trip to 127.0.0.1:27015. The last check is
      "is the whole bridge alive" without Claude in the loop.
- [ ] **ELSC**: live-test the two guarded tick paths; confirm no regression. (DLL already rebuilt.)
- [ ] **ELSC**: add a **game-version gate** that disables fitment *writes* if `Game.Version` ≠ known-good
      build (the hardcoded b3788 offsets are the top stability risk on a future patch).

### Day 2 — the platform feature + safety docs
- [ ] **ELSC Gap A**: implement the generic JSON action dispatcher + ship one example category + schema doc.
      (Biggest "platform" win; well-scoped; build verifies locally.)
- [ ] **MCP docs**: front-page "your first three messages" (lift from TESTING.md), a "If the game crashes"
      recovery paragraph in USAGE, and a "writes/patches can crash (saves safe) — say no if unsure" note.
- [ ] **MCP scope decision**: either move experimental modules to `experimental/` (cleaner tool list) OR
      label the HOLD tools "experimental, unverified" in their docstrings. Min viable = the label.

### Day 3 — polish, dead-code, release
- [ ] **ELSC**: delete confirmed-dead files (`WheelFitment/PatternScanner.cs`, `WheelBones.cs`,
      `WheelFitmentNative.cs`); rebuild; verify 0 errors. Note: `TransmissionHUD.cs` is **NOT** dead (default
      corner HUD) — keep it.
- [ ] **ELSC**: gate dev harnesses (`autorun.txt`/`ui_preview.txt`/telemetry CSV) behind a debug flag so
      they don't `File.Exists` every frame in a shipped build.
- [ ] **ELSC**: drop the ~330 MB of backups/`integrated_mods` from the release package.
- [ ] **MCP**: optional — DragHUD sprite caching (only matters when drag HUD on; 56 `new CustomSprite`/frame).
- [ ] Final smoke test both; secrets sweep; commit core-only; **push**.

---

## Optimization checklist (status)
**MCP — done:** ✅ page-protection leak · ✅ TCP_NODELAY · ✅ par_struct dup (earlier) · ✅ doc contradiction.
**MCP — remaining:** ☐ ctypes private-WinDLL hoist (post-test) · ☐ drop dead `"diff"` from `_OFFTHREAD_COMMANDS`
(no handler) · ☐ experimental restructure (scope call) · ☐ DragHUD-equivalent N/A.
**ELSC — done:** ✅ tick guards (MT + stance) · ✅ logging off by default (earlier).
**ELSC — remaining:** ☐ version-gate fitment writes · ☐ DragHUD sprite caching (`DragHUD.cs:67,106-113`) ·
☐ throttle `World.GetNearbyPeds` in `CheckMechanicCleanup` (Main.cs ~6907) · ☐ cache per-frame menu strings
(Main.cs ~6505,6559) · ☐ delete 3 dead WheelFitment files · ☐ consolidate `CategoryConfig.cs`↔`MenuConfig.cs`.

## Must-have-for-launch shortlist (if the weekend gets tight)
1. MCP `setup.ps1` + `doctor.ps1` (onboarding is the #1 adoption barrier).
2. ELSC Gap A dispatcher + example category (makes the "platform" promise real).
3. ELSC fitment version-gate (stability on patch).
4. MCP front-page first-prompts + crash-recovery + risky-tools note (3 small doc edits).
5. Secrets sweep before any public push.
