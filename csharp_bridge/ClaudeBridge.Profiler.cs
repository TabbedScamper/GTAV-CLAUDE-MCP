using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;

namespace ClaudeBridge
{
    /// <summary>
    /// Per-frame profiler — PHASE 1 (safe layer): cannot crash the game (no patching, no game-memory writes).
    ///
    ///   frametime          rolling frame-time / FPS / hitch report (sampled every OnTick)
    ///   frametime_reset    clear the rolling window (optionally set hitch_ms threshold)
    ///   scripts_managed    enumerate every running SHVDN C# script (ELSC + other users' mods) via reflection
    ///
    /// "frametime" answers "is the game hitching, and how badly"; "scripts_managed" answers "which C# mods are
    /// even loaded". Together with Phase 2 (native-call attribution) they tell you WHICH mod is causing the cost.
    /// </summary>
    public partial class ClaudeBridge
    {
        // ───────────────────────────── frame-time monitor ─────────────────────────────
        // OnTick fires once per rendered frame, so the wall-time delta between ticks IS the frame time.
        static readonly Stopwatch _frameClock = Stopwatch.StartNew();
        static double _lastMark = -1;
        const int RING = 2048;                       // ~34s @ 60fps of history
        static readonly double[] _frameMs = new double[RING];
        static int _ringPos = 0;                     // next write slot
        static long _frames = 0;                     // total frames seen since reset
        static double _hitchMs = 50.0;               // a frame slower than this (=20 FPS spike) counts as a hitch
        static long _hitches = 0;
        static double _worstMs = 0;

        static void SampleFrame()
        {
            double now = _frameClock.Elapsed.TotalMilliseconds;
            if (_lastMark >= 0)
            {
                double dt = now - _lastMark;
                _frameMs[_ringPos] = dt;
                _ringPos = (_ringPos + 1) % RING;
                _frames++;
                if (dt > _hitchMs) _hitches++;
                if (dt > _worstMs) _worstMs = dt;
            }
            _lastMark = now;
        }

        static void ResetFrameStats(Dictionary<string, object> p)
        {
            if (p != null && p.TryGetValue("hitch_ms", out var h) && h != null)
                _hitchMs = Convert.ToDouble(h);
            _frames = 0; _hitches = 0; _worstMs = 0; _ringPos = 0; _lastMark = -1;
            for (int i = 0; i < RING; i++) _frameMs[i] = 0;
        }

        object FrameTimeReport(Dictionary<string, object> p)
        {
            // Snapshot the populated portion of the ring (newest `window` samples).
            int have = (int)Math.Min(_frames, (long)RING);
            if (have == 0) return new Dictionary<string, object> { ["note"] = "no frames sampled yet (let the game run a moment)" };
            int window = p != null && p.TryGetValue("window", out var w) ? Math.Min(ToInt(w), have) : have;
            var samples = new double[window];
            for (int i = 0; i < window; i++)
            {
                int idx = (_ringPos - 1 - i + RING) % RING;   // walk backwards from newest
                samples[i] = _frameMs[idx];
            }
            Array.Sort(samples);
            double sum = 0; foreach (var s in samples) sum += s;
            double avg = sum / window;
            double Pct(double q) => samples[Math.Min(window - 1, (int)(q * window))];
            return new Dictionary<string, object>
            {
                ["frames_total"] = _frames,
                ["window"] = window,
                ["avg_ms"] = Math.Round(avg, 3),
                ["avg_fps"] = Math.Round(1000.0 / Math.Max(avg, 0.001), 1),
                ["min_ms"] = Math.Round(samples[0], 3),
                ["median_ms"] = Math.Round(Pct(0.50), 3),
                ["p95_ms"] = Math.Round(Pct(0.95), 3),
                ["p99_ms"] = Math.Round(Pct(0.99), 3),
                ["max_ms"] = Math.Round(samples[window - 1], 3),
                ["worst_ever_ms"] = Math.Round(_worstMs, 3),
                ["hitch_ms_threshold"] = _hitchMs,
                ["hitches"] = _hitches,
                ["hitch_pct"] = Math.Round(100.0 * _hitches / Math.Max(_frames, 1), 2),
            };
        }

