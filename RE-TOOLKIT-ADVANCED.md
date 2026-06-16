# RE Toolkit — advanced tier (built + research)

The "find → modify → change → repair anything" tier, from a 5-agent source sweep (Cheat Engine/scanmem,
IDA SigMaker, kylehalladay/MS-docs, FiveM/Menyoo/alexguirre). All BUILT + tested off-game and wired into
`bridge.py`/`server.py` unless marked. Companion to `RE-TOOLKIT.md`.

## What shipped (modules)
| Module | Tools | Status |
|---|---|---|
| `re_tools_scan.py` | `scan_first` `scan_next` `scan_results` `scan_count` `scan_undo` `scan_reset` | ✅ tested 8/8 |
| `re_tools.py` (+) | `make_signature` | ✅ built (needs capstone to run) |
| `re_tools_patch.py` | `patch_bytes` `nop` `restore_patch` `list_patches` `restore_all_patches` `alloc_cave` `capture_stack` | ✅ tested 9/9 |
| `re_tools_pardump.py` (+) | `enum_decode` `par_struct` | ✅ tested |
| `re_tools_dynamic.py` (+) | `repair_vehicle_full` | ⚠️ untested (needs game) |

---

## 1. Value scan + narrowing (`scan_first`/`scan_next`) — find an address you can SEE but don't know
The CE/scanmem workflow: **unknown → take damage → `decreased` → heal → `increased` → idle → `unchanged`**,
repeat until a few addresses remain, then `make_signature` / watch them.
- **Every compare is `cur` vs `user_value` OR `old_value`** (scanmem `scanroutines.c`). first scan =
  exact|unknown|bigger|smaller|between; next scan = changed|unchanged|increased|decreased|exact|incby|decby.
- **Two storage modes** (the key design): `unknown` first scan keeps a **region snapshot** (baseline);
  the first narrowing converts to a **sparse (addr,value) list**. Switch to list ASAP to bound memory.
- **Perf:** numpy `frombuffer` (zero-copy view of live pages) + vectorized compare, ~10–100× the
  python loop; aligned typed dtype = the "fast scan" (~4×). Pure-python fallback (`bytes.find` exact)
  works without numpy. Runs off-thread (read-only, paused-safe). Default region = private+writable heap.
