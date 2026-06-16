"""
author_animation.py — HEADLESS custom-animation pipeline orchestrator.

Chains: (1) Blender+Sollumz export of an FBX/Mixamo clip -> .ycd.xml  ->  (2) package the .ycd into a
mods RPF (CodeWalker.API)  ->  (3) hot-reload it via the in-game bridge  ->  (4) play it to verify.

DESIGN (read this — it's why this is safe, not soup):
  * Each stage is INDEPENDENTLY usable and SKIPPABLE. Pass an `.fbx` to run from scratch, an `.ycd_xml`
    to skip Blender, or an `.ycd` already in the RPF to skip straight to inject+play. So the pipeline is
    useful from whatever artifact you have.
  * The stages this orchestrator can fully verify (inject via the bridge `reload_content_changeset`, and
    play via `call_native_by_name`) are solid and self-tested below.
  * The stages that depend on EXTERNAL tools this script can't run/verify here (Blender export, and the
    CodeWalker.API import request BODY — its exact fields live in the CW.API Swagger/.http) are
    PLUGGABLE and default to DRY-RUN: they print the exact command/request they WOULD issue so you can
    verify against your tool before firing. Override field names / commands via the config args.
  * `--dry-run` (default for the external stages) issues NO real external calls.

Stdlib only. Run `python tools/author_animation.py --self-test` for the offline orchestration test,
or `python tools/author_animation.py --help` for the CLI.  See docs/CUSTOM-ANIMATIONS.md.
"""
import argparse, json, os, socket, struct, subprocess, sys, time, urllib.request, urllib.parse

DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 27015               # the in-game bridge socket
DEFAULT_CW_API = "http://127.0.0.1:5555"  # CodeWalker.API default
# Canonical TASK_PLAY_ANIM native arg template (sourced from Examples/PATTERNS/05-animation.md).
# Order: ped, dict, clip, blendIn, blendOut, duration, flag, playbackRate, lockX, lockY, lockZ.
PLAY_BLEND_IN, PLAY_BLEND_OUT, PLAY_DURATION, PLAY_RATE = 8.0, -8.0, -1, 1.0


# ---------------------------------------------------------------- bridge socket client
class BridgeClient:
    """Talks the in-game bridge protocol: 4-byte LE length + JSON {command, params}."""
    def __init__(self, host=DEFAULT_BRIDGE_HOST, port=DEFAULT_BRIDGE_PORT, timeout=30.0):
        self.host, self.port, self.timeout = host, port, timeout

    def send(self, command, params=None):
        payload = json.dumps({"command": command, "params": params or {}}).encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall(struct.pack("<I", len(payload)) + payload)
                head = s.recv(4)
                if len(head) < 4:
                    return {"error": "bridge closed connection", "connected": False}
                size = struct.unpack("<I", head)[0]
                body = b""
                while len(body) < size:
                    chunk = s.recv(min(4096, size - len(body)))
                    if not chunk:
                        break
                    body += chunk
                return json.loads(body.decode("utf-8"))
        except ConnectionRefusedError:
            return {"error": "bridge not running (start GTA + load bridge.py)", "connected": False}
        except socket.timeout:
            return {"error": "bridge timeout", "connected": False}
        except Exception as e:
            return {"error": f"bridge error: {e}", "connected": False}

    def available(self):
        r = self.send("status")
        return isinstance(r, dict) and not r.get("error")


