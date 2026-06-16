# 05 — Animation, scenarios, synced scenes, facial, clipsets, prop-anims

Mined from **rpemotes-reborn** (the best-labeled emote DB), **Menyoo** (`PedAnimation.cpp` + `PedAnimList.txt`),
and **SHVDN** (`TaskInvoker`, `AnimationFlags`, `FwSyncedScene`). This is the gap the skateboard needs —
note the **prop+bone+anim** card especially. Animation = wrapping the load-dict→`TASK_PLAY_ANIM` flow.

---

### Load anim dict then play a clip (the core flow)
**Category:** animation
**Problem:** Play any (dict, clip) on a ped reliably — the dict must be streamed before TASK_PLAY_ANIM or the ped does nothing.
**Method:** 1) `REQUEST_ANIM_DICT(dict)`. 2) Poll `HAS_ANIM_DICT_LOADED(dict)` in a yield loop until true (~1-2s timeout). 3) `TASK_PLAY_ANIM(ped, dict, clip, blendIn, blendOut, duration, flags, startPhase, phaseControlled, ikFlags, allowOverride)` — SHVDN uses `8.0, -8.0, -1, flags, 0.0, false, 0, 0`; rpemotes `5.0, 5.0, dur|-1, flags, 0, false,false,false`. 4) `REMOVE_ANIM_DICT(dict)` is safe right after (the running task keeps its own ref).
**Gotcha:** `duration = -1` = play full clip once unless the Loop flag is set. `startPhase` is 0.0-1.0 (fraction), NOT ms. Blend-delta sign is ignored by the game (+8/-8 and +5/+5 both work).
**Source:** shvdn-api/source/scripting_v3/GTA/Entities/Peds/Task/TaskInvoker.cs; rpemotes/client/Utils.lua

### eScriptedAnimFlags bit values and common combos
**Category:** animation
**Problem:** Build the `flags` int for the behavior you want (loop / upper-body / hold-pose / secondary).
**Method:** Sum bits: `1` Loop, `2` StayInEndFrame(hold), `4` RepositionWhenFinished, `8` NotInterruptable, `16` UpperBodyOnly, `32` Secondary(runs alongside movement), `64` ReorientWhenFinished, `128` AbortOnPedMovement, `256` Additive, `512` TurnOffCollision, `1024` OverridePhysics, `2048` IgnoreGravity, `1048576` HideWeapon. Common: looped idle `1`; hold pose `1+2=3`; looping upper-body gesture while walking `1+16+32=49`; rpemotes "moving" emote `51` (1+2+16+32 = loop upper body without freezing the legs).
**Gotcha:** rpemotes' own `AnimFlag` enum (LOOP=1, MOVING=51, STUCK=50) is a convenience set, not the raw flags — `51` is the go-to for "gesture while still walking."
**Source:** shvdn-api/.../Peds/AnimationFlags.cs; rpemotes/types.lua, client/Emote.lua

### Positioned playback with TASK_PLAY_ANIM_ADVANCED
**Category:** animation
**Problem:** Play a clip at an exact world position+orientation (snap a ped to a mark, or sync several at one spot).
**Method:** Load the dict yourself first, then `TASK_PLAY_ANIM_ADVANCED(ped, dict, clip, x,y,z, rotX,rotY,rotZ, blendIn, blendOut, timeToPlay, flags, startPhase, rotationOrder, ikFlags)`. SHVDN: `timeToPlay=-1, flags=0, startPhase=0, rotOrder=2(YXZ), ikFlags=0`. Rotation in degrees (pitch, roll, heading).
**Gotcha:** Does NOT auto-load the dict. To sync several peds, give them all the SAME pos/rot + flags `4096(ExtractInitialOffset)+1024(OverridePhysics)` so each uses its authored offset from the common origin.
**Source:** shvdn-api/.../Peds/Task/TaskInvoker.cs

