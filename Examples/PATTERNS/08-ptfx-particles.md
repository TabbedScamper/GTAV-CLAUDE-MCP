# 08 — PTFX (particle effects)

Mined from **Menyoo** (`PTFX.cpp`, `PtfxSubs.cpp`), **nitanmarcel/GTA-V-Particle-Effects** (SHVDN), and
**rpemotes** (`PTFX.lua`, particle-on-emote). The #1 gotcha: **`USE_PARTICLE_FX_ASSET` must be re-called
right before EVERY spawn**, not once at load.

---

### Load a PTFX asset before using it
**Category:** ptfx-assets
**Problem:** A particle dictionary (asset) must be streamed in before any effect from it can spawn.
**Method:** `REQUEST_NAMED_PTFX_ASSET(asset)` → loop `WAIT(0)` while `!HAS_NAMED_PTFX_ASSET_LOADED(asset)` → then `USE_PARTICLE_FX_ASSET(asset)` immediately before the start call.
**Gotcha:** `REQUEST_NAMED_PTFX_ASSET` is async — spawning before `HAS_NAMED_PTFX_ASSET_LOADED` silently produces nothing.
**Source:** rpemotes/client/PTFX.lua, menyoo/.../Scripting/PTFX.cpp

### Rebind the asset before EVERY spawn (the #1 PTFX gotcha) ⭐
**Category:** ptfx-assets
**Problem:** The native that sets which asset a start call uses is a one-shot global, not per-handle.
**Method:** Call `USE_PARTICLE_FX_ASSET(asset)` (a.k.a. `_SET_PTFX_ASSET_NEXT_CALL`) on the line immediately before EACH `START_PARTICLE_FX_*` call. Menyoo does this inside every `Start(...)` overload.
**Gotcha:** It only sets the asset for the NEXT start call. Two back-to-back spawns without re-calling it → the second uses whatever asset was last set (often wrong) or fails. Re-bind every single time.
**Source:** menyoo/.../Scripting/PTFX.cpp

### Spawn a looped effect at a coord / on an entity / on a bone
**Category:** ptfx
**Problem:** Place a persistent effect (fire, smoke, aura, sparks) in the world or on something.
**Method:** `USE_PARTICLE_FX_ASSET(asset)` then one of:
- `handle = START_PARTICLE_FX_LOOPED_AT_COORD(effect, x,y,z, rotX,rotY,rotZ, scale, axisX,axisY,axisZ, isMeteredScale)`
- `handle = START_PARTICLE_FX_LOOPED_ON_ENTITY(effect, entity, offX,offY,offZ, rotX,rotY,rotZ, scale, axisX,axisY,axisZ)` (offset in entity local space)
- bone: resolve `bone = GET_PED_BONE_INDEX(ped, boneId)` or `GET_ENTITY_BONE_INDEX_BY_NAME(ent, "name")`, then `handle = START_PARTICLE_FX_LOOPED_ON_ENTITY_BONE(effect, entity, offX,offY,offZ, rotX,rotY,rotZ, boneIndex, scale, axisX,axisY,axisZ)`.
**Gotcha:** Looped spawns return an INT handle (≥0) — keep it for control/teardown. **In the bone native, `boneIndex` sits AFTER the rotation triplet and BEFORE scale** (different from the non-bone native). Effect moves with the entity automatically — don't re-spawn each frame.
**Source:** menyoo/.../Scripting/PTFX.cpp; rpemotes/client/PTFX.lua

### Spawn a one-shot (non-looped) effect
**Category:** ptfx
**Problem:** Fire a single burst — explosion, blood hit, muzzle puff, splash — that self-cleans.
**Method:** `USE_PARTICLE_FX_ASSET(asset)`, optionally `SET_PARTICLE_FX_NON_LOOPED_COLOUR(r,g,b)` / `_ALPHA(a)` FIRST, then `START_PARTICLE_FX_NON_LOOPED_AT_COORD(...)` / `_ON_ENTITY(...)` / `_ON_PED_BONE(...)` (same arg shapes as looped).
**Gotcha:** Non-looped returns a **BOOL** (success), NOT a handle — you can't recolor/move/stop it later, so set colour/alpha BEFORE starting (they're global one-shots with no handle arg). Menyoo tries non-looped first and falls back to looped on `false`.
**Source:** menyoo/.../Scripting/PTFX.cpp; ptfx-mod/PTFX/PTFX/Ptfx.cs

