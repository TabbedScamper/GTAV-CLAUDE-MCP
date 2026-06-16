using System;
using System.Drawing;
using System.IO;
using GTA;
using GTA.Math;
using GTA.Native;
using GTA.UI;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// HeistInteractions — the C#/SHVDN half of the heist engine. It runs the PLAYER-DRIVEN, animated beats
// that PyLoaderV's Python bridge CAN'T (synchronized scenes + the text/help builders need a real SHVDN
// script-thread context — see memory gtav-interactive-beats-need-csharp). The Python runner ORCHESTRATES:
// it writes ONE current beat to %LOCALAPPDATA%\GTAV-Claude-MCP\heist_beat.json; this script executes it
// (marker -> proximity -> Press E -> synced scene -> phase-poll) and writes heist_status.json back. The
// runner waits for state==done with the matching seq, then writes the next beat.
//
// Beat kinds:
//   goto        : draw a marker; done when player within radius (no key)
//   press_anim  : marker + "Press E" -> synchronized-scene animation (ped + props aligned) -> done
//   open_vault  : ACTIVATE_INTERIOR_ENTITY_SET("SET_VAULT_DOOR_OPEN") at a coord -> done
//   unlock_door : SET_STATE_OF_CLOSEST_DOOR_OF_TYPE + DOOR_SYSTEM auto-open (walk-in) -> done
//   clear       : stop everything, restore control
// Pattern harvested from Cayo Perico SP + M8T (Examples/PATTERNS/15).
namespace ClaudeChatUI
{
    public class HeistInteractions : Script
    {
        private readonly string beatFile;
        private readonly string statusFile;
        private int readTimer = 0;

        private int curSeq = -1;          // seq of the beat we're currently running
        private string state = "idle";    // idle | prompting | running | done
        private JObject beat;             // the active beat
        private int scene = -1;
        private Prop[] props = new Prop[0];
        private float runT0 = 0f;         // GameTime ms when the anim started
        private bool controlOff = false;

        private const int MOVER_FLAG = 1148846080;   // 0x44000000 == 1000.0f bits (the M8T/Cayo loop+hold flag)

        public HeistInteractions()
        {
            string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GTAV-Claude-MCP");
            Directory.CreateDirectory(dir);
            beatFile = Path.Combine(dir, "heist_beat.json");
            statusFile = Path.Combine(dir, "heist_status.json");
            Tick += OnTick;
        }

        private void OnTick(object sender, EventArgs e)
        {
            try
            {
                if (++readTimer > 4) { readTimer = 0; ReadBeat(); }   // ~65ms
                if (beat == null) return;
                string kind = (string)beat["kind"] ?? "";
                switch (kind)
                {
                    case "goto":        TickGoto(); break;
                    case "press_anim":  TickPressAnim(); break;
                    case "open_vault":  TickOpenVault(); break;
                    case "unlock_door": TickUnlockDoor(); break;
                    case "clear":       DoClear(); break;
                }
            }
            catch (Exception ex) { GTA.UI.Notification.Show("~r~Heist beat error: " + ex.Message); }
        }

        // ---- protocol -------------------------------------------------------
        private void ReadBeat()
        {
            try
            {
                if (!File.Exists(beatFile)) { beat = null; return; }
                string txt = File.ReadAllText(beatFile);
                if (string.IsNullOrWhiteSpace(txt)) { beat = null; return; }
                JObject j = JObject.Parse(txt);
                int seq = (int?)j["seq"] ?? 0;
                if (seq != curSeq)         // a NEW beat arrived -> reset and start it
                {
                    CleanupScene();
                    curSeq = seq; beat = j; state = "prompting"; scene = -1;
                    runT0 = 0f;
                    WriteStatus();
                }
            }
            catch { /* mid-write race; try next tick */ }
        }

        private void WriteStatus()
        {
            try
            {
                float prog = (scene >= 0) ? Function.Call<float>(Hash.GET_SYNCHRONIZED_SCENE_PHASE, scene) : 0f;
                var o = new JObject { ["seq"] = curSeq, ["state"] = state, ["progress"] = prog };
                File.WriteAllText(statusFile, o.ToString(Formatting.None));
            }
            catch { }
        }

