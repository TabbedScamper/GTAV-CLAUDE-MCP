# 11 — Mission framework (the SHVDN mission state machine)

Mined from **SimpleGangWar** (a real C# mission-style script — Tick loop, stage state, spawn waves,
win/lose, cleanup) + **SHVDN** (`Script` lifecycle, `Blip`/`World` wrappers). This is the **mission
state-machine** pattern — how to structure a multi-stage objective mission in single-player — which is the
gap between "wander/react" and "complete a mission." **To be enriched by decompiling M8T's heist mods**
(see `Examples/MISSION-HARVEST-BRIEF.md`); this is the general scaffold those will refine.

> For the agent: this is *how missions are built*, so it knows what to read (objective blips → `mission_sense`)
> and how stages progress. For us: it's the template for authoring/running missions.

---

### Hang the mission loop off the Tick event, never a blocking while-loop
**Category:** mission-loop
**Problem:** Run mission logic every frame without freezing the game thread.
**Method:** Subclass `GTA.Script`; in the constructor wire `Tick += MainLoop;` (`void MainLoop(object, EventArgs)`). SHVDN raises `Tick` every frame on the script's own thread; all per-frame work (state switch, completion checks, marker draw, re-tasking) lives there. Throttle with `Interval` (ms; 0 = every frame) — gangwar uses 500ms idle, 100ms once running. `Script.Wait(ms)`/`Yield()` yield, and ONLY from inside Tick or it throws "Illegal call to Script.Wait outside main loop."
**Gotcha:** Tick is cooperative single-thread — a long synchronous loop inside ONE Tick stalls the whole game. Spread work across frames or `Wait`.
**Source:** shvdn-api/.../Shvdn/Script/Script.cs

### Drive the mission with a stage enum and one switch per frame
**Category:** state-machine
**Problem:** Sequence multi-stage flow (Idle→Briefing→GoTo→Combat→Escape→Complete/Failed) deterministically.
**Method:** `enum Stage { Initial=0, GoTo=1, Combat=2, Escape=3, Complete=4, Failed=5 }` + `Stage stage = Stage.Initial;`. Inside Tick, switch/gate on `stage`. Enum ordering lets `if (stage >= Stage.Running)` act as a phase test. Advance by assigning the field, doing the enter-actions (create blip, set objective text) at the moment of transition: `case Stage.GoTo: if (arrived) { blip.Delete(); SetupCombat(); stage = Stage.Combat; } break;`.
**Gotcha:** Give stages explicit ordered int values so `>=`/`<` phase comparisons work. Do enter-actions once at the transition, not re-run every frame.
**Source:** gangwar/SimpleGangWar.cs

### Player-triggered transitions via KeyDown/KeyUp, separate from Tick
**Category:** state-machine
**Problem:** Let the player start/confirm stages without polling keys every frame.
**Method:** Wire `KeyUp += OnKeyUp;` (`void OnKeyUp(object, KeyEventArgs)`); test `e.KeyCode`. One hotkey can mean different things per `stage`. SHVDN raises `KeyDown` on press/hold, `KeyUp` on release.
**Gotcha:** A two-press confirm (first press → `Stage.StopKeyPressed` + "press again", second → tear down) is cheap insurance for destructive transitions. Keep key handlers to state changes; let Tick do continuous work.
**Source:** gangwar/SimpleGangWar.cs

### Represent an objective as a coord/entity + a GPS-route blip
**Category:** objective
**Problem:** Mark where the player must go and draw the GPS line (what `mission_sense` then reads).
**Method:** Coord: `Blip blip = World.CreateBlip(coord);` (ADD_BLIP_FOR_COORD) or `World.CreateBlip(coord, radius)` (radius). Entity target: `Blip blip = ped.AddBlip();` (ADD_BLIP_FOR_ENTITY — tracks it). Style: `blip.Sprite = BlipSprite.TargetA; blip.Color = BlipColor.Yellow; blip.Name = "Objective"; blip.ShowRoute = true;` (SET_BLIP_ROUTE = the GPS line `DOES_BLIP_HAVE_GPS_ROUTE` reports). Store the handle to `blip.Delete()` on stage exit.
**Gotcha:** Setting `Sprite` RESETS Color and Name — set Sprite FIRST, then color/name. One objective = one blip you own and must delete on stage exit or blips pile up. **`ShowRoute=true` is what makes `mission_sense` pick it as the routed destination.**
**Source:** shvdn-api/.../Blip/Blip.cs, World.cs

### Draw the ground marker every frame inside Tick
**Category:** objective
**Problem:** Show the 3D objective cylinder (markers are immediate-mode).
**Method:** `World.DrawMarker(MarkerType.VerticalCylinder, coord, Vector3.Zero, Vector3.Zero, new Vector3(1,1,1), Color.FromArgb(160,255,200,0));` (DRAW_MARKER) — call EVERY Tick while the stage is active.
**Gotcha:** Immediate-mode — no handle, nothing to clean up, but must be re-issued every frame, so it lives in the per-stage Tick branch, not the transition. Use alpha < 255 so it reads translucent.
**Source:** shvdn-api/.../World.cs

