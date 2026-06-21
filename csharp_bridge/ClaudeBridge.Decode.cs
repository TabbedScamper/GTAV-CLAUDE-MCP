using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;

namespace ClaudeBridge
{
    /// <summary>
    /// "What does this memory actually do?" — without watching the screen.  The core RE method is DIFFERENTIAL:
    ///   snapshot a region  ->  cause ONE thing to change (a native, an input, time passing)  ->  diff  ->  the
    ///   offsets that moved ARE that thing.  Plus `watch` (which offsets change on their own over time -> finds
    ///   live values like rpm/suspension) and `correlate` (write a candidate, read a MEANING-bearing native back,
    ///   see what the engine derived -> decode a field by its semantic effect).
    ///
    ///   snapshot {addr,size,label}
    ///   diff {label}                       -> changed offsets, old/new as int+float
    ///   watch {addr,size,seconds,hz}       -> offsets that changed + min/max + #changes (hottest first)
    ///   correlate {addr,type,values,native,native_args} -> write each value, call the native, report its return
    /// </summary>
    public partial class ClaudeBridge
    {
        class Snap { public long addr; public byte[] bytes; }
        static readonly Dictionary<string, Snap> _snaps = new Dictionary<string, Snap>();

        object Snapshot(Dictionary<string, object> p)
        {
            long a = Addr(p);
            int size = p.TryGetValue("size", out var s) ? ToInt(s) : 256;
            if (size <= 0 || size > 1 << 20) return new Dictionary<string, object> { ["error"] = "size out of range (1..1MB)" };
            string label = Str(p, "label", "default");
            var bytes = ReadBytes(a, size);
            if (bytes == null) return new Dictionary<string, object> { ["error"] = $"could not read {size} bytes at 0x{a:X}" };
            _snaps[label] = new Snap { addr = a, bytes = bytes };
            return new Dictionary<string, object> { ["success"] = true, ["label"] = label, ["address"] = $"0x{a:X}", ["size"] = size };
        }

        object Diff(Dictionary<string, object> p)
        {
            string label = Str(p, "label", "default");
            if (!_snaps.TryGetValue(label, out var snap)) return new Dictionary<string, object> { ["error"] = $"no snapshot '{label}'" };
            var now = ReadBytes(snap.addr, snap.bytes.Length);
            if (now == null) return new Dictionary<string, object> { ["error"] = "region no longer readable" };
            var changes = new List<object>();
            int i = 0;
            while (i < now.Length)
            {
                if (now[i] != snap.bytes[i])
                {
                    // align to 4 for int/float interpretation; report the dword that contains the change
                    int off = i & ~3;
                    if (off + 4 <= now.Length)
                    {
                        int oldI = BitConverter.ToInt32(snap.bytes, off), newI = BitConverter.ToInt32(now, off);
                        float oldF = BitConverter.ToSingle(snap.bytes, off), newF = BitConverter.ToSingle(now, off);
                        changes.Add(new Dictionary<string, object>
                        {
                            ["offset"] = $"0x{off:X}",
                            ["old_int"] = oldI, ["new_int"] = newI,
                            ["old_float"] = SaneF(oldF), ["new_float"] = SaneF(newF),
                        });
                        i = off + 4; continue;
                    }
                }
                i++;
            }
            // refresh the snapshot so successive diffs are incremental
            snap.bytes = now;
            return new Dictionary<string, object> { ["label"] = label, ["changed"] = changes.Count, ["changes"] = changes.Take(200).ToList() };
        }

        object Watch(Dictionary<string, object> p)
        {
            long a = Addr(p);
            int size = p.TryGetValue("size", out var s) ? ToInt(s) : 256;
            double seconds = p.TryGetValue("seconds", out var sec) ? Math.Min(Convert.ToDouble(sec), 20) : 3;
            int hz = p.TryGetValue("hz", out var h) ? Math.Min(Math.Max(ToInt(h), 5), 200) : 30;
            if (size <= 0 || size > 65536) return new Dictionary<string, object> { ["error"] = "size out of range (1..64KB)" };

