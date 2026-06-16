# 07 — Authoring your OWN animations (.ycd data model, from Sollumz)

Mined from **Sollumz** (`ycd/ycdexport.py`, `ycdimport.py`, `properties.py`, `operators.py`,
`tools/animationhelper.py`) — the open-source Blender → GTA V YCD pipeline. This is the
**authoring** half (making new clips), complementing `05-animation.md` (playing them). The full
end-to-end pipeline + how it plugs into our inject/reload chain is in `docs/CUSTOM-ANIMATIONS.md`.

> Authoring is an OFFLINE step (Blender + Sollumz → `.ycd.xml` → CodeWalker → `.ycd`). These cards
> let the toolkit *understand* the format so the headless parts can be automated and so Claude can
> reason about why a custom anim breaks.

---

### The ClipDictionary hierarchy: Animations vs Clips
**Category:** ycd-format
**Problem:** Know what a `.ycd` holds and why there are two separate lists.
**Method:** `.ycd` = `ClipDictionary` with two parallel lists: `animations` (raw baked per-bone track data) and `clips` (named, hashed, *playable* things that reference an animation by hash + add timing/tags). The game only ever plays **Clips**. Sollumz mirrors this as a 4-level empty tree: `CLIP_DICTIONARY` → `ANIMATIONS`/`CLIPS` containers → `ANIMATION`/`CLIP` children.
**Gotcha:** Animations and Clips are decoupled and linked only by a hash string — there's no object pointer in the file. If one animation fails to export, the whole dictionary aborts (clips would dangle).
**Source:** sollumz/ycd/ycdexport.py

