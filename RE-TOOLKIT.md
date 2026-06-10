# RE Toolkit — implementation blueprint for deep-modding capabilities

How to add the heavy reverse-engineering capabilities to the in-process bridge **natively**
(ctypes-first, minimal deps) with good performance. Built from a 5-agent research sweep of CE/x64dbg
internals, FiveM/Menyoo/YimMenu source, alexguirre's parser dumps, and benchmarked Python perf.
Confidence tags inline. Companion to `DISCOVERIES.md` / `RPF-REFERENCE.md` / `RPF-HOTRELOAD-RUNBOOK.md`.

The bridge runs **inside GTA5.exe** (PyLoaderV/CPython 3.12), so the game's address space *is* ours —
ctypes reads/writes memory directly (no `ReadProcessMemory`). That single fact makes almost all of this
feasible in pure Python.

---

## 0. Dependency policy — "no bunch of extra software" (benchmarked)

| Dep | Verdict | Footprint | Why |
|---|---|---|---|
| **capstone** | **INSTALL** (the one hard dep) | ~1.3 MB prebuilt `py3-none-win_amd64` wheel, **no compiler** | Real x86-64 disassembly. Hand-writing a decoder is a multi-month project. |
| **iced-x86** | *prefer IF a cp312 wheel exists* | <1 MB native wheel, zero per-instr alloc, structured operand API | Better than capstone for our use (extract `[reg+disp]` offsets + branch targets as fields) — **but verify a CPython-3.12 win_amd64 wheel exists first**; if not, capstone is the safe drop-in. |
| **numpy** | **OPTIONAL** (import-guarded) | one prebuilt wheel, no compiler | ~4× on **aligned value/pointer scans only**. Must match the embedded cp312 ABI. Always keep a pure fallback. |
| **Frida / pymem / keystone / MinHook-as-dep** | **AVOID** | Frida agent ~15 MB | We're already injected — Frida duplicates our runtime. pymem is RPM (out-of-process, strictly worse). Do hooking in pure ctypes. |

**Net new hard dependency: capstone (~1.3 MB).** Everything else is pure ctypes or an optional accelerator.

---

## 1. Performance foundation (applies to everything below)

**1.1 In-process memory access** — read a whole readable region **once** with `ctypes.string_at(addr,size)`
(or reuse a `bytearray` via `ctypes.memmove`), wrap in `memoryview` for zero-copy slicing, then parse the
snapshot. Decode single fields with `struct.unpack_from(fmt, buf, off)` (no slice alloc); walk aligned
pointer slots with `memoryview(buf).cast('Q')`. Never `string_at` per field in a loop.

**1.2 Scanning — `bytes.find`/`re`, NEVER a Python byte loop** (measured on 80 MB):
- literal AOB → `bytes.find` (**24 ms**)
- masked AOB → `re` with wildcards as `.` under **`re.DOTALL`** (**47 ms**)
- pure-Python byte loop → **~2000 ms (~85× slower)** ❌

The masked-AOB→regex compiler (this should become the scanner core — it also speeds up our *existing*
`scan_pattern`):
```python
import re
def compile_aob(aob: str) -> re.Pattern:
    parts = [b"." if t in ("??","?") else re.escape(bytes([int(t,16)])) for t in aob.split()]
    return re.compile(b"".join(parts), re.DOTALL)   # DOTALL is mandatory (so '.' matches 0x0A)
# matches = [m.start() for m in compile_aob(pat).finditer(region_bytes)]
```

**1.3 numpy — only for aligned value/pointer scans** (`np.frombuffer(buf,dtype='<u8')` + `np.flatnonzero(a==target)`,
~4× faster). **numpy is ~10× SLOWER for AOB** (per-shift full-buffer allocs) — AOB stays pure `bytes.find`/`re`
regardless of numpy. Import-guard it:
```python
try: import numpy as _np; HAVE_NUMPY = True
except ImportError: _np = None; HAVE_NUMPY = False
```

**1.4 60fps + the GIL** — a frame is 16.6 ms; a single 80 MB `bytes.find` (24 ms) blows 3 frames AND holds
the GIL the whole C call. So: run heavy read-only scans on the **off-thread socket thread** (paused-safe,
already built), **chunked into ~4–8 MB slices** so the GIL gets release points between calls. Reserve the
**Deferred-per-frame** path for work that touches *live* game state (entity-pool deref, hooks, spawns).
Bound each on-thread chunk with `time.perf_counter()` (~2 ms budget) and resume next frame.