# ---------------------------------------------------------------- CodeWalker.API client (scaffold)
class CodeWalkerAPI:
    """Confirmed endpoints (service-status/set-config/reload-services) are solid; the import BODY is a
    documented SCAFFOLD — verify field names against your CW.API Swagger (http://localhost:5555)."""
    def __init__(self, base_url=DEFAULT_CW_API, timeout=120.0, import_fields=None):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        # Overridable mapping of our concept -> the CW.API form field names. Defaults are best-effort
        # from the README; CHANGE THESE to match your Swagger if import fails.
        self.import_fields = import_fields or {"rpf": "rpfPath", "file": "fileName", "input": "inputPath", "xml": "xml"}

    def _get(self, path, params=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def status(self):
        return self._get("/api/service-status")

    def jenkins(self, text, encoding=0):
        return self._get("/api/hash/jenkins", {"input": text, "encoding": encoding})

    def import_request(self, ycd_xml_path, rpf_path, dest_name):
        """Return the request DESCRIPTION this would POST (so dry-run can show it). The actual POST is
        in import_file(); kept separate so the orchestrator can print before firing."""
        f = self.import_fields
        return {"method": "POST", "url": f"{self.base}/api/import",
                "form": {f["rpf"]: rpf_path, f["file"]: dest_name, f["input"]: ycd_xml_path, f["xml"]: "true"}}

    def import_file(self, ycd_xml_path, rpf_path, dest_name):
        req = self.import_request(ycd_xml_path, rpf_path, dest_name)
        data = urllib.parse.urlencode(req["form"]).encode("utf-8")
        rq = urllib.request.Request(req["url"], data=data, method="POST")
        with urllib.request.urlopen(rq, timeout=self.timeout) as r:
            body = r.read().decode("utf-8")
            try:
                return json.loads(body)
            except ValueError:
                return {"raw": body, "http_status": r.status}

    def available(self):
        try:
            self.status(); return True
        except Exception:
            return False


def _stage(name, status, **extra):
    return {"stage": name, "status": status, **extra}


# ---------------------------------------------------------------- the pipeline
def author_animation(*, fbx=None, blend=None, ycd_xml=None, ycd=None,
                     dict_name, clip_name, rpf=None, group=None,
                     skeleton=None, play=True, anim_flag=1,
                     work_dir=None, blender_exe="blender", blender_script=None,
                     bridge=None, cw=None, dry_run=True, runner=None):
    """Run the custom-animation pipeline. Provide ONE source: blend (a .blend with a Sollumz
    CLIP_DICTIONARY — recommended), fbx (experimental scaffold), ycd_xml (skip Blender), or ycd
    (skip Blender+package). `group` = the DLC changeset group to reload (e.g. '<PACK>_AUTOGEN').
    `bridge`/`cw`/`runner` are injectable for testing. Returns {ok, stages:[...]}."""
    bridge = bridge or BridgeClient()
    cw = cw or CodeWalkerAPI()
    runner = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True))
    work_dir = work_dir or os.getcwd()
    stages = []

    # ---- stage 1: EXPORT (Blender + Sollumz) — OPTIONAL, dry-run-first ----
    if (blend or fbx) and not ycd_xml and not ycd:
        out_xml = os.path.join(work_dir, f"{dict_name}.ycd.xml")
        script = blender_script or os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_export_ycd.py")
        src_flag = ["--blend", blend] if blend else ["--fbx", fbx]
        argv = [blender_exe, "--background", "--python", script, "--",
                *src_flag, "--out", out_xml, "--dict", dict_name, "--clip", clip_name]
        if skeleton:
            argv += ["--skeleton", skeleton]
        if dry_run:
            stages.append(_stage("export", "dry-run", command=" ".join(argv),
                                 note="Blender export NOT run (dry-run). Verify blender_export_ycd.py against your Sollumz version."))
            ycd_xml = out_xml  # assume it would exist, so downstream dry-run can continue
        else:
            res = runner(argv)
            ok = getattr(res, "returncode", 1) == 0 and os.path.exists(out_xml)
            stages.append(_stage("export", "ok" if ok else "error",
                                 returncode=getattr(res, "returncode", None),
                                 stderr=(getattr(res, "stderr", "") or "")[-2000:], out=out_xml))
            if not ok:
                return {"ok": False, "stages": stages}
            ycd_xml = out_xml
    elif blend or fbx:
        stages.append(_stage("export", "skipped", note="ycd_xml/ycd already provided"))

    # ---- stage 2: PACKAGE (.ycd.xml -> .ycd in a mods RPF via CodeWalker.API) — pluggable, dry-run-first ----
    if ycd and not ycd_xml:
        stages.append(_stage("package", "skipped", note="prebuilt .ycd provided; assumed already in RPF", ycd=ycd))
    else:
        if not rpf:
            stages.append(_stage("package", "error", error="rpf target path required to package the .ycd"))
            return {"ok": False, "stages": stages}
        dest = f"{dict_name}.ycd"
        req = cw.import_request(ycd_xml, rpf, dest)
        if dry_run:
            stages.append(_stage("package", "dry-run", request=req,
                                 note="CodeWalker.API import NOT sent (dry-run). Verify form fields vs your CW.API Swagger."))
        else:
            try:
                r = cw.import_file(ycd_xml, rpf, dest)
                err = isinstance(r, dict) and r.get("error")
                stages.append(_stage("package", "error" if err else "ok", response=r, request=req))
                if err:
                    return {"ok": False, "stages": stages}
            except Exception as e:
                stages.append(_stage("package", "error", error=str(e), request=req,
                                     hint="Is CodeWalker.API running on :5555? Check /api/service-status and the import field names."))
                return {"ok": False, "stages": stages}

    # ---- stage 3: INJECT (hot-reload the RPF changeset via the bridge) — SOLID ----
    if not group:
        stages.append(_stage("inject", "error", error="group required (the DLC changeset group, e.g. '<PACK>_AUTOGEN')"))
        return {"ok": False, "stages": stages}
    if dry_run:
        stages.append(_stage("inject", "dry-run", command="bridge reload_content_changeset", params={"group": group}))
    else:
        r = bridge.send("reload_content_changeset", {"group": group})
        ok = isinstance(r, dict) and r.get("success")
        stages.append(_stage("inject", "ok" if ok else "error", response=r))
        if not ok:
            return {"ok": False, "stages": stages}

    # ---- stage 4: PLAY (load the dict + play the clip on the player ped) — SOLID ----
    if play:
        play_plan = [
            ("call_native_by_name", {"name": "PLAYER_PED_ID", "return_type": "int"}),
            ("call_native_by_name", {"name": "REQUEST_ANIM_DICT", "args": [dict_name], "return_type": "void"}),
            # then poll HAS_ANIM_DICT_LOADED, then TASK_PLAY_ANIM(ped, dict, clip, ...11 args...)
        ]
        if dry_run:
            stages.append(_stage("play", "dry-run", plan=play_plan,
                                 task_play_anim_args=["<ped>", dict_name, clip_name, PLAY_BLEND_IN, PLAY_BLEND_OUT,
                                                      PLAY_DURATION, anim_flag, PLAY_RATE, False, False, False]))
        else:
            ped_r = bridge.send("call_native_by_name", {"name": "PLAYER_PED_ID", "return_type": "int"})
            ped = ped_r.get("result") if isinstance(ped_r, dict) else None
            if not ped:
                stages.append(_stage("play", "error", error="could not get player ped", response=ped_r))
                return {"ok": False, "stages": stages}
            bridge.send("call_native_by_name", {"name": "REQUEST_ANIM_DICT", "args": [dict_name], "return_type": "void"})
            loaded = False
            for _ in range(40):  # ~2s of polling
                hr = bridge.send("call_native_by_name", {"name": "HAS_ANIM_DICT_LOADED", "args": [dict_name], "return_type": "bool"})
                if isinstance(hr, dict) and hr.get("result"):
                    loaded = True
                    break
                time.sleep(0.05)
            if not loaded:
                stages.append(_stage("play", "error", error=f"anim dict '{dict_name}' never loaded (is it really in the reloaded RPF?)"))
                return {"ok": False, "stages": stages}
            tp = bridge.send("call_native_by_name", {"name": "TASK_PLAY_ANIM",
                  "args": [ped, dict_name, clip_name, PLAY_BLEND_IN, PLAY_BLEND_OUT, PLAY_DURATION,
                           anim_flag, PLAY_RATE, False, False, False], "return_type": "void"})
            stages.append(_stage("play", "ok" if not (isinstance(tp, dict) and tp.get("error")) else "error", response=tp))

    return {"ok": all(s["status"] in ("ok", "skipped", "dry-run") for s in stages), "stages": stages}


