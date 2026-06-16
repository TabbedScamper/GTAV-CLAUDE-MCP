# 10 — Relationships, combat tuning & squads

Mined from **GTAV-SimpleGangWar** (the canonical two-faction example), **Menyoo** (relationships,
bodyguards, combat), and **SHVDN** (the CombatAttributes/FleeAttributes enums). Two prizes:
`SET_RELATIONSHIP_BETWEEN_GROUPS` is one-directional, and **Hate alone doesn't make peds fight** —
`SET_BLOCKING_OF_NON_TEMPORARY_EVENTS` does.

---

### Create two hostile factions (full relationship-group recipe) ⭐
**Category:** relationship
**Problem:** Two groups that fight each other on sight.
**Method:** 1) `ADD_RELATIONSHIP_GROUP("name", &groupHash)` per group — it writes the hash through the out-pointer (init hash=0). 2) `SET_RELATIONSHIP_BETWEEN_GROUPS(relationship, group1, group2)` then **again with group1/group2 SWAPPED**. Use `5` (Hate). 3) `SET_PED_RELATIONSHIP_GROUP_HASH(ped, groupHash)` per ped. Relationship ints: 0 Companion, 1 Respect, 2 Like, 3 Neutral, 4 Dislike, 5 Hate.
**Gotcha:** `SET_RELATIONSHIP_BETWEEN_GROUPS` is ONE-DIRECTIONAL — set only A→B and the war is lopsided. Call it twice, swapped. Also set each group's relationship to ITSELF to Respect (`SET_RELATIONSHIP_BETWEEN_GROUPS(1, g, g)`) or allies infight.
**Source:** menyoo/.../Scripting/World.cpp; gangwar/SimpleGangWar.cs

### Friendly/hostile to the player via the player's group
**Category:** relationship
**Problem:** Decide whether a ped treats the player as ally or enemy.
**Method:** Get the player group hash (SHVDN `Game.Player.Character.RelationshipGroup`), then `SET_RELATIONSHIP_BETWEEN_GROUPS(5, enemyGroup, playerGroup)` + swapped for hostile, or `1` (Respect) both ways for friendly.
**Gotcha:** Relationship group governs who-hates-who ONLY; following is a separate ped-group system (bodyguard card). A ped can Respect the player and still not follow.
**Source:** gangwar/SimpleGangWar.cs

### Force a ped to actually fight (the "why won't my ped attack" fix) ⭐
**Category:** aggression
**Problem:** Ped is hostile by relationship but just stands, flees, or ignores enemies.
**Method:** All three: 1) `SET_BLOCKING_OF_NON_TEMPORARY_EVENTS(ped, true)` — stop ambient AI overriding combat with idle/flee. 2) `SET_PED_FLEE_ATTRIBUTES(ped, 0, 0)` to clear flee (or bit 512 NeverFlee enabled). 3) `SET_PED_COMBAT_ATTRIBUTES(ped, 46, true)` (+ `5` AlwaysFight). Then task `TASK_COMBAT_HATED_TARGETS_AROUND_PED(ped, radius, 0)`. Also `SET_PED_KEEP_TASK(ped, true)`.
**Gotcha:** Relationship Hate alone is NOT enough — ambient non-temporary events (flee/cower/wander) preempt combat every few seconds. `SET_BLOCKING_OF_NON_TEMPORARY_EVENTS=true` is the single most important call.
**Source:** menyoo/.../Menu/Routine.cpp; gangwar/SimpleGangWar.cs

### Key combat-attribute IDs for SET_PED_COMBAT_ATTRIBUTES
**Category:** combat-tuning
**Problem:** The exact attribute IDs that control cover, vehicles, aggression, accuracy.
**Method:** `SET_PED_COMBAT_ATTRIBUTES(ped, attributeId, enabled)` — one flag per call. IDs (SHVDN enum index): 0 CanUseCover, 1 CanUseVehicles, 2 CanDoDrivebys, 3 CanLeaveVehicle, 5 AlwaysFight, 13 Aggressive, 14 CanInvestigate, 17 AlwaysFlee, 21 CanChaseTargetOnFoot, 27 PerfectAccuracy, 34 CanShootWithoutLos, 46 CanFightArmedPedsWhenNotArmed, 52 CanCharge.
**Gotcha:** Each call toggles ONE bit — you can't OR them; loop per ID. Brawler-who-won't-run: enable 5 (AlwaysFight), disable 3 (CanLeaveVehicle), disable 17 (AlwaysFlee). 27 (PerfectAccuracy) overrides SET_PED_ACCURACY entirely.
**Source:** shvdn-api/.../Peds/CombatAttributes.cs; menyoo STSTasks.cpp

