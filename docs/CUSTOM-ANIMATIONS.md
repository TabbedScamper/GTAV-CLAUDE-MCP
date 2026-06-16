# Building your OWN animations (not just reusing R*'s)

> The long-standing wall in GTA modding. It's been solved — here's the discovered pipeline and, more
> importantly, **how it plugs into what this toolkit already owns** (inject + reload + play). The
> `.ycd` data-model details are in `Examples/PATTERNS/07-custom-animation-authoring.md`; the play side
> is `05-animation.md`; the asset-inject tools are `TOOLS-UNDERSTAND-AND-INJECT.md`.

## Yes — three working authoring routes (2025-2026)
1. **Blender + Sollumz** (open, free — recommended). Rig to the GTA skeleton, keyframe, export. Sollumz
   does YCD create/edit/export. Note: YCD still exports as **CodeWalker `.ycd.xml`**, then CodeWalker
   converts to binary `.ycd` (most other formats are direct-binary now; YCD isn't yet).
2. **3ds Max + GIMS Evo + AnimKit** (the veteran pro route). AnimKit is the dedicated tool; needs OpenIV
   (ped+skeleton) and CodeWalker (preview). More setup, longest track record.
3. **CodeWalker** decodes `.ycd → XML`, edits, re-imports — both an editor for animated objects and the
   packaging step for the Blender route.

**The practical shortcut: retarget Mixamo / FBX / mocap.** You don't have to keyframe from scratch —
import an existing animation, retarget it onto the GTA ped skeleton (Sollumz `retarget_animation`),
export. This opens the entire Mixamo library to GTA.

## Why it was hard (the gotchas, now known — see PATTERNS/07)
- **Bones are addressed by hash, not name** — you MUST have the correct target skeleton, or tracks bind
  to the wrong bones. Wrong skeleton = silent breakage.
- **The mover track (bone #0, tracks 5/6) = root motion.** Miss it and the entity "moonwalks in place"
  instead of traveling. Sollumz drives it from the armature's `delta_location/rotation`.
- **Clip ↔ Animation are linked only by a hash string** — the Animation's `hash` and the Clip's
  `animation_hash` must match exactly or the clip plays nothing.
- **GTA anims are 30fps**, densely baked on export; quaternions are 4 quantized channels with a
  per-component sign-flip fix.
- **Track IDs are sparse game constants** (0=pos, 1=rot, 2=scale, 5/6=mover, 7/8=camera, 17/18=UV).

## How it plugs into THIS toolkit — the end-to-end loop
Authoring is offline (Blender/3ds Max), but everything after is already ours, and the authoring's
headless parts are scriptable:

```
[author]   Blender + Sollumz  (or retarget a Mixamo FBX)
              └─ headless: `blender --background --python export.py` drives Sollumz operators
                 → produces  myanims.ycd.xml
[package]  CodeWalker.API (flobros, HTTP :5555)
              └─ POST /api/import  myanims.ycd.xml  → binary .ycd inside a mods RPF
                 (and /api/hash/jenkins to get the dict's name hash)
[inject]   bridge  reload_content_changeset   → hot-mounts the RPF, no game restart
[play]     bridge  REQUEST_ANIM_DICT("myanims") → poll HAS_ANIM_DICT_LOADED → TASK_PLAY_ANIM
              └─ exactly the PATTERNS/05 recipes; a custom dict behaves like a stock one
```

**What's automatable vs human:**
- **Mechanical / scriptable:** retargeting an existing FBX/Mixamo clip, the `.ycd.xml` export
  (`blender --background`), packaging (CodeWalker.API), inject (`reload_content_changeset`), and play
  (the native recipes). A Mixamo-clip → in-game-playing loop is largely hands-off.
- **Human / creative:** keyframing an original animation from scratch (the art). Even here, the toolkit
  can scaffold the scene (correct skeleton, mover setup, clip/hash wiring) so only the keyframes are manual.

## Required pieces (install once)
- **Blender + Sollumz** (MIT) — the authoring app. `blender --background` for headless export.
- **CodeWalker / CodeWalker.API** (flobros, MIT) — `.ycd.xml` → `.ycd`, package into RPF.
- **A correct target skeleton** — the ped/object skeleton whose bone tags you're animating (from OpenIV/CodeWalker).
- The bridge's existing **`reload_content_changeset`** + **animation natives** — already shipped.

## The headless helper — `tools/author_animation.py` (BUILT)
The pipeline is now a single staged orchestrator. Stdlib-only; each stage is skippable; the external
stages default to **dry-run** (print the exact command/request before firing).

```
# See what's installed:
python tools/author_animation.py --preflight

# Dry-run from a .blend (recommended — shows the full plan, runs nothing external):
python tools/author_animation.py --blend skate.blend --dict myskate --clip push \
    --rpf "mods/update/x64/dlcpacks/myskate/dlc.rpf" --group MYSKATE_AUTOGEN

# Execute for real (needs Blender+Sollumz, CodeWalker.API on :5555, and GTA+bridge running):
python tools/author_animation.py --blend skate.blend --dict myskate --clip push \
    --rpf "...dlc.rpf" --group MYSKATE_AUTOGEN --go

# Already have a .ycd.xml? skip Blender:   --ycd-xml myskate.ycd.xml ...
# Already have a packaged .ycd? skip to inject+play:   --ycd myskate.ycd ...
```

**Stages:** (1) export — `blender --background tools/blender_export_ycd.py` (Sollumz YCD → `.ycd.xml`);
(2) package — CodeWalker.API `POST /api/import` (`.ycd.xml` → binary `.ycd` in the RPF); (3) inject —
bridge `reload_content_changeset {group}`; (4) play — bridge `PLAYER_PED_ID → REQUEST_ANIM_DICT → poll
HAS_ANIM_DICT_LOADED → TASK_PLAY_ANIM`.

**What's verified vs what you verify once:**
- *Self-tested offline* (`--self-test`): the staging/skip/dry-run logic, the inject call, and the exact
  play-native sequence — all solid.
- *Verify once against your tools* (clearly marked in the code): the **Sollumz export operator** call in
  `tools/blender_export_ycd.py` (operator signatures drift between Sollumz versions — prefer the `--blend`
  path), and the **CodeWalker.API import form-field names** in `author_animation.py` (`import_fields`,
  check your CW.API Swagger at :5555). Both default to dry-run so you see the request before sending.

**Recommended workflow:** build/retarget the animation in Blender+Sollumz once (the creative part —
a CLIP_DICTIONARY with correct skeleton + mover), save a `.blend`, then `author_animation --blend ... --go`
drives export → package → inject → play hands-off. Retargeting a Mixamo FBX into that `.blend` is the
shortcut to skip keyframing.
