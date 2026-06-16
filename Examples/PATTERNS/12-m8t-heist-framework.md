# 12 — M8T heist framework (real shipped GTAO-style missions, decompiled)

Mined from **M8T's actual heist mods** (Lombank, Uptown, StashHouse, GTA6, Nightclubs, Casino), decompiled
from the SHVDN DLLs with ILSpy. This is the **concrete, battle-tested mission framework** — the real-heist
specifics that ground `PATTERNS/11` and tell `mission_sense` exactly what to read. The headline: it's all
built from primitives we already have (blips, markers, ShowSubtitle, distance triggers, task natives).

> Decompiled source is git-ignored in `Examples/_decompiled/m8t-*`. These cards are our distilled writeup.

---

### Structure: one Script + onTick running PARALLEL int state machines (no enums)
**Category:** mission-scaffold
**Problem:** Run a long multi-objective heist without coroutines/enums.
**Method:** `public class XHeist : Script`; constructor only wires `Tick += onTick` (+ `KeyDown` sometimes); no `Interval` (every frame). All logic in one `onTick`. The state is plain `int` fields (`mai`, `nrob`, `tii`, `casinoindex`, `keyindex`) each driven by its own `switch`, all running every frame; a master counter is the spine, secondary counters run sub-branches in parallel. Cross-machine handoff = one machine sets another's index/flag (`keyactive=true; keyindex=0;`). AND-gates use bools (`if (key1 && key2)` before the finale). `index = -1` = disable that machine.
**Gotcha:** Stage numbers are non-contiguous (0→10→20, branch islands like 1011) and fields are overloaded — **follow the `x = <next>` assignments, not case order.** No enums = machine-named ints make order opaque; the transition function often owns the jump (e.g. `entern()` sets `mai=2`).
**Source:** all M8T mods (lombank/uptown/stashhouse/gta6/casino/nightclubs)

### Objective blip: sprite + COLOUR-AS-STATE + ShowRoute (what a sensor reads) ⭐
**Category:** objective-blip
**Problem:** Mark the current objective and encode its state in the blip.
**Method:** Shared `spawnblip(Vector3 pos, string name, int sprite, BlipColor color, bool shortrange, bool showroute, bool addtolist, List<Blip> list)` → `World.CreateBlip`, set Sprite/Color/Name, `ShowRoute=showroute`, add to a tracked `List<Blip>`. Conventions: **start/hub** blips use custom sprites (Uptown 500, Casino 431, Nightclub 93) + colour 50/53; **in-mission objective** blips are tiny `sprite 1` whose **COLOUR signals state**: `0` white = pending/not-yet-active, `2` = attack/destroy-this-now (+`IsShortRange=false` so it shows at any range), `66` = escape/deliver waypoint. `ShowRoute=true` (or a `waypoint:true` overload) draws the GPS line.
**Gotcha:** **Setting `Sprite` RESETS Color and Name** — set Sprite first. **Read `Blip.Color` not just existence** — colour is the objective's state. `ShowRoute=true` is the exact `DOES_BLIP_HAVE_GPS_ROUTE` signal `mission_sense` keys on. Blip names are literal strings, never GXT.
**Source:** uptown/lombank/stashhouse spawnblip; colours uptown L2347-2463, lombank L885-1198

### Ground marker + proximity + context key for every interaction/trigger
**Category:** trigger
**Problem:** Let the player trigger a step (start mission, enter, grab) by standing in a spot.
**Method:** Every interactable draws a cylinder each tick + distance-checks: `World.DrawMarker((MarkerType)1, pos, Vector3.Zero, Vector3.Zero, new Vector3(0.5f,0.5f,0.5f), Color.FromArgb(30,144,255))` (DodgerBlue), then `if (player.Position.DistanceTo(pos) < 1.5f) { DisplayHelp("Press ~INPUT_CONTEXT~ to …"); if (Game.IsControlJustPressed(2, (Control)51)) <do> }`. `(Control)51` = INPUT_CONTEXT (E). Coarse approach gates use ~17–90 m; context actions ~1.5–1.8 m.
**Gotcha:** Marker is drawn unconditionally; the help prompt + key check live INSIDE the distance test. Markers are immediate-mode — re-draw every frame. Mission START is just a trigger blip + this marker/distance/key (no menu, no phone). The "keyboard" build swaps `Game.IsKeyPressed(Keys.E)`.
**Source:** casino casinoindex case 0 L1613; stashhouse L1116-1135; gta6 L1546

