"""
blender_export_ycd.py — run INSIDE Blender by author_animation.py:
    blender --background --python blender_export_ycd.py -- --blend scene.blend --out anims.ycd.xml --dict ... --clip ...
    blender --background --python blender_export_ycd.py -- --fbx walk.fbx  --out anims.ycd.xml --dict ... --clip ... --skeleton player

Produces a CodeWalker `.ycd.xml` (Sollumz exports YCD as XML; CodeWalker/CodeWalker.API converts to binary .ycd).

HONESTY / SCOPE (read before using):
  * The STANDARD-BLENDER parts here are solid: argv parsing after `--`, FBX import, 30fps scene setup,
    saving output. These run on any Blender.
  * The SOLLUMZ-SPECIFIC call (the actual YCD export) is the ONE seam you must VERIFY against your
    installed Sollumz version — operator names/signatures drift between Sollumz releases. It's marked
    `>>> VERIFY <<<` below with the operator we documented (sollumz.export_assets) and the data model
    from Examples/PATTERNS/07-custom-animation-authoring.md. If it errors, open Sollumz once in the GUI,
    note the exact File>Export>Sollumz operator + options, and adjust the marked block.
  * RECOMMENDED path: --blend (you build/retarget the CLIP_DICTIONARY in Blender+Sollumz once, save the
    .blend; this script just opens it and exports). The --fbx path additionally tries to scaffold the
    Sollumz scene from a raw FBX, which is EXPERIMENTAL and the most version-sensitive — prefer --blend.

Reminders from PATTERNS/07 (why exports break): bones are addressed by HASH so the TARGET SKELETON must
match; the MOVER track (bone #0, tracks 5/6) carries world translation or the anim 'moonwalks in place';
the Animation's hash must equal the Clip's animation_hash; an empty Action aborts the whole dictionary;
GTA anims are 30fps.
"""
import sys, os


def _args():
    # Blender passes script args after a literal "--"
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    import argparse
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--blend", help="a .blend already containing a Sollumz CLIP_DICTIONARY (recommended)")
    g.add_argument("--fbx", help="a raw FBX/Mixamo clip to scaffold (EXPERIMENTAL)")
    ap.add_argument("--out", required=True, help="output .ycd.xml path")
    ap.add_argument("--dict", dest="dict_name", required=True)
    ap.add_argument("--clip", dest="clip_name", required=True)
    ap.add_argument("--skeleton", default=None)
    return ap.parse_args(argv)


def _log(*a):
    print("[blender_export_ycd]", *a, flush=True)


def main():
    a = _args()
    try:
        import bpy
    except ImportError:
        _log("ERROR: not running inside Blender (no bpy). Invoke via: blender --background --python this.py -- ...")
        return 2

    # --- standard Blender: 30fps + load source -----------------------------------------------------
    bpy.context.scene.render.fps = 30
    bpy.context.scene.render.fps_base = 1.0

    if a.blend:
        if not os.path.exists(a.blend):
            _log("ERROR: blend not found:", a.blend); return 2
        bpy.ops.wm.open_mainfile(filepath=a.blend)
        _log("opened", a.blend)
    else:
        if not os.path.exists(a.fbx):
            _log("ERROR: fbx not found:", a.fbx); return 2
        bpy.ops.import_scene.fbx(filepath=a.fbx)
        _log("imported FBX", a.fbx, "(EXPERIMENTAL scaffold path — prefer --blend)")
        _log("NOTE: a raw FBX still needs a Sollumz CLIP_DICTIONARY + correct GTA skeleton + mover setup")
        _log("      before export. See PATTERNS/07. Build that once in Blender and save a .blend, then")
        _log("      use --blend for a reliable headless export.")

    # --- ensure Sollumz is present ----------------------------------------------------------------
    if not (hasattr(bpy.ops, "sollumz") and hasattr(bpy.ops.sollumz, "export_assets")):
        _log("ERROR: Sollumz add-on not enabled (bpy.ops.sollumz.export_assets missing).")
        _log("       Enable Sollumz in Blender Preferences > Add-ons, then retry.")
        return 3

    # --- find the CLIP_DICTIONARY object ----------------------------------------------------------
    clip_dict = None
    for ob in bpy.data.objects:
        if getattr(ob, "sollum_type", None) in ("sollumz_clip_dictionary", "CLIP_DICTIONARY") \
           or str(getattr(ob, "sollum_type", "")).lower().endswith("clip_dictionary"):
            clip_dict = ob
            break
    if clip_dict is None:
        _log("ERROR: no Sollumz CLIP_DICTIONARY object in the scene.")
        _log("       (--blend should contain one; the --fbx path does NOT auto-build it — see PATTERNS/07.)")
        return 4
    _log("found CLIP_DICTIONARY:", clip_dict.name)

    # select only it (Sollumz export typically acts on selection/active)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        clip_dict.select_set(True)
        bpy.context.view_layer.objects.active = clip_dict
    except Exception as e:
        _log("warn: could not set selection:", e)

    out_dir = os.path.dirname(os.path.abspath(a.out)) or "."
    os.makedirs(out_dir, exist_ok=True)

    # ============================ >>> VERIFY against your Sollumz version <<< ======================
    # Sollumz exports YCD as CodeWalker .ycd.xml via the `sollumz.export_assets` operator (it dispatches
    # on the selected CLIP_DICTIONARY -> export_ycd -> write_xml). The exact keyword args (directory vs
    # filepath, export_settings) vary by release. The call below is the documented shape; if it errors,
    # check File > Export > Sollumz in the GUI and adjust.
    try:
        bpy.ops.sollumz.export_assets(directory=out_dir)
        _log("export_assets(directory=%r) returned" % out_dir)
    except TypeError:
        # older/newer signature fallback
        try:
            bpy.ops.sollumz.export_assets(filepath=a.out)
            _log("export_assets(filepath=%r) returned" % a.out)
        except Exception as e:
            _log("ERROR: sollumz.export_assets failed (VERIFY the operator signature):", e)
            return 5
    except Exception as e:
        _log("ERROR: sollumz.export_assets failed (VERIFY the operator signature):", e)
        return 5
    # ===============================================================================================

    # Sollumz names the file after the object; normalize to the requested --out if needed.
    if not os.path.exists(a.out):
        produced = os.path.join(out_dir, f"{clip_dict.name}.ycd.xml")
        if os.path.exists(produced) and os.path.abspath(produced) != os.path.abspath(a.out):
            os.replace(produced, a.out)
            _log("renamed", produced, "->", a.out)

    if os.path.exists(a.out):
        _log("OK ->", a.out)
        return 0
    _log("ERROR: expected output not found:", a.out, "(check the export operator settings)")
    return 6


if __name__ == "__main__":
    sys.exit(main())
