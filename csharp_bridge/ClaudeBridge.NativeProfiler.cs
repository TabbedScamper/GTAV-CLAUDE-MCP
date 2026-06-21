using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using System.Threading;
using HarmonyLib;

namespace ClaudeBridge
{
    /// <summary>
    /// PHASE 2 — per-frame, per-script native-call profiler.  "Which mod is doing what, every frame?"
    ///
    /// SHVDN v3 does NOT route natives through ScriptHookV's exported nativeInit/nativeCall — it has its own
    /// invoker (SHVDN.NativeFunc.InvokeInternal). So instead of risky x64 inline hooks, we patch SHVDN's OWN
    /// managed methods with Harmony (safe, in the same AppDomain as the scripts) and attribute every call to the
    /// script the runtime says is executing (SHVDN.ScriptDomain.ExecutingScript). All targets were verified live
    /// via shvdn_introspect against SHVDN 3.7.0 — nothing here is a guessed signature.
    ///
    ///   scripts_perf         per-script CPU time per frame (always-on; one Stopwatch per Script.DoTick)
    ///   scripts_perf_reset   clear the timing window
    ///   natprof_start        begin counting native calls per script (and per-hash with {per_hash:true})
    ///   natprof_stop         stop counting (the count path is a no-op bool check when stopped → ~zero overhead)
    ///   natprof_report       per-script calls/frame + hottest native hashes
    ///   profiler_status      are the Harmony patches live? any init error?
    ///
    /// SAFETY: every prefix/postfix is wrapped so it can NEVER throw into SHVDN's hot path; the native-count path
    /// is gated OFF by default; patches are removed on Abort (reload). If Harmony init fails the bridge is unaffected.
    /// </summary>
    public partial class ClaudeBridge
    {
        sealed class Stat
        {
            public long ticks;       // DoTick calls (≈ frames this script ran)
            public long totalUs;     // cumulative DoTick wall time (microseconds)
            public long maxUs;       // worst single DoTick
            public long natives;     // native calls made while this script was executing
            public ConcurrentDictionary<ulong, long> perHash;  // optional per-native breakdown
        }

        static Harmony _harmony;
        static volatile bool _patched;
        static string _initError;
        static volatile bool _natProfOn;
        static volatile bool _perHashOn;

        // per-script stats, keyed by the SHVDN.Script instance (same object for timing + native attribution)
        static readonly ConcurrentDictionary<object, Stat> _stats = new ConcurrentDictionary<object, Stat>();
        static readonly object _gameThreadKey = new object();   // sentinel for native calls with no executing script

        static readonly Stopwatch _profClock = Stopwatch.StartNew();
        static double _epochMs;
        static readonly double _usPerTick = 1_000_000.0 / Stopwatch.Frequency;

        // cached reflection accessors (resolved once in InitProfiler)
        static Func<object> _getExecutingScript;
        static MethodInfo _getName;
        static MethodInfo _getFilename;

        static Stat StatFor(object script) => _stats.GetOrAdd(script ?? _gameThreadKey, _ => new Stat());

