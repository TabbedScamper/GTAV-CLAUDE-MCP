# 15 — Player-driven heist interactions + guard AI (Cayo Perico SP, decompiled)

Mined from **Cayo Perico Heist in SP v11.0** (CayoPericoHeistInSP.dll, HKHModHelperNew.dll, SmartGuards.dll —
ILSpy-decompiled SHVDN C#) + the **Enable All Interiors** mod (EnableAllinteriors.dll). These are the
gold-standard SP-heist references for the NATURAL, player-DRIVEN feel: walk up → "Press E" → a
frame-perfect animation where the ped + props + doors all move in lockstep. Synced-scene hashes match
PATTERNS/14 (verified vs native_db).

> THE BIG ARCHITECTURAL FINDING: this whole class of effect — synchronized scenes AND the bottom-center
> text builder — works from **C#/SHVDN** but NOT from PyLoaderV's Python `gta.invoke` (the per-call socket
> model can't hold the multi-call builder's script-thread state). So the INTERACTIVE/animated beats belong
> in the C# companion; Python orchestrates (scenario, coords, sequencing) and the C# renders. See
> `gtav-bottom-center-text-via-csharp` memory + DISCOVERIES.

---

### The player-driven interaction loop: marker → proximity → Press E → synced scene ⭐
**Category:** interaction
**Problem:** Let the player TRIGGER a heist action at a spot and watch a perfectly-aligned animation — not an autoplay cutscene.
**Method:** Per frame, for each interaction point: `World.DrawMarker(MarkerType.1, pos, …, Color.Goldenrod)`; if `player.Position.DistanceTo(spot) < 1.25f` → `DisplayHelpTextThisFrame("Press ~INPUT_CONTEXT~ to …")` and `if (Game.IsControlJustPressed(Control.Context /*51*/))` start the beat. On trigger: (1) spawn the props at the player, freeze them (`IsPositionFrozen=true`, `IsCollisionEnabled=false`, `IsInvincible=true`); (2) read the ANCHOR from the target prop: `anchor = targetProp.GetOffsetPosition(new Vector3(0,0.03f,0)); rotZ = targetProp.Rotation.Z`; (3) snap the player to the anchor (`player.Position = anchor` — a sub-step, not a "teleport away"); (4) `Game.Player.CanControlCharacter = false`; (5) build the synced scene (next card).
**Gotcha:** `Control 51` = INPUT_CONTEXT (E / dpad-right). The marker is drawn UNCONDITIONALLY each frame; the help + key check live INSIDE the distance test. The `+0.03f` Y offset + `rotZ` from the prop are what make the anim line up with the world object. Disable control for the duration, restore after.
**Source:** CayoPericoHeistInSP keypad-hack L33275-33345; bolt-cutter gate L10761-10852

### Frame-perfect multi-entity animation = ONE synchronized scene, many entities
**Category:** animation-scene
**Problem:** The ped, the tool prop, the bag, AND the gate/door must all animate in exact lockstep so nothing clips or floats.
**Method:** `scene = CREATE_SYNCHRONIZED_SCENE(anchor.x,y,z, 0,0, rotZ, 2)` → optionally `SET_SYNCHRONIZED_SCENE_LOOPED`/UPDATE bounds → for EACH prop `PLAY_SYNCHRONIZED_ENTITY_ANIM(prop, scene, "<perEntityClip>", dict, 1000.0, -8f, -8f, rotZ+180f, 1148846080, 0)` → for the PED `TASK_SYNCHRONIZED_SCENE(ped, scene, dict, "<pedClip>", 1000.0, -4.0, 128, 0, 1148846080, 0)`. Every entity uses the SAME scene id + the same dict; R* ships a matching clip per entity (e.g. bolt-cutter scene: ped `action_male`, `action_bag`, `action_chain`, `action_lgate`, `action_rgate`, `action_cutter`). Multi-phase (enter→loop→exit): DELETE + RE-CREATE the scene between phases so frames reset.
**Gotcha:** props use `rotZ+180f` (face the ped); ped clip blends differ (`-4.0/128` vs props `-8f/-8f`). `1148846080` = the loop/hold flag (0x44000000 as int bits). The ped gets BOTH a TASK_SYNCHRONIZED_SCENE and a PLAY_SYNCHRONIZED_ENTITY_ANIM (redundant but ensures commitment). **This is the piece that fails through the Python bridge — do it in C#.**
**Source:** CayoPericoHeistInSP L33300-33345 (hack), L10780-10852 (bolt cutters)

### Completion via synced-scene PHASE (held-key & mash variants)
**Category:** completion
**Problem:** Know when the player-driven beat is done; support hold-to-progress.
**Method:** Poll `GET_SYNCHRONIZED_SCENE_PHASE(scene)` (0→1). C# decompiles show it as int-encoded float bits: `0.5≈1054353216`, `0.75≈1059353216`, `1.0=1065353216` — compare those, or read it as a float (cleaner). Stage gates at .5/.75/1.0 fire SFX/VFX then advance. Hold-to-progress (torch/drill): only advance phase while `Game.IsControlPressed(Control.Attack/*24*/)` is held (+ `INPUT_MOVE_LR/UD` 30/31 to steer); release pauses. Some beats hand off to a separate minigame class that signals a completion flag.
**Gotcha:** if you read the native as int you MUST compare the float-bit magic numbers; requesting `return_type=float` avoids that. Recreate the scene per phase or the loop never reaches 1.0.
**Source:** CayoPericoHeistInSP gate-progress L10861-10880; torch L34126-34259