### Objective TEXT: long-duration ShowSubtitle (colour-coded) + the help-text native chain
**Category:** objective-text
**Problem:** Keep the current objective on screen + show contextual key prompts.
**Method:** Two channels, both LITERAL strings (never GXT): (1) Persistent objective = `UI.ShowSubtitle("Shoot the main ~g~gate lock.", 100000)` / `"Drill the ~g~door lock."` / `"Grab the ~g~loot."` / `"Kill the ~r~Security."` / `"Leave the ~y~bank."` / `"Lose the cops."` / `"~g~Heist passed."` — re-issued every tick with an absurd duration so it sticks. (2) Context prompt via the help chain: `BEGIN_TEXT_COMMAND_DISPLAY_HELP("STRING")` → `ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME(text)` → `END_TEXT_COMMAND_DISPLAY_HELP(0,0,1,-1)` — e.g. "Press ~INPUT_CONTEXT~ to enter the bank.", "Hold ~INPUT_ENTER~ to blow the vault.".
**Gotcha:** **Colour tags carry semantic meaning a sensor could parse:** `~g~`=loot/objective-target, `~y~`=location, `~r~`=enemy, `~b~`=teller, `~m~`=coke, `~p~`=diamonds, `~w~`=white. No dedicated objective native — it's just a long subtitle, so it must be re-called each tick or it's overwritten. **Objective text is NOT readable back via natives** (set-only) — a sensor gets it from the blip, not the text.
**Source:** all mods; help wrapper DisplayexitDCHstarthelp; subtitle strings uptown/casino/nightclubs

### Completion tests: distance, anim-phase>0.99, IsDead, WantedLevel==0, loot count
**Category:** completion
**Problem:** Decide when a stage is done.
**Method:** Per-stage predicate in onTick: reached → `player.Position.DistanceTo(pos) < 1.5f`; left area → `> 100f`; scripted beat done → `GET_SYNCHRONIZED_SCENE_PHASE(sc) > 0.99f` or `GET_ENTITY_ANIM_CURRENT_TIME(...) > 0.99f`; target killed → `((Entity)ped).IsDead`; loot done → `lootcollected == N`; heist "passed" → `WantedLevel == 0` after looting + left area. On success: delete that blip, set the next index.
**Gotcha:** **"Heist passed" needs BOTH leaving the area AND `WantedLevel==0`.** `GET_SYNCHRONIZED_SCENE_PHASE > 0.99` is THE canonical "is this scripted beat finished" gate the nested machine hangs on. Player is set `IsInvincible=true` during scripted anims so they can't fail mid-beat.
**Source:** stashhouse L1092-1491; uptown L2496-2651; nightclubs tl1i L2333

