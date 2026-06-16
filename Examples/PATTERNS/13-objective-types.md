# 13 — Objective-type catalog (from R*'s actual decompiled SP scripts)

Mined from the decompiled GTA V script corpus (R*'s real single-player missions + activities:
agency_heist, armenian, fbi*, finale_heist*, carsteal*, exile*, docks*, and `am_*` activities). Each
objective type is grounded in the REAL natives R* uses to set it up + detect completion — the accurate
vocabulary the generator (`tools/scenario_gen.py`) and runner (`tools/mission_runner.py`) build on.

> Decompiler note: `GET_DISTANCE_BETWEEN_COORDS(a,b,1) < r` and the optimized `VDIST2(a,b) < r*r`
> (squared) are used interchangeably for proximity — both real.

| Type | Player does | Detect (real native) | Setup | Seen in |
|---|---|---|---|---|
| **goto** | reach a marked spot | `GET_DISTANCE_BETWEEN_COORDS < r` / `IS_ENTITY_IN_ANGLED_AREA` / `IS_ENTITY_AT_COORD` | ADD_BLIP_FOR_COORD, SET_BLIP_ROUTE, CREATE_CHECKPOINT, SET_NEW_WAYPOINT | armenian, agency_heist, fbi* |
| **eliminate** | kill all hostiles | loop `IS_PED_DEAD_OR_DYING` / `IS_ENTITY_DEAD` until all dead | CREATE_PED, SET_PED_RELATIONSHIP_GROUP_HASH, GIVE_WEAPON_TO_PED, TASK_COMBAT_PED, ADD_BLIP_FOR_ENTITY | armenian, fbi*, finale_heist, exile |
| **assassinate** | kill one named target | `IS_PED_DEAD_OR_DYING` + `HAS_ENTITY_BEEN_DAMAGED_BY_ENTITY(target, player)` | CREATE_PED, SET_ENTITY_IS_TARGET_PRIORITY, ADD_BLIP_FOR_ENTITY | assassin_*, finale_heist, carsteal3 |
| **stealth_kill** | quiet takedown, no alarm | `TASK_STEALTH_KILL` then `IS_PED_DEAD_OR_DYING`; `GET_PED_ALERTNESS_STATE` stays low | CREATE_PED + TASK_START_SCENARIO_IN_PLACE, silenced weapon | fbi4_prep2 |
| **intimidate/holdup** | aim at civ → surrender | `IS_PLAYER_FREE_AIMING_AT_ENTITY` / `IS_PLAYER_TARGETTING_ENTITY` + anim `mp_am_hold_up` | CREATE_PED, CREATE_OBJECT (bag), NETWORK_ADD_PED_TO_SYNCHRONISED_SCENE | am_hold_up |
| **chase** | pursue a fleeing vehicle | catch via `GET_DISTANCE_BETWEEN_COORDS`; `TASK_VEHICLE_CHASE` | spawn target+pursuer, ADD_BLIP_FOR_ENTITY | carsteal4, fbi* |
| **lose_tail/lose_cops** | shed wanted/pursuers | `GET_PLAYER_WANTED_LEVEL == 0`; fail `IS_PLAYER_BEING_ARRESTED` | SET_PLAYER_WANTED_LEVEL[_NOW], DISPLAY_HELP | exile, am_distract_cops |
| **escort/follow** | keep ally alive & near | ally proximity; fail `IS_PED_INJURED`/`IS_ENTITY_DEAD`; `TASK_FOLLOW_TO_OFFSET_OF_ENTITY` | CREATE_PED, SET_PED_AS_GROUP_MEMBER, blip | exile2, carsteal2, fbi* |
| **wait_for/regroup** | wait for ally/time | ally in zone (`IS_ENTITY_IN_ANGLED_AREA`) or `GET_GAME_TIMER > deadline` | area coords, blip, help | agency_heist, finale_heist, docks_prep |
| **collect/pickup** | walk into a pickup | `HAS_PICKUP_BEEN_COLLECTED`; portable via CREATE_PORTABLE_PICKUP | CREATE_PICKUP[_ROTATE], blip | am_dead_drop, am_ga_pickups |
| **collect_sequence** | touch points in order | per-point `GET_DISTANCE_BETWEEN_COORDS < r` over a coord array | CREATE_OBJECT/CHECKPOINT per point | am_cp_collection (130 pts), docks_prep |
| **crate_drop** | recover a dropped crate | `DOES_ENTITY_EXIST` + distance to zone | CREATE_OBJECT, FREEZE_ENTITY_POSITION, parachute/blip | am_crate_drop |
| **deliver/drop_off** | bring vehicle/object to zone | `IS_ENTITY_IN_ANGLED_AREA`/`IS_ENTITY_IN_AREA`; veh also `IS_PED_IN_VEHICLE` | ADD_BLIP_FOR_COORD, CREATE_CHECKPOINT | docks_prep, finale_heist_prep, am_imp_exp |
| **steal_vehicle** | take a target vehicle | `IS_PED_IN_VEHICLE(player, veh)` + `GET_PED_IN_VEHICLE_SEAT == player` + `IS_VEHICLE_DRIVEABLE` | CREATE_VEHICLE, ADD_BLIP_FOR_ENTITY | carsteal*, docks_prep, am_hot_property |
| **destroy** | wreck a vehicle/object | `!IS_VEHICLE_DRIVEABLE` / `IS_ENTITY_DEAD` / `GET_ENTITY_HEALTH <= t` | spawn target, ADD_EXPLOSION, blip | am_destroy_veh, finale_heist, docks_heist, fbi4 |
| **plant** | attach + detonate device | `ATTACH_ENTITY_TO_ENTITY`+`IS_ENTITY_ATTACHED`; then ADD_EXPLOSION → dead | REQUEST_MODEL, CREATE_OBJECT, ATTACH_ENTITY_TO_ENTITY | docks_heistb, finale_heist2a |
| **sabotage** | tamper via interaction | synch-scene/anim done + `HAS_ENTITY_BEEN_DAMAGED_BY_ENTITY`/state | TASK_PLAY_ANIM / synch-scene at target | finale_heist2a, docks_heistb, agency_heist2 |
| **criminal_damage** | destroy N props for score | sum `HAS_ENTITY_BEEN_DAMAGED_BY_ENTITY(obj, player)` vs target | array of CREATE_OBJECT, HUD counter | am_criminal_damage |
| **ram/disable_vehicle** | ram target till immobile | `HAS_ENTITY_BEEN_DAMAGED_BY_ENTITY` + `GET_ENTITY_HEALTH <= t` + `!IS_VEHICLE_DRIVEABLE` | target spawn + chase, blip | am_destroy_veh, fbi4 |
| **survive** | stay alive vs waves/timer | `GET_GAME_TIMER - start >= dur`; fail `IS_PLAYER_DEAD` | wave CREATE_PED + TASK_COMBAT_PED, countdown | am_distract_cops, exile, agency_heist3a |
| **defend** | hold a spot/ally | all attackers dead or timer; fail defended `IS_ENTITY_DEAD` | CREATE_PED + TASK_GUARD_CURRENT_POSITION, blip | agency_heist2/3b, finale_heist2b, am_gang_call |
| **capture_area** | stay in zone to accrue | `IS_ENTITY_IN_ANGLED_AREA` while a GET_GAME_TIMER counter advances | zone coords, blip, HUD progress | am_king_of_the_castle |
| **race_checkpoints** | hit ordered checkpoints, timed | per-cp `IS_ENTITY_AT_COORD`/distance; `GET_GAME_TIMER` | CREATE_CHECKPOINT(+DELETE), next blip | am_hunt_the_beast, race scripts |
| **transport_passenger** | pick up ped, drive to dropoff | board `IS_PED_IN_VEHICLE(passenger, veh)`; arrive `IS_ENTITY_AT_COORD(veh)` | CREATE_PED + TASK_ENTER_VEHICLE; heli = TASK_HELI_MISSION | am_taxi, am_boat_taxi, am_heli_taxi |
| **hunt_animal** | track + kill an animal | `IS_PED_DEAD_OR_DYING(animal)`; distance/scent cues | CREATE_PED (animal), scent blips/checkpoints | am_hunt_the_beast |
| **score_minigame** | hit targets for a score | per-hit `HAS_ENTITY_BEEN_DAMAGED_BY_ENTITY`; timed windows | minigame state, HUD score | am_darts, am_armwrestling, range_modern, gunclub |
| **drill/safecrack** | play the breach interaction | anim/synch-scene done — `IS_ENTITY_PLAYING_ANIM`; scaleform UI | REQUEST_ANIM_DICT("mini@safe_cracking"), TASK_PLAY_ANIM_ADVANCED | gb_*safecracker, public_mission_controller |
| **distract_cops** | draw & HOLD wanted | `GET_PLAYER_WANTED_LEVEL >= threshold` (inverse of lose_tail) | SET_PLAYER_WANTED_LEVEL | am_distract_cops |
| **recon/scan** | get a target on-screen/in-sight | `IS_ENTITY_ON_SCREEN` / `IS_SPHERE_VISIBLE` (grounded substitute for "photograph") | blip on target | am_dead_drop, fbi3 |

**Not grounded in this corpus** (don't claim): phone *photograph*/snapmatic, graffiti *tag/spray*, dedicated
*hack-terminal* minigame (the safe-crack **drill** is the grounded analog). The synch-scene/anim system
(`TASK_SYNCHRONIZED_SCENE`, scenario tasks) is the workhorse completion mechanism behind
plant/sabotage/holdup/drill — completion gated on anim phase (PATTERNS/05).