### Ambient scenarios (in place / at position)
**Category:** scenario
**Problem:** A long looping ambient behavior (lean, smoke, guard, cop idle, drink) with built-in enter/exit + optional prop — no dict loading.
**Method:** `TASK_START_SCENARIO_IN_PLACE(ped, scenarioName, timeToLeave, playEnterAnim)` (rpemotes: `(ped, name, 0, true)`); or `TASK_START_SCENARIO_AT_POSITION(ped, name, x,y,z, heading, timeToLeave, playEnterAnim, warp)`. Names like `WORLD_HUMAN_LEANING`, `WORLD_HUMAN_SMOKING`, `WORLD_HUMAN_GUARD_STAND`, `WORLD_HUMAN_COP_IDLES`, `PROP_HUMAN_SEAT_CHAIR`.
**Gotcha:** `timeToLeave=0` = never auto-leave (stays until tasks cleared). Some scenarios are male-only. Exit cleanly with `CLEAR_PED_TASKS_IMMEDIATELY` — a plain ClearPedTasks can leave the enter/exit half-played.
**Source:** rpemotes/client/Emote.lua; shvdn-api/.../TaskInvoker.cs

### Synchronized scenes: create → task → drive phase
**Category:** synced-scene
**Problem:** Choreograph multiple peds/objects from one shared origin with manual phase scrubbing (cutscene-style).
**Method:** 1) Load the dict. 2) `sceneId = CREATE_SYNCHRONIZED_SCENE(x,y,z, rotX,rotY,rotZ, rotationOrder=2)` (-1 = fail). 3) Per ped: `TASK_SYNCHRONIZED_SCENE(ped, sceneId, dict, clip, blendIn, blendOut, flags, ragdollFlags, moverBlendIn, ikFlags)`. 4) Drive: `SET_SYNCHRONIZED_SCENE_PHASE(sceneId, 0..1)`, read `GET_SYNCHRONIZED_SCENE_PHASE`, optional `_LOOPED`/`_RATE`.
**Gotcha:** The game garbage-collects a synced scene the instant nothing references it — task a ped onto it the SAME frame you create it or `Exists()` flips false. Use this only when you need frame-accurate phase/paired sync; for simple paired emotes, prefer the cheaper paired-emote trick below.
**Source:** shvdn-api/.../FwSyncedScene.cs, TaskInvoker.cs

### Facial animation overlay
**Category:** facial
**Problem:** Add a facial expression/mood on top of the body anim.
**Method:** One-shot `PLAY_FACIAL_ANIM(ped, facialClip, facialDict)` (name THEN dict). Persistent mood: `SET_FACIAL_IDLE_ANIM_OVERRIDE(ped, animName, 0)`; clear `CLEAR_FACIAL_IDLE_ANIM_OVERRIDE(ped)`. Whole set: `SET_FACIAL_CLIPSET(ped, clipset)`. Moods: `mood_happy_1`, `mood_angry_1`, `mood_drunk_1`, `smoking_hold_1`.
**Gotcha:** Facial is independent of and survives the body task — clear it explicitly.
**Source:** rpemotes/client/Expressions.lua, Emote.lua; shvdn-api/.../Peds/Ped.cs

### Movement clipset (walk styles)
**Category:** clipset
**Problem:** Change how a ped walks/runs (swagger, injured, crouched) — a state, not a one-shot.
**Method:** `REQUEST_CLIP_SET(clipset)` + poll `HAS_CLIP_SET_LOADED`, then `SET_PED_MOVEMENT_CLIPSET(ped, clipset, blend)` (rpemotes blend 0.2). Reset `RESET_PED_MOVEMENT_CLIPSET(ped, blend)`. E.g. `move_ped_crouched`, `move_m@swagger`, `move_m@drunk@verydrunk`.
**Gotcha:** Clipsets load via `REQUEST_CLIP_SET`, NOT `REQUEST_ANIM_DICT`. The clipset is a persistent STATE — survives ClearPedTasks; reset it manually.
**Source:** rpemotes/client/Walk.lua, Crouch.lua; shvdn-api/.../Peds/Ped.cs