### Timer beats with Game.GameTime (the "5 minutes or cops" mechanic)
**Category:** fail
**Problem:** Time-limit a stage and react if it expires.
**Method:** Stamp a deadline `int deadline = Game.GameTime + ms;` then each tick `if (Game.GameTime > deadline) <react>` — e.g. raise wanted: `Game.Player.WantedLevel = N;` (M8T force-sets `Game.Player.WantedLevel = wnt` to script cop response). `Game.GameTime` is the engine ms clock (pauses with the game). GTA6 reuses int fields as both stage and timestamp (`iint = Game.GameTime + 30000`).
**Gotcha:** Use `Game.GameTime`, NOT wall-clock. M8T scripts the cop response by directly setting WantedLevel (and saves/restores `Game.MaxWantedLevel`/a `wantsave` field so cops don't derail other stages). This card is the literal mechanic for "5 minutes to get out or cops are called."
**Source:** gta6 wantsave/iint L1419-1438; uptown wnt; stashhouse wanted handling

### Fail = death → inline cleanup → reset (NO Aborted handler — a flaw to fix)
**Category:** fail
**Problem:** What happens on death / abandon.
**Method:** No "Mission Failed" stage. Top of onTick: `if (mai > 1 && Game.Player.Character.IsDead) { Script.Wait(3000); clearall(); init=true; mai=1; }` — tears down spawned entities, rewinds to the hub. "Lose the cops" is a STALL state (`while WantedLevel != 0` keep printing the subtitle), not a fail.
**Gotcha:** **None of the M8T mods wire `Script.Aborted`** — so reloading the script mid-heist while alive LEAKS every prop/ped/blip permanently. PATTERNS/11 has the fix (wire Aborted → Teardown); our own mission runner MUST do this.
**Source:** all mods (death handler at onTick top)

### Cutscene beats: fade+teleport+wait, or native REQUEST_CUTSCENE / SYNCHRONIZED_SCENE
**Category:** cutscene
**Problem:** Cinematic cuts and scripted story/loot animations.
**Method:** (a) Cheap cut (no free-cam): `Game.Player.CanControlCharacter=false; Game.FadeScreenOut(1000); Wait(1000); SET_ENTITY_COORDS_NO_OFFSET(...); Wait(1000); Game.FadeScreenIn(1000); CanControlCharacter=true;`. (b) Real R* cutscene: `REQUEST_CUTSCENE("xm4_rob_cas_mcs1", 8)` → bind peds `SET_CUTSCENE_PED...(ped,"MP_1")` → `START_CUTSCENE(0)` → poll `IS_CUTSCENE_ACTIVE` for done (save/restore outfit, delete cs-only peds). (c) Loot anim: `REQUEST_ANIM_DICT` → `CREATE_SYNCHRONIZED_SCENE` → `TASK_SYNCHRONIZED_SCENE`(ped) + `PLAY_SYNCHRONIZED_ENTITY_ANIM`(prop) → advance on `GET_SYNCHRONIZED_SCENE_PHASE > 0.99`. Interior via `REQUEST_IPL("vw_casino_main")`.
**Gotcha:** No M8T mod uses scripted free-cameras — cuts are fade+teleport. During beats they re-assert `DisableControlThisFrame` (sprint 21/attack 24/257) every frame + `IsInvincible`. A "spam to speed up" loot mechanic bumps `SET_SYNCHRONIZED_SCENE_RATE` per `IsControlJustPressed(223)`. (These beats are what a reflex agent can't do — the gap.)
**Source:** casino L955-976; gta6 cutscene L2381-2404; nightclubs SYNCHRONIZED_SCENE L1355-2333

### Reward: accumulate totaltake → pay once on clean getaway
**Category:** reward
**Problem:** Pay only after a successful escape, with randomized loot value.
**Method:** `int totaltake` accumulates per grab (randomized via `GET_RANDOM_INT_IN_RANGE` tiers, e.g. Cash +5257 / Gold +14257 / Cocaine +9557), then ONE payout at the terminal stage when `WantedLevel==0` + player alive + left area: `Game.Player.Money += totaltake; UI.ShowSubtitle("~g~Heist passed.", 11000); clearheist();`. Casino is a flat `Money += 5250000` at the buyer. Loot grab plays `PLAY_SOUND_FRONTEND(-1, "ROBBERY_MONEY_TOTAL", "HUD_FRONTEND_CUSTOM_SOUNDSET", 1)`.
**Gotcha:** Money is a lump at the end — dying after looting but before `WantedLevel==0` forfeits everything (`totaltake` resets to 0 on re-entry). Casino loot grabs are cosmetic; the whole payout is hardcoded at the buyer.
**Source:** uptown totaltake L1558-2152, payout L2646; casino L1038; nightclubs L1252

### Entity tracking Lists + clear*() teardown (the cleanup contract)
**Category:** cleanup
**Problem:** Remove every spawned prop/ped/vehicle/blip on reset/complete.
**Method:** Everything spawned goes into typed `List<>` fields (`proplist`, `pedlist`, `coppeds`, `vehlist`, `objblips`, `lootlist`) via `spawn*tolist(..., list)` helpers. Teardown family `clearpeds()/clearblips()/clearcars()/clearinterior()/clearheist()/clearall()` iterate: `if (DOES_ENTITY_EXIST(item)) ((Entity)item).Delete();`, `if (DOES_BLIP_EXIST(b)) b.Remove();`, then `list.Clear()`. Persistent map markers + `permlist` exterior props are excluded so they survive resets.
**Gotcha:** Funnel complete AND fail through the same `clearall()`. Exclude the hub/start blip from `objblips` so it survives. (And wire `Aborted` — which M8T didn't.)
**Source:** uptown clear* L4913-5198; stashhouse clearall L2684

### Mission selection: walk-in trigger blips, no menu (the open-world hub)
**Category:** mission-select
**Problem:** Offer multiple missions without a UI menu.
**Method:** Each mission = named world blips scattered on the map; a shared idle hub state (`mai=1`). Multiple missions run independent `switch(mai)`/`switch(mai2)` in the same tick, all sharing the `mai=1` hub. The "selected" mission is simply whichever trigger marker the player walks into and confirms with E. No NativeUI/LemonUI/phone anywhere.
**Gotcha:** Two state machines sharing one idle value is the dispatch trick — there's no "selected mission" variable. The death/cleanup handler gates on `mai > 1` so it only fires when a mission is actually active.
**Source:** nightclubs switch mai L1179 + mai2 L1823

### The "framework" is copy-pasted helpers (no base class) — name them
**Category:** shared-base
**Problem:** What's the reusable scaffold to mirror when authoring a mission.
**Method:** No common base class — M8T duplicates the same helpers in every mod: `spawnblip(...)`, `spawnproptolist(rdr)(...)`, `spawnpedtolist(...)`, `entern(pos,hed)`/`tp(...)` (fade+IPL+teleport into the interior, sets the next stage), `DisplayexitDCHstarthelp(string)` (help-text wrapper), and the `clear*()` teardown family. Settings persist via a P/Invoke `IniFile` (`scripts\\X.ini`) for loot type / blip id; progress is all in-memory (no save-game).
**Gotcha:** This is the exact set of operations our mission runner needs: set objective blip+marker+text, spawn tracked entities, detect completion, advance, clean up. We can do all of it through the bridge (blips/markers/spawn/wanted natives + world_sense/mission_sense for completion).
**Source:** all M8T mods
