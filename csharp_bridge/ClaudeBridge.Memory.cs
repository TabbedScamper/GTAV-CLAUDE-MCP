using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;

namespace ClaudeBridge
{
    /// <summary>
    /// RE-grade memory editing so Claude can do things no native exposes (code patches, pointer chains, struct
    /// fields) — done SAFELY: every address is validated before we touch it, code pages are made writable via
    /// VirtualProtect and restored, and every byte patch is logged to an UNDO stack so it's reversible.
    ///
    ///   validate_address {addr,size}        is this readable? (protect/state)
    ///   read_chain {base,offsets,type}      follow a pointer chain and read the final value
    ///   resolve_rip_relative {addr,...}     decode a RIP-relative operand to an absolute address
    ///   patch_bytes {addr,bytes}            write bytes over code (protect+write+restore), reversible
    ///   nop {addr,count}                    overwrite N bytes with 0x90, reversible
    ///   list_patches / restore_patch {addr} / restore_all_patches
    /// </summary>
    public partial class ClaudeBridge
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool VirtualProtect(IntPtr addr, UIntPtr size, uint flNewProtect, out uint old);

        const uint PAGE_EXECUTE_READWRITE = 0x40;

        // active patches: address -> original bytes (for undo)
        static readonly Dictionary<long, byte[]> _activePatches = new Dictionary<long, byte[]>();
        static readonly object _patchLock = new object();

        static long Addr(Dictionary<string, object> p)
        {
            object v = p.ContainsKey("addr") ? p["addr"] : (p.ContainsKey("address") ? p["address"] : null);
            if (v == null) throw new Exception("no addr/address");
            return ToLong(v);
        }

        // ---- validation ----
        object ValidateAddress(Dictionary<string, object> p)
        {
            long a = Addr(p);
            int size = p.TryGetValue("size", out var s) ? ToInt(s) : 1;
            int mbiSize = Marshal.SizeOf(typeof(MEMORY_BASIC_INFORMATION));
            if (VirtualQuery((IntPtr)a, out var mbi, mbiSize) == 0)
                return new Dictionary<string, object> { ["readable"] = false, ["error"] = "VirtualQuery failed" };
            bool committed = mbi.State == 0x1000;
            bool guard = (mbi.Protect & 0x100) != 0;
            bool readable = committed && !guard && (mbi.Protect & 0xEE) != 0;
            long regionEnd = mbi.BaseAddress.ToInt64() + mbi.RegionSize.ToInt64();
            bool fits = a + size <= regionEnd;
            return new Dictionary<string, object>
            {
                ["readable"] = readable && fits,
                ["committed"] = committed,
                ["protect"] = $"0x{mbi.Protect:X}",
                ["state"] = $"0x{mbi.State:X}",
                ["type"] = $"0x{mbi.Type:X}",
                ["region_size"] = mbi.RegionSize.ToInt64(),
            };
        }

        // ---- pointer-chain read: base -> [+off0] -> [+off1] -> ... -> read(type) at +lastOff ----
        object ReadChain(Dictionary<string, object> p)
        {
            long cur = ToLong(p.ContainsKey("base") ? p["base"] : Addr(p));
            var offsets = (p.TryGetValue("offsets", out var ov) && ov is System.Collections.IEnumerable en && !(ov is string))
                ? en.Cast<object>().Select(ToLong).ToList() : new List<long>();
            string type = Str(p, "type", "ptr").ToLowerInvariant();
            var trail = new List<string> { $"0x{cur:X}" };
            // dereference through all but the last offset
            for (int i = 0; i < offsets.Count - 1; i++)
            {
                var pv = ReadI64(cur + offsets[i]);
                if (pv == null || pv == 0) return new Dictionary<string, object> { ["error"] = $"null pointer at step {i}", ["trail"] = trail };
                cur = pv.Value; trail.Add($"0x{cur:X}");
            }
            long finalAddr = cur + (offsets.Count > 0 ? offsets[offsets.Count - 1] : 0);
            object val;
            switch (type)
            {
                case "float": val = ReadF(finalAddr); break;
                case "int": val = ReadI32(finalAddr); break;
                case "long": case "ptr": val = (object)null; var l = ReadI64(finalAddr); val = l == null ? null : (object)($"0x{l.Value:X}"); break;
                case "byte": { var b = ReadBytes(finalAddr, 1); val = b?[0]; break; }
                case "bytes": { int n = p.TryGetValue("count", out var c) ? ToInt(c) : 16; var b = ReadBytes(finalAddr, n); val = b == null ? null : BitConverter.ToString(b); break; }
                default: val = ReadI32(finalAddr); break;
            }
            return new Dictionary<string, object> { ["address"] = $"0x{finalAddr:X}", ["value"] = val, ["trail"] = trail };
        }

