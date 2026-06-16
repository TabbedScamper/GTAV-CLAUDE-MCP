# 01 — Native invocation, marshalling & memory (the bridge's lowest layer)

Mined from **ScriptHookVDotNet** (`source/core/`, the reference native-invoker + memory map) and
**gta5view** (SP save format). This is the highest-value file for the bridge itself.

---

### Marshal native args as a flat ulong[] stack — int/bool zero-extend, float BIT-CAST
**Category:** native-marshalling
**Problem:** Convert int/bool/float/string args into the 64-bit slots ScriptHookV's `nativePush64` expects.
**Method:** Every arg → one `ulong`. bool→`1/0`; int→cast through `uint` first (so a negative handle doesn't sign-fill the upper 32 bits); float→**reinterpret the bytes, do NOT numeric-convert**: `*(uint*)&f`; IntPtr→`(ulong)ptr`; string→pin UTF-8, push pointer. Call cycle: `nativeInit(hash)` → loop `nativePush64(arg)` → `nativeCall()` returns `ulong*` to the result. ScriptHookV mangled exports: `?nativeInit@@YAX_K@Z`, `?nativePush64@@YAX_K@Z`, `?nativeCall@@YAPEA_KXZ`.
**Gotcha:** Float MUST be bit-cast — `(ulong)3.5f` truncates to 3. Int cast through `uint` before widening or negative handles corrupt the slot.
**Source:** shvdn-api/source/core/NativeFunc.cs

### Read a Vector3 return as 8-byte-strided floats (the "FixVectors" issue)
**Category:** native-marshalling
**Problem:** Vector3-returning natives (GET_ENTITY_COORDS/ROTATION/VELOCITY) write each component on its own 16-byte register slot; reading 3 contiguous floats gives garbage Y/Z.
**Method:** Result buffer is `ulong*`. Read float at **+0x00 (X), +0x08 (Y), +0x10 (Z)** — stride 8, total 24 bytes. (SHVDN overlays `[StructLayout(Explicit, Size=0x18)]` NativeVector3.)
**Gotcha:** Stride is 8, not 4. Only the low dword of each slot is valid; padding between is junk. **Our bridge already requests `return_type=vector3` for these — this card explains why.**
**Source:** shvdn-api/source/scripting_v2/GTA.Native/Native.cs

### Output (pointer) args: pre-allocate a 24-byte buffer, pass its address, read back
**Category:** native-marshalling
**Problem:** Natives taking `int*`/`Vector3*` out-params need a stable slot the engine writes into.
**Method:** Allocate **24 bytes** (widest case = Vector3 at stride 8), pass its address as the arg, then reinterpret after the call (`*(int*)buf`, or the strided Vector3 overlay). Free when done.
**Gotcha:** 24 (not 4/12) because Vector3 out uses the 0x00/0x08/0x10 strided layout. Own/pin the buffer for the whole call.
**Source:** shvdn-api/source/scripting_v2/GTA.Native/Native.cs

### Convert an entity handle → game address via the script-GUID resolver
**Category:** memory
**Problem:** Turn a script handle (what natives return) into the raw `CEntity*` for direct offset reads.
**Method:** Find the resolver once: pattern `85 ED 74 0F 8B CD E8 ?? ?? ?? ?? 48 8B F8 48 85 C0 74 2E`, resolve the RIP `call`: `func = *(int*)(addr+7) + addr + 11`. `GetEntityAddress(handle) = func(handle)`. Works for ped/vehicle/object/pickup.
**Gotcha:** Don't hand-roll pool math for entities — this engine fn does the fwScriptGuid lookup correctly for all types. `+7/+11` is the standard `E8 rel32` resolve (call sits 4 bytes into the match).
**Source:** shvdn-api/source/core/NativeMemory.cs (GetEntityAddress)

### Resolve pool pointers from `mov reg,[rip+disp]` — the +3/+7 idiom
**Category:** pattern-scan
**Problem:** Get live pool pointers (ped/object/vehicle/pickup/checkpoint/blip).
**Method:** Each pool loads via `48 8B 05 xx xx xx xx` (`mov rax,[rip+disp32]`), disp masked `????`. Resolve: `poolPtr = *(int*)(addr+3) + addr + 7`. Patterns: ped `48 8B 05 ?? ?? ?? ?? 41 0F BF C8 0F BF 40 10`; object `48 8B 05 ?? ?? ?? ?? 8B 78 10 85 FF`; vehicle `48 8B 05 ?? ?? ?? ?? F3 0F 59 F6 48 8B 08`; pickup `4C 8B 05 ?? ?? ?? ?? 40 8A F2 8B E9`.
**Gotcha:** `+3` skips the 3-byte opcode, `+7` is next-instruction. Result is a pointer-to-pointer — deref once more to reach the `fwBasePool`. (Corroborates STUDIES.md §2 / fivem-offset-dumper.)
**Source:** shvdn-api/source/core/NativeMemory.cs

### Three RIP-resolve idioms — pick the delta by instruction type
**Category:** pattern-scan
**Problem:** Not every reference is +3/+7; `lea`, `call`, mid-pattern operands, and embedded offsets differ.
**Method:** (1) `mov/lea r,[rip+d]`, 3-byte opcode → `*(int*)(a+3)+a+7`. (2) `call rel32` (`E8`) → `*(int*)(a+1)+a+5`; if the call is N bytes into the match → `*(int*)(a+N)+a+N+4`. (3) Operand offset into the match → index to it, e.g. `*(int*)(a+0x25)+a+0x29`. **Plain struct field offsets** are read as a literal dword/byte at a fixed delta with **no RIP add**: `vehicleClassOffset = *(uint*)(addr+0x10)`, `ArmorOffset = *(int*)(addr+11)`.
**Gotcha:** Rule = *displacement + address-of-byte-after-the-full-instruction*. Mis-counting instruction length shifts every read. Embedded literal offsets (field offsets, vfunc indices) are absolute — do NOT add the address to those.
**Source:** shvdn-api/source/core/NativeMemory.cs (static ctor)

### fwBasePool slot math — index = handle>>8, counter = handle & 0xFF
**Category:** memory
**Problem:** Convert handle↔slot address without the engine (for building/interior/checkpoint pools with no GUID resolver).
**Method:** `fwBasePool`: PoolAddress@0x00, Flags(byte[])@0x08, Capacity@0x10, SlotSize@0x14, FirstEmpty@0x18, used&flags@0x20 (`& 0x3FFFFFFF`). Handle = `(index<<8) | counter`. `GetAddress(i) = Mask(i) & (PoolAddress + i*SlotSize)`. Validate: `flags[index] & 0x7F == handle & 0xFF`. Reverse: `i=(addr-PoolAddress)/SlotSize; handle=(i<<8)|(flags[i]&0x7F)`.
**Gotcha:** `Mask(i)` is branchless validity: high bit `0x80` = occupied; invalid slot → address 0. Counter is low 7 bits only. "Full" when `Capacity - Used <= 256` (ScriptHookV bumps capacity ≥3072).
**Source:** shvdn-api/source/core/Structs/FwBasePool.cs

### Enumerate entities by minting GUIDs from the fwScriptGuid pool
**Category:** memory
**Problem:** Get handles for all live peds/vehicles/objects, mirroring natives.
**Method:** Deref the fwScriptGuid pool; walk target pool slots `0..Capacity`; skip `!IsValid(i)`; `GetAddress(i)`; distance/model-filter; mint with engine `createGuid(address)` (pattern `48 F7 F9 49 8B 48 08 48 63 D0 C1 E0 08 0F B6 1C 11 03 D8`, then `func = addr - 0x68`). Vehicles use a `RageSysMemPoolAllocator`, not a plain fwBasePool.
**Gotcha:** Check `fwScriptGuidPool->IsFull()` and **fail loud** before each mint — overflow silently loses entities. Fresh GUIDs each frame; don't reuse across frames. (This is the canonical version of our `enumerate_entities`.)
**Source:** shvdn-api/source/core/NativeMemory.cs (FwScriptGuidPoolTask)

### Walk the model-hash table to classify a model hash
**Category:** memory
**Problem:** Given a model hash, find `CModelInfo*` + read type (ped/vehicle/weapon) / vehicle class without a native.
**Method:** Off one pattern region (`66 81 F9 ?? ?? 74 10 4D 85 C0`, back up `*(int*)(a-0x21)+a-0x1D`): get `modelHashTable`, `modelHashEntries`, `modelNum1..4`. Lookup: `bucket = hash % entries`; walk `HashNode*` list (Next/Hash/Data); validity `(*(int*)(modelNum2+4*(data>>5)) & (1<<(data&0x1F)))` and `data<modelNum1`; `modelInfo = *(ulong*)(modelNum4 + modelNum3*data)`. Type = `*(byte*)(modelInfo+157) & 0x1F`; vehicle class = `*(byte*)(modelInfo+vehicleClassOffset) & 0x1F`.
**Gotcha:** It's `base + stride*index` (modelNum4 + modelNum3*data), not a flat pointer array. **Skip known stub hashes that crash on load** (astron2 0xA71D0D4F, cyclone2 0x170341C2, etc.) — same fail-closed spirit as our native allowlist.
**Source:** shvdn-api/source/core/NativeMemory.cs (GetModelInfo)

### Pattern-scan best practice — BMH, cache once, fail loud
**Category:** pattern-scan
**Problem:** Scan the module efficiently and detect breakage on game updates.
**Method:** Range = `MainModule.BaseAddress` for `ModuleMemorySize`. Boyer–Moore–Horspool with a 256-entry skip table; `'?'` bytes stored as `-1` (always match). Run ALL resolves once in a static ctor, cache results. Branch patterns on build version where layouts changed (e.g. fwScriptGuid pool differs before/after b3788).
**Gotcha:** On no-match, return null AND **log the pattern** — a silently-null pointer becomes a crash later; a null here is your first signal a pattern needs re-derivation after a patch. Naive scan is the fallback for very short patterns.
**Source:** shvdn-api/source/core/MemScanner.cs

### Read/patch script globals via ScriptHookV getGlobalPtr
**Category:** memory
**Problem:** Read/write a script global (mission/economy/state) by ID.
**Method:** Import `?getGlobalPtr@@YAPEA_KH@Z` → `IntPtr getGlobalPtr(int index)` returns a pointer to the global's 64-bit slot (or Zero). Deref to read; write through to patch. (SHVDN uses this to flip the "enable all DLC vehicles" global.)
**Gotcha:** Global IDs differ between builds — never hardcode across versions; gate by game version. Globals are 8-byte slots even for int/float. (This is exactly our `read_global`/`write_global` mechanism.)
**Source:** shvdn-api/source/core/NativeMemory.cs (GetGlobalPtr)

### Parse the SP savegame header (260-byte block, magic 00 00 00 01)
**Category:** save-format
**Problem:** Validate an SP save and extract its title.
**Method:** Read first **260** bytes; valid only if first 4 = `00 00 00 01`. Split the header on byte `0x01`; element [1] is the title payload. R* photo/meta (RagePhoto) family = 4-byte LE magic, fixed header, then `JPEG`/`JSON`/`TITL`/`DESC` 4-char markers each followed by a LE uint32 length + payload.
**Gotcha:** gta5view reads only the **header/title** — money/stats/vehicles live in the **encrypted save body it never touches**. So there's no clean public path to edit SP money via the save file → use the in-game stat/global path instead (STUDIES.md §5: `Game.Player.Money`). For metadata use the Snapmatic/RagePhoto offsets (`seek(offset+264)`, verify the 4-char marker).
**Source:** gta5view-tool/SavegameData.cpp, RagePhoto.cpp