### Tune combat skill: ability, movement, range, accuracy, senses
**Category:** combat-tuning
**Problem:** Set how good and aggressive a fighter is.
**Method:** `SET_PED_COMBAT_ABILITY(ped, 0|1|2)` (Poor/Average/Professional). `SET_PED_COMBAT_MOVEMENT(ped, 0|1|2|3)` (Stationary/Defensive/Offensive/Suicidal). `SET_PED_COMBAT_RANGE(ped, 0|1|2)` (Near/Medium/Far). `SET_PED_ACCURACY(ped, 0-100)`. `SET_PED_SEEING_RANGE(ped, meters)` + `SET_PED_HEARING_RANGE(ped, meters)`. `SET_PED_FIRING_PATTERN(ped, patternHash)`.
**Gotcha:** Movement 0/3 "don't work as expected" (gang-war note). **For squads to engage across distance you MUST raise `SET_PED_SEEING_RANGE` to the inter-spawn distance** or far-apart sides never aggro. ability is 0–2 (engine clamps higher).
**Source:** gangwar/SimpleGangWar.cs; menyoo natives.h

### Spawn a battle-ready ped (full per-ped setup)
**Category:** squad
**Problem:** One repeatable recipe to spawn a fighter on a faction.
**Method:** 1) `CREATE_PED(...)`. 2) give weapon + ammo. 3) set health/armor/accuracy. 4) `SET_PED_RELATIONSHIP_GROUP_HASH(ped, faction)`. 5) `SET_PED_COMBAT_RANGE`/`_MOVEMENT`. 6) `SET_PED_COMBAT_ATTRIBUTES(ped, 46, true)` (+5 AlwaysFight). 7) `SET_PED_SEEING_RANGE(ped, spawnDistance)`. 8) `CLEAR_PED_TASKS_IMMEDIATELY`. 9) `SET_PED_KEEP_TASK(ped, true)`. 10) each tick: if idle, re-task toward enemies.
**Gotcha:** Set relationship group AND combat attributes BEFORE tasking, and **re-task idle peds every tick** — they finish/lose tasks and go idle mid-battle.
**Source:** gangwar/SimpleGangWar.cs

### Drive squads toward the enemy (offensive tasking)
**Category:** squad
**Problem:** Make a side advance and engage rather than camp.
**Method:** Per ped each tick if not fighting: `TASK_COMBAT_HATED_TARGETS_AROUND_PED(ped, radius, 0)`, OR run them across the map first (`TASK_GO_TO_COORD…`/RunTo enemy spawn) to get into LOS, OR `TASK_COMBAT_PED(ped, target, 0, 16)`. Set `SET_PED_COMBAT_MOVEMENT(ped, 2)` (Offensive) to advance while fighting.
**Gotcha:** `FightAgainstHatedTargets` only engages within `radius` AND seeing range — combine with a large seeing range. Run-to-spawn gets the sides into LOS so the hate relationship actually triggers combat.
**Source:** gangwar/SimpleGangWar.cs; menyoo Routine.cpp

### Bodyguards: add peds to the PLAYER's group so they follow
**Category:** squad
**Problem:** Spawned allies that follow the player.
**Method:** 1) `GET_PLAYER_GROUP(player)` → group id. 2) `SET_PED_AS_GROUP_MEMBER(ped, groupId)` (or `_GROUP_LEADER`). 3) `SET_PED_CAN_TELEPORT_TO_GROUP_LEADER(ped, groupId, true)`. 4) make them friendly via relationship (Respect both ways) so they don't attack you. Iterate: `GET_PED_AS_GROUP_MEMBER(groupId, n)`, `IS_PED_GROUP_MEMBER(ped, groupId)`.
**Gotcha:** **Ped groups (following) and relationship groups (hate/like) are SEPARATE systems** — group membership makes them follow but NOT friendly, and vice-versa. Set both. For a new `CREATE_GROUP`, add the leader the SAME frame or the engine GCs the empty group.
**Source:** menyoo/.../Scripting/GTAped.cpp; shvdn-api/.../PedGroup.cs

### Squad formation and spacing
**Category:** squad
**Problem:** How group members arrange around their leader.
**Method:** `SET_GROUP_FORMATION(groupId, formationType)` (0 Default, 1 Circle, 2 wedge, 3 line/column). `SET_GROUP_FORMATION_SPACING(groupId, x, y, z)` (Menyoo passes y,z = -1.0 = keep default). `RESET_GROUP_FORMATION_DEFAULT_SPACING(groupHandle)`.
**Gotcha:** Pass -1.0 (0xBF800000) for axes you want left at default. Formation applies to the whole group id, not per-ped.
**Source:** menyoo/.../Scripting/GTAped.cpp

### Flee-attribute bitflags (fine-grained "won't run" control)
**Category:** aggression
**Problem:** Stop or shape fleeing precisely.
**Method:** `SET_PED_FLEE_ATTRIBUTES(ped, attributeFlags, enable)` — BITWISE: enable=1 adds bits, enable=0 removes. Bits: 1 UseCover, 2 UseVehicle, 512 NeverFlee, 1024 DisableCover, 2048 DisableExitVehicle, 32768 CowerInsteadOfFlee, 65536 ForceExitVehicle. Clear all: `(ped, 0, 0)`.
**Gotcha:** Additive — `(ped, 0, 0)` clears all only via enable=0. For guaranteed no-flee enable bit 512 (NeverFlee) AND pair with `SET_BLOCKING_OF_NON_TEMPORARY_EVENTS(ped, true)`.
**Source:** shvdn-api/.../FleeAttributes.cs; menyoo Routine.cpp
