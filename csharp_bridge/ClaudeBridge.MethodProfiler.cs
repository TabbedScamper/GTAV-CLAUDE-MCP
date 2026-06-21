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
    /// PHASE 2b — per-METHOD timing.  "Which of a mod's own functions are hot?" — the method-level view that
    /// complements the native-call view (natprof says GET_ENTITY_MODEL is called a lot; methprof says it's
    /// PlateSharedByAnotherCar burning the time).
    ///
    /// On demand, Harmony-patches any set of managed methods (default: ELSC's per-frame methods) with a generic
    /// timing prefix/postfix and records call count + inclusive wall time per method. Works on ANY loaded mod's
    /// methods, so you can profile other users' scripts too.
    ///
    ///   methprof_start {targets:["Type:Method","Type:*", ...]}   patch + begin timing (default ELSC OnTick set)
    ///   methprof_report {top}                                    per-method calls + total/avg/max ms, hot-first
    ///   methprof_stop                                            remove the patches (overhead gone); stats kept
    ///   methprof_clear                                           drop collected stats
    ///
    /// Target syntax:  "ExtendedLSC.Main:OnTick"  (exact),  "Main:OnTick" (match by simple class name),
    ///                 "VehicleStanceManager:*" (every declared method of the type).
    ///
    /// SAFETY: separate Harmony id from the native profiler (independently removable); prefixes/postfixes never
    /// throw into the patched method; each Patch is try/caught so one un-patchable method can't abort the batch;
    /// patches are removed on bridge Abort/reload.
    /// </summary>
    public partial class ClaudeBridge
    {
        sealed class MStat { public long calls; public long totalUs; public long maxUs; }

        const string METH_ID = "claudebridge.methprof";
        static Harmony _methHarmony;
        static readonly ConcurrentDictionary<MethodBase, MStat> _mstats = new ConcurrentDictionary<MethodBase, MStat>();
        static readonly HashSet<MethodBase> _patchedMethods = new HashSet<MethodBase>();
        static double _methEpochMs;

        // Sensible default if the caller names no targets: ELSC's per-frame entry points.
        static readonly string[] _defaultTargets =
        {
            "Main:OnTick", "Main:DumpMenuState", "Main:BindPlateForCurrentVehicle", "Main:DedupeWorldPlates",
            "Main:DrawCollisionDebug", "WindowTintManager:Tick", "VehicleSnapshotManager:Tick",
            "VehicleStanceManager:Update",
        };

        // ───────────────────────────── target resolution ─────────────────────────────
        static IEnumerable<Type> ResolveTypes(string left)
        {
            foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types;
                try { types = a.GetTypes(); }
                catch (ReflectionTypeLoadException ex) { types = ex.Types.Where(t => t != null).ToArray(); }
                catch { continue; }
                foreach (var t in types)
                    if (t != null && (t.FullName == left || t.Name == left))
                        yield return t;
            }
        }

        static IEnumerable<MethodInfo> ResolveMethods(string spec)
        {
            int colon = spec.LastIndexOf(':');
            if (colon <= 0) yield break;
            string left = spec.Substring(0, colon), m = spec.Substring(colon + 1);
            const BindingFlags F = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;
            foreach (var t in ResolveTypes(left))
            {
                MethodInfo[] all;
                try { all = t.GetMethods(F); } catch { continue; }
                foreach (var mi in all)
                {
                    if (m != "*" && mi.Name != m) continue;
                    if (mi.IsAbstract || mi.ContainsGenericParameters) continue;
                    bool hasBody; try { hasBody = mi.GetMethodBody() != null; } catch { hasBody = false; }
                    if (!hasBody) continue;
                    yield return mi;
                }
            }
        }

        // ───────────────────────────── Harmony patch bodies (never throw) ─────────────────────────────
        static void M_Pre(out long __state) { __state = Stopwatch.GetTimestamp(); }

        static void M_Post(MethodBase __originalMethod, long __state)
        {
            try
            {
                long us = (long)((Stopwatch.GetTimestamp() - __state) * _usPerTick);
                var st = _mstats.GetOrAdd(__originalMethod, _ => new MStat());
                Interlocked.Increment(ref st.calls);
                Interlocked.Add(ref st.totalUs, us);
                long old;
                do { old = Interlocked.Read(ref st.maxUs); if (us <= old) break; }
                while (Interlocked.CompareExchange(ref st.maxUs, us, old) != old);
            }
            catch { }
        }

        // ───────────────────────────── commands ─────────────────────────────
        object MethProfStart(Dictionary<string, object> p)
        {
            string[] targets = _defaultTargets;
            if (p != null && p.TryGetValue("targets", out var tv) && tv is System.Collections.IEnumerable en && !(tv is string))
            {
                var list = en.Cast<object>().Select(o => o?.ToString()).Where(s => !string.IsNullOrEmpty(s)).ToArray();
                if (list.Length > 0) targets = list;
            }

            if (_methHarmony == null) _methHarmony = new Harmony(METH_ID);
            var pre = new HarmonyMethod(typeof(ClaudeBridge).GetMethod(nameof(M_Pre), BindingFlags.NonPublic | BindingFlags.Static));
            var post = new HarmonyMethod(typeof(ClaudeBridge).GetMethod(nameof(M_Post), BindingFlags.NonPublic | BindingFlags.Static));

            var patched = new List<string>();
            var failed = new List<object>();
            var unresolved = new List<string>();
            foreach (var spec in targets)
            {
                var methods = ResolveMethods(spec).ToList();
                if (methods.Count == 0) { unresolved.Add(spec); continue; }
                foreach (var mi in methods)
                {
                    if (_patchedMethods.Contains(mi)) { patched.Add(Sig(mi) + " (already)"); continue; }
                    try { _methHarmony.Patch(mi, prefix: pre, postfix: post); _patchedMethods.Add(mi); patched.Add(Sig(mi)); }
                    catch (Exception ex) { failed.Add(new Dictionary<string, object> { ["method"] = Sig(mi), ["error"] = ex.Message }); }
                }
            }
            _mstats.Clear();
            _methEpochMs = _profClock.Elapsed.TotalMilliseconds;
            return new Dictionary<string, object>
            {
                ["success"] = true,
                ["patched_count"] = patched.Count,
                ["patched"] = patched,
                ["failed"] = failed,
                ["unresolved_targets"] = unresolved,
                ["note"] = "timing started; call methprof_report after the game runs a bit.",
            };
        }

        object MethProfReport(Dictionary<string, object> p)
        {
            int top = p != null && p.TryGetValue("top", out var t) ? ToInt(t) : 30;
            double elapsedSec = Math.Max((_profClock.Elapsed.TotalMilliseconds - _methEpochMs) / 1000.0, 0.001);
            var rows = _mstats
                .Where(kv => kv.Value.calls > 0)
                .Select(kv =>
                {
                    var st = kv.Value; double totalMs = st.totalUs / 1000.0;
                    return new Dictionary<string, object>
                    {
                        ["method"] = Sig(kv.Key),
                        ["calls"] = st.calls,
                        ["total_ms"] = Math.Round(totalMs, 2),
                        ["avg_ms"] = Math.Round(totalMs / st.calls, 4),
                        ["max_ms"] = Math.Round(st.maxUs / 1000.0, 4),
                        ["ms_per_sec"] = Math.Round(totalMs / elapsedSec, 2),
                    };
                })
                .OrderByDescending(d => (double)d["ms_per_sec"])
                .Take(top).ToList();
            return new Dictionary<string, object>
            {
                ["success"] = true,
                ["patched_methods"] = _patchedMethods.Count,
                ["window_sec"] = Math.Round(elapsedSec, 1),
                ["note"] = "ms_per_sec = wall-time this method burns per real second (inclusive of callees). "
                         + "Patch a parent AND its children to see where a hot parent spends its time.",
                ["methods"] = rows,
            };
        }

        object MethProfStop()
        {
            try { _methHarmony?.UnpatchAll(METH_ID); } catch { }
            _patchedMethods.Clear();
            return new Dictionary<string, object> { ["success"] = true, ["unpatched"] = true, ["stats_kept"] = _mstats.Count };
        }

        object MethProfClear() { _mstats.Clear(); _methEpochMs = _profClock.Elapsed.TotalMilliseconds; return "ok"; }

        internal static void TeardownMethodProfiler()
        {
            try { _methHarmony?.UnpatchAll(METH_ID); } catch { }
            _patchedMethods.Clear();
        }

        static string Sig(MethodBase m) => (m.DeclaringType != null ? m.DeclaringType.Name : "?") + "." + m.Name;
    }
}
