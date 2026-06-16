# 09 — Ped AI & tasks (move, drive, combat, flee, sequences)

Mined from **SHVDN** (`TaskInvoker`, `Tasks.cs`, `TaskSequence.cs`, the Combat/Movement/Vehicle flag
enums) and **Menyoo** (`natives.h` signatures + real bodyguard/combat usage). For scene depth: make peds
walk, drive, fight, flee, follow, and run scripted sequences. The make-it-stick native is
`SET_BLOCKING_OF_NON_TEMPORARY_EVENTS`.

---

### Build and run a task sequence (the ped=0 trick) ⭐
**Category:** task-sequence
**Problem:** Chain multiple tasks (walk → wait → fight) so a ped runs them in order.
**Method:** 1) `OPEN_SEQUENCE_TASK(&seqId)` (pointer out-param). 2) Add tasks but pass **ped handle = 0** to every `TASK_*` (e.g. `TASK_FOLLOW_NAV_MESH_TO_COORD(0, x,y,z, ...)`, `TASK_PAUSE(0, 3000)`, `TASK_COMBAT_PED(0, target, 0, 16)`). 3) optional `SET_SEQUENCE_TO_REPEAT(seqId, repeat)`. 4) `CLOSE_SEQUENCE_TASK(seqId)`. 5) `TASK_PERFORM_SEQUENCE(realPed, seqId)`. 6) `CLEAR_SEQUENCE_TASK(&seqId)` AFTER performing.
**Gotcha:** Inside the sequence the ped arg MUST be 0 (placeholder) — using the real handle silently breaks it. `CLEAR_PED_TASKS(ped)` before PERFORM; `CLEAR_SEQUENCE_TASK` only after.
**Source:** shvdn-api/.../Peds/TaskSequence.cs, Tasks.cs

### Make scripted tasks stick (stop peds abandoning them) ⭐
**Category:** task-status
**Problem:** Ambient peds drop your task on any "shocking event" (gunfire, the player, a car) and revert to default AI.
**Method:** `SET_BLOCKING_OF_NON_TEMPORARY_EVENTS(ped, true)` (0x9F8AA94D6D97DBF4). Also `SET_PED_KEEP_TASK(ped, true)` (0x971D38760FBC02EF) so the task survives when the ped goes ambient. Per-task variant: `TASK_SET_BLOCKING_OF_NON_TEMPORARY_EVENTS(ped, toggle)`.
**Gotcha:** This is THE single most important call for scene depth — without it peds in a sequence/combat wander off on the first disturbance. (SHVDN: `Ped.BlockPermanentEvents` / `AlwaysKeepTask`.)
**Source:** shvdn-api/.../Peds/Ped.cs; menyoo/.../Submenus/FunnyVehicles.cpp

### Poll whether a scripted task is still running
**Category:** task-status
**Problem:** Know when a ped finished / is performing / never started, to sequence next steps.
**Method:** `GET_SCRIPT_TASK_STATUS(ped, taskNameHash)` (0x77F1BEB8863288D5) → `ScriptTaskStatus`: 0 WaitingToStart, 1 Performing, 2 Dormant, 3 Vacant, 7 Finished. Pass `Any = 0x55966344` for the live state regardless of which task. Useful hashes: PerformSequence 0x0E763797, GoToCoordAnyMeans 0x93399E79, FollowNavMeshToCoord 0x2A89B8A7, SmartFleePed 0x6BA30179, WanderStandard 0xBBA3B7CA, EnterVehicle 0x950B6492.
**Gotcha:** Querying a specific hash returns `7 Finished` if that hash ISN'T the current task — so `7` means "not running this," not necessarily "completed." Use `Any` for the live state.
**Source:** shvdn-api/.../Peds/ScriptTaskStatus.cs, ScriptTaskNameHash.cs

### Walk/run a ped to a coord via the nav mesh
**Category:** ai-move
**Problem:** Path a ped naturally (around walls, stairs) to a position.
**Method:** `TASK_FOLLOW_NAV_MESH_TO_COORD(ped, x,y,z, moveBlendRatio, timeBeforeWarp, radius, navFlags, finalHeading)`. moveBlendRatio: 1.0 walk, 2.0 run, 3.0 sprint. timeBeforeWarp = ms before teleport if stuck (-1 never). radius ≈ 0.25. finalHeading 40000.0 = "leave as is".
**Gotcha:** If no path exists the ped just stands still — no error. Use `TASK_GO_STRAIGHT_TO_COORD(ped, x,y,z, moveBlendRatio, time, finalHeading, targetRadius)` to ignore paths.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs

### Send a ped to a point by ANY means (will commandeer a car)
**Category:** ai-move
**Problem:** Get a ped to a far position, letting them grab a vehicle if faster.
**Method:** `TASK_GO_TO_COORD_ANY_MEANS(ped, x,y,z, moveBlendRatio, vehicle, useLongRangeVehiclePathing, drivingFlags, maxRangeToShootTargets)`. vehicle = a handle to use or 0 = pick any. drivingFlags = see the driving-style card. maxRangeToShootTargets = -1 to disable.
**Gotcha:** A non-existent `vehicle` handle silently fails (no task). Pass 0 for "any." For far destinations set `useLongRangeVehiclePathing = true`.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs; menyoo natives.h (0x5BC448CB78FA3E88)

### Follow / go to another entity (companion)
**Category:** ai-move
**Problem:** Make a ped approach or tail another entity.
**Method:** One-shot approach: `TASK_GOTO_ENTITY_OFFSET_XY(ped, target, timeout, ox,oy,oz, moveSpeed, persist)`. Persistent follow: `TASK_FOLLOW_TO_OFFSET_OF_ENTITY(ped, entity, ox,oy,oz, movementSpeed, timeout, stoppingRange, persistFollowing)` (0x304AE42E357B8C7E).
**Gotcha:** For a bodyguard who keeps up, use `TASK_FOLLOW_TO_OFFSET_OF_ENTITY` with `persistFollowing=true` and `stoppingRange` ~2–10; the OFFSET_XY one completes once. Pair with `SET_PED_KEEP_TASK`.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs; menyoo natives.h

### Make a ped wander ambiently
**Category:** ai-move
**Problem:** Idle, lifelike wandering so the scene isn't full of statues.
**Method:** `TASK_WANDER_STANDARD(ped, 0, 0)` (heading + keep-moving bool). Bounded: `TASK_WANDER_IN_AREA(ped, x,y,z, radius, minLength, timeBetweenWalks)`.
**Gotcha:** For wander, LEAVE `BLOCKING_OF_NON_TEMPORARY_EVENTS` off — wandering IS the ambient behavior that blocking would suppress.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs

### Drive a ped's vehicle to a coordinate
**Category:** ai-drive
**Problem:** Make a ped drive to a destination with realistic traffic behavior.
**Method:** `TASK_VEHICLE_DRIVE_TO_COORD_LONGRANGE(ped, vehicle, x,y,z, speed, driveMode, stopRange)` (0x158BB33F920D360C) — best for long trips. Full: `TASK_VEHICLE_DRIVE_TO_COORD(ped, vehicle, x,y,z, speed, p6, vehicleModel, drivingMode, stopRange, straightLineDistance)`. speed in m/s (15–30 normal).
**Gotcha:** Ped must already be in the driver seat. speed is m/s not mph. Use LONGRANGE when far from the player or nodes won't stream and the car stops.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs

### Driving-style bitflags (the real values)
**Category:** ai-drive
**Problem:** Control how a driving ped behaves (obey lights, swerve, plough through).
**Method:** `drivingFlags` bitfield: StopForVehicles=1, StopForPeds=2, SwerveAroundAllVehicles=4, SteerAroundStationaryVehicles=8, SteerAroundPeds=16, SteerAroundObjects=32, StopAtTrafficLights=128, AllowGoingWrongWay=512, Reverse=1024, UseWanderFallback=2048, ForceStraightLine=16777216, StopAtDestination=2147483648. Composite modes: Normal `786603 (0xC00AB)`, IgnoreLights `2883621 (0x2C0025)`, AvoidTraffic `786469`, PloughThrough `262144`, Rushed `0x400C0025`. Scalar shorthands `6`=AvoidTrafficExtremely, `5`=SometimesOvertake.
**Gotcha:** Same magic numbers as legacy `DrivingStyle`. Add `UseWanderFallback (2048)` so a failed path cruises randomly instead of beelining.
**Source:** shvdn-api/.../VehicleAi/Task/VehicleDrivingFlags.cs, DrivingStyle.cs