- Float compares use an epsilon band (`eps`, CE's rounded/truncated modes).
Sources: scanmem `scanroutines.c`/`value.h`, CE `memscan.pas` + wiki, numpy.frombuffer.

## 2. Auto-signature (`make_signature`) — make a finding survive game patches
Reverse of scanning: emit a minimal **unique** AOB that's stable across Rockstar updates.
- **Wildcard the operand bytes that relocate, keep opcodes literal** (SigMaker rule, `[op.offb, size)`):
  call/jmp **rel32**, RIP-relative **disp32**, and `mov reg,imm64` **image-address** immediates. Keep
  constants/zero/short-rel8/no-operand instructions **literal** (best uniqueness anchors).
- **Grow instruction-by-instruction until exactly one module match** (early-exit at the 2nd match), then
  **trim trailing wildcards**. Prefer `.text`; don't start with a wildcard.
- **Durability:** a patch relinks → addresses move (the bytes you wildcarded) but the opcode stream is
  stable (the bytes you kept). Cache `{build_id, signature}` so a finding self-heals: re-scan the sig on
  the new build. Uses our capstone `disasm` + regex scanner (uniqueness via `finditer`, stop at 2).
Sources: A200K / ajkhoury / kweatherman SigMaker, GuidedHacking.

## 3. Code modification (`patch_bytes`/`nop`/`alloc_cave`/`capture_stack`) — change behavior
- **`patch_bytes` / `nop`** — reversible (original bytes snapshotted + WAL'd before the write;
  `restore_patch`/`restore_all_patches`). `nop` is **length-aware** (disasm → NOP whole instructions
  with `0x90`, never half one). VirtualProtect→RWX→restore + FlushInstructionCache. Gated to executable
  in-module unless `allow_outside`. Use to disable a check (`nop` a `jz`/`call`).
- **`alloc_cave(size, near)`** — VirtualAlloc RWX **within ±2GB** of the module (so a 5-byte rel32
  jmp/call can reach it) by spiraling outward probing MEM_FREE pages (kylehalladay
  `AllocatePageNearAddress`). For trampolines / injected logic.
- **`capture_stack`** — `RtlCaptureStackBackTrace` (the easy 90% path), return addresses symbolized to
  `GTA5.exe+offset` via the `.pdata` func table. Use from a watchpoint/hook to learn *who* called.
  (Manual unwind via `RtlVirtualUnwind` + our `RUNTIME_FUNCTION`/`UNWIND_INFO` is the fallback for
  unwinding a captured CONTEXT from another thread.)
- **Detours:** for non-trivial hooks use `cyminhook` (MinHook wheel) — it does stolen-instruction
  relocation/RIP-fixups; pure-ctypes only for a simple "5-byte jmp into a cave". (researched; not shipped)
- **Safety:** patches are tiny + reversible; the real risk is a **torn write vs the live game thread** —
  patch during a quiet moment, prefer single-instruction patches, keep it on the game thread.
Sources: kylehalladay "X64 Function Hooking by Example", MS docs (VirtualAlloc/Query, RtlCaptureStackBackTrace,
RtlVirtualUnwind, x64 exception handling), x64dbg.

## 4. Repair / modify subsystems (entry-point map)
- **Vehicle repair** (`repair_vehicle_full`, natives): `SET_VEHICLE_FIXED` + `SET_VEHICLE_DEFORMATION_FIXED`
  + engine/petrol/body health + dirt 0 (Menyoo `GTAvehicle::Repair`). For deformation that `FIXED` leaves,
  the memory route is the CVehicle **fragInst** deformation buffer. FiveM resolves the **CVehicleDamage**
  struct off CVehicle by AOB (`F3 44 0F 11 4C 24 ? E8 ? ? ? ? EB 7A`, read disp@-11) — build-robust.
  Broken-part bitfields live ~`+0x77C..0x84C` (per-build, Menyoo table).
- **Model/archetype + streaming:** `fwArchetype`/`CBaseModelInfo` (size 0x70: `flags`, `numRefs`,
  `assignedStreamingSlot`, `assetType`); `fwArchetypeManager::GetArchetypeFromHashKey(hash,id)` →
  archetype ptr; `streaming::Manager::GetInstance()->RequestObject/ReleaseObject(id)` to load/unload.
- **Stats:** SP cash/skills are **packed stats in `CStatsMgr`**, not loose ints → use `STAT_SET_INT`/
  `STAT_SET_FLOAT`/`STAT_SET_MASKED_INT` with names `SP0_TOTAL_CASH`, `SP0_SHOOTING_ABILITY`, etc.
- **Enums** (`enum_decode`): the parser dump's enum blocks → int field → named value (e.g. `eVehicleClass`
  6 → `VC_SPORT`); handles bitflags. Read width from the field's `ENUM.{8,16,32}BIT` tag.
- Other native-clean targets: decorators (`DECOR_*`), factories (`CREATE_PED/VEHICLE`), time/weather.
> **Offsets are per-build** — re-resolve by AOB and verify against the imported parser dump. The most
> complete CVehicleDamage/CStatsMgr layouts (Yimura/GTAV-Classes, YimMenu) were DMCA'd, so those exact
> offsets must be re-derived; FiveM headers, MenyooSP, and alexguirre dumps are the live references.
Sources: MenyooSP `GTAvehicle.cpp`/`GTAmemory.cpp`, FiveM `EntitySystem.h`/`Streaming.h`/`VehicleExtraNatives.cpp`,
alexguirre rage-parser-dumps, citizenfx natives.

## Recommended workflow (ties it together)
1. `scan_first("unknown")` → change the value in-game → `scan_next(increased/decreased/...)` → narrow.
2. `scan_results` → pick the address → `make_signature` it (durable across patches) → `note_finding`.
3. `find_xrefs`/`capture_stack`/`disasm` → find the code that uses it → `identify`/`par_struct` to map
   the owning struct → `inspect(struct=...)` for named fields → `enum_decode` int fields.