### Live-control a running looped effect
**Category:** ptfx-control
**Problem:** Change a spawned looped fx via its handle.
**Method:** `SET_PARTICLE_FX_LOOPED_COLOUR(handle, r,g,b, false)` (r/g/b **0.0–1.0**), `SET_PARTICLE_FX_LOOPED_ALPHA(handle, a)` (0–1), `SET_PARTICLE_FX_LOOPED_SCALE(handle, scale)`, `SET_PARTICLE_FX_LOOPED_OFFSETS(handle, ox,oy,oz, rx,ry,rz)`, `SET_PARTICLE_FX_LOOPED_EVOLUTION(handle, "evoName", value, false)`.
**Gotcha:** Colour/alpha are normalized **0.0–1.0 floats, not 0–255** (Menyoo divides by 255). Only LOOPED handles can be modified; non-looped can't.
**Source:** menyoo/.../Scripting/PTFX.cpp; rpemotes/client/PTFX.lua

### Stop, remove, check existence
**Category:** ptfx-control
**Problem:** Tear down a looped effect cleanly without touching a dead handle.
**Method:** `DOES_PARTICLE_FX_LOOPED_EXIST(handle)` → alive? `STOP_PARTICLE_FX_LOOPED(handle, false)` (graceful, fades) or `REMOVE_PARTICLE_FX(handle, false)` (immediate). Area: `REMOVE_PARTICLE_FX_IN_RANGE(x,y,z, radius)`. Free memory: `REMOVE_NAMED_PTFX_ASSET(asset)`. Set your stored handle to -1 after.
**Gotcha:** Guard `handle == -1` before querying. rpemotes uses STOP (graceful) for emotes; Menyoo uses REMOVE (hard).
**Source:** menyoo/.../Scripting/PTFX.cpp; rpemotes/client/PTFX.lua

### Full looped-on-bone lifecycle (copy-paste shape)
**Category:** ptfx-attach
**Problem:** End-to-end: attach a persistent effect to a moving bone, remove later (e.g. grind sparks on a skateboard, exhaust flame).
**Method:** 1) `REQUEST_NAMED_PTFX_ASSET("core")` + poll. 2) `bone = GET_ENTITY_BONE_INDEX_BY_NAME(veh, "exhaust")`. 3) `USE_PARTICLE_FX_ASSET("core")` (rebind here). 4) `h = START_PARTICLE_FX_LOOPED_ON_ENTITY_BONE("ent_amb_steam_pipe_lgt", veh, 0,0,0, 0,0,0, bone, 1.0, 0,0,0)`. 5) live: `SET_PARTICLE_FX_LOOPED_COLOUR(h, 1.0,0.3,0.0, false)`. 6) teardown: `if (DOES_PARTICLE_FX_LOOPED_EXIST(h)) REMOVE_PARTICLE_FX(h, false); h=-1; REMOVE_NAMED_PTFX_ASSET("core")`.
**Gotcha:** Load (step 1) is once; rebind (step 3) is before EVERY start. Forgetting step 3 = the classic silent/wrong-asset bug.
**Source:** synthesized from menyoo PTFX.cpp + rpemotes PTFX.lua

### Asset → effect name structure + ~12 common pairs
**Category:** ptfx-assets
**Problem:** Every spawn uses an (asset, effect) PAIR; you need real ones.
**Method:** Asset = the streamed dictionary (`"core"`, `"scr_indep_fireworks"`) passed to `USE_PARTICLE_FX_ASSET`; effect = the named particle inside it (first arg to `START_PARTICLE_FX_*`). Common pairs (Menyoo-verified marked ✓; bare-`core` are standard, cross-check vs DurtyFree `particleEffectsCompact.json`):
| Effect | asset | effect name |
|---|---|---|
| Flame ✓ | `core` | `ent_sht_flame` |
| Petrol fire | `core` | `fire_petrol_one` |
| Exhaust/ambient smoke | `core` | `exp_grd_petrol_smoke` |
| Tyre/burnout smoke ✓ | `scr_mp_creator` | `scr_mp_plane_landing_tyre_smoke` |
| Lighter sparks ✓ | `scr_mp_house` | `scr_sh_lighter_sparks` |
| Electrical sparks | `core` | `ent_dst_elec_fire_sp` |
| Water splash | `core` | `water_splash_ped_in` |
| Grenade explosion | `core` | `exp_grd_grenade` |
| Building explosion ✓ | `scr_agencyheist` | `scr_fbi_exp_building` |
| Blood impact | `core` | `blood_entry_wall` |
| Steam/sparking pipe | `core` | `ent_amb_steam_pipe_lgt` |
| Firework burst ✓ | `scr_indep_fireworks` | `scr_indep_firework_starburst` |
| Muzzle flash | `core` | `muz_assault_rifle` |
**Gotcha:** The effect name is meaningless without its owning asset streamed in under that exact name. `core` / `core_snow` hold most ambient effects. The full machine-readable DB is DurtyFree's `particleEffectsCompact.json` (what ptfx-mod downloads).
**Source:** menyoo/.../Submenus/PtfxSubs.cpp; ptfx-mod/PTFX/PTFX/Ptfx.cs (DurtyFree data source)