def preflight(bridge=None, cw=None, blender_exe="blender"):
    """Report which stages can actually run right now."""
    bridge = bridge or BridgeClient()
    cw = cw or CodeWalkerAPI()
    import shutil
    return {"blender": bool(shutil.which(blender_exe)), "codewalker_api": cw.available(), "bridge": bridge.available()}


# ---------------------------------------------------------------- CLI
def _main(argv=None):
    ap = argparse.ArgumentParser(description="Headless custom-animation pipeline (export->package->inject->play).")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--blend", help="a .blend with a Sollumz CLIP_DICTIONARY (recommended headless path)")
    src.add_argument("--fbx", help="source FBX/Mixamo clip (experimental scaffold, needs Blender+Sollumz)")
    src.add_argument("--ycd-xml", help="prebuilt CodeWalker .ycd.xml (skip Blender)")
    src.add_argument("--ycd", help="prebuilt binary .ycd already in the RPF (skip Blender+package)")
    ap.add_argument("--dict", dest="dict_name", required=False, help="anim dictionary name (REQUEST_ANIM_DICT)")
    ap.add_argument("--clip", dest="clip_name", required=False, help="clip name to play (TASK_PLAY_ANIM)")
    ap.add_argument("--rpf", help="target mods RPF path (for packaging)")
    ap.add_argument("--group", help="DLC changeset group to reload, e.g. '<PACK>_AUTOGEN'")
    ap.add_argument("--skeleton", help="target skeleton (for Blender retarget/export)")
    ap.add_argument("--anim-flag", type=int, default=1, help="TASK_PLAY_ANIM flag (1=loop)")
    ap.add_argument("--blender-exe", default="blender")
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--go", action="store_true", help="actually execute (default is dry-run for external stages)")
    ap.add_argument("--preflight", action="store_true", help="report which stages can run, then exit")
    ap.add_argument("--self-test", action="store_true", help="run the offline orchestration self-test")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()
    if a.preflight:
        print(json.dumps(preflight(blender_exe=a.blender_exe), indent=2)); return 0
    if not a.dict_name or not a.clip_name:
        ap.error("--dict and --clip are required (except for --self-test/--preflight)")
    res = author_animation(fbx=a.fbx, blend=a.blend, ycd_xml=a.ycd_xml, ycd=a.ycd, dict_name=a.dict_name,
                           clip_name=a.clip_name, rpf=a.rpf, group=a.group, skeleton=a.skeleton, play=not a.no_play,
                           anim_flag=a.anim_flag, blender_exe=a.blender_exe, dry_run=not a.go)
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


