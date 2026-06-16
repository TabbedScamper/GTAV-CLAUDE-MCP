# 16 — HKH Mission Creator: the data-driven mission schema + 105 objective types ⭐

Mined from **HKH191's MissionHeist Creator 1.1.1** (HKH_MissionCreator.dll, ~95k lines ILSpy). This is a
SHIPPED, data-driven mission BUILDER: missions are pure data (INI files) executed by a generic runtime
interpreter. It is the closest thing to a reference implementation for OUR auto-mission-generator — model
the generator's schema + objective vocabulary on this. Re-decompile from
`Examples/_sources/HKH191/4231a5-*/.../HKH_MissionCreator.dll`.

> Takeaway for `scenario_gen.py` / `mission_runner.py`: adopt this 3-section schema (Mission / Objectives /
> Map) and the objective-type catalog. Each objective = a typed record with target + completion params; the
> runtime is one stage-index state machine calling `Event.RunEvent()` until done.

---

### Mission file = INI with [Mission] + [Objectives] + [Map]; runtime is a stage-index interpreter ⭐
**Category:** mission-schema
**Problem:** Define a whole mission as DATA and execute it generically (no per-mission code).
**Method:** One `.ini` per mission in `scripts\HKH_MissionCreator\Missions\<name>.ini`, three sections:
`[Mission]` header (MissionName, UniqueMissionID, Description, StartCoord, BlipSprite/Color, UseTimeLimit/
TimeLimit, MissionPay, AverageHeistPay, GiveDuffleBag, PlayHeistCompleteFinish, MissionGroup); `[Objectives]`
= an ordered list of `<New Objective>` records (pipe-delimited fields, see next card); `[Map]` = serialized
entities (`<Entity>…</Entity>` in Menyoo ObjectPlacementMap format — peds/vehicles/props with transform +
config). Runtime: `Mission : Script` holds `List<Event> Events` + `int CurrentMissionStage`; each tick
`if (stage != -1 && stage < Events.Count) Events[stage].RunEvent(this)`. `RunEvent` checks its own
completion predicate, fires lifecycle triggers, and `stage++` when done. Save/load round-trips through
`GetMissionContents()` / `LoadMissionFromString_WithoutMenu()`.
**Gotcha:** Vector3 serializes as "X:..f Y:..f Z:..f"; fields are `|`-delimited with trim. Entities in
`[Map]` bind to objectives by stored integer IDs resolved to live handles at spawn. Stage `-1` = inactive.
**Source:** MissionCreator GetMissionContents L8427-8454, RunEvent loop L88023, load L88089-88327

### Objective record = typed Event with target + completion params (the generator vocabulary)
**Category:** mission-schema
**Problem:** One uniform record that covers "go here / kill that / hack this / loot until $X".
**Method:** `Mission.Event` fields: `EventID`, `EventType Type`, `Subtitle` (objective text), blip config
(sprite/color/size/name/alpha), `TargetEntityID`+`TargetEntitiesID[]` (entity targets), `TargetDestination`
+`TargetDestinations[]` (coords), `TargetRotation`, `ObjectiveRange` (proximity radius), `ObjectiveTimeOut`
(timer), `TargetScore`/`CurrentScore` (for "achieve N" types), plus specialized handlers (`SafeCracking`,
`KeypadClass`, `Container`, `PassiveRadar`). Lifecycle triggers in three buckets:
`SecondaryEvents_Start/_During/_End`. Optional/parallel objectives via `OptionalObjectives[]` +
`EventGrouping` (group several EventIDs to run together). INI line:
`<New Objective> EventID|Type|UseGenericBlip|Subtitle|TargetDestination|TargetEntityID|ObjectiveRange|
ObjectiveTimeOut|ObjectiveTaskId|TargetRotation|ObjectiveBoolId|SecondaryTargetEntityID|[ids…]|[coords…]`.
**Gotcha:** completion is per-type (distance < range, entity dead, score >= target, timer, bool flag). The
specialized handlers (SafeCracking is a 0-7 state machine gated on anim-done + fingerprint-hack result) are
where the rich beats live. Optional objectives track completion independently of the main chain.
**Source:** MissionCreator Event class L73373-73440, INI serialize L8500, SafeCracking L73168-73305