### Detect stage completion by distance, death, or count each Tick
**Category:** completion
**Problem:** Decide when a stage's objective is met and advance.
**Method:** In the stage's Tick branch: reached → `Game.Player.Character.Position.DistanceTo(coord) < radius` (or `World.GetDistance` = GET_DISTANCE_BETWEEN_COORDS); target killed → `targetPed.IsDead`; all enemies dead → prune a `List<Ped>` then `count==0 && counter>=quota`; area/item → distance/flag. On success: delete the stage blip, `stage = Stage.Next;`, set the next objective.
**Gotcha:** `World.GetDistance` (straight-line) for trigger radii; `CalculateTravelDistance` only for road-path UI. Always `ped.Exists()` before touching a tracked ped — a despawned handle is stale.
**Source:** gangwar/SimpleGangWar.cs, World.cs

### Check fail conditions every Tick and branch to Failed
**Category:** fail
**Problem:** End the mission when the player or a protected entity is lost.
**Method:** Test fail predicates each Tick → `stage = Stage.Failed;`: player `Game.Player.Character.IsDead` / arrested (`IS_PLAYER_BEING_ARRESTED`); escort `escort.IsDead || !escort.Exists()`; target escaped (`DistanceTo > escapeRadius`); timer `int deadline = Game.GameTime + ms;` then `Game.GameTime > deadline`. (Gangwar saves/zeros `Game.MaxWantedLevel` so cops don't derail the scenario.)
**Gotcha:** Evaluate fail BEFORE completion so "objective done but player dying" resolves as a fail. Use `Game.GameTime` (pauses with the game), not wall-clock, for timers.
**Source:** gangwar/SimpleGangWar.cs

### Track every spawned ped/vehicle/blip in lists you own
**Category:** entity-lifecycle
**Problem:** Know which world entities are yours, to manage and later destroy.
**Method:** `List<Ped> spawnedAllies/Enemies/deadPeds` + Blip fields. On spawn: `Ped p = World.CreatePed(model, pos); p.Weapons.Give(...); p.Health = p.MaxHealth = hp; spawnedAllies.Add(p); counter++;`. Protected entities: `SET_ENTITY_AS_MISSION_ENTITY(p, true, true)` (won't cull); gangwar uses `p.AlwaysKeepTask = true` + `MarkAsNoLongerNeeded()` for dead ones.
**Gotcha:** Don't mutate a list while foreach-ing — collect removals into a scratch list, then remove in a second loop. Keep total-ever counters separate from live `.Count` (quotas need total; cleanup needs alive).
**Source:** gangwar/SimpleGangWar.cs

### Clean up ALL mission entities/blips on complete/fail/abort
**Category:** entity-lifecycle
**Problem:** Leave the world un-littered when the mission ends by ANY path.
**Method:** One `Teardown()`: restore `Interval`; `blip.Delete()` each owned blip; `foreach (Ped p in list) if (p.Exists()) p.Delete();`; `.Clear()` the lists; restore globals (`Game.MaxWantedLevel`). Call it from Complete, Failed, AND from `Aborted += OnAborted;` — SHVDN raises `Aborted` on unload/reload specifically for cleanup.
**Gotcha:** **You MUST wire `Aborted`** — if you only clean up on Complete/Fail, a mid-mission script reload orphans every ped + blip permanently. Guard deletes with `Exists()`.
**Source:** gangwar/SimpleGangWar.cs, Script.cs

### Reward on complete, notify and reset on fail
**Category:** reward
**Problem:** Close the loop with feedback + payout.
**Method:** Complete: `Game.Player.Money += reward;` + `GTA.UI.Notification.Show("Mission complete! $"+reward);`. Failed: `GTA.UI.Notification.Show("~r~Mission failed");`. Either way `Teardown()` + `stage = Stage.Initial;` to re-arm. Stage prompts via `GTA.UI.Screen.ShowHelpText(...)` / `ShowSubtitle(...)`; color codes `~r~`/`~g~`/`~y~`.
**Gotcha:** Funnel BOTH Complete and Failed through the same teardown+reset so the mission replays cleanly; a fail path that skips cleanup corrupts the next run.
**Source:** gangwar/SimpleGangWar.cs

### Keep the scene alive: re-task idle peds and top up waves each Tick
**Category:** mission-loop
**Problem:** Spawned actors go idle (finish a task / lose their target); the encounter must self-sustain.
**Method:** Each Tick (gated on the running stage): per team (1) spawn up to a cap with an anti-flood check (`World.GetNearbyPeds(spawnpoint, dist)` counting alive members); (2) iterate the list — if `ped.IsDead` delete its blip + queue removal + `MarkAsNoLongerNeeded()`; else if `ped.IsIdle && !ped.IsRunning` re-task: `ped.Task.RunTo(target)` / `ped.Task.FightAgainstHatedTargets(range)`. Tune with `SET_PED_COMBAT_ATTRIBUTES ped 46 true`, `SET_PED_SEEING_RANGE`.
**Gotcha:** Peds silently stop fighting once their target dies — poll `IsIdle`/`IsRunning` every Tick and re-task or the scene freezes. `ClearAllImmediately()` + `AlwaysKeepTask = true` right after spawn makes the re-task stick. (This is the C# mirror of PATTERNS/09's make-tasks-stick.)
**Source:** gangwar/SimpleGangWar.cs
