using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using GTA;
using GTA.Math;
using GTA.Native;

namespace ClaudeBridge
{
    /// <summary>
    /// Minimal SHVDN TCP bridge: lets Python (dyno.py et al.) call natives, read process memory, and AOB-scan the
    /// game module — for driving the game and reverse-engineering memory offsets. Runs under ScriptHookVDotNet on
    /// BOTH Legacy and Enhanced (no PyLoaderV / embedded Python needed). Protocol = 4-byte little-endian length
    /// prefix + JSON {command, params}; reply = same framing with {result|error}. Listens on 127.0.0.1:27015.
    ///
    /// Native calls run on the SHVDN script thread (queued to OnTick); pure memory reads/scans run on the socket
    /// thread (raw ReadProcessMemory, never crashes on a bad address — returns null instead).
    /// </summary>
    public partial class ClaudeBridge : Script
    {
        const int PORT = 27015;
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 64 * 1024 * 1024 };

        // Work that must touch natives → queued to the game thread.
        class Job { public Func<object> Work; public object Result; public string Error; public readonly ManualResetEventSlim Done = new ManualResetEventSlim(false); }
        static readonly ConcurrentQueue<Job> Jobs = new ConcurrentQueue<Job>();

        TcpListener _listener;
        volatile bool _stop;

        public ClaudeBridge()
        {
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, PORT);
                _listener.Start();
                new Thread(AcceptLoop) { IsBackground = true, Name = "ClaudeBridge.Accept" }.Start();
                SafeLog($"ClaudeBridge listening on 127.0.0.1:{PORT}");
            }
            catch (Exception ex) { SafeLog("start error: " + ex); }

            Tick += OnTick;
            Aborted += (s, e) => { _stop = true; try { _listener?.Stop(); } catch { } try { TeardownProfiler(); } catch { } try { TeardownMethodProfiler(); } catch { } try { RestoreAllPatches(null); } catch { } };

            // Install the per-frame profiler (Harmony patches on SHVDN's own tick + native invoker). Best-effort:
            // a failure here leaves the rest of the bridge fully functional (profiler_status reports the error).
            try { InitProfiler(); SafeLog(_patched ? "profiler patched" : "profiler init failed: " + _initError); }
            catch (Exception ex) { SafeLog("profiler init threw: " + ex); }
        }

        // Best-effort log to a writable spot; NEVER throws (the game root isn't writable, so a naive write would
        // crash the script's construction). Try the scripts folder first, then the base dir, then give up silently.
        static void SafeLog(string msg)
        {
            string line = $"[{DateTime.Now:HH:mm:ss}] {msg}\n";
            foreach (var p in new[] {
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "scripts", "ClaudeBridge.log"),
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "ClaudeBridge.log") })
            {
                try { File.AppendAllText(p, line); return; } catch { }
            }
        }

        void OnTick(object sender, EventArgs e)
        {
            // Per-frame profiling sample (frame-time + hitch tracking). Cheap, never throws.
            SampleFrame();

            // Process deferred spawn requests (model streaming happens across ticks).
            try { ProcessSpawns(); } catch { }

            // Drain queued native work on the script thread (bounded per tick so we never stall the game).
            int budget = 64;
            while (budget-- > 0 && Jobs.TryDequeue(out var job))
            {
                try { job.Result = job.Work(); }
                catch (Exception ex) { job.Error = ex.Message; }
                job.Done.Set();
            }
        }

        // Run a delegate on the game thread and block until it finishes (socket thread waits here).
        static object OnGameThread(Func<object> work)
        {
            var job = new Job { Work = work };
            Jobs.Enqueue(job);
            if (!job.Done.Wait(5000)) throw new Exception("game-thread timeout");
            if (job.Error != null) throw new Exception(job.Error);
            return job.Result;
        }

        // ───────────────────────────── socket plumbing ─────────────────────────────
        void AcceptLoop()
        {
            while (!_stop)
            {
                try
                {
                    var client = _listener.AcceptTcpClient();
                    new Thread(() => Serve(client)) { IsBackground = true }.Start();
                }
                catch { if (_stop) break; }
            }
        }

        void Serve(TcpClient client)
        {
            using (client)
            using (var ns = client.GetStream())
            {
                try
                {
                    byte[] lenBuf = ReadExact(ns, 4);
                    if (lenBuf == null) return;
                    int len = BitConverter.ToInt32(lenBuf, 0);
                    if (len <= 0 || len > Json.MaxJsonLength) return;
                    byte[] body = ReadExact(ns, len);
                    if (body == null) return;

                    object reply;
                    try
                    {
                        var msg = (Dictionary<string, object>)Json.DeserializeObject(Encoding.UTF8.GetString(body));
                        string cmd = msg.TryGetValue("command", out var c) ? Convert.ToString(c) : "";
                        var p = msg.TryGetValue("params", out var pp) ? pp as Dictionary<string, object> : null;
                        reply = new Dictionary<string, object> { ["result"] = Dispatch(cmd, p ?? new Dictionary<string, object>()) };
                    }
                    catch (Exception ex) { reply = new Dictionary<string, object> { ["error"] = ex.Message }; }

                    byte[] outBuf = Encoding.UTF8.GetBytes(Json.Serialize(reply));
                    ns.Write(BitConverter.GetBytes(outBuf.Length), 0, 4);
                    ns.Write(outBuf, 0, outBuf.Length);
                }
                catch { }
            }
        }

        static byte[] ReadExact(NetworkStream ns, int n)
        {
            byte[] b = new byte[n]; int got = 0;
            while (got < n) { int r = ns.Read(b, got, n - got); if (r <= 0) return null; got += r; }
            return b;
        }

        // ───────────────────────────── command dispatch ─────────────────────────────
        object Dispatch(string cmd, Dictionary<string, object> p)
        {
            switch (cmd)
            {
                case "ping": return "pong";
                case "call_native_by_name": return OnGameThread(() => CallNative(ResolveHash(Str(p, "name")), Arr(p, "args"), Str(p, "return_type", "void")));
                case "call_native": return OnGameThread(() => CallNative(ParseHash(p["hash"]), Arr(p, "args"), Str(p, "return_type", "void")));
                case "cur_veh": return OnGameThread(CurVeh);
                case "entity_addr":
                case "veh_addr": return OnGameThread(() => EntityAddr(ToInt(p["handle"])));
                case "read": return ReadMem(p);
                case "write": return WriteMem(p);
                case "scan": return Scan(Str(p, "pattern"), p.TryGetValue("count", out var ct) ? ToInt(ct) : 16);
                case "scan_mem": return ScanMem(Str(p, "pattern"),
                        p.TryGetValue("count", out var ct2) ? ToInt(ct2) : 16,
                        p.TryGetValue("start", out var sa) ? ToLong(sa) : 0x10000L,
                        p.TryGetValue("end", out var ea) ? ToLong(ea) : 0x7FFFFFFF0000L);
                case "module": return ModuleInfo();
                // ── per-frame profiler (Phase 1: safe layer) ──
                case "frametime": return FrameTimeReport(p);
                case "frametime_reset": ResetFrameStats(p); return "ok";
                case "scripts_managed": return OnGameThread(() => ManagedScripts(p));
                case "shvdn_introspect": return ShvdnIntrospect(p);
                // ── per-frame native-call profiler (Phase 2: Harmony) ──
                case "scripts_perf": return ScriptsPerf(p);
                case "scripts_perf_reset": ScriptsPerfReset(); return "ok";
                case "natprof_start": return NatProfStart(p);
                case "natprof_stop": return NatProfStop();
                case "natprof_report": return NatProfReport(p);
                case "profiler_status": return ProfilerStatus();
                // ── per-method timing (Phase 2b: Harmony) ──
                case "methprof_start": return MethProfStart(p);
                case "methprof_report": return MethProfReport(p);
                case "methprof_stop": return MethProfStop();
                case "methprof_clear": return MethProfClear();
                // ── in-game chat (command Claude from inside the game; no PyLoaderV) ──
                case "status": return StatusInfo(p);
                case "queue_user_message": return QueueUserMessage(p);
                case "get_pending_messages": return GetPendingMessages(p);
                case "has_pending_messages": return HasPendingMessages(p);
                case "await_user_message": return AwaitUserMessage(p);
                case "chat_post": return ChatPost(p);
                case "hud_message": return HudMessage(p);
                case "set_overlay": return SetOverlay(p);
                case "get_chat_history": return GetChatHistory(p);
                // ── high-level action verbs (spawn / teleport / weather / etc.) ──
                case "get_world_state": return GetWorldState(p);
                case "is_in_vehicle": return IsInVehicle(p);
                case "get_vehicle_info": return GetVehicleInfo(p);
                case "teleport": return Teleport(p);
                case "heal": return Heal(p);
                case "set_invincible": return SetInvincible(p);
                case "set_wanted": return SetWanted(p);
                case "give_weapon": return GiveWeapon(p);
                case "set_weather": return SetWeather(p);
                case "set_time": return SetTime(p);
                case "repair_vehicle": return RepairVehicle(p);
                case "explosion": return Explosion(p);
                case "spawn_vehicle": return SpawnVehicle(p);
                case "spawn_ped": return SpawnPed(p);
                case "enter_vehicle": return EnterVehicle(p);
                case "nearby_vehicles": return NearbyVehicles(p);
                case "nearby_peds": return NearbyPeds(p);
                case "commands": return Commands(p);
                // ── RE-grade memory editing (safe, reversible) ──
                case "validate_address": return ValidateAddress(p);
                case "read_chain": return ReadChain(p);
                case "resolve_rip_relative": return ResolveRipRelative(p);
                case "patch_bytes": return PatchBytes(p);
                case "nop": return Nop(p);
                case "list_patches": return ListPatches(p);
                case "restore_patch": return RestorePatch(p);
                case "restore_all_patches": return RestoreAllPatches(p);
                // ── decode "what does this memory do" (differential RE, no eyes needed) ──
                case "snapshot": return Snapshot(p);
                case "diff": return Diff(p);
                case "watch": return Watch(p);
                case "correlate": return Correlate(p);
                case "reload_scripts": SendKey(0x2D); return "ok";   // VK_INSERT — SHVDN reload
                case "send_keys": SendKeyName(Str(p, "keys")); return "ok";
                default: throw new Exception("unknown command: " + cmd);
            }
        }

        // ── natives ──
        static object CurVeh()
        {
            int ped = Function.Call<int>(Hash.PLAYER_PED_ID);
            int v = Function.Call<int>(Hash.GET_VEHICLE_PED_IS_IN, ped, false);
            return v;
        }

        static object EntityAddr(int handle)
        {
            var ent = Entity.FromHandle(handle);
            return ent != null ? ent.MemoryAddress.ToInt64() : 0L;
        }

        static Hash ResolveHash(string name)
        {
            if (string.IsNullOrEmpty(name)) throw new Exception("no native name");
            return (Hash)Enum.Parse(typeof(Hash), name, true);
        }

        static Hash ParseHash(object o)
        {
            string s = Convert.ToString(o).Trim();
            ulong h = s.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
                ? Convert.ToUInt64(s.Substring(2), 16) : Convert.ToUInt64(s);
            return (Hash)h;
        }

        static object CallNative(Hash hash, object[] rawArgs, string rt)
        {
            var args = new InputArgument[rawArgs.Length];
            for (int i = 0; i < rawArgs.Length; i++) args[i] = Coerce(rawArgs[i]);
            switch ((rt ?? "void").ToLowerInvariant())
            {
                case "int": return Function.Call<int>(hash, args);
                case "uint": return (long)Function.Call<int>(hash, args) & 0xFFFFFFFFL;
                case "long": return Function.Call<long>(hash, args);
                case "float": return Function.Call<float>(hash, args);
                case "bool": return Function.Call<bool>(hash, args);
                case "string": return Function.Call<string>(hash, args);
                case "vector3": { var v = Function.Call<Vector3>(hash, args); return new double[] { v.X, v.Y, v.Z }; }
                default: Function.Call(hash, args); return null;
            }
        }

        // JSON number/bool/string → InputArgument. Whole numbers → int (handles/flags); fractional → float (coords).
        static InputArgument Coerce(object o)
        {
            if (o is bool b) return b;
            if (o is string s) return s;
            if (o is int i) return i;
            if (o is long l) return (int)l;
            double d = Convert.ToDouble(o);
            if (d == Math.Floor(d) && Math.Abs(d) < 2.1e9) return (int)d;
            return (float)d;
        }

        // ── memory ──
        object ReadMem(Dictionary<string, object> p)
        {
            long addr;
            if (p.ContainsKey("addr")) addr = ToLong(p["addr"]);
            else
            {
                long baseAddr = (long)OnGameThread(() => EntityAddr(ToInt(p["handle"])));
                if (baseAddr == 0) return null;
                addr = baseAddr + (p.ContainsKey("offset") ? ToLong(p["offset"]) : 0);
            }
            string type = Str(p, "type", "int").ToLowerInvariant();
            int count = p.TryGetValue("count", out var ct) ? ToInt(ct) : 1;

            switch (type)
            {
                case "float": return ReadF(addr);
                case "int": return ReadI32(addr);
                case "uint": { var v = ReadI32(addr); return v == null ? (object)null : (long)(uint)v.Value; }
                case "long":
                case "ptr": return ReadI64(addr);
                case "byte": return ReadBytes(addr, 1)?[0];
                case "bytes": { var bs = ReadBytes(addr, count); return bs == null ? null : (object)BitConverter.ToString(bs); }
                case "floats": { var list = new List<object>(); for (int k = 0; k < count; k++) list.Add(ReadF(addr + k * 4)); return list; }
                default: return ReadI32(addr);
            }
        }

        // ── memory write (data structs only; for verifying offsets during RE) ──
        object WriteMem(Dictionary<string, object> p)
        {
            long addr;
            if (p.ContainsKey("addr")) addr = ToLong(p["addr"]);
            else
            {
                long baseAddr = (long)OnGameThread(() => EntityAddr(ToInt(p["handle"])));
                if (baseAddr == 0) return false;
                addr = baseAddr + (p.ContainsKey("offset") ? ToLong(p["offset"]) : 0);
            }
            string type = Str(p, "type", "float").ToLowerInvariant();
            object val = p.TryGetValue("value", out var vv) ? vv : 0;
            byte[] bytes;
            switch (type)
            {
                case "float": bytes = BitConverter.GetBytes((float)Convert.ToDouble(val)); break;
                case "int": bytes = BitConverter.GetBytes((int)ToLong(val)); break;
                case "long":
                case "ptr": bytes = BitConverter.GetBytes(ToLong(val)); break;
                case "byte": bytes = new[] { (byte)ToLong(val) }; break;
                case "bytes":
                    var parts = Convert.ToString(val).Split(new[] { ' ', '-' }, StringSplitOptions.RemoveEmptyEntries);
                    bytes = new byte[parts.Length];
                    for (int i = 0; i < parts.Length; i++) bytes[i] = Convert.ToByte(parts[i], 16);
                    break;
                default: bytes = BitConverter.GetBytes((float)Convert.ToDouble(val)); break;
            }
            return WriteProcessMemory(GetCurrentProcess(), (IntPtr)addr, bytes, bytes.Length, out int w) && w == bytes.Length;
        }

        // ── AOB scan over the main game module ──
        object Scan(string pattern, int max)
        {
            ParsePattern(pattern, out byte[] bytes, out bool[] mask);
            var mod = Process.GetCurrentProcess().MainModule;
            long start = mod.BaseAddress.ToInt64(), end = start + mod.ModuleMemorySize;
            var hits = new List<string>();
            const int CHUNK = 0x100000;
            byte[] buf = new byte[CHUNK + bytes.Length];
            for (long a = start; a < end && hits.Count < max; a += CHUNK)
            {
                int want = (int)Math.Min(CHUNK + bytes.Length, end - a);
                int read = RawRead(a, buf, want);
                if (read < bytes.Length) continue;
                for (int i = 0; i + bytes.Length <= read && hits.Count < max; i++)
                {
                    bool ok = true;
                    for (int j = 0; j < bytes.Length; j++) { if (mask[j] && buf[i + j] != bytes[j]) { ok = false; break; } }
                    if (ok) hits.Add("0x" + (a + i).ToString("X"));
                }
            }
            return hits;
        }

        // AOB scan over ALL committed+readable memory (walks VirtualQuery regions) — for data in MAPPED/heap memory
        // outside the main module (e.g. the carcols WindowColors table). Slower than module scan; sparse regions skip fast.
        object ScanMem(string pattern, int max, long start, long end)
        {
            ParsePattern(pattern, out byte[] bytes, out bool[] mask);
            var hits = new List<string>();
            byte[] buf = new byte[0x100000 + bytes.Length];
            long addr = start;
            int mbiSize = Marshal.SizeOf(typeof(MEMORY_BASIC_INFORMATION));
            while (addr < end && hits.Count < max)
            {
                if (VirtualQuery((IntPtr)addr, out var mbi, mbiSize) == 0) break;
                long rbase = mbi.BaseAddress.ToInt64();
                long rsize = mbi.RegionSize.ToInt64();
                if (rsize <= 0) break;
                bool committed = mbi.State == 0x1000;                       // MEM_COMMIT
                bool readable = (mbi.Protect & 0xEE) != 0 && (mbi.Protect & 0x100) == 0; // R/RW/ER/ERW/WC/EWC, not GUARD
                if (committed && readable)
                {
                    for (long a = rbase; a < rbase + rsize && hits.Count < max; a += 0x100000)
                    {
                        int want = (int)Math.Min(0x100000 + bytes.Length, rbase + rsize - a);
                        int read = RawRead(a, buf, want);
                        if (read < bytes.Length) continue;
                        for (int i = 0; i + bytes.Length <= read && hits.Count < max; i++)
                        {
                            bool ok = true;
                            for (int j = 0; j < bytes.Length; j++) { if (mask[j] && buf[i + j] != bytes[j]) { ok = false; break; } }
                            if (ok) hits.Add("0x" + (a + i).ToString("X"));
                        }
                    }
                }
                addr = rbase + rsize;
            }
            return hits;
        }

        [DllImport("kernel32.dll")] static extern int VirtualQuery(IntPtr addr, out MEMORY_BASIC_INFORMATION buf, int len);
        [StructLayout(LayoutKind.Sequential)]
        struct MEMORY_BASIC_INFORMATION
        {
            public IntPtr BaseAddress;
            public IntPtr AllocationBase;
            public uint AllocationProtect;
            public uint __align1;
            public IntPtr RegionSize;
            public uint State;
            public uint Protect;
            public uint Type;
            public uint __align2;
        }

        static void ParsePattern(string pat, out byte[] bytes, out bool[] mask)
        {
            var parts = pat.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            bytes = new byte[parts.Length]; mask = new bool[parts.Length];
            for (int i = 0; i < parts.Length; i++)
            {
                if (parts[i] == "?" || parts[i] == "??") { mask[i] = false; bytes[i] = 0; }
                else { mask[i] = true; bytes[i] = Convert.ToByte(parts[i], 16); }
            }
        }

        object ModuleInfo()
        {
            var m = Process.GetCurrentProcess().MainModule;
            return new Dictionary<string, object>
            {
                ["name"] = m.ModuleName,
                ["base"] = m.BaseAddress.ToInt64(),
                ["size"] = m.ModuleMemorySize,
                ["file"] = m.FileName,
            };
        }

        // ── raw process memory (never throws on a bad address; returns null/short) ──
        [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int size, out int read);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool WriteProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int size, out int written);

        static int RawRead(long addr, byte[] buf, int size)
        {
            return ReadProcessMemory(GetCurrentProcess(), (IntPtr)addr, buf, size, out int read) ? read : 0;
        }
        static float? ReadF(long a) { var b = ReadBytes(a, 4); return b == null ? (float?)null : BitConverter.ToSingle(b, 0); }
        static int? ReadI32(long a) { var b = ReadBytes(a, 4); return b == null ? (int?)null : BitConverter.ToInt32(b, 0); }
        static long? ReadI64(long a) { var b = ReadBytes(a, 8); return b == null ? (long?)null : BitConverter.ToInt64(b, 0); }
        static byte[] ReadBytes(long a, int n) { byte[] b = new byte[n]; return RawRead(a, b, n) == n ? b : null; }

        // ── key injection (reload / menu driving) ──
        [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
        [DllImport("user32.dll", SetLastError = true)] static extern IntPtr FindWindow(string cls, string win);
        [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr wparam, IntPtr lparam);
        // Post the key straight to the game window (grcWindow) so it works even when GTA isn't the foreground window
        // (the bridge often reloads while the user is alt-tabbed). Falls back to foreground keybd_event if not found.
        static void SendKey(byte vk)
        {
            IntPtr hwnd = FindWindow("grcWindow", null);
            if (hwnd != IntPtr.Zero)
            {
                PostMessage(hwnd, 0x100, (IntPtr)vk, IntPtr.Zero);   // WM_KEYDOWN
                Thread.Sleep(20);
                PostMessage(hwnd, 0x101, (IntPtr)vk, IntPtr.Zero);   // WM_KEYUP
            }
            else { keybd_event(vk, 0, 0, UIntPtr.Zero); Thread.Sleep(30); keybd_event(vk, 0, 2, UIntPtr.Zero); }
        }
        static void SendKeyName(string name)
        {
            if (string.IsNullOrEmpty(name)) return;
            byte vk;
            string k = name.Trim().ToUpperInvariant();
            if (k.Length == 2 && k[0] == 'F' && char.IsDigit(k[1])) vk = (byte)(0x70 + (k[1] - '1'));   // F1..F9
            else if (k == "F10") vk = 0x79; else if (k == "F11") vk = 0x7A; else if (k == "F12") vk = 0x7B;
            else if (k == "INSERT") vk = 0x2D; else if (k == "ENTER") vk = 0x0D; else if (k == "BACKSPACE") vk = 0x08;
            else if (k.Length == 1) vk = (byte)k[0];
            else return;
            SendKey(vk);
        }

        // ── param helpers ──
        static string Str(Dictionary<string, object> p, string k, string def = null) => p.TryGetValue(k, out var v) && v != null ? Convert.ToString(v) : def;
        static object[] Arr(Dictionary<string, object> p, string k)
        {
            if (!p.TryGetValue(k, out var v) || v == null) return new object[0];
            if (v is object[] a) return a;
            if (v is System.Collections.ArrayList al) return al.ToArray();
            return new object[0];
        }
        static int ToInt(object o) => (int)ToLong(o);
        static long ToLong(object o)
        {
            if (o is string s)
                return s.StartsWith("0x", StringComparison.OrdinalIgnoreCase) ? Convert.ToInt64(s.Substring(2), 16) : long.Parse(s);
            return Convert.ToInt64(o);
        }
    }
}
