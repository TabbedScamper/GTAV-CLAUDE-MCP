# auto_drive (EXPERIMENTAL — pulled from the shipping mod)

Context-steering autonomous driver prototype. **Not loaded by the bridge** — parked here on 2026-06-12
because reliable self-play driving is a deep rabbit hole vs. its payoff. The *sensing* primitives below
are solid and verified, and the raycast finding is reusable far beyond driving.

To re-enable: copy `auto_drive.py` back into `pyscript/`, re-add the load block in `bridge.py`
(top-level import + a hot-reload entry in `_reload_re_toolkit`), F9.

## What was VERIFIED live
- **8-direction raycast sensor ring** — proximity-accurate (car at 9.0m → reads 8.1m; walls 10–18m), returns
  the hit entity handle. Detects low supercars *and* walls/peds.
- **Rollover recovery** — `IS_VEHICLE_ON_ALL_WHEELS`/`GET_ENTITY_ROLL`/`IS_ENTITY_UPSIDEDOWN` →
  `SET_VEHICLE_ON_GROUND_PROPERLY`. Flipped a car to roll −170°, snapped it back to −0.3° on 4 wheels.
- **Native cop finder** — `GET_CLOSEST_VEHICLE` over police MODELS (no fragile memory pools).
- **Context steering** (Game AI Pro 2, Fray): danger map (sensors) + interest map (escape/objective) →
  best-scoring heading slot. Pure logic self-tested.

## The reusable breakthrough — calling OUT-PARAM natives through the bridge
The bridge could never use `GET_SHAPE_TEST_RESULT`, `GET_CLOSEST_VEHICLE_NODE`, `REMOVE_BLIP`, etc.
(all marked "needs out-param support"). The full recipe, now proven:

1. **Pointer args pass straight through.** `_coerce_arg` leaves any `*`-typed param as-is, and
   `gta.invoke` passes the Python int as the 64-bit pointer value. So pass a **buffer address** (int)
   where the native wants `Vector3*`/`BOOL*`/`Entity*`. Use a module-level `ctypes.create_string_buffer`
   and `ctypes.addressof`; read results back with the bridge's `read_int`/`read_float`.
2. **Do START + GET in ONE game-thread command.** A shape-test handle is tied to the script-thread that
   created it and is invalid from a *separate* bridge call (we saw status 0 every time across two calls).
   Put the whole probe inside one handler (NOT in `_OFFTHREAD_COMMANDS`) using the inline `_call_native_safe`,
   and use `START_EXPENSIVE_SYNCHRONOUS_SHAPE_TEST_LOS_PROBE` so the result is ready that same frame.
3. **ScriptHookV `Vector3` is 24 bytes, not 12.** Each float has 4 bytes of padding (8-byte stride):
   x@+0, y@**+8**, z@**+16**. Buffer layout for `GET_SHAPE_TEST_RESULT(handle, BOOL* hit, V3* endCoords,
   V3* normal, Entity* ent)`: `hit@+0 | endCoords@+8 | normal@+32 | entity@+56`. Reading y/z at +4/+8
   (the 12-byte assumption) pulls padding → zeros → every distance clamps to max range (looks "all clear").
4. **Anchor rays to the ground, cast two heights.** Vehicle origin sits ~0.4m above ground; +0.4 more put
   the ray ~0.8m up and it sailed over low cars. Cast at `ground+0.35` (bumpers) and `ground+0.95`
   (walls/peds), take the nearer hit. Ground = `origin_z - GET_ENTITY_HEIGHT_ABOVE_GROUND`.

These four are the general pattern for **any** out-param native via the bridge — the real long-term value
of this detour.