        // ───────────────────────────── SHVDN introspection (Phase 2 prep) ─────────────────────────────
        // TEMP diagnostic: dump the SHVDN core runtime's method surface so the Harmony patch targets
        // (per-script tick + native invoke) are chosen against the REAL signatures, not guessed.
        object ShvdnIntrospect(Dictionary<string, object> p)
        {
            try
            {
                var asm = AppDomain.CurrentDomain.GetAssemblies()
                    .FirstOrDefault(a => string.Equals(a.GetName().Name, "ScriptHookVDotNet", StringComparison.OrdinalIgnoreCase));
                if (asm == null)
                    return new Dictionary<string, object> { ["error"] = "SHVDN core assembly not found" };
                const BindingFlags ANY = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance | BindingFlags.DeclaredOnly;
                var result = new Dictionary<string, object> { ["assembly"] = asm.GetName().FullName };
                string[] wanted = (p != null && p.TryGetValue("types", out var tv) && tv is System.Collections.IEnumerable en && !(tv is string))
                    ? en.Cast<object>().Select(o => o.ToString()).ToArray()
                    : new[] { "SHVDN.Script", "SHVDN.ScriptDomain", "SHVDN.NativeFunc" };
                foreach (var tn in wanted)
                {
                    var t = asm.GetType(tn);
                    if (t == null) { result[tn] = "(type not found)"; continue; }
                    var methods = new List<string>();
                    foreach (var m in t.GetMethods(ANY))
                    {
                        var ps = string.Join(", ", m.GetParameters().Select(x => x.ParameterType.Name + " " + x.Name));
                        string kind = m.IsStatic ? "static " : "";
                        methods.Add($"{kind}{m.ReturnType.Name} {m.Name}({ps})");
                    }
                    methods.Sort();
                    var fields = t.GetFields(ANY).Select(f => (f.IsStatic ? "static " : "") + f.FieldType.Name + " " + f.Name).OrderBy(s => s).ToList();
                    result[tn] = new Dictionary<string, object> { ["methods"] = methods, ["fields"] = fields };
                }
                return result;
            }
            catch (Exception ex) { return new Dictionary<string, object> { ["error"] = ex.Message }; }
        }

        // ───────────────────────────── managed-script enumerator ─────────────────────────────
        // Reflect SHVDN's core ScriptDomain to list every running C# script. The core types (SHVDN.*) live in the
        // "ScriptHookVDotNet" assembly (the .asi-hosted runtime), distinct from the GTA.* API assembly we compile
        // against — so we find it by name at runtime and read members defensively (any miss → reported, never thrown).
        // Run on the game thread (the caller queues this) so we don't enumerate the list while the domain mutates it.
        object ManagedScripts(Dictionary<string, object> p)
        {
            try
            {
                var core = AppDomain.CurrentDomain.GetAssemblies()
                    .FirstOrDefault(a => string.Equals(a.GetName().Name, "ScriptHookVDotNet", StringComparison.OrdinalIgnoreCase));
                Type sdType = core?.GetType("SHVDN.ScriptDomain");
                if (sdType == null)   // fallback: scan every loaded assembly for the type
                {
                    foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
                    {
                        try { sdType = a.GetType("SHVDN.ScriptDomain"); if (sdType != null) break; } catch { }
                    }
                }
                if (sdType == null)
                    return new Dictionary<string, object> { ["error"] = "SHVDN.ScriptDomain type not found (unexpected SHVDN build?)" };

                const BindingFlags ANY = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
                object domain = sdType.GetProperty("CurrentDomain", ANY)?.GetValue(null)
                              ?? sdType.GetField("CurrentDomain", ANY)?.GetValue(null);
                if (domain == null)
                    return new Dictionary<string, object> { ["error"] = "ScriptDomain.CurrentDomain was null" };

                // runningScripts is a List<SHVDN.Script>; copy to an array under the game thread to avoid races.
                var rsMember = sdType.GetField("runningScripts", ANY);
                object rsVal = rsMember != null ? rsMember.GetValue(domain)
                                                : sdType.GetProperty("RunningScripts", ANY)?.GetValue(domain);
                var list = (rsVal as System.Collections.IEnumerable)?.Cast<object>().ToList();
                if (list == null)
                    return new Dictionary<string, object> { ["error"] = "could not read runningScripts collection" };

                var outScripts = new List<object>();
                foreach (var s in list)
                {
                    if (s == null) continue;
                    Type st = s.GetType();
                    string Get(string prop)
                    {
                        try { var v = st.GetProperty(prop, ANY)?.GetValue(s); return v?.ToString(); } catch { return null; }
                    }
                    bool? GetB(string prop)
                    {
                        try { var v = st.GetProperty(prop, ANY)?.GetValue(s); return v is bool b ? b : (bool?)null; } catch { return null; }
                    }
                    outScripts.Add(new Dictionary<string, object>
                    {
                        ["name"] = Get("Name"),
                        ["filename"] = Get("Filename"),
                        ["running"] = GetB("IsRunning"),
                        ["paused"] = GetB("IsPaused"),
                    });
                }
                outScripts.Sort((a, b) => string.Compare(
                    (string)((Dictionary<string, object>)a)["name"],
                    (string)((Dictionary<string, object>)b)["name"], StringComparison.OrdinalIgnoreCase));
                return new Dictionary<string, object>
                {
                    ["success"] = true,
                    ["count"] = outScripts.Count,
                    ["scripts"] = outScripts,
                    ["note"] = "every running SHVDN C# mod. Self = ClaudeBridge; yours = ExtendedLSC; rest are other users' mods.",
                };
            }
            catch (Exception ex)
            {
                return new Dictionary<string, object> { ["error"] = "reflection failed: " + ex.Message };
            }
        }
    }
}