### Prop + bone + anim combo (how to build a skateboard/umbrella rider) ⭐
**Category:** prop-anim
**Problem:** Make a ped hold/use an object that rides a bone while an anim plays (board, umbrella, cup, box).
**Method:** 1) `REQUEST_MODEL(hash)` + poll. 2) `CreateObject(propHash, pedX,pedY,pedZ+0.2, isNetwork, false, false)`. 3) optional `SET_ENTITY_COLLISION(obj,false,false)`. 4) `ATTACH_ENTITY_TO_ENTITY(obj, ped, GET_PED_BONE_INDEX(ped, boneId), offX,offY,offZ, rotX,rotY,rotZ, false, false, false, false, rotOrder=1, true)`. 5) load-dict→TASK_PLAY_ANIM. 6) `SET_MODEL_AS_NO_LONGER_NEEDED`. On stop: delete object, then ClearPedTasks. Schema per prop: `Prop`(model), `PropBone`(id), `PropPlacement={offX,offY,offZ,rotX,rotY,rotZ}` (+ optional Second* set).
**Gotcha:** Common bone ids: right hand `28422` (PH_R_Hand), left hand `18905`/`60309`. **For a skateboard, attach the board to a foot/root bone (e.g. SKEL_Root/spine) with a downward Z offset and play a balancing loop.** Create the prop at the ped's coords FIRST, then attach — attaching a far-away object glitches one frame.
**Source:** rpemotes/client/Emote.lua (addProp), client/AnimationList.lua (PropEmotes)

### Paired / shared emotes (handshake, hug) without a synced scene
**Category:** animation
**Problem:** Two peds interact in matched anims facing each other.
**Method:** Play a normal clip on each ped from the same dict (e.g. `mp_ped_interaction` → `handshake_guy_a` + `handshake_guy_b`) and position the partner via sync offsets, NOT a synced scene. Fields: `secondPlayersAnim`, `SyncOffsetFront` (1.0), `SyncOffsetSide` (0.0), `SyncOffsetHeight` (0.0), `SyncOffsetHeading` (180 = face each other).
**Gotcha:** Cheap, robust alternative to CREATE_SYNCHRONIZED_SCENE — no scene-id lifetime to manage. Offsets in meters relative to the initiator's facing. Use a real synced scene only for exact phase lock.
**Source:** rpemotes/client/AnimationList.lua (RP.Shared), types.lua

### Scrub and re-speed a running anim
**Category:** animation
**Problem:** Pause on a frame, jump to a phase, fast-forward/slow-mo an anim already playing.
**Method:** `SET_ENTITY_ANIM_SPEED(entity, dict, clip, mult)` (0=freeze, 1=normal, 2=double); `SET_ENTITY_ANIM_CURRENT_TIME(entity, dict, clip, phase)` (phase 0-1, NOT seconds); read `GET_ENTITY_ANIM_CURRENT_TIME` (0-1, wraps on loop).
**Gotcha:** Addresses the anim by name, so the exact (dict,clip) must still be the active task. Speed 0 is the clean dynamic "hold pose" (vs baking the `StayInEndFrame=2` flag).
**Source:** shvdn-api/.../Entities/Entity.cs

### Stopping anims: targeted vs nuke
**Category:** animation
**Problem:** End an anim with a blend-out, or hard-clear everything.
**Method:** Blend out one: `STOP_ANIM_TASK(ped, dict, clip, -4.0)`. Clear all (blended): `CLEAR_PED_TASKS(ped)`. Instant: `CLEAR_PED_TASKS_IMMEDIATELY(ped)`. In a vehicle prefer `CLEAR_PED_SECONDARY_TASK` then `CLEAR_PED_TASKS`.
**Gotcha:** STOP_ANIM_TASK is anim-task only — scenarios need ClearPedTasks(Immediately); clipsets need RESET_PED_MOVEMENT_CLIPSET; facial needs CLEAR_FACIAL_IDLE_ANIM_OVERRIDE; attached props must be deleted separately.
**Source:** shvdn-api/.../TaskInvoker.cs; rpemotes/client/Emote.lua

