# 04 — Spawning, world placement & scene serialization

Mined from **Menyoo** (C++, raw natives — best for exact names/arg order) and **MapEditor** (C#/SHVDN —
clearest for reference-counted release, throttling, and the Menyoo-interop serializer). They agree on
the core ordering: **request → poll → create → reposition → release.**

---

### Stream a model: request, poll, THEN create
**Category:** streaming · *seen in 2 mods*
**Problem:** Any CREATE_* returns 0/null if the model isn't resident.
**Method:** `REQUEST_MODEL(hash)` → loop `WAIT(0)` while `!HAS_MODEL_LOADED(hash)` with a ~3000–7500ms timeout → then CREATE. Guard first with `IS_MODEL_VALID(hash) && IS_MODEL_IN_CDIMAGE(hash)` (vehicles also `IS_MODEL_A_VEHICLE`). Props needing physics: also `REQUEST_COLLISION_FOR_MODEL` / `HAS_COLLISION_FOR_MODEL_LOADED`.
**Gotcha:** Async — never create on the request frame. Poll with `WAIT(0)` (yield a frame) each iteration, not a busy-spin, or the streamer never advances. Bound with a timeout and bail — some hashes never load. (Our `spawn_vehicle` already does request-then-poll non-blocking; this confirms the discipline.)
**Source:** menyoo/.../Scripting/Model.cpp

### Release the model AFTER spawning, not before; reference-count it
**Category:** streaming · *seen in 2 mods*
**Problem:** Free the model template without orphaning the entity.
**Method:** After CREATE succeeds: `SET_MODEL_AS_NO_LONGER_NEEDED(hash)`. The entity persists; only the template is released. If many entities share a hash, only release on the last (MapEditor keeps a `UsedModels` list; releases when count hits 0).
**Gotcha:** Release per-spawn AFTER creation. Menyoo deliberately leaves bulk-unload commented out at scene-load so fresh props don't pop before they settle.
**Source:** mapeditor-guad/PropStreamer.cs, menyoo/.../Scripting/Model.cpp

### Pick the right CREATE_* + arg order; then re-set position/rotation
**Category:** spawn · *seen in 2 mods*
**Problem:** Correct native + args per entity type.
**Method:**
- Prop: `CREATE_OBJECT(hash, x,y,z, isNetwork=1, netMissionEntity=1, dynamic)` — or `CREATE_OBJECT_NO_OFFSET(...)` to spawn exactly at coords (CREATE_OBJECT applies a bounding-box offset).
- Vehicle: `CREATE_VEHICLE(hash, x,y,z, heading, isNetwork=1, netMissionEntity=1, p7=0)`.
- Ped: `CREATE_PED(pedType=26, hash, x,y,z, heading, isNetwork=1, netMissionEntity=1)`; in seat: `CREATE_PED_INSIDE_VEHICLE(veh, pedType, hash, seatIndex, isNetwork, netMissionEntity)`.
**Gotcha:** Set position+rotation AGAIN right after creation — create natives can land slightly off (Menyoo creates, then `Position_set` then `Rotation_set`). Choose CREATE_OBJECT vs _NO_OFFSET deliberately (bounding-box offset).
**Source:** menyoo/.../Scripting/World.cpp, mapeditor-guad/PropStreamer.cs

### Make a spawn persist (mission entity) and freeze statics
**Category:** persistence · *seen in 2 mods*
**Problem:** Stop the engine despawning placed entities when the player leaves; pin static decor.
**Method:** `SET_ENTITY_AS_MISSION_ENTITY(handle, false, true)` (keep-alive form). Static scenery: also `FREEZE_ENTITY_POSITION(handle, true)`. Track every handle in your own list/DB.
**Gotcha:** The engine garbage-collects entities NOT marked as mission entities — unmarked spawns vanish on streaming-out. A non-frozen static prop drifts/falls. The `(h, false, true)` arg form is keep-alive; `(h, false, …)` then delete is the removal form (next card).
**Source:** menyoo/.../Scripting/GTAentity.cpp, mapeditor-guad/PropStreamer.cs