### Track ID → channel mapping (the authoritative table)
**Category:** bone-tracks
**Problem:** Know which track id means which channel — the core of the format.
**Method:** From `Track(IntEnum)` + `TrackFormatMap`: **0 = BonePosition (Vector3)**, **1 = BoneRotation (Quaternion)**, **2 = BoneScale (Vector3)**; **5 = MoverPosition (Vector3)**, **6 = MoverRotation (Quaternion)**; **7 = CameraPosition**, **8 = CameraRotation**; **17/18 = UV0/UV1**; **27 = CameraFOV (Float)**, **28 = CameraDOF**. `TrackFormat`: Vector3=0, Quaternion=1, Float=2. Blender fcurve paths map: `location`→0, `rotation_quaternion`→1, `scale`→2.
**Gotcha:** Track IDs are SPARSE game constants, not sequential (jumps 2→5→7→17…; 3/4 don't exist). Position always precedes rotation. Always look up the format from `TrackFormatMap`, never assume.
**Source:** sollumz/tools/animationhelper.py

### Bones are addressed by HASH, not name (skeleton is load-bearing)
**Category:** bone-tracks
**Problem:** Understand why you must have the right skeleton to author or read animation data.
**Method:** `BoneId.bone_id` is a 32-bit **bone-tag hash**, not a name. Canonical fcurve path is `pose.bones["#{tag}"].location`. Export reads each `pose_bone.bone.bone_properties.tag` (the integer that becomes `bone_id`) and converts name-based paths → `#{tag}` via the armature's name→tag map.
**Gotcha:** Bone tags must match the **target ped/object skeleton's** tags exactly. A wrong skeleton = wrong tags = tracks bind to the wrong bones (or none). Without the armature you can't resolve names↔hashes at all. This is *the* reason custom anims historically broke.
**Source:** sollumz/tools/animationhelper.py, tools/blenderhelper.py

### The Mover track = root motion (travels the world vs in-place) ⭐
**Category:** mover
**Problem:** Make an animation move the entity through the world (a walk that travels) vs play on the spot.
**Method:** Root motion = two tracks on synthetic bone **#0**: `MoverPosition` (track 5, Vector3) + `MoverRotation` (track 6, Quaternion). In Blender these are the armature object's `delta_location` / `delta_rotation_quaternion`. When a sequence uses either mover track, the exporter auto-ORs `AnimationFlag.RootMotion (16)` into the animation flags.
**Gotcha:** Forgetting the mover (or its RootMotion flag) = the entity won't translate → the classic "moonwalking in place" bug. Mover is stored as bone #0 but driven by the armature object's delta transform, not a real bone. `moverfixup` tags correct drift on specific axes.
**Source:** sollumz/tools/animationhelper.py, ycd/ycdexport.py

### Frames, duration, and 30fps bake semantics
**Category:** animation-data
**Problem:** Know how time is sampled and how many frames bake on export.
**Method:** `duration_secs = action_frame_span / scene_FPS`. `export_frame_count = max(max_keyframes, ceil(duration_frames+1))`. Every track is **densely baked**: the exporter loops each export frame and calls `fcurve.evaluate(...)` — not sparse keyframes. `sequence_frame_limit = frame_count + 30`.
**Gotcha:** GTA anims are conceptually **30fps** (tests gate on 29.97). Export frame count depends on the *current Blender scene FPS* because duration is FPS-derived — changing scene FPS changes duration and all downstream clip times. Sub-frame timing precision is lost in the bake.
**Source:** sollumz/tools/animationhelper.py, ycd/ycdexport.py

### Channel quantization + the quaternion sign-flip fix
**Category:** animation-data
**Problem:** Understand the per-component compression so values survive the round-trip.
**Method:** Each track splits into per-component lists (Vec3→X/Y/Z, Quat→X/Y/Z/W). Constant component → `Static*`. Else `build_values_channel`: ≤10% unique → `IndirectQuantizeFloat` (dedup table + indices); else `QuantizeFloat`. `quantum = max(min_consecutive_delta, range/1048576)` (20-bit).
**Gotcha:** Quaternions are stored as **4 independent quantized channels** (not 3+reconstructed-W). So the exporter applies a "flicker fix": negate `quat[i]` when `dot(quat[i-1], quat[i]) < 0`, because RAGE lerps quaternions per-component with no sign check. ALL quaternion math (bone-space transform, camera fix) must happen BEFORE this sign pass.
**Source:** sollumz/ycd/ycdexport.py, tools/animationhelper.py

### A Clip references an animation by hash + adds timing
**Category:** clip
**Problem:** Understand the playable Clip and its timing fields.
**Method:** `clip_from_object` emits `ClipAnimation` (single) or `ClipAnimationList` (multi). Each sets `animation_hash` (the referenced Animation's hash), `start_time`/`end_time` secs, `rate = (end-start)/clip.duration`. Clip fields: `hash` (the name the game looks up), `name = "pack:/"+name`.
**Gotcha:** The clip references the animation purely by `animation_hash` string — **the Animation's hash and the Clip's animation_hash MUST match exactly or the clip plays nothing.** `rate>1` = faster than source; start/end frames let a clip play a sub-range of a longer animation.
**Source:** sollumz/ycd/ycdexport.py, properties.py

### Clip Tags = timeline events (footsteps, audio, MoVE)
**Category:** clip
**Problem:** Attach gameplay events at points along the clip timeline.
**Method:** `ClipTag` has `name`, `start_phase`/`end_phase` (**0..1 normalized**, NOT seconds), and typed `attributes` (Float/Int/Bool/Vector3/4/String/HashString). Tags sorted by start_phase. Operator templates: `moveevent`, `audio`, `foot` (heel/right Bools), `moverfixup`, `facial`, `object`, `armsik`.
**Gotcha:** Phases are fractions of clip duration → real time = phase × clip.duration, so re-timing the clip moves all tags. The tag signature hash calc is noted in-code as NOT yet byte-matching R*'s ("good enough for now") — don't rely on signature exactness.
**Source:** sollumz/ycd/properties.py, ycd/ycdexport.py, operators.py

### Target type changes the data path: armature vs camera vs material
**Category:** animation-data
**Problem:** Know how the animation target affects required scene objects + value space.
**Method:** `target_id_type` = ARMATURE / CAMERA / MATERIAL. Armatures convert every bone from Blender pose space → RAGE local space via `calculate_bone_space_transform_matrix` (each bone's `matrix_local` relative to parent). Cameras rotate the rotation quaternion by **−90° about local X** (RAGE cameras aim +Y, Blender −Z). Materials store UV anim on the material datablock.
**Gotcha:** The pose→local conversion makes exported bone values **skeleton-dependent** — the same Action on a different skeleton produces different stored values. Camera anims silently need the −90° fix or the camera points sideways. Default vectors differ per track (pos/rot/scale default (0,0,0); UV0 (1,0,0); UV1 (0,1,0)).
**Source:** sollumz/ycd/ycdexport.py, tools/animationhelper.py, ycd/properties.py

### Retarget one animation onto a different skeleton ⭐ (the Mixamo unlock)
**Category:** retarget
**Problem:** Reuse an Action (or Mixamo/mocap) on a different armature and keep values correct.
**Method:** `retarget_animation(anim, old_target, new_target)` (auto-runs on target change): renames action groups `#tag`↔bone-name; converts every fcurve path canonical↔target form; re-transforms bone location/rotation and camera rotation between old/new bone spaces via `calculate_bone_space_transform_matrix`; runs `setup_armature/camera/material_for_animation`. It also adds COPY_ROTATION constraints `RB_L/R_ThighRoll`←`SKEL_L/R_Thigh` (since .yed roll exprs aren't supported).
**Gotcha:** Retarget remaps by the *difference* of bone local matrices between skeletons — if a tag exists on both with different rest poses it's corrected; **if a tag is missing on the new skeleton, that channel is left in old space (silently wrong).** This is how you bring Mixamo/FBX in: import, map the skeleton, retarget onto the GTA ped skeleton, export.
**Source:** sollumz/tools/animationhelper.py

### Export entry point + the minimal authoring recipe
**Category:** export
**Problem:** The actual operator and the minimum scene setup to produce a `.ycd`.
**Method:** Operator `sollumz.export_assets` dispatches `CLIP_DICTIONARY` → `export_ycd()` → `clip_dictionary_from_object()` → `write_xml()`. **Output is CodeWalker `.ycd.xml`** (NOT binary) — CodeWalker (or CodeWalker.API) converts `.ycd.xml` → binary `.ycd`. Minimal recipe: armature with correct bone tags + an Action with keyframes → set the ANIMATION's `action`/`hash`/`target_id` → a CLIP with `hash`/`name`/`duration` whose `animation_hash` == the Animation's hash → select the CLIP_DICTIONARY root → export.
**Gotcha:** Breakers: wrong skeleton (wrong tags), missing mover (no world translation), animation-hash ≠ clip's animation_hash (plays nothing), empty action (aborts the whole dictionary). The `.ycd.xml`→`.ycd` conversion is a separate CodeWalker step — that's the seam where our CodeWalker.API + reload chain takes over.
**Source:** sollumz/sollumz_operators.py, ycd/ycdexport.py, ycd/ycdimport.py
