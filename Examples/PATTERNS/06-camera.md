# 06 — Scripted cameras (cinematic, follow, interp, DOF, shake)

Mined from **SHVDN** (`Camera.cs`, `ScriptCameraDirector`, `GameplayCamera`) and **Menyoo**
(`Camera.cpp`, `GameplayCamera.cpp`). The #1 gotcha: **`SET_CAM_ACTIVE` alone shows nothing — you MUST
call `RENDER_SCRIPT_CAMS(true, …)`.** The follow-cam pair (attach + point-at) is the skate camera.

---

### Create a scripted camera with position, rotation, and FOV
**Category:** camera
**Problem:** Spawn a script-controlled camera with a full frame in one call.
**Method:** `handle = CREATE_CAM_WITH_PARAMS("DEFAULT_SCRIPTED_CAMERA", x,y,z, rotX,rotY,rotZ, fov, startActive, rotOrder)`. Use `rotOrder=2` (YXZ, the game default). FOV in degrees (~65 default). Bare `CREATE_CAM(name, startActive)` exists but takes no frame.
**Gotcha:** Use a valid cam name (`"DEFAULT_SCRIPTED_CAMERA"`); a bad name yields a handle you can't move. Handle -1 = failure (pool full); only ≥0 is valid. `startActive` only sets SET_CAM_ACTIVE — it does NOT put the cam on screen.
**Source:** menyoo/.../Natives/natives.h; shvdn-api/.../Camera/Camera.cs

### Make the script camera actually visible (THE mandatory step) ⭐
**Category:** camera
**Problem:** Camera created + active, but the screen still shows the gameplay cam.
**Method:** After `SET_CAM_ACTIVE(cam, true)`, you MUST call `RENDER_SCRIPT_CAMS(render, ease, easeTime, p3, p4, p5)`. Instant takeover: `RENDER_SCRIPT_CAMS(true, false, 0, false, 0, 0)`. Blend in over N ms: `RENDER_SCRIPT_CAMS(true, true, 3000, true, 0, 0)`. Release to gameplay: `RENDER_SCRIPT_CAMS(false, false, 3000, false, 0, 0)` (ease=true to blend back).
**Gotcha:** This is the #1 camera gotcha. SET_CAM_ACTIVE alone never shows the cam. Always pair `SET_CAM_ACTIVE(cam,true)` + `RENDER_SCRIPT_CAMS(true,…)` to enter, `RENDER_SCRIPT_CAMS(false,…)` to exit. 6th arg is always 0.
**Source:** shvdn-api/.../Camera/ScriptCameraDirector.cs; menyoo/.../Scripting/Camera.cpp

### Set position, rotation, FOV after creation
**Category:** camera
**Problem:** Move/aim an existing cam (or build one from a bare CREATE_CAM).
**Method:** `SET_CAM_COORD(cam, x,y,z)`; `SET_CAM_ROT(cam, rotX,rotY,rotZ, rotOrder=2)`; `SET_CAM_FOV(cam, degrees)`. Euler degrees: X=pitch, Y=roll, Z=yaw/heading. Read `GET_CAM_COORD`, `GET_CAM_ROT(cam, 2)`, `GET_CAM_FOV`.
**Gotcha:** Rotation in DEGREES; the order arg must match between SET and GET — use 2 consistently. Heading is the Z component.
**Source:** menyoo/.../Scripting/Camera.cpp; shvdn-api/.../Camera/Camera.cs

### Point the camera at a coord or a moving entity
**Category:** camera
**Problem:** Auto-orient the cam to look at a static point or track a moving target.
**Method:** Static: `POINT_CAM_AT_COORD(cam, x,y,z)`. Entity: `POINT_CAM_AT_ENTITY(cam, entity, offX,offY,offZ, isRelative)` — (0,0,0) aims at origin; `isRelative=true` = offset in the entity's local space. Bone: `POINT_CAM_AT_PED_BONE(cam, ped, boneIndex, ox,oy,oz, true)`. Cancel: `STOP_CAM_POINTING(cam)`.
**Gotcha:** While pointing is active, SET_CAM_ROT has no effect — STOP_CAM_POINTING to get manual rotation back. Pointing orients but does NOT move the cam.
**Source:** menyoo/.../Scripting/Camera.cpp; shvdn-api/.../Camera/Camera.cs

### Attach the camera to an entity for a trailing / follow (skate) cam ⭐
**Category:** attachment
**Problem:** Make the cam ride a moving entity at a fixed offset (chase/skate cam behind the player).
**Method:** `ATTACH_CAM_TO_ENTITY(cam, entity, offX,offY,offZ, isRelative)` — `isRelative=true` so the offset is local (Y=-5 behind, Z=2 above). **Combine with `POINT_CAM_AT_ENTITY(cam, entity, 0,0,0, true)` — that pair IS the trailing/skate cam.** Bone: `ATTACH_CAM_TO_PED_BONE`. Full pos+rot copy: `HARD_ATTACH_CAM_TO_ENTITY(...)`. Detach: `DETACH_CAM(cam)`.
**Gotcha:** Plain ATTACH keeps position but NOT the entity's rotation — it won't swing behind a turning car alone. Add POINT_CAM_AT_ENTITY for orientation, or use HARD_ATTACH to inherit rotation. `isRelative=true` makes the offset track the entity's facing.
**Source:** menyoo/.../Scripting/Camera.cpp; shvdn-api/.../Camera/Camera.cs

### Interpolate (cinematic blend) between two cameras
**Category:** interpolation
**Problem:** Smoothly fly from one framing to another over a duration.
**Method:** Create BOTH cams (camFrom active/rendering, camTo at the destination frame), then `SET_CAM_ACTIVE_WITH_INTERP(camTo, camFrom, durationMs, easeLocation, easeRotation)` — destination FIRST, source SECOND. Screen must already be under script cams. Poll `IS_CAM_INTERPOLATING(cam)` for completion.
**Gotcha:** Arg order is camTo, camFrom — reversing blends the wrong way. The destination becomes active; camFrom auto-deactivates. Both cams must exist for the whole blend — don't DESTROY_CAM the source mid-interp. Do NOT call RENDER_SCRIPT_CAMS again per blend.
**Source:** menyoo/.../Scripting/Camera.cpp; shvdn-api/.../Camera/Camera.cs

### Interpolate a single camera to a new frame in place
**Category:** interpolation
**Problem:** Move one cam to a new pos/rot/fov over time without a second handle.
**Method:** `SET_CAM_PARAMS(cam, x,y,z, rotX,rotY,rotZ, fov, durationMs, graphTypePos, graphTypeRot, rotOrder=2)`. `duration=0` snaps instantly.
**Gotcha:** With non-zero duration it internally clones the cam (needs a free pool slot) and fails silently if the pool is full. Use duration=0 to set a frame reliably.
**Source:** shvdn-api/.../Camera/Camera.cs

### Camera shake (handheld / impact feel)
**Category:** shake
**Problem:** Organic motion or an explosion jolt.
**Method:** `SHAKE_CAM(cam, shakeName, amplitude)` — names: `HAND_SHAKE`, `SMALL/MEDIUM/LARGE_EXPLOSION_SHAKE`, `JOLT_SHAKE`, `VIBRATE_SHAKE`, `ROAD_VIBRATION_SHAKE`, `DRUNK_SHAKE`, `SKY_DIVING_SHAKE`. Amplitude ~0-1 subtle. Live tweak `SET_CAM_SHAKE_AMPLITUDE(cam, v)`; stop `STOP_CAM_SHAKING(cam, immediate)`.
**Gotcha:** Shake name is a string (a wrong one silently does nothing). SET_CAM_SHAKE_AMPLITUDE only affects an already-running shake.
**Source:** menyoo/.../Scripting/Camera.cpp; shvdn-api/.../Camera/Camera.cs

### Depth of field (cinematic shallow focus)
**Category:** dof
**Problem:** Blur background/foreground so the subject pops.
**Method:** `SET_CAM_USE_SHALLOW_DOF_MODE(cam, true)` → `SET_CAM_NEAR_DOF(cam, nearM)` → `SET_CAM_FAR_DOF(cam, farM)` → `SET_CAM_DOF_STRENGTH(cam, 0..1)` (near 1 = heavy blur). Advanced: `SET_CAM_DOF_FNUMBER_OF_LENS`, `SET_CAM_DOF_FOCAL_LENGTH_MULTIPLIER`.
**Gotcha:** DOF does nothing unless shallow mode is ON and strength > 0 — near/far alone won't blur. Near/far are the focus band in meters; the subject sits between them.
**Source:** menyoo/.../Scripting/Camera.cpp; shvdn-api/.../Camera/Camera.cs

### Seed a script cam from the current gameplay camera
**Category:** gameplay-cam
**Problem:** Start a scripted cam exactly where the player's view is, for a seamless cut.
**Method:** `pos=GET_GAMEPLAY_CAM_COORD()`, `rot=GET_GAMEPLAY_CAM_ROT(2)`, `fov=GET_GAMEPLAY_CAM_FOV()`, feed into `CREATE_CAM_WITH_PARAMS(...)`. Also `GET_GAMEPLAY_CAM_RELATIVE_HEADING/PITCH` (re-apply on release).
**Gotcha:** Use the same rotOrder (2) you feed the script cam. Seed → `RENDER_SCRIPT_CAMS(true, false, 0, …)` gives a frame-perfect cut with no jump.
**Source:** menyoo/.../Scripting/GameplayCamera.cpp; shvdn-api/.../Camera/GameplayCamera.cs

### Tear down: release the screen, then destroy
**Category:** camera
**Problem:** Cleanly return control to the gameplay camera and free handles.
**Method:** Order matters: 1) `RENDER_SCRIPT_CAMS(false, false, 3000, false, 0, 0)` (ease=true to blend back). 2) `SET_CAM_ACTIVE(cam, false)`. 3) `DESTROY_CAM(cam, false)` per cam, or `DESTROY_ALL_CAMS(false)`. Guard with `DOES_CAM_EXIST(cam)`.
**Gotcha:** Destroying the cam WITHOUT first `RENDER_SCRIPT_CAMS(false,…)` can leave the screen stuck/black on a dead cam — always release rendering BEFORE destroying.
**Source:** menyoo/.../Scripting/Camera.cpp; shvdn-api/.../Camera/Camera.cs