### The 105 objective-type catalog (mined from R*'s actual mission vocabulary)
**Category:** objective-types
**Problem:** A complete, grounded vocabulary of "things a mission can ask the player to do."
**Method:** `EventType` enum (L2442-2692). Grouped:
- **Move:** Go_To_Position, Go_To_Entity, Go_To_Destination_Timed, Leave_Area, Use_Teleport_Marker, Teleport_Into_Named_Interior
- **Vehicle:** Enter_Vehicle, Choose_Vehicle, Drive_Vehicle_To_Destination, Drive_To_Destination, Leave_Vehicle, Respray_Vehicle, Paint_Vehicle_To_Color, Damage_Vehicle, Destroy/Puncture_Vehicle_Tyre, Achieve_Vehicle_Speed(+at position/specific), Vehicle_Enter_Garage
- **Air:** Fly_To_Altitude/Destination(+ignore altitude/and altitude), Fly_Specific_*, Enter/Choose_Aircraft_With_Bomb_Bay, Destroy_Air_Targets, Aircraft_Enter_Hangar
- **Tow/Deliver:** Tow_Specific_Vehicle, Tow_Trailer/Vehicle_To_Position, Deliver_Ped_In_Any/Specific_Vehicle, Deliver_Vehicle_Docks, Deliver_Cargo_Crate_Via_Airdrop/Cargobob_Hook, Deliver_Holdable_Crate_To_Coordinate
- **Combat:** Kill_Specific_Ped, Kill_Ped_Group, Kill_Ped_With_Specific/Silenced_Weapon (+group), Stealth_Kill_Ped(+group), Kill_Ped_Using_Entity/Vehicle_Explosion/Vehicle_Collision/Falling_Prop/Head_Shot, Arrest_ped, LSPDFR_Arrest_*/Write_Ticket, Escape_Enemies/Cops, Get_Cops, Follow_Target_For_Time/To_Destination
- **Destroy:** Destroy_Specific_Vehicle/Vehicle_Group/Specific_Object/Object_Group, Damage_* variants
- **Heist core:** Loot_Specific_Valuable, Loot_Valuables, Loot_Until_Pay_Is_Greater_Than, Crack_Safe, Hack_Keypad/Laptop/Fingerprint/Radar/Security_Panel, Use_Fleeca_Drill/Casino_Drill/Casino_Laser_Drill
- **Interact:** Interact_With_Entity(+Multiple), Place_Thermite/Bomb_On_Wall, Open_Container_Small/Large, Pull_Switch/Lever, Push_Button, Cut_Wire/Lock_With_Boltcutters, Blow_Lock_With_Bomb, Cut_Lock_with_Torch, Cut_Underwater_Grate, Rewire_Circuit_Box, Push_Crate_To_Destination, Search_Swatvan, Use_Phonebox, Unlock_Vehicle_Door_Pins, Hotwire_Vehicle_Rotating_Lock
- **Misc:** Achieve_Drift_Points(+variants), Achieve_Ped/Vehicle_Kills, Photograph_Entity, Wait_For_Time, Play_Countdown, Play_Cutscene, Mop_Area, Move_Trash_Bag(s)_To_Location/Rear_Of_Vehicle, GTAO business presence checks, Mission_Complete
**Gotcha:** many map 1:1 to natives we already have; the heist/interact ones use synced-scene beats (do them
C#-side — PATTERNS/15). Use this as the menu of objective types the generator can compose; not all need to
ship at once.
**Source:** MissionCreator EventType enum L2442-2692

### Secondary events = lifecycle triggers (spawn/sound/condition) fired Start/During/End of each objective
**Category:** triggers
**Problem:** Make a mission reactive — spawn reinforcements mid-objective, fail on a condition, branch.
**Method:** Each Event carries `SecondaryEvents_Start/_During/_End`. A `SecondaryEvent` has `Type`
(Global_SecondayEvent action), `CallTime` (Start/During/End), `EventCauseEffect` (Do_Nothing/Continue/
Fail_Mission/…), `RepeatOption`, a `Data[]` param list, plus nested `TaskSequenceEvents[]` and
`ConditionalEvents[]` (branch on a condition). INI: `<SecondaryEvent> Type|CallTime|CauseEffect|Repeat|Data…`,
`<TaskSequence>…`, `<ConditionalEvent>…`. `Create_Event_Group` groups objective IDs for parallel play.
**Gotcha:** this is how M8T-style "cops on timeout" / "wave on breach" are expressed as DATA rather than
code — the generator can attach reactions to any objective without bespoke logic.
**Source:** MissionCreator secondary-event parse L8540-8551, grouping L88288-88313

### Mission entities via Menyoo ObjectPlacementMap (peds/vehicles/props as data)
**Category:** world
**Problem:** Place the mission's peds/vehicles/props (the cast + set) as data, spawned on demand.
**Method:** `[Map]` holds `MenyooMapLoader.ObjectPlacementMap` records: model `Hash`/`Inputtedhash`,
`ObjType` (Ped/Vehicle/Prop), `Position`/`Rotation`, ped config (`Scenario`, `AnimDict`/`AnimName`, Health,
Armour, WeaponHash), vehicle config (Primary/Secondary/Pearlescent color, Livery), attachment
(EntityToAttachToHandle/Bone/Offset/Rotation), flags (CannotBeDeleted/Spawned, SpawnDist). Spawned lazily
when the mission/objective needs them; tracked for cleanup. Round-trips via
`ConvertObjectPlacementMapDataToString`. This is the standard **Menyoo .xml/.ini placement format** — so
existing Menyoo maps can seed mission sets (see the `MenyooMap_to_ObjectPlacementMap` converter mod).
**Gotcha:** the same ObjectPlacementMap format is the lingua franca across HKH191 mods — reuse it for our
spawn/cleanup tracking and to import community map placements as mission scenery.
**Source:** MissionCreator ObjectPlacementMap L60591-60656, map parse L8655-8660, spawn L88260-88273
