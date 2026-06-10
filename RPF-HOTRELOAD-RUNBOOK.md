# Runtime DLC / data-file hot-reload — in-game runbook

Goal: let Claude **mount edited add-on content and reload it without restarting GTA** — cars, peds,
handling, weapons, models, maps. (Audio does **not** hot-reload; see `RPF-REFERENCE.md`.)

Everything here is grounded in **real, source-confirmed code** — primarily FiveM (`citizenfx/fivem`),
the only open codebase that actually does this. Signatures, AOB patterns, and call orders below are
quoted from that source. **AOBs drift across game builds — always re-scan and verify on your binary.**

> Tools used (all exposed over MCP, so the headless host can drive them autonomously):
> `find_string` · `find_xrefs` · `scan_pattern` · `resolve_rip_relative` · `inspect` · `call_function`
> · `reload_content_changeset` · `note_finding`/`get_findings` (persist discovered addresses across
> reloads) · `get_environment` (edition/module base+size).
>
> **Record everything you discover with `note_finding`** — addresses found this session survive F9
> reloads and become the cached inputs to `call_function`. This is the "record values like last night"
> loop, pointed at engine functions.

---

## The two reload paths (pick by what changed)

| You changed… | Use | Confirmed source |
|---|---|---|
| A few **handling/stat values** on a spawned vehicle | direct memory write (already works) | — |
| An edited **handling.meta** (many fields / new entry) | **`parManager::LoadFileIntoStructure`** re-parse | FiveM `handling-loader-five` |
| Edited **vehicles/carcols/peds/weapons .meta** | `CDataFileMgr` mounter unmount→remount, or `parManager` re-parse | FiveM `LoadStreamingFile.cpp` |
| A whole **new/edited dlc.rpf** of files | **`fiPackfile` open+mount** (+ device overlay) | FiveM `ReMountDefaultDevice` |
| Toggle an already-registered **map/IPL changeset** | `reload_content_changeset(group)` (verified natives, safe) | alloc8or nativedb |

**Recommended order to build confidence:** Path A (changeset natives, zero RE) → Path C (parManager
re-parse, one engine call) → Path B (full rpf mount). Validate `call_function` on a harmless function
FIRST (see §0).

---

## §0. Smoke-test `call_function` before anything risky

A wrong signature hard-crashes the game, so prove the primitive on something safe first.

1. `get_environment` → note GTA5.exe base + size (confirms module bounds the guard uses).
2. Find a trivial engine function with a known signature (e.g. via a string anchor you can verify), or
   reuse a function you already resolved. Call it with `call_function` and check the return matches a
   known-good value. Only then proceed.
3. If the host reports a crash afterward, read `get_crash_logs` — the `call_function` WAL entry shows
   the exact address/args of the last call.

---

## §A. Safest: re-apply a content changeset (verified natives, no RE)

Use `reload_content_changeset(group)`. It calls `REVERT_CONTENT_CHANGESET_GROUP_FOR_ALL`
(`0x3C1978285B036B25`) then `EXECUTE_CONTENT_CHANGESET_GROUP_FOR_ALL` (`0x6BEDF5769AC2DC07`) on
`joaat(group)` — the same pair R\* uses for map swaps (`REVERT("GROUP_MAP_SP"); EXECUTE("GROUP_MAP")`).

- Works for: re-applying/toggling **already-registered** changesets (map/IPL groups, a pack's
  `<NAME>_AUTOGEN`).
- Does **not**: re-read edited bytes on disk, or mount a new rpf. If the change doesn't show, you need
  Path B/C.

---

## §C. Surgical: re-parse an edited .meta with `parManager` (the proven handling path)

This is FiveM's `handling-loader-five` approach — it **re-reads the file from disk** and parses it into
the live struct array. Most valuable path for "I edited handling.meta, make it live."

**Engine functions (FiveM-confirmed signatures + AOBs — re-scan on your build):**
```
rage::parManager::LoadFileIntoStructure   ("ParseFileIntoStructure")
   bool (Parser* parMgr, const char* path, const char* ext, void* dtd, void* outStruct, bool, void*)
   AOB (call site): E8 ? ? ? ? 0F B7 44 24 58 4C 8D 2D     (resolve the E8 rel32 to the function)
   called as: (*g_parser)->ParseFileIntoStructure("…/handling.meta", "meta", HandlingData_DTD,
                                                   &handlingDataList, true, nullptr)

CHandlingData::ProcessEntry   (per-type post-parse finalize)
   void (CHandlingData* entry)
   AOB: 48 8B F9 8B 89 ? 01 00 00 0F   (function entry at match -0x0A)

ModifyHandlingForVehicles   — re-points already-spawned CVehicleModelInfo at the new CHandlingData
```

