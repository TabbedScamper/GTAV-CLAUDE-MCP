# North Star — "build me a skateboard mod, and get close"

> The bar: someone says *"create a skateboard mod with nice animations, tricks, and decent physics"*
> and Claude + GTAV-CLAUDE-MCP gets meaningfully close — grabbing bone names, attaching an in-game
> model, playing animations, scripting physics, and drawing UI. This file decomposes that
> "impossible" ask into capability primitives and tracks what's COVERED vs the GAP, so every build
> session knows what to reach for and what to harvest next. The skateboard is a stand-in for any
> hard composite mod (parkour, a grappling hook, a fishing minigame, a basketball...).

## Decomposition — what a skateboard mod actually needs

| # | Primitive | Status | What we have / what's missing |
|---|---|---|---|
| 1 | **A board model in-world** | ✅ COVERED | Spawn via `resolve(...)` + the streaming recipe (request→poll→create→release). Find a board-like base-game prop with `gtadata_find` / CodeWalker; or an add-on model via the RPF hot-reload path. |
| 2 | **Attach board to the ped's feet** | ✅ COVERED | Recipes: *"Resolve bone INDICES at attach time"* (`GET_ENTITY_BONE_INDEX_BY_NAME` → `SKEL_L_Foot`/`SKEL_R_Foot`) + *"Attach entity to entity (child, parent)"*. Per-foot offsets = a little in-game trial (use `set`/`get` to tune). |
| 3 | **Animations** (ride stance, push, ollie, kickflip) | ✅ COVERED | `recipe_search("animation")` + the **prop+bone+anim** card (rpemotes) = the exact skateboard-rider recipe; `anim_search`/`anim_get` resolve labels→dict/clip/flags. Full superset = alexguirre animations-list. |
| 4 | **Physics / movement feel** | ◑ PARTIAL | Best path: **make the board a VEHICLE** so RAGE physics carry it — then `set_wheel_fitment` + `get/set_handling` (initial_drive_force, suspension_raise, traction) tune the feel. Script-velocity fallback (`SET_ENTITY_VELOCITY` + ground raycast) is twitchier. Balance still needs tuning. |
| 5 | **Trick input + HUD/score UI** | ✅ COVERED | Recipes: control reading (`IS_DISABLED_CONTROL_*`), text/sprite/safezone drawing, scaleform method calls, instructional buttons, notifications, frontend sounds. |
| 6 | **A nice skate camera** | ✅ COVERED | `recipe_search("camera")` — the **attach-to-entity + point-at = follow/skate cam** card, plus RENDER_SCRIPT_CAMS, interp, DOF, shake, gameplay-cam seed. |

## The takeaway (updated after the animation+camera harvest)
**Five of six primitives are now COVERED**; only physics-feel remains PARTIAL — and it has a concrete
grounded path (board-as-vehicle + the handling tools). The skateboard has gone from "parts" to
"buildable end-to-end" — the remaining work is in-game tuning/iteration, not missing capability.

## Build a board-as-vehicle skateboard — the end-to-end recipe is now assemblable
1. `resolve` a board model (or author one via Blender+Sollumz → CodeWalker.API → `reload_content_changeset`).
2. Spawn ped + board; `recipe_search("prop bone")` → attach board to a foot/root bone (or make it a vehicle).
3. `anim_get(...)` riding/push clips → the prop+bone+anim card to play them.
4. `recipe_search("camera")` → attach a follow cam + DOF for the cinematic look.
5. Tune feel with `set_handling` / `set_wheel_fitment` (if board-as-vehicle).
6. Trick input + score HUD from the UI recipes.

## Remaining harvest targets (depth, not blockers)
- **PTFX + peds/AI/tasks** — grind sparks, ambient skaters, combat/sequences for scene depth.
- **Integrate the inject tools** (docs/TOOLS-UNDERSTAND-AND-INJECT.md): CodeWalker.API (headless asset
  inject), NHA_GTA_CT parser (patch-day durability), GhidraMCP (binary understanding).

## How a build session should use the layer (the loop)
1. `recipe_search("<thing>")` → get the verified native sequence + gotcha **before** writing code.
2. `resolve("<model/ped/weapon>")` → exact hash (+ crash check) instead of guessing.
3. Build on the bridge primitives; for vehicles use `set_wheel_fitment` / `set_handling`.
4. Hit a gap (no recipe)? Harvest a reference mod into `Examples/` (the rule in CLAUDE.md), distill a
   card, regen recipes — the tool gets smarter with each build.