# ---------------------------------------------------------------- offline self-test
def _self_test():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    calls = {"bridge": [], "cw": [], "runner": []}

    class FakeBridge:
        def send(self, command, params=None):
            calls["bridge"].append((command, params))
            if command == "reload_content_changeset":
                return {"success": True, "group": params["group"]}
            if command == "call_native_by_name":
                n = params.get("name")
                if n == "PLAYER_PED_ID":      return {"result": 2}
                if n == "HAS_ANIM_DICT_LOADED": return {"result": True}
                return {"success": True, "result": None}
            return {"success": True}
        def available(self): return True

    class FakeCW:
        def import_request(self, x, rpf, dest):
            return {"method": "POST", "url": "http://cw/api/import", "form": {"rpfPath": rpf, "fileName": dest, "inputPath": x, "xml": "true"}}
        def import_file(self, x, rpf, dest):
            calls["cw"].append((x, rpf, dest)); return {"success": True}

    def fake_runner(argv):
        calls["runner"].append(argv)
        class R: returncode = 0; stderr = ""
        return R()

    # 1) dry-run from FBX: every external stage is a dry-run, NO real calls
    r = author_animation(fbx="walk.fbx", dict_name="myanims", clip_name="skate_push", rpf="mods/x.rpf",
                         group="MYPACK_AUTOGEN", bridge=FakeBridge(), cw=FakeCW(), runner=fake_runner, dry_run=True)
    assert [s["stage"] for s in r["stages"]] == ["export", "package", "inject", "play"], r
    assert all(s["status"] == "dry-run" for s in r["stages"]), r
    assert not calls["bridge"] and not calls["cw"] and not calls["runner"], "dry-run must issue NO real calls"
    assert r["ok"]

    # 2) live run from a prebuilt .ycd.xml (skip Blender): package+inject+play execute for real (mocked)
    calls = {"bridge": [], "cw": [], "runner": []}
    fb = FakeBridge()
    r = author_animation(ycd_xml="myanims.ycd.xml", dict_name="myanims", clip_name="skate_push", rpf="mods/x.rpf",
                         group="MYPACK_AUTOGEN", bridge=fb, cw=FakeCW(), runner=fake_runner, dry_run=False)
    stages = {s["stage"]: s["status"] for s in r["stages"]}
    assert stages == {"package": "ok", "inject": "ok", "play": "ok"}, r
    assert calls["cw"] == [("myanims.ycd.xml", "mods/x.rpf", "myanims.ycd")], calls["cw"]
    assert ("reload_content_changeset", {"group": "MYPACK_AUTOGEN"}) in calls["bridge"]
    # the play sequence: PLAYER_PED_ID -> REQUEST_ANIM_DICT -> HAS_ANIM_DICT_LOADED -> TASK_PLAY_ANIM
    play_names = [p.get("name") for (c, p) in calls["bridge"] if c == "call_native_by_name"]
    assert play_names[:2] == ["PLAYER_PED_ID", "REQUEST_ANIM_DICT"] and "TASK_PLAY_ANIM" in play_names, play_names
    tpa = [p for (c, p) in calls["bridge"] if c == "call_native_by_name" and p.get("name") == "TASK_PLAY_ANIM"][0]
    assert tpa["args"][1:3] == ["myanims", "skate_push"] and len(tpa["args"]) == 11, tpa

    # 3) prebuilt .ycd: package is skipped
    r = author_animation(ycd="myanims.ycd", dict_name="myanims", clip_name="skate_push",
                         group="MYPACK_AUTOGEN", bridge=FakeBridge(), cw=FakeCW(), runner=fake_runner, dry_run=True)
    assert [s["status"] for s in r["stages"] if s["stage"] == "package"] == ["skipped"], r

    print("author_animation self-test PASSED — staging/skip/dry-run/play-sequence all correct")
    print("  (Blender export + CodeWalker.API import need the real tools; run with --go once installed.)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