**Discovery steps (headless):**
1. `scan_pattern("E8 ?? ?? ?? ?? 0F B7 44 24 58 4C 8D 2D")` → the call site. `resolve_rip_relative` on
   the `E8` (offset_position=1, instruction_size=5) → `parManager::LoadFileIntoStructure`.
   `note_finding("parManager_LoadFileIntoStructure", value=<addr>, verified=false)`.
2. `scan_pattern("48 8B F9 8B 89 ?? 01 00 00 0F")` → `ProcessEntry` is at that match **−0x0A**.
   `note_finding("CHandlingData_ProcessEntry", ...)`.
3. Find the `g_parser` instance pointer and the handling DTD/array — `find_string("handling")` /
   inspect around the parse call site; `inspect` to confirm the structures.

**Apply steps (once addresses are noted + verified):**
4. `call_function(parManager_LoadFileIntoStructure, arg_types=["ptr","string","string","ptr","ptr","bool","ptr"],
   args=[g_parser, "<path>/handling.meta", "meta", dtd_ptr, out_struct_ptr, true, 0], return_type="bool")`.
5. For each new/changed entry: `call_function(CHandlingData_ProcessEntry, ["ptr"], [entry_ptr], "void")`.
6. `call_function(ModifyHandlingForVehicles, ...)` (or respawn the test vehicle) so spawned cars adopt it.
7. Verify with `get_vehicle_info` / `inspect` on the live CHandlingData.

> The same `parManager` call backs **every** `.meta` type (vehicles/carcols/peds/weapons) — only the
> DTD + target store differ. Handling is the proven example; the others follow the same shape.

**Alternative for vehicles/carcols/peds/weapons:** the engine's per-type `CDataFileMgr` mounter
(`CDataFileMountInterface::LoadDataFile`/`UnloadDataFile`). Unmount→remount re-reads the file. FiveM
keeps `g_dataFileMounters` indexed by data-file type id — locate that array, find the mounter for the
type, call `UnloadDataFile(entry)` then `LoadDataFile(entry)`.

---

## §B. Full: mount a new/edited dlc.rpf at runtime (FiveM `ReMountDefaultDevice`)

For dropping in a whole edited rpf of files. **Confirmed call sequence + signatures from FiveM.**

**Engine functions (FiveM AOBs — entry = match + the listed delta; re-scan per build):**
```
fiPackfile::fiPackfile()  (ctor)
   AOB: 44 89 41 28 4C 89 41 38 4C 89 41 50 48 8D            entry at  -0x1E
   vtable ptr AOB: 44 89 41 28 4C 89 41 38 4C 89 41 50 48 8D 05   (RIP-rel @ +15)

fiPackfile::OpenPackfile(const char* archive, bool bTrue, int type, intptr_t veryFalse) -> bool
   AOB: 48 8D 68 98 48 81 EC 40 01 00 00 41 8B F9            entry at  -0x18
   FiveM calls it as: OpenPackfile(path, /*bTrue*/true, /*type*/0, /*veryFalse*/0)

fiPackfile::Mount(const char* mountPoint) -> void
   AOB: 84 C0 74 1D 48 85 DB 74 0F 48                        entry at  -0x1E

fiDeviceRelative::SetPath(const char* relativeTo, bool allowRoot, fiDevice* baseDevice) -> void
   AOB: 49 8B F9 48 8B D9 4C 8B CA 48                        entry at  -0x17
fiDeviceRelative::Mount(const char* mountPoint, bool) -> void
   AOB: 44 8A 81 14 01 00 00 48 8B DA 48 8B F9 48 8B D1      entry at  -0x0D

fiDevice::GetDevice(const char* path, bool allowRoot) -> fiDevice*     (cdecl)
   AOB: 41 B8 07 00 00 00 48 8B F1 E8                        entry at  -0x1F
fiDevice::MountGlobal(const char* mountPoint, fiDevice* dev, bool allowRoot) -> bool   (cdecl)
   AOB: 41 8A F0 48 8B F9 E8 ? ? ? ? 33 DB 85 C0             entry at  -0x28
fiDevice::Unmount(const char* rootPath) -> void   (cdecl)
   AOB: E8 ? ? ? ? 85 C0 75 23 48 83                         entry at  -0x22
```

**The confirmed minimal sequence (FiveM `ReMountDefaultDevice`):**
```cpp
rage::fiPackfile* pf = new rage::fiPackfile();          // 1. allocate + run ctor (writes vtable)
pf->OpenPackfile("<path>\\dlc.rpf", true, 0, 0);        // 2. open/parse RPF7 header  (must return true)
pf->Mount("dlc_<name>:/");                              // 3. install in the device stack
// optional overlay onto another root:
//   fiDeviceRelative* rel = new fiDeviceRelative(); rel->SetPath("dlc_<name>:/", true, nullptr);
//   rel->Mount("dlcpacks:/<name>/");  OR  fiDevice::MountGlobal("dlcpacks:/<name>/", dev, true);
```

