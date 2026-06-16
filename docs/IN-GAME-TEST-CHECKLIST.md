# In-game TEST checklist — run this when you're home

Everything below was built + logic-tested offline this session but needs **in-game verification** (it calls
real natives / drives the player). Work top to bottom — each layer builds on the one above, so a failure
high up explains failures below it. For each item: do the action, check the result, tick the box. **If it
fails, tell me: the item number + what actually happened + any error text** (and `get_crash_logs` if GTA
crashed) — that's enough for me to fix it.

Safe order: reads → writes → single actions → autonomous. Nothing here is destructive; spawns/missions
clean themselves up.

## 0. Setup (do once)
- [ ] GTA V running (single-player), bridge loaded with **F9**. (If a module changed, delete `pyscript/__pycache__` then F9.)
- [ ] In the F11 panel or host log, confirm these loaded with NO error: `world_sense`, `agent_actions`,
      `mission_sense`, `gta_catalog`, `gta_recipes`, `vehicle_tuning`. (If one says "load failed", item 0 fails — send me the line.)
- [ ] `status` returns success (bridge alive).

## 1. Sensing — "does it know where it is + what's happening?"
- [ ] **`get_world_state`** — read it standing somewhere. Check: player `x/y/z` look right, `health`,
      `wanted`, on-foot/`vehicle`, and a readable `summary` line. *(If pos/health are wrong → the memory
      offsets need adjusting; that's isolated in `world_sense.gather_player`.)*
- [ ] **`describe_location`** — returns a nearest landmark + bearing for where you are.
- [ ] **`world_events`** — take some damage / gain a wanted star, then call it. Expect `took_damage` /
      `wanted_changed` events.
- [ ] **`get_objective`** — set a **map waypoint** (or be on a mission), then call it. Expect an `objective`
      with `coords` and `has_route: true`. *(This is the sensor that lets it follow missions.)*

## 2. Catalog + recipes (quick data sanity)
- [ ] **`resolve` "police car"** → returns a vehicle hash. `resolve "ak47"` → a weapon hash.
- [ ] **`recipe_search` "attach prop to bone"** → returns the prop+bone recipe.

## 3. Acting — the Executor verbs (single actions)
- [ ] **`act` `walk_to`** with coords ~30m away (on foot) → the player walks/runs there.
- [ ] **`act` `drive_to`** with coords (while in a vehicle) → the player drives there.
- [ ] **`act` `wander`** (on foot) → the player wanders; **`act` `stop`** → stops.
- [ ] **`act` `engage`** near a hostile / **`act` `flee`** → fights / flees.
      *(If a verb errors with "native not available", tell me which — the task native name needs a tweak.)*

## 4. Vehicle tuning (ExtendedLSC-relevant)
- [ ] **`set_wheel_fitment`** `track_front 0.05 camber_front -0.1` (in a car) → wheels widen/lean; re-call to persist.
- [ ] **`get_handling`** → reads CHandlingData floats. **`set_handling` `initial_drive_force 0.5`** → car feels different.
      *(If handling reads look like garbage, the `+0x960` candidate offset didn't validate on your build — tell me.)*

## 5. The play-and-watch agent (autonomous)
- [ ] **`python tools/play_agent.py --goto <x> <y> <z>`** (dry-run) → narrates sensible decisions toward the point.
- [ ] **`... --go`** → the reflex agent actually drives there, fights when attacked, flees when hurt. Watch it.
- [ ] **`... --go --claude`** (needs Agent SDK + `claude /login`) → Claude sets goals; reflex executes. *(Optional.)*

## 6. The mission engine — the game master (`mission_runner.py`)
- [ ] **`python tools/mission_runner.py bank_heist`** (dry-run) → prints the 4 objectives + timer countdown.
- [ ] **`... bank_heist --go`** → run it and PLAY it yourself. Check, in order:
  - [ ] a routed **objective blip / checkpoint** appears at each stage (native presentation);
  - [ ] reaching the bank advances to the next objective;
  - [ ] **let the 5-min timer expire on purpose** → you get a wanted level (cops called);
  - [ ] finishing the escape clean → "Mission passed" + the run is logged.
- [ ] **`python tools/mission_runner.py bank_heist --review`** → shows the run you just did.
      *(If the checkpoint/objective-text don't SHOW but the logic advances → the native presentation needs the
      tweak; the run still works, it just looks bare. Tell me which native.)*

## 7. Generated content — "endless grounded stuff"
- [ ] **`python tools/scenario_gen.py --list`** → 17 themes, 22 objective types.
- [ ] **`python tools/scenario_gen.py --theme heist --location "Fleeca" --save fleeca`** → writes a scenario.
- [ ] **`python tools/scenario_gen.py --random --save tonight`** → a surprise grounded mission.
- [ ] **`python tools/mission_runner.py tonight --go`** → play a *generated* mission. Spot-check it makes sense at the place.

## 8. Combat/spawn objective execution (the new wired types)
- [ ] Generate an **assault** or **hit**: `scenario_gen.py --theme assault --save assault1`, then `mission_runner.py assault1 --go`.
  - [ ] the **eliminate/assassinate** objective **spawns hostiles** at the spot;
  - [ ] killing them all **advances** the objective;
  - [ ] on mission end, spawned peds/vehicles are **cleaned up** (no litter).
  *(If peds don't spawn → the `CREATE_PED` sequence needs a model-load tweak; tell me. Vehicles spawn via the
  proven `spawn_vehicle`, so a **destroy/chase/smash** theme is the more reliable spawn test.)*

## 9. Build-and-play, combined (`play_heist.py`)
- [ ] **`python tools/play_heist.py bank_heist`** (dry-run) → narrates BOTH `[DIRECTOR]` (objectives) and
      `[PLAYER]` (the actor's verbs) each tick.
- [ ] **`... bank_heist --go`** → the director sets up the heist AND the actor plays it. Watch it drive to the
      bank, hold during the grab, escape. → the "build a heist and play it" loop.
- [ ] **`... tonight --go`** → same, on a *generated* mission.

## 9b. Protagonist commentary (native voice reactions)
- [ ] `say` with `context "GENERIC_WAR_CRY"` → the protagonist shouts the real line. Try `say "GENERIC_CURSE_HIGH"`.
- [ ] `say "GENERIC_THANKS" voice "trevor"` → forces Trevor's voice. *(If a context is silent, it doesn't
      exist for that voice — tell me which, I'll swap it in `speech_contexts.json`.)*
- [ ] `comment` with `event "took_damage"` → a fitting reaction; call it twice fast → 2nd is skipped (cooldown).
- [ ] During a `play_heist.py ... --go` run: the protagonist should react at mission start, on objectives, on
      the timer/cops, and on pass/fail — and to combat. *(This is the live commentary on the heist.)*

## 10. Custom animation pipeline (only if Blender/Sollumz + CodeWalker.API are installed)
- [ ] **`python tools/author_animation.py --preflight`** → reports which tools are present.
- [ ] If present: a `--blend ... --go` run exports → packages → injects → plays a custom clip. *(Optional/advanced.)*

---

### What to send me back
For anything that fails: **"item N — <what happened> — <error text>"**. The most useful single thing is
**item 1 (`get_world_state`)** and **item 6 (`mission_runner --go`)** — if those two work, the whole stack
is close, and the rest is small native-name/offset tweaks I can do fast from your report.