            var baseline = ReadBytes(a, size);
            if (baseline == null) return new Dictionary<string, object> { ["error"] = "unreadable" };
            int dwords = size / 4;
            var changeCount = new int[dwords];
            var minF = new float[dwords]; var maxF = new float[dwords];
            var lastI = new int[dwords];
            for (int d = 0; d < dwords; d++) { float f = BitConverter.ToSingle(baseline, d * 4); minF[d] = f; maxF[d] = f; lastI[d] = BitConverter.ToInt32(baseline, d * 4); }

            int interval = 1000 / hz;
            var sw = Stopwatch.StartNew();
            int samples = 0;
            while (sw.Elapsed.TotalSeconds < seconds)
            {
                var cur = ReadBytes(a, size);
                if (cur != null)
                {
                    samples++;
                    for (int d = 0; d < dwords; d++)
                    {
                        int iv = BitConverter.ToInt32(cur, d * 4);
                        if (iv != lastI[d]) { changeCount[d]++; lastI[d] = iv; }
                        float f = BitConverter.ToSingle(cur, d * 4);
                        if (!float.IsNaN(f) && !float.IsInfinity(f)) { if (f < minF[d]) minF[d] = f; if (f > maxF[d]) maxF[d] = f; }
                    }
                }
                Thread.Sleep(interval);
            }
            var rows = Enumerable.Range(0, dwords).Where(d => changeCount[d] > 0)
                .OrderByDescending(d => changeCount[d])
                .Take(60)
                .Select(d => (object)new Dictionary<string, object>
                {
                    ["offset"] = $"0x{d * 4:X}",
                    ["changes"] = changeCount[d],
                    ["min_float"] = SaneF(minF[d]), ["max_float"] = SaneF(maxF[d]),
                }).ToList();
            return new Dictionary<string, object>
            {
                ["address"] = $"0x{a:X}", ["size"] = size, ["samples"] = samples, ["seconds"] = Math.Round(seconds, 1),
                ["active_offsets"] = rows.Count, ["note"] = "offsets that changed on their own; min/max float shows the range (rpm 0..1, suspension oscillates, etc.)",
                ["fields"] = rows,
            };
        }

        // Decode a field by its semantic EFFECT: write each candidate, call a meaning-bearing native, report the return.
        // e.g. write floats to a vehicle offset, read GET_VEHICLE_ENGINE_HEALTH -> if it tracks, that offset is engine health.
        object Correlate(Dictionary<string, object> p)
        {
            long a = Addr(p);
            string type = Str(p, "type", "float").ToLowerInvariant();
            string native = Str(p, "native");
            var nativeArgs = Arr(p, "native_args");
            var values = (p.TryGetValue("values", out var vv) && vv is System.Collections.IEnumerable en && !(vv is string))
                ? en.Cast<object>().ToList() : new List<object>();
            if (string.IsNullOrEmpty(native) || values.Count == 0)
                return new Dictionary<string, object> { ["error"] = "need {native, values:[...]}" };

            byte[] saved = ReadBytes(a, type == "float" || type == "int" ? 4 : 8);
            if (saved == null) return new Dictionary<string, object> { ["error"] = $"unreadable at 0x{a:X}" };
            var rt = Str(p, "native_return", "float");
            var results = new List<object>();
            try
            {
                foreach (var val in values)
                {
                    byte[] b = type == "float" ? BitConverter.GetBytes((float)Convert.ToDouble(val)) : BitConverter.GetBytes((int)ToLong(val));
                    WriteProcessMemory(GetCurrentProcess(), (IntPtr)a, b, b.Length, out _);
                    Thread.Sleep(60);  // let a frame tick so the engine consumes it
                    object ret = OnGameThread(() => CallNative(ResolveHash(native), nativeArgs, rt));
                    results.Add(new Dictionary<string, object> { ["wrote"] = val, ["native_returned"] = ret });
                }
            }
            finally { WriteProcessMemory(GetCurrentProcess(), (IntPtr)a, saved, saved.Length, out _); }  // always restore
            return new Dictionary<string, object>
            {
                ["address"] = $"0x{a:X}", ["native"] = native,
                ["note"] = "if native_returned tracks the values you wrote, this offset feeds that native's meaning.",
                ["results"] = results,
            };
        }

        static object SaneF(float f) => (float.IsNaN(f) || float.IsInfinity(f) || Math.Abs(f) > 1e12) ? (object)null : Math.Round(f, 4);
    }
}