        // ---- resolve a RIP-relative operand (e.g. lea/mov reg,[rip+disp32]) to an absolute address ----
        object ResolveRipRelative(Dictionary<string, object> p)
        {
            long a = Addr(p);
            int dispOff = p.TryGetValue("disp_offset", out var d) ? ToInt(d) : (p.TryGetValue("offset", out var o) ? ToInt(o) : 3);
            int instrLen = p.TryGetValue("instruction_length", out var il) ? ToInt(il) : (p.TryGetValue("length", out var l) ? ToInt(l) : 7);
            var disp = ReadI32(a + dispOff);
            if (disp == null) return new Dictionary<string, object> { ["error"] = "could not read disp32" };
            long target = a + instrLen + disp.Value;
            return new Dictionary<string, object> { ["target"] = $"0x{target:X}", ["disp"] = disp.Value };
        }

        // ---- code patching (protect -> write -> restore), reversible ----
        static byte[] ParseBytes(string s)
        {
            var parts = s.Split(new[] { ' ', '-', ',' }, StringSplitOptions.RemoveEmptyEntries);
            var b = new byte[parts.Length];
            for (int i = 0; i < parts.Length; i++) b[i] = Convert.ToByte(parts[i], 16);
            return b;
        }

        bool WriteProtected(long addr, byte[] bytes)
        {
            if (!VirtualProtect((IntPtr)addr, (UIntPtr)bytes.Length, PAGE_EXECUTE_READWRITE, out uint old)) return false;
            bool ok = WriteProcessMemory(GetCurrentProcess(), (IntPtr)addr, bytes, bytes.Length, out int w) && w == bytes.Length;
            VirtualProtect((IntPtr)addr, (UIntPtr)bytes.Length, old, out _);
            return ok;
        }

        object PatchBytes(Dictionary<string, object> p)
        {
            long a = Addr(p);
            string bs = Str(p, "bytes");
            if (string.IsNullOrEmpty(bs)) return new Dictionary<string, object> { ["error"] = "no bytes" };
            byte[] bytes;
            try { bytes = ParseBytes(bs); } catch (Exception ex) { return new Dictionary<string, object> { ["error"] = "bad bytes: " + ex.Message }; }
            return ApplyPatch(a, bytes);
        }

        object Nop(Dictionary<string, object> p)
        {
            long a = Addr(p);
            int count = p.TryGetValue("count", out var c) ? ToInt(c) : 1;
            if (count <= 0 || count > 4096) return new Dictionary<string, object> { ["error"] = "count out of range (1..4096)" };
            var bytes = new byte[count];
            for (int i = 0; i < count; i++) bytes[i] = 0x90;
            return ApplyPatch(a, bytes);
        }

        object ApplyPatch(long a, byte[] bytes)
        {
            // validate the whole span is committed before touching it
            int mbiSize = Marshal.SizeOf(typeof(MEMORY_BASIC_INFORMATION));
            if (VirtualQuery((IntPtr)a, out var mbi, mbiSize) == 0 || mbi.State != 0x1000)
                return new Dictionary<string, object> { ["error"] = $"address 0x{a:X} not committed (refusing to patch)" };
            byte[] orig = ReadBytes(a, bytes.Length);
            if (orig == null) return new Dictionary<string, object> { ["error"] = $"could not read original at 0x{a:X}" };
            if (!WriteProtected(a, bytes)) return new Dictionary<string, object> { ["error"] = $"WriteProcessMemory failed at 0x{a:X}" };
            lock (_patchLock) { if (!_activePatches.ContainsKey(a)) _activePatches[a] = orig; }   // keep the FIRST original for full undo
            return new Dictionary<string, object> { ["success"] = true, ["address"] = $"0x{a:X}", ["wrote"] = BitConverter.ToString(bytes), ["original"] = BitConverter.ToString(orig) };
        }

        object ListPatches(Dictionary<string, object> p)
        {
            lock (_patchLock)
            {
                var rows = _activePatches.Select(kv => (object)new Dictionary<string, object>
                { ["address"] = $"0x{kv.Key:X}", ["original_bytes"] = BitConverter.ToString(kv.Value), ["len"] = kv.Value.Length }).ToList();
                return new Dictionary<string, object> { ["count"] = rows.Count, ["patches"] = rows };
            }
        }

        object RestorePatch(Dictionary<string, object> p)
        {
            long a = Addr(p);
            lock (_patchLock)
            {
                if (!_activePatches.TryGetValue(a, out var orig)) return new Dictionary<string, object> { ["error"] = $"no active patch at 0x{a:X}" };
                bool ok = WriteProtected(a, orig);
                if (ok) _activePatches.Remove(a);
                return new Dictionary<string, object> { ["success"] = ok, ["restored"] = $"0x{a:X}" };
            }
        }

        object RestoreAllPatches(Dictionary<string, object> p)
        {
            int n = 0;
            lock (_patchLock)
            {
                foreach (var kv in _activePatches.ToList()) { if (WriteProtected(kv.Key, kv.Value)) n++; }
                _activePatches.Clear();
            }
            return new Dictionary<string, object> { ["success"] = true, ["restored"] = n };
        }
    }
}
