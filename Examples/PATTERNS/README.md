# PATTERNS — distilled "how GTA V actually works" cards

> Knowledge mined from real, shipping mods (read as clean source — no decompiler). Each **card** is a
> reusable technique: *problem → method (exact natives/offsets/arg order) → the gotcha that isn't in
> any doc*. This is the distilled output of the harvest pipeline — **not** the mods' code (that stays
> git-ignored in `_sources/`). These files ARE committed; they're our own writeup with attribution.

**For Claude:** when a task touches one of these areas, read the relevant file first — these cards
encode the ordering rules and gotchas that otherwise cause silent failures or crashes. They complement
`CLAUDE.md` (bridge usage), `STUDIES.md` (capability map), and `DISCOVERIES.md` (this project's own RE).

## Files
| File | Covers | Mined from |
|---|---|---|
| [01-native-invocation-and-memory.md](01-native-invocation-and-memory.md) | calling natives, arg/return marshalling, pools, handles↔addresses, AOB/RIP resolve idioms, model hash table, globals, save format | SHVDN, gta5view |
| [02-vehicle-dynamics.md](02-vehicle-dynamics.md) | drivetrain memory, wheel walk, handling ptr, pattern-scanned offsets, re-assert, patch-the-writer, wheel fitment | ManualTransmission, HandlingEditor, VStancer |
| [03-ui-hud-scaleform.md](03-ui-hud-scaleform.md) | drawing, text commands, texture streaming, resolution/safezone, controls/input, scaleform, notifications, sound | LemonUI, NativeUI, RAGENativeUI, MenuBase |
| [04-spawning-world-scenes.md](04-spawning-world-scenes.md) | model streaming, create/persist/delete entities, attachment, ground-snap, the Spooner scene schema | Menyoo, MapEditor |
| [05-animation.md](05-animation.md) | TASK_PLAY_ANIM flow + flags, scenarios, synced scenes, facial, clipsets, **prop+bone+anim**, emote DB | rpemotes, Menyoo, SHVDN |
| [06-camera.md](06-camera.md) | scripted cams, RENDER_SCRIPT_CAMS, follow/attach (skate cam), interp, DOF, shake, gameplay-cam seed | SHVDN, Menyoo |
| [07-custom-animation-authoring.md](07-custom-animation-authoring.md) | **make your OWN anims**: .ycd data model, track-id→channel map, bone-by-hash, mover/root-motion, clips/tags, retarget | Sollumz |
| [08-ptfx-particles.md](08-ptfx-particles.md) | particle effects: asset load + per-spawn rebind, looped/non-looped at coord/entity/bone, live control, name pairs | Menyoo, ptfx-mod, rpemotes |
| [09-ped-ai-tasks.md](09-ped-ai-tasks.md) | ped AI: move/nav, drive (style flags), combat, flee, **task sequences (ped=0)**, make-tasks-stick, task status | SHVDN, Menyoo |
| [10-relationships-combat-squads.md](10-relationships-combat-squads.md) | factions (relationship groups, both directions), combat tuning (attribute IDs), bodyguards, "why won't my ped fight" | GangWar, Menyoo, SHVDN |
| [11-mission-framework.md](11-mission-framework.md) | **mission state machine**: Tick loop, stage enum, objective blip+route+marker, completion/fail, entity cleanup, rewards | GangWar, SHVDN |
| [12-m8t-heist-framework.md](12-m8t-heist-framework.md) | **real shipped heist framework** (decompiled): parallel state machines, blip colour-as-state, objective text, timer→cops, cutscene/reward/cleanup | M8T heists |
| [13-objective-types.md](13-objective-types.md) | **catalog of ~28 objective types** from R*'s real scripts (goto/eliminate/destroy/collect/chase/defend/transport/...) + real detect/setup natives | decompiled R* scripts |

## Cross-cutting rules that show up everywhere (read these once)
1. **Immediate-mode UI:** every `DRAW_*` / scaleform paints for ONE frame. Re-issue every tick.
2. **Streaming is async:** `REQUEST_*` then poll `HAS_*_LOADED` on the draw/create path — never use on the request frame.
3. **Memory writes get overwritten:** the sim re-derives physics each frame. Re-assert every tick, or patch the writer instruction.
4. **Never hardcode offsets:** pattern-scan a code signature and read the disp32 out of the matched instruction, so it survives game patches. Fail LOUD on no-match.
5. **Strings into scaleform/text are a 3-native sandwich:** `BEGIN_TEXT_COMMAND_*` → `ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME` → `END_TEXT_COMMAND_*`. Split >~98 UTF-8 bytes.
6. **Disabled controls need the disabled readers:** after `DISABLE_*`, read with `IS_DISABLED_CONTROL_*`, not `IS_CONTROL_*`.

*Cards corroborated by multiple mods are flagged "seen in N mods" — higher confidence. Re-verify any
offset/AOB against the live build before trusting it (same rule as STUDIES.md).*