        // ---- helpers --------------------------------------------------------
        private Vector3 V(JToken t) => new Vector3((float)t[0], (float)t[1], (float)t[2]);
        private Ped Me => Game.Player.Character;

        // Snap a coord's Z to the real ground so markers sit ON the floor and anchors don't lift the
        // player into the air (the Python-read Z can be ~1m high). Falls back to the passed Z.
        private Vector3 Grounded(Vector3 p)
        {
            OutputArgument outZ = new OutputArgument();
            if (Function.Call<bool>(Hash.GET_GROUND_Z_FOR_3D_COORD, p.X, p.Y, p.Z + 1.0f, outZ, false, false))
            {
                float gz = outZ.GetResult<float>();
                if (Math.Abs(gz - p.Z) < 5f) return new Vector3(p.X, p.Y, gz);  // sane correction only
            }
            return p;
        }

        private void HelpThisFrame(string msg)
        {
            Function.Call(Hash.BEGIN_TEXT_COMMAND_DISPLAY_HELP, "STRING");
            Function.Call(Hash.ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME, msg);
            Function.Call(Hash.END_TEXT_COMMAND_DISPLAY_HELP, 0, false, true, -1);
        }

        private void Marker(Vector3 p)
        {
            Vector3 g = Grounded(p);   // g.Z is true ground; cylinder base sits at the ground coord
            World.DrawMarker(MarkerType.VerticalCylinder, new Vector3(g.X, g.Y, g.Z - 0.1f), Vector3.Zero,
                Vector3.Zero, new Vector3(0.8f, 0.8f, 0.8f), Color.FromArgb(200, 240, 200, 40),
                true, false, false, null, null, false);
        }

        private void SetControl(bool on)
        {
            Game.Player.CanControlCharacter = on;
            Function.Call(Hash.SET_PED_CAN_RAGDOLL, Me.Handle, on);
            controlOff = !on;
        }

        // ---- beat: goto -----------------------------------------------------
        private void TickGoto()
        {
            Vector3 m = V(beat["marker"]);
            float r = (float?)beat["radius"] ?? 2.0f;
            Marker(m);
            if (state != "done" && Me.Position.DistanceTo2D(m) < r)
            {
                state = "done"; WriteStatus();
            }
        }

        // ---- beat: press_anim (the synchronized-scene interaction) -----------
        private void TickPressAnim()
        {
            Vector3 m = V(beat["marker"]);
            float r = (float?)beat["radius"] ?? 1.6f;
            if (state == "prompting")
            {
                Marker(m);
                if (Me.Position.DistanceTo2D(m) < r)
                {
                    HelpThisFrame((string)beat["prompt"] ?? "Press ~INPUT_CONTEXT~ to interact");
                    if (Game.IsControlJustPressed(GTA.Control.Context))
                        StartAnim();
                }
            }
            else if (state == "running")
            {
                float hold = (float?)beat["hold_seconds"] ?? 6f;
                float phase = scene >= 0 ? Function.Call<float>(Hash.GET_SYNCHRONIZED_SCENE_PHASE, scene) : 1f;
                bool timeUp = (Game.GameTime - runT0) > hold * 1000f;
                if (timeUp || phase > 0.985f)
                {
                    FinishAnim();
                }
            }
        }

