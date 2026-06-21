using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using GTA;
using GTA.Math;
using GTA.Native;

namespace ClaudeBridge
{
    /// <summary>
    /// High-level "do something fun" verbs, built on the SHVDN GTA.* API (safe marshalling, no raw hashes) so a
    /// player can tell Claude "spawn a tank / make it night / heal me" and it just works on Legacy + Enhanced.
    /// Everything that touches game state runs on the game thread (OnGameThread). Model-loading spawns are deferred
    /// to the tick loop so the socket never blocks waiting on streaming.
    /// </summary>
    public partial class ClaudeBridge
    {
        // ---- deferred spawn queue (model load happens across ticks) ----
        class SpawnReq { public string model; public float dist; public bool isPed; public string note; }
        static readonly ConcurrentQueue<SpawnReq> _spawnQueue = new ConcurrentQueue<SpawnReq>();
        // Models currently being streamed in: model -> pending request. Processed in ProcessSpawns() each tick.
        static readonly List<(Model model, Vector3 pos, float heading, bool isPed, int started)> _pendingSpawns
            = new List<(Model, Vector3, float, bool, int)>();

        // Called every tick from OnTick (added below).
        static void ProcessSpawns()
        {
            // 1) promote queued requests to pending (request the model once)
            while (_spawnQueue.TryDequeue(out var req))
            {
                try
                {
                    var ped = Game.Player.Character;
                    if (ped == null || !ped.Exists()) continue;
                    var model = new Model(req.model);
                    if (!model.IsValid) { SafeLog($"spawn: invalid model '{req.model}'"); continue; }
                    model.Request();
                    Vector3 pos = ped.Position + ped.ForwardVector * Math.Max(2f, req.dist);
                    _pendingSpawns.Add((model, pos, ped.Heading, req.isPed, Game.GameTime));
                }
                catch (Exception ex) { SafeLog("spawn enqueue error: " + ex.Message); }
            }
            // 2) create the ones whose model finished streaming (timeout after 8s)
            for (int i = _pendingSpawns.Count - 1; i >= 0; i--)
            {
                var s = _pendingSpawns[i];
                try
                {
                    if (s.model.IsLoaded)
                    {
                        if (s.isPed) { var p = World.CreatePed(s.model, s.pos, s.heading); }
                        else { var v = World.CreateVehicle(s.model, s.pos, s.heading); v?.PlaceOnGround(); }
                        s.model.MarkAsNoLongerNeeded();
                        _pendingSpawns.RemoveAt(i);
                    }
                    else if (Game.GameTime - s.started > 8000)
                    {
                        s.model.MarkAsNoLongerNeeded();
                        _pendingSpawns.RemoveAt(i);
                        SafeLog($"spawn: model '{s.model.Hash}' timed out loading");
                    }
                }
                catch (Exception ex) { _pendingSpawns.RemoveAt(i); SafeLog("spawn create error: " + ex.Message); }
            }
        }

        // ---- world / player state ----
        object GetWorldState(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            var v = (ped != null && ped.Exists() && ped.IsInVehicle()) ? ped.CurrentVehicle : null;
            var pos = ped?.Position ?? Vector3.Zero;
            return new Dictionary<string, object>
            {
                ["position"] = new[] { Round(pos.X), Round(pos.Y), Round(pos.Z) },
                ["heading"] = Round(ped?.Heading ?? 0),
                ["health"] = ped?.Health ?? 0,
                ["max_health"] = ped?.MaxHealth ?? 0,
                ["armor"] = ped?.Armor ?? 0,
                ["wanted"] = Game.Player.WantedLevel,
                ["in_vehicle"] = v != null,
                ["vehicle"] = v != null ? VehNameOf(v) : null,
                ["weather"] = World.Weather.ToString(),
                ["game_time_min"] = World.CurrentTimeOfDay.Hours * 60 + World.CurrentTimeOfDay.Minutes,
            };
        });