        // ───────────────────────────── init / teardown ─────────────────────────────
        internal static void InitProfiler()
        {
            try
            {
                var core = AppDomain.CurrentDomain.GetAssemblies()
                    .FirstOrDefault(a => string.Equals(a.GetName().Name, "ScriptHookVDotNet", StringComparison.OrdinalIgnoreCase));
                if (core == null) { _initError = "SHVDN core assembly not found"; return; }
                const BindingFlags ANY = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;

                var scriptT = core.GetType("SHVDN.Script");
                var nativeT = core.GetType("SHVDN.NativeFunc");
                var domainT = core.GetType("SHVDN.ScriptDomain");
                if (scriptT == null || nativeT == null || domainT == null) { _initError = "SHVDN types missing"; return; }

                _getName = scriptT.GetMethod("get_Name", ANY);
                _getFilename = scriptT.GetMethod("get_Filename", ANY);
                var execGetter = domainT.GetMethod("get_ExecutingScript", ANY);
                if (execGetter != null)
                    _getExecutingScript = (Func<object>)Delegate.CreateDelegate(typeof(Func<object>), execGetter);

                _harmony = new Harmony("claudebridge.profiler");

                // (a) per-script timing — Script.DoTick() (instance, no args)
                var doTick = scriptT.GetMethod("DoTick", ANY, null, Type.EmptyTypes, null);
                if (doTick == null) { _initError = "SHVDN.Script.DoTick not found"; return; }
                _harmony.Patch(doTick,
                    prefix: new HarmonyMethod(typeof(ClaudeBridge).GetMethod(nameof(DoTick_Pre), ANY)),
                    postfix: new HarmonyMethod(typeof(ClaudeBridge).GetMethod(nameof(DoTick_Post), ANY)));

                // (b) native counting — every SHVDN.NativeFunc.InvokeInternal overload (the call chokepoint)
                var prefix = new HarmonyMethod(typeof(ClaudeBridge).GetMethod(nameof(Native_Pre), ANY));
                int n = 0;
                foreach (var mi in nativeT.GetMethods(ANY).Where(m => m.Name == "InvokeInternal"))
                {
                    try { _harmony.Patch(mi, prefix: prefix); n++; } catch { /* skip an overload we can't patch */ }
                }
                if (n == 0) { _initError = "no NativeFunc.InvokeInternal overloads patched"; return; }

                _patched = true;
                _epochMs = _profClock.Elapsed.TotalMilliseconds;
            }
            catch (Exception ex) { _initError = ex.Message; }
        }

        internal static void TeardownProfiler()
        {
            try { _harmony?.UnpatchAll("claudebridge.profiler"); } catch { }
            _patched = false;
        }

        // ───────────────────────────── Harmony patch bodies (must never throw) ─────────────────────────────
        // Timing: __state carries the start timestamp from prefix to postfix.
        static void DoTick_Pre(object __instance, out long __state) { __state = Stopwatch.GetTimestamp(); }

        static void DoTick_Post(object __instance, long __state)
        {
            try
            {
                long us = (long)((Stopwatch.GetTimestamp() - __state) * _usPerTick);
                var s = StatFor(__instance);
                Interlocked.Increment(ref s.ticks);
                Interlocked.Add(ref s.totalUs, us);
                long old;
                do { old = Interlocked.Read(ref s.maxUs); if (us <= old) break; }
                while (Interlocked.CompareExchange(ref s.maxUs, us, old) != old);
            }
            catch { }
        }

        // Native counting: gated; matches `hash` by name across all InvokeInternal overloads.
        static void Native_Pre(ulong hash)
        {
            if (!_natProfOn) return;   // ~zero overhead when stopped
            try
            {
                object script = _getExecutingScript != null ? _getExecutingScript() : null;
                var s = StatFor(script);
                Interlocked.Increment(ref s.natives);
                if (_perHashOn)
                {
                    var ph = s.perHash;
                    if (ph == null) { ph = new ConcurrentDictionary<ulong, long>(); s.perHash = ph; }
                    ph.AddOrUpdate(hash, 1, (_, v) => v + 1);
                }
            }
            catch { }
        }

        // ───────────────────────────── report helpers ─────────────────────────────
        static string NameOf(object script)
        {
            if (ReferenceEquals(script, _gameThreadKey)) return "(game thread / bridge)";
            try { return _getName?.Invoke(script, null) as string ?? "(unknown)"; } catch { return "(unknown)"; }
        }