        private void StartAnim()
        {
            Vector3 anchor = Grounded(beat["anchor"] != null ? V(beat["anchor"]) : V(beat["marker"]));
            float heading = (float?)beat["heading"] ?? Me.Heading;
            string dict = (string)beat["dict"];
            string pedClip = (string)beat["ped_clip"] ?? "action";

            Function.Call(Hash.REQUEST_ANIM_DICT, dict);
            int guard = 0;
            while (!Function.Call<bool>(Hash.HAS_ANIM_DICT_LOADED, dict) && guard++ < 100) Script.Wait(0);

            SetControl(false);
            // snap the player to the anchor (sub-step, NOT a teleport-away) so the anim lines up
            Me.Position = anchor;
            Me.Heading = heading;

            scene = Function.Call<int>(Hash.CREATE_SYNCHRONIZED_SCENE, anchor.X, anchor.Y, anchor.Z, 0f, 0f, heading, 2);

            // props: spawn frozen + collisionless, then bind each to the SAME scene with its own clip
            var pj = beat["props"] as JArray;
            if (pj != null)
            {
                props = new Prop[pj.Count];
                for (int i = 0; i < pj.Count; i++)
                {
                    string model = (string)pj[i]["model"];
                    string clip = (string)pj[i]["clip"];
                    Model mdl = new Model(model); mdl.Request(1000);
                    int g2 = 0; while (!mdl.IsLoaded && g2++ < 100) Script.Wait(0);
                    Prop pr = World.CreateProp(mdl, anchor, false, false);
                    if (pr != null)
                    {
                        pr.IsPositionFrozen = true; pr.IsCollisionEnabled = false;
                        Function.Call(Hash.PLAY_SYNCHRONIZED_ENTITY_ANIM, pr.Handle, scene, clip, dict,
                            1000f, -8f, -8f, heading + 180f, MOVER_FLAG, 0);
                        props[i] = pr;
                    }
                    mdl.MarkAsNoLongerNeeded();
                }
            }

            // the player ped joins the scene (TASK + a redundant PLAY for commitment, exactly like Cayo)
            Function.Call(Hash.TASK_SYNCHRONIZED_SCENE, Me.Handle, scene, dict, pedClip,
                1000f, -4f, 128, 0, MOVER_FLAG, 0);
            Me.IsInvincible = true;
            runT0 = Game.GameTime;
            state = "running"; WriteStatus();
        }

        private void FinishAnim()
        {
            Me.Task.ClearAll();
            Me.IsInvincible = false;
            SetControl(true);
            CleanupScene();
            state = "done"; WriteStatus();
        }

        private void CleanupScene()
        {
            if (props != null)
                foreach (var p in props) { try { p?.Delete(); } catch { } }
            props = new Prop[0];
            scene = -1;
            if (controlOff) SetControl(true);
        }

        // ---- beat: open_vault (real geometry via interior entity set) --------
        private void TickOpenVault()
        {
            if (state == "done") return;
            Vector3 at = V(beat["at"]);
            int interior = Function.Call<int>(Hash.GET_INTERIOR_AT_COORDS, at.X, at.Y, at.Z);
            if (interior != 0)
            {
                Function.Call(Hash.DEACTIVATE_INTERIOR_ENTITY_SET, interior, "SET_VAULT_DOOR_CLOSED");
                Function.Call(Hash.ACTIVATE_INTERIOR_ENTITY_SET, interior, "SET_VAULT_DOOR_OPEN");
                Function.Call(Hash.REFRESH_INTERIOR, interior);
            }
            Function.Call(Hash.PLAY_SOUND_FRONTEND, -1, "Hack_Success", "DLC_HEIST_BIOLAB_PREP_HACKING_SOUNDS", true);
            state = "done"; WriteStatus();
        }

        // ---- beat: unlock_door (walk-in via the door system) -----------------
        private void TickUnlockDoor()
        {
            string model = (string)beat["model"];
            Vector3 at = V(beat["at"]);
            int mh = Function.Call<int>(Hash.GET_HASH_KEY, model);
            Function.Call(Hash.SET_STATE_OF_CLOSEST_DOOR_OF_TYPE, mh, at.X, at.Y, at.Z, false, 0f, false);
            // auto-open: register in the door system within 5m (harvested from Enable-All-Interiors)
            int doorEnum = Function.Call<int>(Hash.GET_HASH_KEY, model + "_" + (int)at.X + "_" + (int)at.Y);
            if (!Function.Call<bool>(Hash.IS_DOOR_REGISTERED_WITH_SYSTEM, doorEnum))
                Function.Call(Hash.ADD_DOOR_TO_SYSTEM, doorEnum, mh, at.X, at.Y, at.Z, false, false, false);
            Function.Call(Hash.DOOR_SYSTEM_SET_AUTOMATIC_RATE, doorEnum, 30f, false, false);
            Function.Call(Hash.DOOR_SYSTEM_SET_AUTOMATIC_DISTANCE, doorEnum, 5f, false, false);
            if (state != "done") { state = "done"; WriteStatus(); }   // fire-and-stay (runner moves on)
        }

        private void DoClear()
        {
            CleanupScene();
            if (state != "done") { state = "done"; WriteStatus(); }
        }
    }
}