**1.5 Caching keyed on a build-id** — persist all **RVA-relative** artifacts (`.pdata` table, RTTI map,
resolved AOBs normalized to RVA, parser dumps) to JSON keyed on `game_build_id = hash(file_version +
module_size + header_chunk)`. Patch → id changes → auto-invalidate → rescan. Keep **ASLR-absolute
addresses session-only in RAM** (rebased each launch). Our existing `native_db.json` is the right shape —
just add a build-id key. [Confirmed reasoning + benchmarks]

---

## 2. Static code analysis (read-only, safe off-thread)

### 2.1 Disassembler — `disasm(addr, count)` [capstone / iced-x86]
Read bytes via ctypes → decode → return per-instruction: mnemonic, length, **memory-operand displacement
(= struct-offset discovery!)**, RIP-relative targets, and **call/branch targets**. This turns `find_xrefs`
results into readable, followable code. iced-x86 exposes `memory_displacement`/`memory_base`/
`near_branch_target`/`flow_control` as structured fields; capstone via `insn.operands` + `X86_OP_MEM`.

### 2.2 Function boundaries via the in-memory PE `.pdata` — `func_bounds(addr)` [pure ctypes]
**The elegant one.** GTA5.exe's exception directory (`IMAGE_DIRECTORY_ENTRY_EXCEPTION`, index 3) is an
array of `RUNTIME_FUNCTION {BeginAddress, EndAddress, UnwindData}` (12 bytes, RVAs, **sorted**). Parse it
**once** from the loaded module (walk `GetModuleHandle(NULL)` → PE headers → DataDir[3]; all already
mapped, no file I/O), cache `(starts[], funcs[])`, then address→function is an O(log n) `bisect`. Skip
chained entries (`UnwindData & 1`). This is the authoritative function table x64 RE tools use; prologue
scanning (`48 89 5C 24`, `48 83 EC`, `55`, INT3 padding) is only the leaf-function fallback.

### 2.3 RTTI → C++ class name — `identify(obj_ptr)` [pure ctypes]
GTA5.exe ships MSVC RTTI for engine classes (CPed/CVehicle/etc — confirmed via Yimura/YimMenu/CE).
Walk: `vtable = [obj]`; `COL = [vtable-8]`; on **x64** COL fields are 4-byte **image-base-relative RVAs**
— recover base via `base = COL_addr - COL.pSelf` (must equal `GetModuleHandle(NULL)` → validity gate);
`TypeDescriptor = base + COL.pTypeDescriptor`; mangled name at `TypeDescriptor+0x10` = `.?AVCVehicle@@`
→ strip `.?AV`/`@@` → `CVehicle`. This makes `inspect` label *what an object actually is*. Build a
`vtable→classname` cache by sweeping `.rdata` once. (Use `msvcrt.__unDName`/dbghelp only for complex
names, off-thread — not thread-safe.)

### 2.4 vtable dump — `vtable(addr)` — walk qword slots while each points into `.text` (cross-check with
the `.pdata` map); cross-reference methods with `disasm`.

---

## 3. Dynamic analysis (the Cheat-Engine power tools)

### 3.1 "What accesses this address" — `watch_access(addr, rw, len)` [HW debug registers + VEH, pure ctypes]
The single biggest RE feature. Set a **hardware data breakpoint** in DR0–DR3 (exactly 4) via
`Get/SetThreadContext` on **every** game thread (enumerate via `CreateToolhelp32Snapshot`/`Thread32Next`,
filter `th32OwnerProcessID==GetCurrentProcessId()`, suspend→set→resume), and catch the `#DB`
(`EXCEPTION_SINGLE_STEP 0x80000004`) with a **Vectored Exception Handler** (`AddVectoredExceptionHandler(1,
cb)`). The handler's `CONTEXT.Rip` is the instruction that touched your value — *that's the answer*.

Critical specifics (all confirmed):
- Use the **x64 CONTEXT** (`_pack_=16`, `DWORD64` debug regs, `Rip`, `M128A` XMM area) — copy WinAppDbg's
  `context_amd64.py` verbatim; a wrong offset = garbage Rip. The common `icecr4ck` CONTEXT is **32-bit** —
  do not use it.
- **DR7**: local-enable bit `1<<(2*n)`; R/W at `16+4*n` (`11`=read|write, `01`=write-only); LEN at `18+4*n`
  (`00`=1, `01`=2, `11`=4, `10`=8 — note the ordering; address must be LEN-aligned).