### Detect whether an anim is actually playing
**Category:** animation
**Problem:** Know when a one-shot finished, or confirm a clip is running (to drive props/cleanup).
**Method:** `IS_ENTITY_PLAYING_ANIM(entity, dict, clip, taskFlag=3)`. Pattern: wait until true (started), then loop while true; when false → ended → clean up.
**Gotcha:** Right after tasking it can be false for a frame or two (blend-in) — wait for true before treating false as "finished," or you instantly cancel.
**Source:** rpemotes/client/Emote.lua; menyoo/.../PedAnimation.cpp

### The emote-DB schema to adopt
**Category:** anim-db
**Problem:** A reusable, AI-friendly data model for labeled emotes (rpemotes' — the best-labeled).
**Method:** Per entry: `dict`, `anim` (clip), `label`, `emoteType` (Emotes/PropEmotes/Dances/Shared/Walks/Expressions/Exits); OR `scenario`+`scenarioType` for scenarios. `AnimationOptions`: `Flag`, `EmoteDuration`(ms, -1=full), `BlendIn/Out`, `Prop`+`PropBone`+`PropPlacement[6]` (+Second*), `ExitEmote`, `SyncOffset*` (paired), `PtfxAsset/Name/Placement`.
**Gotcha:** A key can be an anim OR a scenario — disambiguated by `scenario`/`scenarioType` vs `dict`/`anim`. Prefer the explicit named fields over the legacy positional `[1..4]` form. (Seeded into `pyscript/catalogs/anims.json`.)
**Source:** rpemotes/types.lua, client/AnimationList.lua

---

## ~20 ready (dict, clip / scenario) examples for common actions

| Action | Dict (or "scenario") | Clip / Scenario | Label |
|---|---|---|---|
| Lean | scenario | `WORLD_HUMAN_LEANING` | Lean |
| Smoke | scenario | `WORLD_HUMAN_SMOKING` | Smoke |
| Hands up | `random@arrests` | `idle_2_hands_up` | Hands Up |
| Push-ups | `amb@world_human_push_ups@male@base` | `base` | Push ups |
| Sit-ups | `amb@world_human_sit_ups@male@base` | `base` | Sit ups |
| Hip-hop dance | `missfbi3_sniping` | `dance_m_default` | Hood Dance |
| Private dance | `mini@strip_club@private_dance@part1` | `priv_dance_p1` | Private Dance |
| Medic kneel (≈CPR) | scenario | `CODE_HUMAN_MEDIC_KNEEL` | Kneel |
| Cop idle | scenario | `WORLD_HUMAN_COP_IDLES` | Cop |
| Guard stand | scenario | `WORLD_HUMAN_GUARD_STAND` | Guard |
| Jog in place | `amb@world_human_jog_standing@male@fitbase` | `base` | Jog in Place |
| Sleep on ground | `mp_sleep` | `bind_pose_180` | Lie / Sleep |
| Wave arms | `random@car_thief@victimpoints_ig_3` | `arms_waving` | Wave Arms |
| Come here | `gestures@m@standing@fat` | `gesture_come_here_hard` | Come Here |
| Surrender | `random@arrests@busted` | `idle_a` | Surrender |
| Umbrella (prop) | `amb@world_human_drinking@coffee@male@base` | `base` | Umbrella (prop `p_amb_brolly_01`, bone 28422) |
| Box carry (prop) | `anim@heists@box_carry@` | `idle` | Box (prop `hei_prop_heist_box`, bone 60309) |
| Notepad+pencil (2 props) | `missheistdockssetup1clipboard@base` | `base` | Notepad (`prop_notepad_01` b18905 + `prop_pencil_01` b58866) |
| Handshake (paired) | `mp_ped_interaction` | `handshake_guy_a`/`_guy_b` | Handshake (SyncOffsetFront 0.9) |
| Crouch walk (clipset) | clipset | `move_ped_crouched` | crouched movement (blend 0.6) |

*Full searchable dict→clip pairs also live in `menyoo/.../menyooStuff/PedAnimList.txt`.*