### Walk-in interiors: load IPL + unlock the door + register it auto-opening in the DOOR SYSTEM ⭐
**Category:** doors
**Problem:** Let the player physically WALK into a building (no teleport) through a normally-locked door.
**Method:** `REQUEST_IPL(...)` so the interior streams, then per entrance door run the "UnlockDoor" routine: `SET_STATE_OF_CLOSEST_DOOR_OF_TYPE(modelHash, x,y,z, locked:false, heading:0.0, 50.0, 0)` → `GET_STATE_OF_CLOSEST_DOOR_OF_TYPE`(→ door enum) → if `!IS_DOOR_REGISTERED_WITH_SYSTEM(enum)` then `ADD_DOOR_TO_SYSTEM(enum, model, x,y,z, false,false,false)` → `DOOR_SYSTEM_SET_DOOR_STATE(enum, 0, …)`, `DOOR_SYSTEM_SET_AUTOMATIC_RATE(enum, 30f, …)`, `DOOR_SYSTEM_SET_AUTOMATIC_DISTANCE(enum, 5f, …)`. Now the door swings open automatically within 5m — a real walk-in. Run it each tick (cheap) so it stays unlocked.
**Gotcha:** ISOLATED milo placements (casino vault, **FINBANK / Union Depository**) have NO physical path from the street — even the Enable-All-Interiors mod uses a TELEPORT MARKER for those. Walk-in only works for map-connected facades (banks, shops, Lester's). The vault DOOR itself opens cleanly via the interior entity set `ACTIVATE_INTERIOR_ENTITY_SET(id,"SET_VAULT_DOOR_OPEN")` / `DEACTIVATE …("SET_VAULT_DOOR_CLOSED")` — real geometry, not a prop rotation.
**Source:** EnableAllInteriors `UnlockDoor` (SET_STATE_OF_CLOSEST_DOOR_OF_TYPE + DOOR_SYSTEM_*), vault-door entity set L15424; FINBANK teleport marker L17564

### Stealth guard vision: 35° FOV cone + LOS raycast + escalation timer
**Category:** enemies
**Problem:** Guards that believably SEE the player — a cone + line-of-sight, escalating suspicious→combat — for stealth.
**Method:** Per guard (throttled, ~1s batches): (1) distance gate `VDIST2(guard, player) < Radius` (≈120m). (2) FOV: `dirToPlayer = (player.Pos - guard.Pos)`, `headingToPlayer = dirToPlayer.ToHeading()`, `delta = headingToPlayer - guard.Heading`; in-cone if `|delta| <= SeeingAngle` (≈35° → ~70° cone). (3) LOS: `World.Raycast(guardEye = guard.GetOffsetPosition(0,0.1,1) → player.Pos, IntersectFlags 511)`; clear if `DidHit && hitPos.DistanceTo(player) < 0.5f`. In-cone + LOS clear → `TargetVisible`. Escalate with a per-frame `visibleTimer`: ~5-10 frames = alert anim, ~30+ = `ped.Task.Combat(player, flags 0, threatResponse 16)`. Hearing is a parallel trigger: gunshot heard if `player.IsShooting` within 65m (5m if silenced). On engage, cascade to allies within 10m (set their `HasSeenPlayer`, task combat). Accuracy per faction via `GET_RANDOM_INT_IN_RANGE(min,max)` (e.g. security 35-65, military 45-80).
**Gotcha:** vision is cone+LOS only — NO crouch/light/noise modelling (perf tradeoff). The 0.5m raycast tolerance means thin cover may not block. Relationship group (`ADD_RELATIONSHIP_GROUP("HATES_PLAYER")` + `SET_RELATIONSHIP_BETWEEN_GROUPS`) is what makes the engine actually aggress — without it, tasks won't fight. Throttle detection to ~1s and only tick nearby/active guards or it's expensive.
**Source:** SmartGuards FOV+LOS L9692-9727, escalation L9877-9962, hearing L9732-9763, spawn/relationship L1121-1241, accuracy L5950

### Investigate-before-combat (the believable "huh?" beat)
**Category:** enemies
**Problem:** A guard that glimpses you should investigate, not instantly laser you.
**Method:** On first detect, if `AllowInvestigateBeforeShoot`: set `IsInvestigating=true`, `stopInvestigateTime = GameTime + rand(500,3500)`, and `TASK_GO_TO_COORD_WHILE_AIMING_AT_ENTITY(ped, lastKnownPos, player, moveBlend 1f, shoot:false, 0.8f, 0.8f, navMesh:true, 0, false, firingPattern)`. If it reaches the spot / timer expires while still seeing you → full combat; if LOS lost → return to post (`OriginalPosition/Heading`). Dead-body sighting triggers the same investigate state (blip→66, radius ×1.35).
**Gotcha:** an arrest variant approaches at slow moveBlend(90f) and resets at <1.5m — only one guard arrests at a time (`CurrentArrestPed` guard). The timer randomization is what stops the robotic instant-aggro.
**Source:** SmartGuards investigate L9746-9796, arrest L9832-9862, dead-body L10101-10110