- Read **`Dr6` low 4 bits** to demux which slot fired; `ContextFlags=CONTEXT_DEBUG_REGISTERS(0x100010)`.
- **VEH safety (the #1 crash risk):** keep the `WINFUNCTYPE` callback in a module-level global (ctypes
  won't keep it alive → dangling = crash). Handler must be **O(1) and lock-free**: copy `Rip/Dr6` + the
  arg regs (`Rcx/Rdx/R8/R9` + `XMM0-3`) into a preallocated ring buffer and return — **no allocation, no
  locks, no natives, no disk** (GIL-deadlock risk). Drain the ring + symbolize + WAL on the background
  thread. Filter hard: `if code not in (0x80000004,0x80000001): return EXCEPTION_CONTINUE_SEARCH(0)`.
- **Auto-disarm after N hits** (re-zero DR7 enable on all threads) so a hot value doesn't freeze the game.
- **PAGE_GUARD + VEH** is the fallback for >4 watches or whole-region coverage (page-granular, noisier,
  needs trap-flag single-step re-arm).

### 3.2 Function tracing — `trace_func(addr)`
For **≤4 functions**: a **HW exec breakpoint** (DR with R/W=`00`, no code patching) — read args from the
VEH `CONTEXT` (`Rcx/Rdx/R8/R9` + stack `[Rsp+0x28]…` + `XMM0-3`), return in `Rax`/`XMM0`. Safest (no
memory writes). For **many** functions: `cyminhook` (a MinHook wheel — handles trampolines/RIP fixups) or
a pure-ctypes inline hook (`VirtualProtect`→14-byte abs `jmp [rip+0]`→relocate stolen whole-instructions
into a `VirtualAlloc(PAGE_EXECUTE_READWRITE)` trampoline; needs the §2.1 length info for stolen bytes).
**Avoid Frida.**

---

## 4. The knowledge layer — stop rediscovering [mostly static import]

### 4.1 parStructure field maps — the Rosetta Stone [BEST low-effort win]
Import **alexguirre/rage-parser-dumps** (MIT) **JSON tree** for your exact Legacy build (it carries
`field → {name, byte offset, size, type, name-hash}` for every parser/`.meta` class — CHandlingData,
CVehicleModelInfo, weapons, peds, hundreds more). **Use the JSON, not the HTML** (HTML omits offsets).
Flatten to `findings["par"][structHash][offset] = {name,type,size}` → `inspect` becomes an O(1) labeled
decode. Seed the JOAAT dictionary from the struct+field name hashes for free. Build-specific → pull the
build matching your `GTA5.exe`. (Live `parManager`/`parStructure` walk exists as a self-heal fallback for
unpublished builds.)

### 4.2 Runtime-struct offsets — the par dumps DON'T cover CPed/CVehicle/world-ptr/pools (those aren't
parser-reflected). Fill the gap with a small hand-curated pointer table — **reimplement** YimMenu/Menyoo
*patterns* (don't copy GPL/encumbered code), key findings by `(class, offset)` so labels are build-portable.

### 4.3 Script globals — `read_global(i)`/`write_global(i,v)` — use ScriptHookV's exported `getGlobalPtr`
via ctypes (mangled `?getGlobalPtr@@YAPEA_KH@Z`), which does the block math (`block=i>>18`,
`slot=i&0x3FFFF`, 8-byte slots). Deref the returned pointer to read/write. A whole gameplay-state layer.

---

## 5. Reach — operate on the whole world

### 5.1 Pointer scanning — `find_pointers_to(target)` / `read_chain(base,[offsets])` / multi-level maps
Forward scan = aligned value-scan for the 8-byte target (`memoryview.cast('Q')`, or numpy for the heavy
full-memory map). Multi-level = CE's recursive reverse-scan (`for each pointer into [target-maxOff,target]:
recurse`, maxLevel ~3–4, maxOffset 0x800). **Reliability trick (the key):** capture **two pointermaps from
two game launches and intersect** — kills the >99.99% coincidental paths, leaving a `module+off→+off→target`
chain that survives restart. `read_chain` must **validate every hop** (in-process a bad deref hard-crashes
the game — unlike external RPM): `looks_like_usermode_ptr` gate or resolve against cached snapshots.

### 5.2 Entity pools — `enumerate_entities(type)` [Menyoo-confirmed structs/AOBs]
The pools give you ALL peds/vehicles/objects, not just the player. `VehiclePool` (32-bit bitmap validity)
and `GenericPool` (per-slot status byte, top bit `0x80` = valid). Iterate `0..size`, test validity, get the
entity pointer. AOBs (Legacy, version-specific — re-derive per build):
- Entity: `4C 8B 0D ?? ?? ?? ?? 44 8B C1 49 8B 41 08`
- Vehicle: `48 8B 05 ?? ?? ?? ?? F3 0F 59 F6 48 8B 08`
- Ped: `48 8B 05 ?? ?? ?? ?? 41 0F BF C8 0F BF 40 10`
- Object: `48 8B 05 ?? ?? ?? ?? 8B 78 10 85 FF`
Resolve rip-relative: `*(int*)(addr+3) + addr + 7`.
**Pointer↔handle bridge** (so Claude can move between a pool object and a native-callable handle): wrap
Menyoo's `_addEntityToPoolFunc` (ptr→handle, at `pattern-0x68`) and `_entityAddressFunc` (handle→ptr) via
`call_function`. Caveat: the fwScriptGuid pool caps handles (~700 stock / 3072 w/ SHV) — convert lazily.

### 5.3 Nearby entities (no pool walk) — call `GET_PED_NEARBY_PEDS`/`_VEHICLES` via `call_native` with a
ctypes buffer: `buf=(c_int*arrSize)(); buf[0]=maxN; pass addressof(buf); read handles at buf[i*2+2]`.
(This is the "allocate + pass pointer arg + read back" pattern our `call_native` needs to support.)

---

## 6. Recommended build order

1. **Knowledge layer first (§4.1)** — import the par-dump JSON. Biggest force-multiplier per hour; makes
   `inspect` label real field names immediately. Pure data, zero risk.
2. **Static analysis (§2)** — `.pdata` function table + disassembler + RTTI `identify`. All read-only,
   off-thread-safe; together they let Claude *read and label* the binary. (Verify the iced-x86 cp312 wheel;
   else capstone.) Also retrofit the masked-AOB→regex into the existing scanner (free speedup).
3. **`watch_access` (§3.1)** — the HW-breakpoint "what accesses X." The standout dynamic capability; pure
   ctypes; needs careful VEH plumbing + in-game testing.
4. **Pointer-chain + entity pools (§5)** — reach into dynamic objects and the whole world.
5. **Script globals (§4.3)** + **function tracing (§3.2)** as needed.

Most of §2/§4 are read-only and slot onto the paused-safe off-thread path; §3 (debug registers, hooks) is
the riskier dynamic tier — the crash-safe infra (WAL, ring buffer, validate-before-deref, separate host)
is what makes it survivable. Everything caches against a build-id so a game patch auto-invalidates.

---

## Sources (per area)
- **Disasm/PE/RTTI:** iced-x86 & capstone PyPI; auscitte `.pdata`/RUNTIME_FUNCTION; rop.la + lukaszlipski
  MSVC-RTTI-x64; retdec `rtti_msvc.h`; Yimura/GTAV-Classes; YimMenu; Cheat Engine RTTI.
- **HW breakpoints/VEH/hooks:** ling.re hardware-breakpoints; icecr4ck/debugger; WinAppDbg
  `context_amd64.py`; Intel SDM Vol 3B §17.2 + Wikipedia x86 debug register; codereversing VEH;
  PythonForWindows LocalDebugger/HXBreakpoint; kylehalladay x64 hooking; TsudaKageyu/minhook + cyminhook.
- **Knowledge layer:** alexguirre/rage-parser-dumps (MIT) + JsonTreeFormatter; citizenfx/fivem RageParser.h;
  DurtyFree/gta-v-data-dumps; SHVDN NativeMemory.cs (`getGlobalPtr`); Gogsi/GTAV-Research globals.
- **Pointer scan/pools:** cheatengine.org pointer-scan + Dark Byte pointermap; MAFINS/MenyooSP GTAmemory;
  Abel-Liu ScriptHook Pools; FiveM GetGamePool; GTAForums GET_PED_NEARBY_PEDS.
- **Perf:** GuidedHacking pattern-scanning; pymem pattern.py; numpy.frombuffer; capstone/Frida footprints;
  pythonspeed GIL. (Scanning benchmarks measured locally: bytes.find 24ms vs python-loop ~2000ms; numpy
  aligned-value 8.5ms vs bytes.find 32ms; numpy AOB 352ms — loses.)