**Translating to the bridge (no `new` operator — we allocate + call the ctor):**
1. Allocate an object buffer (≈0x650 bytes) — use the game allocator if found, else a committed
   VirtualAlloc region you keep alive. `inspect` to confirm.
2. `call_function(fiPackfile_ctor, ["ptr"], [obj], "void")` — runs the engine ctor (writes the vtable).
   (Or write the vtable pointer yourself from the resolved vtable AOB, then call the ctor.)
3. `call_function(fiPackfile_OpenPackfile, ["ptr","string","bool","int","int64"],
   [obj, "<abs path>\\dlc.rpf", true, 0, 0], "bool")` → **must be true**.
4. `call_function(fiPackfile_Mount, ["ptr","string"], [obj, "dlc_<name>:/"], "void")`.
5. Verify: `call_function(fiDevice_GetDevice, ["string","bool"], ["dlc_<name>:/", true], "ptr")` →
   nonzero device pointer means the mount took. `inspect` it.

**Making the metas LIVE after mounting (the open gap):** opening+mounting makes the *files* readable
but does **not** by itself tell the extra-content system to ingest the metas. FiveM does this by hooking
`CMountableContent::MountContent` (it piggybacks on the engine, rather than calling
`ExecuteContentChangeset` from scratch — that call is **not** in any public repo). Two options:
  - **Easier:** mount the files via Path B, then use **Path C** (`parManager` re-parse) to ingest the
    specific metas you care about. This sidesteps the unsolved changeset-execution step entirely.
  - **Full RE (frontier):** get the manager instance and drive its content array.

**Extra-content manager pointer (FiveM-confirmed acquisition):**
```
g_extraContentManagerLocation = get_address(AOB "79 91 C8 BC E8 ? ? ? ? 48 8D"  @ -0x16)  // RIP-rel global
manager instance = *g_extraContentManagerLocation
content array    @ manager + contentOffset (AOB "48 83 C1 ? 89 82" @ +3)
stride           = uint32 @ AOB "48 69 C0 ? ? ? ? 48 03 43 ? F6 80" @ +3
g_updateContentArray = the engine fn at call AOB "E8 ? ? ? ? 44 8A C3 B2"   // refreshes the array
```

---

## Hard caveats (all source-confirmed)
- **Use OPEN (unencrypted) rpfs** for side-loading — the runtime open path expects `OPEN`/`CFXP`, not
  NG. (We already build OPEN; we can't write NG headlessly anyway — `RPF-REFERENCE.md` §7.5.)
- **Keep the on-disk filename stable** — RPF7 decoding is keyed on the archive filename; renaming
  produces garbage. Build the edited rpf as the same name, in `%TEMP%`, then copy in (Dropbox lock —
  `DISCOVERIES.md` §3).
- **Unique mountpoints** — never reuse a live mountpoint without `Unmount` first (undefined behavior).
- **Device objects leak by design** — FiveM `new`s them and never frees. Repeated remount grows memory;
  for a tight edit loop prefer Path C (re-parse) over re-mounting a fresh packfile each time.
- **AOBs are build-specific** — re-scan every game update; `note_finding(..., verified=true)` only after
  you confirm the resolved address behaves.
- **Audio is out** — `AUDIO_*` data files ingest once at boot; no runtime path. Claude FM already routes
  around this (NAudio), so it's unaffected.

---

## How this runs headless (the "like last night" loop)
The host (`gtav_host.py`, Claude Agent SDK) already gives Claude autonomous MCP access + persistent
session + the findings store. The hot-reload loop is just that loop pointed at engine functions:
1. You edit a `.meta`/rpf on the modding side (CodeWalker.Core → OPEN rpf in `%TEMP%` → copy in).
2. F10: "reload the handling for the adder" (or Claude does it unprompted on a watch).
3. Claude: `get_findings` (cached addresses) → `call_function` the parManager/mount sequence →
   verifies via `inspect`/`get_vehicle_info` → reports now-playing-style status.
4. New addresses discovered along the way get `note_finding`'d, so the next reload is instant.

Sources: FiveM `fiDevice.h`, `fiDeviceClasses.cpp`, `fiDevice.cpp`, `UpdateRpfOverrideMount.cpp`,
`CitizenMount.cpp`, `HookInitialMount.cpp`, `HookLoadingScreens.cpp` (manager ptr), `VFSRagePackfile7.cpp`
(OPEN check), `handling-loader-five/HandlingDataManager.cpp` (parManager re-parse),
`gta-streaming-five/LoadStreamingFile.cpp` (CDataFileMgr mounters); alloc8or nativedb (changeset natives).