### Delete cleanly: take control → un-mission → type-specific delete (by pointer)
**Category:** persistence · *seen in 2 mods*
**Problem:** Reliably remove a (possibly persistent) entity without leaking it.
**Method:** RequestControl (network) → remove blip → `SET_ENTITY_AS_MISSION_ENTITY(handle, false)` to demote → `DELETE_OBJECT(&handle)` / `DELETE_VEHICLE(&handle)` / `DELETE_PED(&handle)` (fallback `DELETE_ENTITY(&handle)`). Detach children first.
**Gotcha:** You CANNOT delete a still-flagged mission entity — un-mission first or the delete silently no-ops. DELETE_* take a POINTER and zero the handle. Menyoo optionally teleports the entity to a junk coord (32.27, 7683.52, 0.57) before delete to force it out of the active area.
**Source:** menyoo/.../Scripting/GTAentity.cpp, menyoo/.../Spooner/EntityManagement.cpp

### Attach entity to entity — exact arg order (child, parent)
**Category:** attachment
**Problem:** Stick a prop/ped onto another entity or bone with a relative offset+rotation.
**Method:** `ATTACH_ENTITY_TO_ENTITY(entity, toEntity, boneIndex, offX,offY,offZ, rotX,rotY,rotZ, p9=false, useSoftPinning=false, collision, isPed=false, vertexIndex=2, fixedRot=true, p15=0)`. Detach: `DETACH_ENTITY(entity, applyVelocity=1, noCollisionUntilClear=1)`.
**Gotcha:** First arg is the CHILD, second the PARENT (doer, getter). `fixedRot=true` holds the local rotation rather than swinging. `boneIndex=0` attaches to the entity ROOT, not a bone. Guard `child != parent`.
**Source:** menyoo/.../Scripting/GTAentity.cpp

### Resolve bone INDICES at attach time — never hardcode/serialize them
**Category:** attachment
**Problem:** Attach to a specific bone (hand, chassis), which needs a runtime index, not a name.
**Method:** `GET_ENTITY_BONE_INDEX_BY_NAME(entity, boneName)` → pass as the `boneIndex` arg. Bone world pos: `GET_WORLD_POSITION_OF_ENTITY_BONE(entity, boneIndex)`.
**Gotcha:** Bone indices are per-entity AND per-model — resolve against the target instance at attach time; never hardcode or serialize the raw index across models. Returns -1/root if the bone doesn't exist — validate.
**Source:** menyoo/.../Scripting/GTAentity.cpp

### Snap an entity to the ground (probe from above + add half-height)
**Category:** world
**Problem:** Place on terrain when you only have X/Y or an approximate Z.
**Method:** `GET_GROUND_Z_FOR_3D_COORD(x, y, probeZ=1000.0, &outGroundZ, 0, 0)` then set Z = `groundZ + model.halfHeight`. Robust fallback: raycast straight down (and up) and use `hit.z + halfHeight`.
**Gotcha:** Probes DOWNWARD from probeZ — start ABOVE the surface (1000.0 standard) or it returns 0/garbage. The ground tile must be STREAMED IN, else 0.0 — raycast is the more robust fallback. Add the model half-height or the pivot sits at ground level and the mesh clips through.
**Source:** menyoo/.../Scripting/World.cpp, menyoo/.../GTAentity.cpp

### Heading vs full quaternion — store both, apply after position
**Category:** world
**Problem:** Preserve exact orientation across save/load when Euler loses precision / gimbal-flips.
**Method:** Simple: pass `heading` (Z yaw) to create, or `entity.Rotation = (pitch, roll, yaw)`. Exact: `GET_ENTITY_QUATERNION(h,&x,&y,&z,&w)` / `SET_ENTITY_QUATERNION(h,x,y,z,w)`. MapEditor stores both and re-applies the quaternion after position.
**Gotcha:** Apply rotation/quaternion AFTER the entity exists and AFTER position (MapEditor: Rotation → quaternion → Position last). Euler order here is (Pitch=X, Roll=Y, Yaw=Z). Prefer the quaternion when present.
**Source:** mapeditor-guad/Quaternion.cs, mapeditor-guad/PropStreamer.cs

### The Menyoo Spooner XML scene schema (the shareable format)
**Category:** scene-format · *seen in 2 mods*
**Problem:** Serialize a whole scene so it round-trips and interops with thousands of community maps.
**Method:** Root `<SpoonerPlacements>` with `<ReferenceCoords><X/><Y/><Z/></>`, optional `<Note>`; one `<Placement>` per entity:
- `<ModelHash>` hex `0x........`; `<Type>` 1=Ped 2=Vehicle 3=Prop; `<Dynamic>`, `<FrozenPos>` bools; `<HashName>`; `<InitialHandle>` (handle AT SAVE TIME — used to re-link attachments);
- `<PositionRotation>`: `<X><Y><Z><Pitch><Roll><Yaw>`;
- flags: `<OpacityLevel> <LodDistance> <IsVisible> <MaxHealth> <Health> <HasGravity> <IsOnFire> <IsInvincible>` + `IsBulletProof/CollisionProof/ExplosionProof/FireProof/MeleeProof/OnlyDamagedByPlayer`;
- `<Attachment isAttached="bool">` → `<AttachedTo>` (parent's InitialHandle), `<BoneIndex>`, `<X><Y><Z>` offset, `<Pitch><Roll><Yaw>`;
- type blocks `<PedProperties>` (incl. `<TaskSequence>`), `<VehicleProperties>`, `<ObjectProperties>` (e.g. `<TextureVariation>` → SET_OBJECT_TINT_INDEX).
**Gotcha:** `<AttachedTo>` stores the save-time handle (meaningless after reload — see next card). `IsCollisionProof` is serialized INVERTED (`!IsCollisionEnabled`). MapEditor writes ModelHash as `"0x"+hash.ToString("x8")`.
**Source:** menyoo/.../Spooner/FileManagement.cpp, mapeditor-guad/MenyooCompatibility.cs

### Re-link attachments on load via a two-pass InitialHandle remap
**Category:** scene-format
**Problem:** Restore parent/child attachments when every entity gets a NEW handle on load.
**Method:** **Pass 1:** spawn EVERY placement, storing `{newEntity, initHandle, attachedToHandle}`; then `WAIT(~1000ms)` to settle. **Pass 2:** for each attached entity, resolve parent — if `attachedTo==myPed`→player, `==myVehicle`→player's car, else find the entry whose `initHandle==attachedToHandle` and attach to its NEW handle.
**Gotcha:** MUST spawn all entities before wiring any attachment (a child can reference a parent listed later). Link key is the stored InitialHandle, not positions. The `WAIT` between spawn and attach matters — without it attachments snap to wrong transforms.
**Source:** menyoo/.../Spooner/FileManagement.cpp

### Respect the entity cap and YIELD during bulk spawning
**Category:** spawn
**Problem:** Spawn many entities (loading a big scene) without crashing/stalling the streamer.
**Method:** Cap total (MapEditor `MAX_OBJECTS=2048`). During bulk create, yield periodically: `if (count % 249 == 0) WAIT(100)`. Retry vehicle creation (it can transiently return null even with the model loaded). Keep parallel handle lists for cleanup/save.
**Gotcha:** The engine chokes spawning hundreds in one frame — interleave `WAIT` every couple hundred. Track static vs dynamic separately so you know which to freeze. (Directly relevant to building heist/chase scenes via the bridge.)
**Source:** mapeditor-guad/PropStreamer.cs