4. To change behavior: `patch_bytes`/`nop` the code (reversible) or `write_memory` the value; to repair:
   `repair_vehicle_full` or reset the relevant struct/stat.

## Still researched-not-built
- **ReClass-style recursive struct expansion** (auto-type + follow pointers + persist named classes) —
  the research agent for it was auto-blocked by a content filter; re-run reframed. `inspect` already does
  ReClass-lite typing; growing it to recursive `expand(addr, depth)` + persisted class defs is the next step.
- **Inline detours** (cyminhook) — researched; ship when a real hook is needed in-game.

---

## Getting GOOD info out of pre-made mods (decompile-quality workflow)

Ghidra on a stripped C++ binary is genuinely messy — wrong types, `FUN_0040…` everywhere, no
direction. The fix is mostly **don't use Ghidra for GTA mods** — pick source material that decompiles
cleanly, and reserve Ghidra for the rare native `.asi`.

### Reference-quality ladder (prefer the top; Ghidra is the floor)
1. **GitHub source** — clean, named, licensed. Always check if a gta5-mods page links its repo.
2. **SHVDN `.dll` (managed .NET) → ILSpy/dnSpyEx** — decompiles to **near-perfect C#**: real method
   names, `Function.Call(Hash.CREATE_VEHICLE, …)` sequences intact. ~80% of gta5-mods *scripts* are
   these. This is the workhorse — transcode the C# native calls straight to bridge `call_native(...)`.
3. **Lua / JS (FiveM resources)** — already source.
4. **Native `.asi` / C++ `.dll` → Ghidra** — lossy, last resort. Use the techniques below.

### .NET (SHVDN) — tools
- **ILSpy / `ilspycmd`** (`dotnet tool install -g ilspycmd`) — headless, scriptable; the default.
  `ilspycmd Mod.dll -o Examples/_decompiled/<mod>` dumps a full C# project.
- **dnSpyEx** (maintained dnSpy fork) — interactive, when you want to step/edit IL.
- Obfuscated (rare for SP mods)? run **de4dot** first, then ILSpy.

### Native `.asi` / C++ — make Ghidra less messy
1. **Recover classes from RTTI** — run Ghidra's `RecoverClassesFromRTTIScript` (MSVC:
   `RTTIWindowsClassRecoverer`). GTA/RAGE binaries keep RTTI, so this restores class hierarchies,
   ctors/dtors, and vtables → far better than raw `FUN_` soup. (Mirrors our live `identify`/RTTI tool.)
2. **Feed it known types** — import a Ghidra Data Type archive built from **GTAV-Classes** headers
   (`CPed/CVehicle/CHandlingData`) and the **ScriptHookV SDK** export signatures, so structs/args type
   correctly instead of `undefined8`. Our `par_index.json` field names can seed struct labels too.
3. **Fix the obvious first** — set calling convention, correct undefined functions (`D`/`F`),
   define data as the right type; the decompiler cascades from there. (NSA "Improving Disassembly and
   Decompilation" class.)
4. **AI-assisted loop = the real "direction" fix.** Run **GhidraMCP** (Ghidra plugin + MCP bridge):
   it exposes `list_functions` / `decompile` / `rename_function` / `set_comment` as tools, so **Claude
   itself reads each messy function, infers intent, and writes back meaningful names + comments** —
   turning aimless `FUN_`/`DAT_` output into a navigable, annotated map. This is the single biggest
   quality win for C++ targets and directly answers "no clear direction."

### Bottom line
Choosing SHVDN-C# (ILSpy) or source over a C++ `.asi` removes the Ghidra problem for ~all script mods.
When a native binary is unavoidable: RTTI-recover → apply known types → drive it with GhidraMCP so
Claude annotates as it reads. Tools: ilspycmd, dnSpyEx, de4dot, Ghidra + RecoverClassesFromRTTI +
GhidraMCP. Refs: ILSpy (icsharpcode/ILSpy), NSA Ghidra advanced class, github.com/LaurieWired/GhidraMCP.
