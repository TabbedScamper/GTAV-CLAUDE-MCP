# Grounded missions — endless content built the game's real way

> Goal: generate "almost endless stuff to do" that is GROUNDED — real game systems, real coordinates,
> nothing invented. This doc covers the honest scope finding, the real-native approach, and the generator.

## The scope finding (why we build, not port)
The GTA Online heist/mission DLC (`mpheist`, `mpsecurity`, the "Safe House"-style content) lives in the
`mp*` dlcpacks — it's **GTA Online** content that needs a live R* session/servers and **does not run in
single-player**. There is no native to "call" an Online mission in story mode; that's why every SP heist
mod (M8T included) is a C# *re-implementation*, not a port. Bringing Online content to SP is both
impossible in story mode and outside this project's single-player-only scope. So we don't port — we
**build SP missions using the game's own real systems**, which IS achievable and is the correct way.

## Built the game's real way (verified natives, not modder hacks)
`mission_runner.py` now presents objectives via the actual systems R*'s own missions use (all confirmed
callable in `native_db.json`), enabled per-scenario with `"native_presentation": true`:
- **`SET_MISSION_FLAG(true/false)`** — tells the engine a mission is active (affects ambient AI/autosave),
  like a real mission.
- **`ADD_BLIP_FOR_COORD` + `SET_BLIP_COLOUR` + `SET_BLIP_ROUTE`(+`_COLOUR`) + `CREATE_CHECKPOINT` /
  `DELETE_CHECKPOINT`** — a **colored objective blip with a GPS route line** (the real mission marker)
  plus the real 3D checkpoint. NOTE: do NOT use `SET_NEW_WAYPOINT` — that sets the *player's own*
  map-click waypoint (white, user-owned), not a mission objective marker. Verified in-game 2026-06-13.
- **`BEGIN_TEXT_COMMAND_PRINT` → `ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME` → `END_TEXT_COMMAND_PRINT`** —
  the real subtitle/objective text system.
- **`PLAY_MISSION_COMPLETE_AUDIO`** on pass; completion judged by real natives (`IS_ENTITY_AT_COORD`,
  `GET_DISTANCE_BETWEEN_COORDS`, `GET_PLAYER_WANTED_LEVEL`).
These are best-effort (called by name through the allowlist — wrong name -> safe error, never a crash) and
need in-game verification; the runner's logic core stays self-tested.

## The content generator — endless, grounded (`tools/scenario_gen.py`)
Composes scenarios from three grounded ingredients:
1. **Objective-type library** — each type maps to REAL natives and declares them: `goto`/`breach`/`deliver`
   (waypoint+checkpoint), `eliminate`/`survive` (ped spawn + combat + IS_PED_DEAD), `grab` (timer+sound),
   `steal_vehicle`, `escape` (wanted+area), `checkpoint` (race). Each objective carries a `native:[...]` list
   — the grounding proof.
2. **Real landmark coordinates** — pulled from `pyscript/world_data/landmarks.json`, so missions happen at
   real places (and jitter *near* them for variety).
3. **Themes** — `heist`, `assault`, `rampage`, `delivery`, `stickup`, `race`, `rescue` — each a sequence of
   objective types. Difficulty scales enemy counts, reward, and timer.

`7 themes × ~20 landmarks × difficulty × random seed = effectively endless`, and every result is a valid
`mission_runner`/`play_heist` scenario. `validate()` enforces grounding (every objective has a real
completion test + a real native; coord objectives sit near a real landmark).

```
python tools/scenario_gen.py --list                              # themes + objective types
python tools/scenario_gen.py --theme heist --location "Fleeca"   # a grounded heist at Fleeca
python tools/scenario_gen.py --random --save tonight             # surprise me -> scenarios/tonight.json
python tools/play_heist.py tonight --go                          # build + play it (game's real way)
```

## How Claude uses it ("give me something to do")
Claude can call `scenario_gen` (or write a scenario directly — it knows the schema + the real natives) to
produce a grounded mission on request, then `play_heist` runs it (director sets it up the game's way, the
actor plays it) and `mission_runner --review` shows what it learned. So "give me a bank heist" / "give me
something to do" → a real, varied, game-grounded mission every time.

## Status
- **Tested here:** the generator (all themes, determinism, difficulty, grounding, runner-compatibility),
  the runner's new completion predicate (`all_targets_dead`), pass/fail/timer, the learning log.
- **Needs in-game verification:** the native presentation (checkpoint/waypoint/print/mission-flag/audio) and
  the spawn execution for `eliminate`/`survive` objectives (the runner needs to spawn + track target handles
  to feed `targets_alive`) — a focused in-game wiring step on top of the tested logic.
