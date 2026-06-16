# Harvest brief — M8T's heist/mission mods (GTA Online missions → single player)

> M8T (gta5-mods.com/users/M8T) has ~18 **ScriptHookVDotNet .NET** heist/mission mods that bring
> GTAO-style missions to single player. They're compiled `.dll`s (no public source) but SHVDN .NET
> **decompiles back to near-perfect C#** with ILSpy. Studying 2-3 reveals the real mission framework —
> exactly the gap captured (as a general scaffold) in `PATTERNS/11-mission-framework.md`. This brief is
> the home-machine recipe to decompile them and hand the source back for mining.

## Why these are worth it (two payoffs)
1. **Mission-framework knowledge** — how a skilled modder structures a full multi-stage heist (briefing →
   travel → infiltrate → combat → grab loot → escape → reward), with objective sequencing, fail states,
   and cutscene/dialogue handling. Refines PATTERNS/11 from "general scaffold" to "battle-tested heist."
2. **Playable targets for the agent** — these missions set **objective blips with GPS routes**, which
   `mission_sense.py` already reads (`DOES_BLIP_HAVE_GPS_ROUTE`). So the agent can plausibly *follow*
   these missions. Decompiling shows exactly which blips/markers/objective-text they set → tells us what
   the sensor should read to track progress.

## Which to grab (3 — different complexity)
| Pick | Why |
|---|---|
| **The Diamond Casino Heist** (v1) | the most elaborate — multi-stage, likely the fullest state machine |
| **Lombank Heist** (v1.4) or **Uptown Bank Heist** (v1.2) | a mature, iterated *bank* heist — the core pattern |
| **Cypress Vice Smuggling / GTA6 Story Heist** (v0.3) | a "story" framed mission — objective/dialogue sequencing |

(Same author → they almost certainly share a base mission class; 2-3 is enough to see the reusable scaffold.)

## Decompile (home machine, ~5 min)
```
# one-time: the .NET decompiler CLI
dotnet tool install -g ilspycmd

# for each downloaded mod (the .dll inside the gta5-mods zip):
ilspycmd "TheDiamondCasinoHeist.dll" -o "Examples/_decompiled/m8t-casino"
ilspycmd "LombankHeist.dll"          -o "Examples/_decompiled/m8t-lombank"
ilspycmd "GTA6StoryHeist.dll"        -o "Examples/_decompiled/m8t-gta6"
```
`Examples/_decompiled/` is git-ignored (their copyrighted code — study only, never republish; we extract
*techniques* with attribution, per `Examples/README.md`). If a mod is obfuscated, run **de4dot** first.
Then tell me "the M8T source is in Examples/_decompiled/" and I'll mine it immediately.

## What I'll extract (the checklist — maps to PATTERNS/11)
For each mod, the cards I'll pull:
- **The mission class + lifecycle** — does it subclass `Script`? where's the `Tick` loop / `Interval`?
  the `Aborted` cleanup?
- **The stage state machine** — the enum/int of stages, the per-stage switch, how each advances. *(11: state-machine)*
- **Objective per stage** — the coord/entity, the blip (`AddBlip`/`CreateBlip`, `Sprite`, `ShowRoute`),
  the marker, the objective TEXT (`ShowSubtitle`/`ShowHelpText`/`BEGIN_TEXT_COMMAND_*`). *(11: objective —
  and this is what tells `mission_sense` what to read)*
- **Completion + fail detection** — distance/death/count tests; player-died/escaped/timer fails. *(11: completion/fail)*
- **Encounter setup** — guard/enemy spawning + combat/relationship tuning, getaway vehicles, loot pickups.
  *(reuses 09/10)*
- **Cutscene / dialogue / camera** — how they handle non-interactive beats (`DO_SCREEN_FADE`, cam natives,
  subtitle sequences) — the part our agent can't yet handle, so worth understanding the shape.
- **Save/config** — `ScriptSettings`/ini for mission params; reward payout.
- **The shared base class** (if 2+ mods share one) — *the reusable mission framework itself*.

## What this unlocks afterward
- **Refine `PATTERNS/11`** into a concrete heist-mission template (could even let us author missions).
- **Tune `mission_sense`** to read exactly the objective signals these missions emit (blip sprite/colour
  conventions, objective text source) so the agent tracks stage progress, not just "where to go."
- **A concrete demo target**: "watch the agent play a M8T heist" — far more tractable than R*'s story
  missions, because it's built on the blip/objective/task primitives the agent already senses and drives.

## Honest scope
This reveals how *modders* build SP missions (readable, buildable) — not R*'s compiled `.ysc` story-mission
internals. For our agent that's the better material: it's the pattern we can both understand and execute.
Timed/stealth/QTE/cutscene beats inside these heists still need objective-text reading (HUD/vision) + finer
skills before the agent clears them unassisted — but go-to / reach-marker / kill-target stages are in reach now.
```