        object IsInVehicle(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            return new Dictionary<string, object> { ["in_vehicle"] = ped != null && ped.Exists() && ped.IsInVehicle() };
        });

        object GetVehicleInfo(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            var v = (ped != null && ped.Exists() && ped.IsInVehicle()) ? ped.CurrentVehicle : null;
            if (v == null) return new Dictionary<string, object> { ["error"] = "not in a vehicle" };
            return new Dictionary<string, object>
            {
                ["name"] = VehNameOf(v),
                ["handle"] = v.Handle,
                ["model_hash"] = v.Model.Hash,
                ["plate"] = v.Mods?.LicensePlate,
                ["speed_mph"] = Round(v.Speed * 2.23694f),
                ["health"] = Round(v.HealthFloat),
                ["engine_on"] = v.IsEngineRunning,
            };
        });

        // ---- actions ----
        object Teleport(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            if (ped == null || !ped.Exists()) return Err("no player ped");
            Entity ent = ped.IsInVehicle() ? (Entity)ped.CurrentVehicle : ped;
            Vector3 dest;
            if (p.ContainsKey("x") && p.ContainsKey("y"))
                dest = new Vector3((float)ToD(p["x"]), (float)ToD(p["y"]), p.ContainsKey("z") ? (float)ToD(p["z"]) : ent.Position.Z);
            else
            {
                float fwd = p.ContainsKey("forward") ? (float)ToD(p["forward"]) : 8f;   // default: hop forward
                dest = ent.Position + ent.ForwardVector * fwd;
            }
            ent.Position = dest;
            return Ok(new Dictionary<string, object> { ["moved_to"] = new[] { Round(dest.X), Round(dest.Y), Round(dest.Z) } });
        });

        object Heal(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            if (ped == null) return Err("no ped");
            ped.Health = ped.MaxHealth;
            ped.Armor = 100;
            if (ped.IsInVehicle()) ped.CurrentVehicle.Repair();
            return Ok(new Dictionary<string, object> { ["health"] = ped.Health });
        });

        object SetInvincible(Dictionary<string, object> p) => OnGameThread(() =>
        {
            bool on = !p.ContainsKey("on") || Convert.ToBoolean(p["on"]);
            Game.Player.IsInvincible = on;
            Game.Player.Character.IsInvincible = on;
            return Ok(new Dictionary<string, object> { ["invincible"] = on });
        });

        object SetWanted(Dictionary<string, object> p) => OnGameThread(() =>
        {
            int lvl = p.ContainsKey("level") ? ToInt(p["level"]) : 0;
            Game.Player.WantedLevel = Math.Max(0, Math.Min(5, lvl));
            return Ok(new Dictionary<string, object> { ["wanted"] = Game.Player.WantedLevel });
        });

        object GiveWeapon(Dictionary<string, object> p) => OnGameThread(() =>
        {
            string name = Str(p, "name") ?? Str(p, "weapon");
            if (string.IsNullOrEmpty(name)) return Err("no weapon name");
            if (!name.StartsWith("WEAPON_", StringComparison.OrdinalIgnoreCase)) name = "WEAPON_" + name;
            uint hash = (uint)Game.GenerateHash(name.ToUpperInvariant());
            int ammo = p.ContainsKey("ammo") ? ToInt(p["ammo"]) : 250;
            Function.Call(Hash.GIVE_WEAPON_TO_PED, Game.Player.Character, hash, ammo, false, true);
            return Ok(new Dictionary<string, object> { ["gave"] = name });
        });

        object SetWeather(Dictionary<string, object> p) => OnGameThread(() =>
        {
            string name = (Str(p, "weather") ?? Str(p, "name") ?? "").ToUpperInvariant();
            if (Enum.TryParse<Weather>(CapFirst(name), true, out var w)) { World.Weather = w; return Ok(new Dictionary<string, object> { ["weather"] = w.ToString() }); }
            // accept raw game weather strings too
            Function.Call(Hash.SET_WEATHER_TYPE_NOW, name);
            return Ok(new Dictionary<string, object> { ["weather"] = name });
        });

        object SetTime(Dictionary<string, object> p) => OnGameThread(() =>
        {
            int h = p.ContainsKey("hour") ? ToInt(p["hour"]) : 12;
            int m = p.ContainsKey("minute") ? ToInt(p["minute"]) : 0;
            Function.Call(Hash.SET_CLOCK_TIME, h % 24, m % 60, 0);
            return Ok(new Dictionary<string, object> { ["time"] = $"{h % 24:00}:{m % 60:00}" });
        });

        object RepairVehicle(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            var v = (ped != null && ped.IsInVehicle()) ? ped.CurrentVehicle : null;
            if (v == null) return Err("not in a vehicle");
            v.Repair(); v.Wash();
            return Ok(new Dictionary<string, object> { ["repaired"] = VehNameOf(v) });
        });

        object Explosion(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            float fwd = p.ContainsKey("forward") ? (float)ToD(p["forward"]) : 12f;
            Vector3 at = ped.Position + ped.ForwardVector * fwd;
            World.AddExplosion(at, ExplosionType.Grenade, 5f, 1f);
            return Ok(new Dictionary<string, object> { ["boom_at"] = new[] { Round(at.X), Round(at.Y), Round(at.Z) } });
        });

        object SpawnVehicle(Dictionary<string, object> p)
        {
            string model = Str(p, "model") ?? Str(p, "name");
            if (string.IsNullOrEmpty(model)) return Err("no model");
            _spawnQueue.Enqueue(new SpawnReq { model = model, dist = p.ContainsKey("distance") ? (float)ToD(p["distance"]) : 6f, isPed = false });
            return Ok(new Dictionary<string, object> { ["queued"] = model, ["note"] = "spawning when the model streams in (a second or two)" });
        }

        object SpawnPed(Dictionary<string, object> p)
        {
            string model = Str(p, "model") ?? Str(p, "name");
            if (string.IsNullOrEmpty(model)) return Err("no model");
            _spawnQueue.Enqueue(new SpawnReq { model = model, dist = p.ContainsKey("distance") ? (float)ToD(p["distance"]) : 3f, isPed = true });
            return Ok(new Dictionary<string, object> { ["queued"] = model });
        }

        object NearbyVehicles(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            float r = p.ContainsKey("radius") ? (float)ToD(p["radius"]) : 60f;
            var list = World.GetNearbyVehicles(ped.Position, r)
                .Where(v => v != null && v.Exists())
                .OrderBy(v => v.Position.DistanceTo(ped.Position)).Take(20)
                .Select(v => (object)new Dictionary<string, object> { ["name"] = VehNameOf(v), ["handle"] = v.Handle, ["dist"] = Round(v.Position.DistanceTo(ped.Position)) })
                .ToList();
            return new Dictionary<string, object> { ["count"] = list.Count, ["vehicles"] = list };
        });

        object NearbyPeds(Dictionary<string, object> p) => OnGameThread(() =>
        {
            var ped = Game.Player.Character;
            float r = p.ContainsKey("radius") ? (float)ToD(p["radius"]) : 60f;
            var list = World.GetNearbyPeds(ped.Position, r)
                .Where(x => x != null && x.Exists() && x.Handle != ped.Handle)
                .OrderBy(x => x.Position.DistanceTo(ped.Position)).Take(20)
                .Select(x => (object)new Dictionary<string, object> { ["handle"] = x.Handle, ["dist"] = Round(x.Position.DistanceTo(ped.Position)), ["dead"] = x.IsDead })
                .ToList();
            return new Dictionary<string, object> { ["count"] = list.Count, ["peds"] = list };
        });

        // ---- self-describing catalog (so Claude/the MCP can discover what the bridge can do) ----
        object Commands(Dictionary<string, object> p) => new Dictionary<string, object>
        {
            ["bridge"] = "ClaudeBridge",
            ["categories"] = new Dictionary<string, object>
            {
                ["chat"] = new[] { "status", "queue_user_message", "get_pending_messages", "has_pending_messages", "await_user_message", "chat_post", "hud_message", "set_overlay", "get_chat_history" },
                ["world_player"] = new[] { "get_world_state", "teleport {x,y,z|forward}", "heal", "set_invincible {on}", "set_wanted {level}", "give_weapon {name,ammo}", "set_weather {name}", "set_time {hour,minute}", "explosion {forward}" },
                ["vehicle"] = new[] { "spawn_vehicle {model,distance}", "is_in_vehicle", "get_vehicle_info", "repair_vehicle" },
                ["world_query"] = new[] { "nearby_vehicles {radius}", "nearby_peds {radius}", "spawn_ped {model,distance}" },
                ["natives_memory"] = new[] { "call_native {name|hash,args,return_type}", "read", "write", "scan", "scan_mem", "module", "cur_veh", "entity_addr" },
                ["profiler"] = new[] { "scripts_managed", "frametime", "scripts_perf", "natprof_start/stop/report", "methprof_start/stop/report", "profiler_status" },
                ["control"] = new[] { "reload_scripts", "send_keys", "ping", "commands" },
            }
        };

        // ---- helpers ----
        static double ToD(object o) => Convert.ToDouble(o);
        static double Round(double d) => Math.Round(d, 2);
        static Dictionary<string, object> Ok(Dictionary<string, object> extra) { extra["success"] = true; return extra; }
        static Dictionary<string, object> Err(string m) => new Dictionary<string, object> { ["error"] = m };
        static string CapFirst(string s) => string.IsNullOrEmpty(s) ? s : char.ToUpper(s[0]) + s.Substring(1).ToLower();
        static string VehNameOf(Vehicle v)
        {
            try { return Function.Call<string>(Hash.GET_DISPLAY_NAME_FROM_VEHICLE_MODEL, v.Model.Hash); } catch { return "vehicle"; }
        }
    }
}