### Drive-wander (ambient traffic)
**Category:** ai-drive
**Problem:** A driver cruising aimlessly for traffic depth.
**Method:** `TASK_VEHICLE_DRIVE_WANDER(ped, vehicle, speed, drivingStyle)` (0x480142959D337D00). speed m/s, drivingStyle = the flags above (e.g. 786603).
**Gotcha:** Ped must be the driver; add 2048 (wander fallback) to the flags.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs

### Enter / leave a vehicle with seat indices
**Category:** ai-drive
**Problem:** Board a specific seat or exit.
**Method:** `TASK_ENTER_VEHICLE(ped, vehicle, timeout, seatIndex, speed, flag, null)` (0xC20E50AA46D09CA8). **Seat indices: -1 = driver, 0 = front passenger, 1 = rear-left, 2 = rear-right, -2 = any free.** speed 1.0 walk / 2.0 run. Leave: `TASK_LEAVE_VEHICLE(ped, vehicle, flags)`. Instant: `TASK_WARP_PED_INTO_VEHICLE(ped, vehicle, seatIndex)`.
**Gotcha:** Seat -1 is the DRIVER (not 0). EnterVehicleFlags: WarpIn=16, JackAnyone=8, WarpToDoor=2, DontCloseDoor=256. LeaveVehicleFlags: WarpOut=16, BailOut=4096 (jump at speed), DontWaitForStop=64.
**Source:** shvdn-api/.../Peds/Task/Vehicle/EnterVehicleFlags.cs, LeaveVehicleFlags.cs

### Make a ped fight a target / nearby enemies
**Category:** ai-combat
**Problem:** Put a ped into combat vs a specific ped or all hated peds around them.
**Method:** Specific: `TASK_COMBAT_PED(ped, targetPed, combatFlags, threatResponseFlags)` (0xF166E48407BAC484) — pass `(ped, target, 0, 16)`. Area: `TASK_COMBAT_HATED_TARGETS_AROUND_PED(ped, radius, combatFlags)` e.g. `(ped, 400.0, 0)`. Timed: `TASK_COMBAT_PED_TIMED(ped, target, ms, flags)`.
**Gotcha:** "Hated targets" only engages Neutral/Dislike/Hate peds — none nearby → task ends instantly. The 4th arg is the THREAT-response flag (16 standard), not combat flags. Set `SET_PED_COMBAT_ABILITY(ped, 2)` first for competence. (See file 10 for why-won't-my-ped-fight.)
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs; menyoo Routine.cpp

### Shoot at a target (without full combat AI)
**Category:** ai-combat
**Problem:** Have an armed ped open fire on an entity/position.
**Method:** `TASK_SHOOT_AT_ENTITY(ped, target, duration, firingPatternHash)` (duration ms, -1 = until dead; 0 = default pattern). Position: `TASK_SHOOT_AT_COORD(ped, x,y,z, duration, pattern)`. Aim only: `TASK_AIM_GUN_AT_ENTITY(ped, target, duration, instantBlend)`.
**Gotcha:** The ped must hold a gun-flagged weapon (not melee/thrown) or nothing happens. `FIRING_PATTERN_FULL_AUTO = 0xC6EE6B4C`.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs

### Flee, react-and-flee, or surrender
**Category:** ai-flee
**Problem:** Civilians running from danger, or hands up.
**Method:** Flee a ped: `TASK_SMART_FLEE_PED(ped, fleeTarget, safeDistance, fleeTime, preferPavements, updateToNearestHated)` (safeDistance 100.0, fleeTime -1 = forever). Flee a coord: `TASK_SMART_FLEE_COORD(ped, x,y,z, distance, time, preferPavements, quitIfOutOfRange)`. React+flee (startle anim first): `TASK_REACT_AND_FLEE_PED(ped, fleeTarget)`. Surrender: `TASK_HANDS_UP(ped, duration, facingPed, timeToFace, flags)`.
**Gotcha:** time -1 = flee forever; positive = ms. `TASK_REACT_AND_FLEE_PED` is the cinematic one (only 2 args). For a panic scene, react-and-flee then smart-flee.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs
