# World-sensing — "where am I + what's happening" (foundation for real-time play)

> The first layer of the real-time agent: turn raw memory/natives into a compact semantic snapshot
> Claude can act on, so you can say "play" and Claude knows its situation. Module: `pyscript/world_sense.py`.
> The hierarchical-control vision this feeds is in the chat history; this doc is the built foundation.

## What it gives you
- **`get_world_state()`** — the snapshot Claude reads each think-cycle:
  - `player`: x/y/z, heading, health, armor, weapon, wanted, on-foot vs vehicle.
  - `location`: coords + nearest landmark (+ bearing) + zone code → human "where".
  - `threats` / `nearby_vehicles` / `nearby_peds`: each annotated with **distance + compass (N..NW) +
    ahead/behind/left/right**, sorted nearest-first.
  - `summary`: a one-line human string, e.g.
    *"on foot, 90hp, pistol, ~40m NE of Maze Bank, 2 stars. Threats: cop 15m NE (ahead-right). 3 vehicle(s) near."*
- **`world_events()`** — diffs the world vs the last snapshot → `took_damage`, `wanted_changed`,
  `entered/exited_vehicle`, `new_threat`, `threat_closing`. **Event-driven re-planning**: Claude acts when
  something changes, not on a timer — the key to being responsive without per-frame inference.
- **`describe_location(x,y,z?)`** — nearest landmark + bearing + zone for any coord (or the player).
- **`nearest_road_node()`** — nearest drivable road node (on-road spawn placement — the getaway-car fix).
- **`raycast(from,to)`** — line-of-sight / ground probe / "what's in front".

## How it's built (verified vs verify-in-game)
- **Self-tested offline (`python world_sense.py`):** the perception *logic* — compass/bearing, distance,
  ahead/behind relative direction, nearest-landmark, the snapshot summary, and the event deltas. This is
  the "summarizer brain" and it's solid.
- **Verify in-game (clearly marked):** the *gathering* — it reads player pos from memory (`+0x90`) and
  health/heading/wanted via natives **by name through the allowlist** (wrong name → safe error, never a
  crash), and pulls nearby entities from the bridge's existing pool listers *defensively* (a missing
  lister is skipped, not fatal). Confirm the gathered values look right on your build.
- **Needs a small bridge extension:** `raycast` result retrieval, `nearest_road_node`, and the zone-name
  string all use **out-param natives** — they're wired best-effort and marked. They light up fully once
  the bridge gains out-param/string-return support (a focused follow-up). Until then, location leans on
  landmark math (exact) + coords, which needs no out-params.

## The spatial database (`pyscript/world_data/`)
- `landmarks.json` — approximate seed; turn coords into "~40m NE of Maze Bank". **Expand from a CodeWalker
  map export** (CodeWalker can lay out the whole map: POIs from YMAPs, the road graph from `.ynd`). See
  `world_data/README.md`. Landmarks are an *enrichment* — the authoritative sense is the game's own
  coords/zone/road-node.

## How this becomes "tell me to play and watch"
This snapshot is step 1 of the hierarchical agent:
1. **Sense (this layer):** `get_world_state()` + `world_events()` → Claude's eyes.  ✅ built
2. **Act:** high-level verbs (`drive_to`, `engage`, `take_cover`, `flee`) that compile to frame-accurate
   control using the ped-AI tasks already harvested (`PATTERNS/09`).  ◑ next
3. **Loop:** a reactive substrate runs the current verb at 60fps and pushes events; Claude wakes on an
   event (or every few seconds), reads the snapshot, and picks the next intent.  ◑ next
4. **Enrich:** CodeWalker map import (landmarks + road graph) for routing; optional screenshot/minimap
   vision for ambiguous scenes.  ○ later

So today you can already ask Claude to "describe my surroundings" / "what's happening" and it gets a real
answer. The next build is the action verbs + the event loop on top — then "play and watch" is the loop
running itself.

## In-game smoke test (once loaded with F9)
1. `world_sense_status` — confirms landmarks loaded.
2. Stand somewhere in-game → `get_world_state` → check pos/health/location/nearby look right.
3. Take some damage / get a wanted star → `world_events` → expect `took_damage` / `wanted_changed`.
4. If pos/health are wrong, the gathering offsets/native names need adjusting for your build (they're
   isolated in `world_sense.gather_player` / `_entity_pos`).