        object ScriptsPerf(Dictionary<string, object> p)
        {
            if (!_patched) return new Dictionary<string, object> { ["error"] = "profiler not active", ["init_error"] = _initError };
            double elapsedMs = _profClock.Elapsed.TotalMilliseconds - _epochMs;
            double elapsedSec = Math.Max(elapsedMs / 1000.0, 0.001);
            var rows = _stats
                .Where(kv => kv.Value.ticks > 0)
                .Select(kv =>
                {
                    var st = kv.Value;
                    double totalMs = st.totalUs / 1000.0;
                    double avgMs = totalMs / Math.Max(st.ticks, 1);
                    return new Dictionary<string, object>
                    {
                        ["script"] = NameOf(kv.Key),
                        ["ticks"] = st.ticks,
                        ["avg_ms"] = Math.Round(avgMs, 4),
                        ["max_ms"] = Math.Round(st.maxUs / 1000.0, 4),
                        ["ms_per_sec"] = Math.Round(totalMs / elapsedSec, 2),     // CPU load this script imposes
                        ["pct_of_60fps_frame"] = Math.Round(100.0 * avgMs / 16.667, 2),
                    };
                })
                .OrderByDescending(d => (double)d["ms_per_sec"])
                .ToList();
            return new Dictionary<string, object>
            {
                ["success"] = true,
                ["window_sec"] = Math.Round(elapsedSec, 1),
                ["note"] = "avg_ms = mean OnTick cost; ms_per_sec = CPU-ms this script burns per real second; "
                         + "pct_of_60fps_frame = share of a 16.67ms budget. Sorted hottest-first.",
                ["scripts"] = rows,
            };
        }

        void ScriptsPerfReset()
        {
            _stats.Clear();
            _epochMs = _profClock.Elapsed.TotalMilliseconds;
        }

        object NatProfStart(Dictionary<string, object> p)
        {
            if (!_patched) return new Dictionary<string, object> { ["error"] = "profiler not active", ["init_error"] = _initError };
            _perHashOn = p != null && p.TryGetValue("per_hash", out var ph) && Convert.ToBoolean(ph);
            // fresh native counts for a clean measurement window
            foreach (var s in _stats.Values) { Interlocked.Exchange(ref s.natives, 0); s.perHash = null; }
            _epochMs = _profClock.Elapsed.TotalMilliseconds;
            _natProfOn = true;
            return new Dictionary<string, object> { ["success"] = true, ["counting"] = true, ["per_hash"] = _perHashOn };
        }

        object NatProfStop()
        {
            _natProfOn = false;
            return new Dictionary<string, object> { ["success"] = true, ["counting"] = false };
        }

        object NatProfReport(Dictionary<string, object> p)
        {
            if (!_patched) return new Dictionary<string, object> { ["error"] = "profiler not active", ["init_error"] = _initError };
            double elapsedSec = Math.Max((_profClock.Elapsed.TotalMilliseconds - _epochMs) / 1000.0, 0.001);
            int topHashes = p != null && p.TryGetValue("top", out var t) ? ToInt(t) : 15;

            var rows = _stats
                .Where(kv => kv.Value.natives > 0)
                .Select(kv =>
                {
                    var st = kv.Value;
                    var row = new Dictionary<string, object>
                    {
                        ["script"] = NameOf(kv.Key),
                        ["native_calls"] = st.natives,
                        ["calls_per_sec"] = Math.Round(st.natives / elapsedSec, 1),
                        ["calls_per_frame"] = st.ticks > 0 ? (object)Math.Round((double)st.natives / st.ticks, 1) : null,
                    };
                    if (_perHashOn && st.perHash != null)
                        row["top_natives"] = st.perHash.OrderByDescending(h => h.Value).Take(topHashes)
                            .Select(h => new Dictionary<string, object> { ["hash"] = $"0x{h.Key:X}", ["calls"] = h.Value }).ToList();
                    return row;
                })
                .OrderByDescending(d => (long)d["native_calls"])
                .ToList();

            long total = _stats.Values.Sum(s => s.natives);
            return new Dictionary<string, object>
            {
                ["success"] = true,
                ["counting"] = _natProfOn,
                ["per_hash"] = _perHashOn,
                ["window_sec"] = Math.Round(elapsedSec, 1),
                ["total_native_calls"] = total,
                ["total_calls_per_sec"] = Math.Round(total / elapsedSec, 0),
                ["note"] = "native hashes are hex; resolve names with native_db.json. calls_per_frame uses each script's own tick count.",
                ["scripts"] = rows,
            };
        }

        object ProfilerStatus()
        {
            return new Dictionary<string, object>
            {
                ["patched"] = _patched,
                ["init_error"] = _initError,
                ["native_counting"] = _natProfOn,
                ["per_hash"] = _perHashOn,
                ["tracked_scripts"] = _stats.Count,
                ["harmony"] = typeof(Harmony).Assembly.GetName().Version?.ToString(),
            };
        }
    }
}
