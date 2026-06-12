# GTAV-Claude-MCP — Capability Studies & Research

> Compiled 2026-06-12 by parallel research agents. Goal: catalog everything that would let the
> in-process Python/ctypes bridge (ScriptHookV + PyLoaderV) **dissect, read, modify, and drive any
> part of GTA V**, plus findings that directly help **ExtendedLSC**. Every claim has a source URL.
> Offsets drift between builds — treat hex values as anchors and **re-verify against the live build /
> pattern-scan** before trusting them (this is what every mature menu does).

---

## 0. Executive summary — what this unlocks & in what order

**The two-tier model of "do anything":**
1. **Natives (safe, ~6,700 commands)** — we already call these by name. The biggest *untapped* power is in categories we under-use: **PTFX particles, Scaleform game-UI movies, DRAW_MARKER/checkpoints, ANIMPOSTFX/timecycle screen FX, full TASK-based AI, animations & synchronized scenes, interior entity-sets, frontend/positional audio, IPL toggling, scripted cameras & render targets, custom text/textures.** All are pure native calls needing only the right `REQUEST_*` + asset-name pairs (documented below).
2. **Direct memory (everything natives don't expose)** — resolve root pointers by AOB pattern scan, then read/write structs: **entity pools** (enumerate every ped/vehicle/object in the world), **script globals** (money, story flags, ownership), **CHandlingData** (live performance tuning), CPed/CVehicle internals (armor, engine health, locks). SHVDN's `NativeMemory.cs` + YimMenuV2's `Pointers.cpp` are the canonical pattern maps.

**Highest-value additions to the bridge (priority order):**
1. **Entity-pool enumeration** → world scan / mass-modify any entity (peds, vehicles, objects we didn't spawn). Resolve `replay_interface`/pool pointers, or simplest: call `GET_GAME_POOL` native. → §2 Pools.
2. **Script globals read/write** → money, mission/property/ownership state. Resolve `ScriptGlobals`, math: `globals[(idx>>18)&0x3F] + (idx&0x3FFFF)`. → §2 Script Globals.
3. **Animation control** → `REQUEST_ANIM_DICT`→`TASK_PLAY_ANIM` + scenarios + synchronized scenes; seed names from alexguirre's list + rpemotes-reborn. → §3.
4. **PTFX + Scaleform + markers + screen FX** → huge visual capability, pure natives. → §4.
5. **Live handling tuning** (`CVehicle+0x918/0x960 → CHandlingData`) and the **wheel-offset/camber natives** (`SET_VEHICLE_WHEEL_X_OFFSET`/`Y_ROTATION`) — directly reusable in ExtendedLSC. → §5.
6. **`createTexture`/`drawTexture` ScriptHookV exports** → render arbitrary PNGs (we already do this via SHVDN CustomSprite in ExtendedLSC; the bridge can bind the C exports directly). → §6.
7. **CodeWalker.Core headless** for offline asset/RPF/XML work, decompiled `.ysc` scripts to discover globals. → §6.

**ExtendedLSC-specific wins (see §7 for the consolidated list):** `Game.Player.Money` already handles SP cash per-character; wheel fitment is fully doable via natives (no memory scan); live performance sliders map to specific `CHandlingData` float offsets; mod-kit/wheel-type enumeration order; VStancer technique via natives.

**Legal/stability note:** YimMenu/YimMenuV2/GTAV-Classes carry Take-Two DMCA risk and their patterns drift every update — use as *RE reference*, re-derive against the live build, and prefer the clearly-licensed SP-safe path (ScriptHookV/SHVDN/Menyoo) for anything shipped.

---

## 1. Open-Source Menus & Trainers (capability reference)

Catalogs open-source GTA V trainers/menus to mine for techniques and code. **YimMenu and GTAV-Classes were DMCA'd by Take-Two in early 2025** and are archived/binary-only, but techniques survive in forks, mirrors, and the still-live YimMenuV2. ScriptHookV-based projects (Menyoo, ENT, SHVDN) remain fully available and most directly applicable to an in-process loader.

### Menyoo PC (MenyooSP) — reference single-player kitchen-sink trainer
- **URL:** https://github.com/MAFINS/MenyooSP (archived Feb 2025; active fork: https://github.com/itsjustcurtis/MenyooSP). Single-player-safe re-uploads: https://github.com/MeyhoMeyho/Menyoo.SP-1
- **License:** GPL v3 · C++ · runs as `.asi` via ScriptHookV.
- **Capabilities:** vehicle spawn/mod, weapons, player/ped model & component swap, ped animations & speech, weather, time, teleport, the **Object Spooner** (full scene builder: spawn/attach/position/rotate objects, peds, vehicles; save/load maps as XML), scenarios, particle FX, cutscene playback, stat editing.
- **Source map (reusable):** `Solution/source/` → `Memory/`, `Menu/`, `Natives/`, `Scripting/`, `Submenus/`, `Util/`. One feature per file in `Submenus/` (`VehicleSpawner.cpp`, `VehicleModShop.cpp`, `PedAnimation.cpp`, `WeatherOptions.cpp`, `Spooner/`…).
- **Adopt:** the **Spooner XML schema** is the de-facto standard for serializing a spawned scene (model hash, position, quaternion, attachment parent, frozen flag) — mirroring it gives instant interop with thousands of community maps. Object name lists (16k+ props): https://www.gta5-mods.com/scripts/complete-object-list-for-menyoo-menu-cayo-perico-heist-update

### Enhanced Native Trainer (ENT) — documented "every subsystem" C++ trainer
- **URL:** https://github.com/gtav-ent/GTAV-EnhancedNativeTrainer (forks: `Ominus404/`, `sondaismith/`)
- **Capabilities:** skin/vehicle/weapon customizers, teleport catalogs, save/load vehicles & skins, **Prop Spawner ~3,815 categorized props**, bodyguards, neon, weapon damage modifier, airbrake, slow-mo.
- **Adopt:** `src/ent-enums.h` — large curated, categorized enums of vehicle/ped/weapon/prop hashes. A ready-made **catalog dataset** so Claude can spawn "a police car" by category.

### YimMenu (GTA Online / SP) — the memory-hooking reference
- **URL:** https://github.com/YimMenu/YimMenu (archived/binary; `docs/` Lua API public; forks `Mr-X-GTA/YimMenu`, `JynxMazes/YimMenu-1.61` retain history). Injected DLL, not ScriptHookV.
- **Architecture to adopt:** `src/hooks/` (vtable/detour hooks, e.g. `hooks/protections/received_event.cpp`), `services/` for stateful subsystems, a **fiber/thread pool** to queue work off the game thread (critical: never block the script thread), `g_local_player` resolved via pattern. Depends on GTAV-Classes for struct layouts.
- **Lua surface = capability ceiling checklist:** modules `Globals, Script, Stats, Tunables, Natives, Network, Transactions, Invoker, Entity, Ped, Vehicle, Vector3`. Anything they expose to Lua, our bridge could expose to Claude. Runtime ref: https://github.com/TupoyeMenu/YimLuaAPI ; docs https://percilator.gitbook.io/yimmenuv2-lua-docs

### YimMenuV2 (GTA5: Enhanced) — STILL LIVE; best in-process native-invoke patterns
- **URL:** https://github.com/YimMenu/YimMenuV2 (`enhanced` + `legacy` branches). Compiled fork: https://github.com/ni-binks/yimmenuv2-compiled
- **`PointerData` they bootstrap (template for our pointer layer):** `SwapChain`, `Hwnd`, `WndProc`, `ScriptGlobals (int64**)`, `NativeRegistrationTable (void*)`, `GetNativeHandler (fn)`, `FixVectors (fn)`.
- **Concrete patterns (Pointers.cpp):**
  - NativeRegistrationTable + GetNativeHandler: `48 8D 0D ? ? ? ? 48 8B 14 FA E8 ? ? ? ? 48 85 C0 75 0A` — call ANY native in-process **without ScriptHookV**.
  - FixVectors (RAGE returns Vector3 components on 16-byte boundaries; MUST repack): `83 79 18 00 48 8B D1 74 4A FF 4A 18 ...`
  - SwapChain: `48 8B 0D ? ? ? ? 48 8B 01 44 8D 43 01 33 D2 FF 50 40 8B C8`
- **Adopt:** the native-invoker over `NativeRegistrationTable` is the single most valuable "do anything" pattern; always run `FixVectors` on vector-returning natives.

### GTAV-Classes (Yimura) — struct/offset reference
- **URL:** https://github.com/Yimura/GTAV-Classes (archived); full mirror with `.hpp` retained: https://github.com/Mr-X-GTA/GTAV-Classes-1
- **Contents:** `CPed, CVehicle, CPlayerInfo, CPedWeaponManager, CWheel, CHandlingData, CVehicleDrawHandler, CPedFactory`, etc. Use as a starting offset map; version-verify before trusting.

### fivem-offset-dumper — cleanest standalone pattern catalog
- **URL:** https://github.com/yfz/fivem-offset-dumper — user-mode AOB scanner with RIP resolution + auto-deref flags. World/pool entry patterns:
  - `world → CWorld*`: `48 8B 05 ? ? ? ? 33 D2 48 8B 40 08 8A CA 48 85 C0 74 16 48 8B` (deref)
  - `replay_interface → CReplayInterface*` (holds ped/vehicle/object/pickup **pools**): `48 8D 0D ? ? ? ? 48 ? ? E8 ? ? ? ? 48 8D 0D ? ? ? ? 8A D8 ...`
  - `camera → CCamera*`: `4C 8B 35 ? ? ? ? 33 FF 32 DB` (deref); `blip_list → CBlipList*`: `4C 8D 05 ? ? ? ? 0F B7 C1`
  - RIP resolve for `48 8B 05` style: `*(int32*)(addr+3) + addr+7`.

### ScriptHookVDotNet (SHVDN) — safest, best-documented API model
- **URL:** https://github.com/scripthookvdotnet/scripthookvdotnet (zlib). Enhanced fork: https://github.com/Chiheb-Bacha/ScriptHookVDotNetEnhanced
- `source/core/NativeFunc.cs` = managed native-invoker design; `GTA.Native.Hash` enum = ready name→hash list to transcode to Python.
- **Spawn discipline (mirror as bridge commands):** `Model.Request()` → poll `Model.IsLoaded` → create → `Model.MarkAsNoLongerNeeded()`. Vehicle mods: `vehicle.Mods.InstallModKit()` then `vehicle.Mods[type].Index = n`.

### ScriptHookV native-caller (for the ctypes loader)
- ScriptHookV exports three C functions our ctypes bridge can bind directly (simplest "call any native", stays on the script thread):
  - `nativeInit(UINT64 hash)` — mangled `?nativeInit@@YAX_K@Z`
  - `nativePush64(UINT64 val)` — `?nativePush64@@YAX_K@Z`
  - `nativeCall()` → `UINT64*` — `?nativeCall@@YAPEA_KXZ`
- Refs: `ivanmeler/OpenVHook/SDK/inc/nativeCaller.h`, `Seanghost117/GTA-V-Internal-Source`, `Freeeaky/GTALua`.

### Closed-source menus (capability checklists only)
- 2Take1/Stand/Cherax are closed; only Lua APIs are open. Cherax docs: https://github.com/SATTY91/Cherax-Lua-API-Documentation . YimMenu-Lua org (readable pure-Lua features): https://github.com/YimMenu-Lua

### Techniques to adopt (prioritized)
1. **Native invocation, two tiers:** primary = ctypes-bind ScriptHookV `nativeInit/Push64/Call`; advanced = `NativeRegistrationTable`+`GetNativeHandler`.
2. **Pattern-scan + RIP layer** (per fivem-offset-dumper) to bootstrap `world`, `replay_interface`(pools), `blip_list`, `camera`, `ScriptGlobals`.
3. **Direct struct read/write** via GTAV-Classes offsets (instant armor, dirt, locks) — version-verify at runtime.
4. **Always FixVectors** on vector-returning natives.
5. **Off-thread work queue** so commands never block the game/script thread.
6. **Embed catalog datasets** (ENT enums, SHVDN Hash enum) as Python dicts.
7. **Adopt Menyoo's Spooner XML** as the scene save/load format.

---

## 2. Game Memory Structures & RE References

Primary sources: **Mr-X-GTA/GTAV-Classes-1** (alloc8or-lineage headers), **YimMenu/YimMenuV2** (`enhanced`+`legacy` branches — current Enhanced pool/global/pointer code), **citizenfx/fivem** (PoolManagement, scrThread), **Gogsi/GTAV-Research** (globals), **Pocakking/BigBase** (ScriptGlobal.hpp). Position-at-+0x90, health-at-+0x280, and global addressing math are **unchanged between legacy ~b3xxx and Enhanced** — only root-pointer AOBs and pool encryption differ.

### Pools — enumerate all peds / vehicles / objects
- **Ped/Object pools** = `rage::fwBasePool` (size 0x30): `m_Entries`@0x08, `m_Flags(uint8*)`@0x10, `m_Size`@0x18, `m_ItemSize`@0x1C. Validity `!(m_Flags[i] & 0x80)`; address `m_Entries + i*m_ItemSize`. **Script handle round-trip:** `guid = (i<<8) + m_Flags[i]`; `index = guid >> 8`.
- **Vehicle pool** = `rage::fwVehiclePool`: `m_PoolAddress(void**)`@0x08, `m_Size`@0x10, `m_BitArray(uint32*)`@0x38, `m_ItemCount`@0x68. Validity `(m_BitArray[i>>5] >> (i&0x1F)) & 1`; address `m_PoolAddress[i]`.
- **Enhanced-build pool encryption (backported from RDR2):** Ped/Object pool pointers wrapped in `PoolEncryption {bool m_IsSet; uint64 m_First, m_Second;}`. Decrypt (from YimMenuV2 Pools.cpp):
  ```c
  uint64 x = _rotl64(m_Second, 30);
  pool = ~_rotl64(_rotl64(x ^ m_First, 32), ((uint8)x & 0x1F) + 2);  // Ped; Object uses +3
  ```
  **Vehicle pool is NOT encrypted on Enhanced** — plain `**VehiclePool`.
- **Simplest path if you can call natives:** `GET_GAME_POOL('CPed'/'CVehicle'/'CObject'/'CPickup')` returns handle arrays (FiveM `PoolManagement.cpp` builds a 178-name pool map). → world scan / ESP / mass-modify.

### CEntity / CPhysical / CDynamicEntity
Inheritance: `fwExtensibleBase → fwEntity → CEntity → CDynamicEntity → CPhysical → {CVehicle, CPed, CObject}`.
- `fwEntity`: vtable@0x00, `m_model_info`@0x20, `m_entity_type(uint8)`@0x28 (3=ped,4=veh,5=obj), `m_transformation_matrix`@0x60, **position@0x90** (= matrix row 3; x/y/z at 0x90/0x94/0x98).
- `CDynamicEntity`: `m_net_object`@0xD0 (network sync object).
- `CPhysical`: `m_damage_bits`@0x188, **`m_health`@0x280, `m_maxhealth`@0x284**. So *every* entity shares position@0x90, health@0x280, maxhealth@0x284, model@0x20, type@0x28.

### CVehicle (size ~0x14A0)
`m_boost`@0x300, `m_engine_health(float)`@0x910, **`m_handling_data(CHandlingData*)`@0x960**, `m_tyre_burst_bitset`@0x96B, `m_dirt_level`@0xA20, `m_type`@0xC28, `m_max_passengers`@0xCA0, `m_num_of_passengers`@0xCA2, **`m_driver(CPed*)`@0xCA8, `m_passengers[15]`@0xCB0**, `m_door_lock_status`@0x13D0. Gear/RPM/clutch/throttle best via natives or `CVehicleControlDataNode`. (Note: another community ref puts handling ptr at `+0x918` — verify on the live build; b3788 work in this project confirmed **+0x960** with raise at CHandlingData+0xD0.)

### CPed (size ~0x1968)
`m_velocity`@0x300, `m_vehicle(CVehicle*)`@0xD10, `m_ped_intelligence(CPedIntelligence*)`@0x10A0, `m_player_info`@0x10A8, `m_inventory`@0x10B0, `m_weapon_manager`@0x10B8, **`m_armor(float)`@0x150C**, `m_cash(uint16)`@0x1614. Health/maxhealth inherited @0x280/0x284.
- `CPedWeaponManager`: `m_selected_weapon_hash`@0x18, `m_weapon_info`@0x20, `m_weapon_object`@0x78.
- `CPedInventory`: `m_infinite_ammo:1`/`m_infinite_clip:1` bitfields @0x78.
- **`CPedFactory` singleton: `m_local_ped(CPed*)`@0x08** = fastest local-player-ped pointer without natives.

### Script Globals (and locals) — the big in-process win
- **Addressing (identical legacy + Enhanced):** `GetGlobalPtr(idx) = ScriptGlobals[(idx>>0x12)&0x3F] + (idx & 0x3FFFF)`. `ScriptGlobals` is `int64**` (≤64 block bases, each 0x40000 bytes).
- **Index semantics (Gogsi):** `Global_14313.f_106` → idx `14313+106`. Arrays: `Global_96440[id]` → 96440 holds size, elements at 96441, stride = `divisor*8`.
- **Unlocks:** money, MP/SP character state, property/business, mission flags, vehicle ownership.
- **Locals/threads:** `ScriptThreads` = `atArray<scrThread*>`. Auto-updater for indices across versions: https://github.com/jayphen-skid/script-assist

### Offset-finding that survives updates (Enhanced AOBs, YimMenuV2 Pointers.cpp)
| Pointer | AOB (Enhanced) | Resolve |
|---|---|---|
| ScriptGlobals | `48 8B 8E B8 00 00 00 48 8D 15 ? ? ? ? 49 89 D8` | +7,+3,Rip → int64** |
| ScriptThreads | `48 8B 05 ? ? ? ? 48 89 34 F8 48 FF C7 48 39 FB 75 97` | +3,Rip |
| PedFactory | `C7 40 30 03 00 00 00 48 8B 0D` | +7,+3,Rip |
| PedPool (enc) | `80 79 4B 00 0F 84 F5 00 00 00 48 89 F1` | +0x18,+3,Rip |
| VehiclePool | `48 83 78 18 0D` | -0xA,+3,Rip |
| ObjectPool (enc) | `48 8B 04 0A C3 0F B6 05` | +5,+3,Rip |
| HandleToPtr/PtrToHandle | `0F 1F 84 00 00 00 00 00 89 F8 0F 28 FE 41` | +0x21,+1,Rip / -0xB,+1,Rip |

`HandleToPtr`/`PtrToHandle` are the official handle↔`CEntity*` converters — call these to bridge native handles ↔ raw struct reads. Enhanced added **Arxan obfuscation** + pool encryption.

**Repos to track:** YimMenuV2 (up-to-date Enhanced), Mr-X-GTA/GTAV-Classes-1 (structs), citizenfx/fivem (pools/scrThread, `GetGamePool`), Gogsi/GTAV-Research (globals), Pocakking/BigBase (simpler reference base), **dr-NHA/NHA_GTA_CT** (Cheat Engine table with auto-offset-update + RTTI classname dump + globals converter — great for interactive offset verification before hardcoding).

---

## 3. Animation System (how it works + how to drive it)

RAGE: animations live in **clip dictionaries** (`.ycd`) in the RPFs; each holds named **clips**. Peds animate via a task system (MoVE network + AI tasks). Scripts drive it all by name through natives — exactly our bridge's surface. **No raw memory needed.**

### Clip dictionaries & TASK_PLAY_ANIM
```
REQUEST_ANIM_DICT(dict) 0xD3BD40951412FEF6 → poll HAS_ANIM_DICT_LOADED(dict) 0x34EBA0F1C2A6ED03
TASK_PLAY_ANIM(ped, dict, anim, blendIn, blendOut, duration, flag, rate, lockX, lockY, lockZ) 0xEA47FE3719165B94
TASK_PLAY_ANIM_ADVANCED(ped, dict, anim, x,y,z, rx,ry,rz, blendIn, blendOut, dur, flag, animTime, rotOrder, ikFlags) 0x83CDB10EA29B370B
```
`duration` -1 = until canceled; `rate` 0–1; `blendIn` 8.0 ≈ instant; `lockX/Y/Z` lock root translation.

**`eScriptedAnimFlags` (sum them):** AF_LOOPING=1, AF_HOLD_LAST_FRAME=2, AF_REPOSITION_WHEN_FINISHED=4, AF_NOT_INTERRUPTABLE=8, AF_UPPERBODY=16, AF_SECONDARY=32, AF_ABORT_ON_PED_MOVEMENT=128, AF_ADDITIVE=256, AF_TURN_OFF_COLLISION=512, AF_OVERRIDE_PHYSICS=1024, AF_IGNORE_GRAVITY=2048, AF_HIDE_WEAPON=1048576, AF_USE_ALTERNATIVE_FP_ANIM=268435456, AF_USE_FULL_BLENDING=1073741824 (full list in citizenfx/natives TaskPlayAnim.md). Common: `1` loop, `1+16=17` looping upper-body, `1+48` loop upper-body secondary (gesture while walking).

**Read/control:** `IS_ENTITY_PLAYING_ANIM(ent,dict,anim,3)`, `GET_ENTITY_ANIM_CURRENT_TIME`/`SET_..` (0–1 scrub), `SET_ENTITY_ANIM_SPEED`, `STOP_ANIM_TASK`, `CLEAR_PED_TASKS`/`_IMMEDIATELY`. Objects/props: `PLAY_ENTITY_ANIM(entity, animName, animDict, ...)` 0x7FB218262B810701 (note reversed arg order).

### Finding anim names (dict+clip pairs)
1. **alexguirre "GTA V Animations List"** — https://alexguirre.github.io/animations-list/ — complete machine-readable dump from the game's `.ycd`, kept current per patch. **Best seed for the bridge.**
2. **Pleb Masters: Forge** — https://forge.plebmasters.de/animations — searchable/previewable (incl. facials).
3. **CodeWalker** — open `.ycd`, read clip names, `.ycd ↔ XML`; **scriptable headlessly via CodeWalker.Core.dll** (same as our `.rel` workflow) to dump every clip name from our on-disk decrypted RPFs.
4. In-game viewers: AnimationV, Animation Viewer (sollaholla) on gta5-mods.

### Scenarios (ambient activity presets)
```
TASK_START_SCENARIO_IN_PLACE(ped, name, timeToLeave, playIntro) 0x142A02425FF02BD9
TASK_START_SCENARIO_AT_POSITION(ped, name, x,y,z, heading, timeToLeave, playIntro, warp) 0xFA4EFC79F69D4F07
```
Lists: **DioneB/gtav-scenarios** https://github.com/DioneB/gtav-scenarios . Examples: `WORLD_HUMAN_SMOKING, WORLD_HUMAN_AA_COFFEE, WORLD_HUMAN_GUARD_STAND, WORLD_HUMAN_LEANING, WORLD_HUMAN_DRINKING, WORLD_VEHICLE_MECHANIC`.

### Facial / gestures
`PLAY_FACIAL_ANIM(ped, animName, animDict)` 0xE1E65CA8AC9C00ED (facial channel layers over body). Dicts `facials@<model>@base / mood_normal_1` (moods: angry/happy/drunk/excited/sleeping/smug/injured/stressed/talking…). Upper-body gestures via TASK_PLAY_ANIM with `AF_UPPERBODY|AF_SECONDARY`; walk styles via `SET_PED_MOVEMENT_CLIPSET(ped, "move_m@swagger", blend)` (reset `RESET_PED_MOVEMENT_CLIPSET`).

### Synchronized scenes (multi-ped lockstep at a world anchor)
**Local:** `CREATE_SYNCHRONIZED_SCENE(x,y,z, roll,pitch,yaw, 2)` → `TASK_SYNCHRONIZED_SCENE(ped, scene, dict, anim, blendIn, blendOut, dur, flag, rate, p9)` per ped → drive with `SET_SYNCHRONIZED_SCENE_PHASE/RATE/LOOPED`, query `GET_SYNCHRONIZED_SCENE_PHASE`. **Networked (MP/object anim):** `NETWORK_CREATE_SYNCHRONISED_SCENE` → `NETWORK_ADD_PED_TO_SYNCHRONISED_SCENE` → `NETWORK_START_SYNCHRONISED_SCENE`; get local handle via `NETWORK_GET_LOCAL_SCENE_FROM_NETWORK_ID`.

### Emote repos (curated, labeled, prop-aware dict+clip DBs — import wholesale)
- **rpemotes-reborn** (most complete) https://github.com/alberttheprince/rpemotes-reborn — `Client/AnimationList.lua` style: `["name"]={"dict","clip","Label", paired/prop opts}`. Includes paired/shared (`_guy_a`/`_guy_b`), facials, walks, animal emotes.
- **dpemotes** https://github.com/andristum/dpemotes (`Client/AnimationList.lua`).
- Examples: handshake `mp_ped_interaction/handshake_guy_a`; coffee (prop `p_amb_coffeecup_01`) `amb@world_human_drinking@coffee@male@idle_a/idle_c`; cpr `mini@cpr@char_a@cpr_str/cpr_pumpchest`.

**Bottom line:** adding full animation control is wrapping the dict-load→`TASK_PLAY_ANIM` flow + scenarios + synced scenes, and **seeding a name DB** from alexguirre's list (exhaustive) + rpemotes-reborn (curated/labeled). Native DB: https://github.com/citizenfx/natives ; flags calc: https://vespura.com/fivem/animations/

---

## 4. Natives Ecosystem & Underused Powerful Natives

### Native databases (keep our name→hash synced to the right build!)
- **alloc8or/gta5-nativedb-data** (canonical, what we use) https://github.com/alloc8or/gta5-nativedb-data — `natives.json` (Legacy) + `natives_gen9.json` (Enhanced). Browse: https://alloc8or.re/gta5/nativedb/ , .../enhanced/. **Legacy vs Enhanced differ — that's why two files.**
- **citizenfx/natives** (FiveM, best for arg semantics + examples) https://docs.fivem.net/natives/ · https://github.com/citizenfx/natives
- alt:V https://natives.altv.mp/ · DottieDot viewer+codegen https://github.com/DottieDot/GTAV-NativeDB · LCPDFR wiki https://www.lcpdfr.com/resources/nativedb/index/
- **Crossmap:** FiveM `ext/natives/` maps canonical hash → per-build index; keep our table matched to the live build.

### Particles / PTFX (big under-used capability)
Flow: `REQUEST_NAMED_PTFX_ASSET(asset)` → `HAS_NAMED_PTFX_ASSET_LOADED` → `USE_PARTICLE_FX_ASSET(asset)` → `START_PARTICLE_FX_LOOPED_AT_COORD(effect, x,y,z, rx,ry,rz, scale, xAxis,yAxis,zAxis, ownedByScript)` → handle. Also `..._LOOPED_ON_ENTITY[_BONE]`, non-looped, and **networked** variants. Control: `SET_PARTICLE_FX_LOOPED_COLOUR/ALPHA/SCALE`, `STOP_PARTICLE_FX_LOOPED`, `REMOVE_PARTICLE_FX`.
- **Name lists:** alexguirre gist https://gist.github.com/alexguirre/af70f0122957f005a5c12bef2618a786 (`[dict]` → effect names); Vespura https://vespura.com/fivem/particle-list/ . Examples: `[core]` → `ent_amb_steam_pipe_lgt`, `water_splash_ped_wade`, `fire_wheel_bike`, `muz_pistol_silencer`; `fire_petroltank_car`; `[veh_thruster]` → `veh_xm_thruster_afterburner`.

### Scaleform / UI (drive the game's own UI movies)
Flow: `REQUEST_SCALEFORM_MOVIE(name)` → `HAS_SCALEFORM_MOVIE_LOADED` → `BEGIN_SCALEFORM_MOVIE_METHOD(h, method)` → `SCALEFORM_MOVIE_METHOD_ADD_PARAM_*` → `END_SCALEFORM_MOVIE_METHOD()` → render `DRAW_SCALEFORM_MOVIE_FULLSCREEN`/`_MOVIE`/`_MOVIE_3D` (every frame).
- Movies: `instructional_buttons` (control-prompt bar), `mp_big_message_freemode` (wasted/passed/shard), `midsized_message`, `popup_warning`, `mp_celebration`, `mp_results_screen`.
- Lists: https://forum.cfx.re/t/full-scaleform-list-decompiled/46771 ; wrapper lib ScaleformUI https://github.com/manups4e/ScaleformUI

### Drawing / markers / checkpoints
`DRAW_MARKER(type, x,y,z, dir.., rot.., scale.., r,g,b,a, bob, faceCam, p19, rotate, texDict, texName, drawOnEnts)` — types 0 cone, 1 cylinder, 2/3 chevron, 4/5 checkered flag, 6 vertical circle, etc. `DRAW_SPRITE(texDict, texName, x,y,w,h, heading, r,g,b,a)` (0–1 coords, needs `REQUEST_STREAMED_TEXTURE_DICT`). `DRAW_RECT`, `DRAW_LINE` (3D), `DRAW_POLY`. Persistent **checkpoints**: `CREATE_CHECKPOINT(type, x,y,z, nextX.., radius, r,g,b,a, reserved)` + `SET_CHECKPOINT_ICON_RGBA`, `DELETE_CHECKPOINT`. Text: `SET_TEXT_*` + `BEGIN_TEXT_COMMAND_DISPLAY_TEXT("STRING")`/`ADD_TEXT_COMPONENT_SUBSTRING_PLAYER_NAME`/`END_..(x,y)`; `SET_DRAW_ORIGIN(x,y,z)` to anchor 2D text to a 3D point.

### Audio
`PLAY_SOUND_FRONTEND(soundId=-1, audioName, soundSet, p3)` — e.g. `SELECT/HUD_FRONTEND_DEFAULT_SOUNDSET`, `CHECKPOINT_PERFECT/HUD_MINI_GAME_SOUNDSET`. `PLAY_SOUND_FROM_ENTITY/_COORD` (positional). `REQUEST_SCRIPT_AUDIO_BANK` before some sounds/speech. Speech: `PLAY_AMBIENT_SPEECH_WITH_VOICE`. Radio: `SET_RADIO_TO_STATION_NAME` (player) vs `SET_VEH_RADIO_STATION` (vehicle) — matches our CLAUDE FM finding. Lists: https://gtamods.com/wiki/PLAY_SOUND_FRONTEND

### Streaming / assets / runtime data
`REQUEST_MODEL/HAS_MODEL_LOADED/SET_MODEL_AS_NO_LONGER_NEEDED` (+`REQUEST_COLLISION_AT_COORD`), `REQUEST_ANIM_DICT/SET/CLIP_SET`, `REQUEST_STREAMED_TEXTURE_DICT`, **`REQUEST_IPL`/`REMOVE_IPL`** (load/unload map chunks, interiors, doors), `REQUEST_NAMED_PTFX_ASSET`, `REQUEST_SCALEFORM_MOVIE`, `REQUEST_WEAPON_ASSET`, `REQUEST_VEHICLE_ASSET`, `REQUEST_CUTSCENE`. **DATAFILE** namespace = runtime structured data (UGC/mission object graph), NOT a way to hot-swap `.meta` game files (those need RPF/DLC); but many runtime equivalents exist (`SET_VEHICLE_HANDLING_FLOAT`, `SET_PED_*`).

### Other powerful categories
- **ENTITY:** SET_ENTITY_COORDS/HEADING/VELOCITY, `APPLY_FORCE_TO_ENTITY`, `ATTACH_ENTITY_TO_ENTITY`, `SET_ENTITY_ALPHA/INVINCIBLE`, raycasts `START_SHAPE_TEST_RAY`/`GET_SHAPE_TEST_RESULT`.
- **TASK (AI):** `TASK_GO_TO_COORD_ANY_MEANS`, `TASK_FOLLOW_NAV_MESH_TO_COORD`, `TASK_COMBAT_PED`, `TASK_VEHICLE_DRIVE_TO_COORD`, sequences `OPEN_SEQUENCE_TASK`/`TASK_*`/`CLOSE_SEQUENCE_TASK`/`TASK_PERFORM_SEQUENCE`.
- **CAM:** `CREATE_CAM_WITH_PARAMS`, `SET_CAM_ACTIVE`, `RENDER_SCRIPT_CAMS(true,ease,t,..)`, `POINT_CAM_AT_*`, `ATTACH_CAM_TO_ENTITY` (relevant to ExtendedLSC custom camera).
- **GRAPHICS FX:** `ADD_DECAL`, `SET_TIMECYCLE_MODIFIER`, **`ANIMPOSTFX_PLAY(name, dur, looped)`** (e.g. `DeathFailOut`, `HeistCelebPass`), `START_SCREEN_EFFECT`.
- **FIRE/EXPLOSION:** `ADD_EXPLOSION(x,y,z, type, dmgScale, audible, invisible, shake)`, `START_SCRIPT_FIRE`.
- **INTERIOR:** `ACTIVATE_INTERIOR_ENTITY_SET`/`DEACTIVATE_..` (prop/furniture swaps), `REFRESH_INTERIOR`.
- **MISC:** `GET_HASH_KEY`, `GET_PROFILE_SETTING(300=SFX,301=Music)`, `GET_GROUND_Z_FOR_3D_COORD`.
- **DECISION_EVENT/BRAIN:** `ADD_SHOCKING_EVENT_AT_POSITION` (crowd panic), `REGISTER_OBJECT/WORLD_POINT_SCRIPT_BRAIN`.

---

## 5. Vehicles, Handling/Performance & Saves

### Live handling tuning (CHandlingData)
Reach handling from a vehicle: **`CVehicle → CHandlingData*`** (this project verified **+0x960** on b3788; some community refs say +0x918 — verify per build). The struct is **shared across all vehicles of that model** — editing affects every instance; for per-car you clone the handling (ikt's "Handling Replacement Library").

**CHandlingData float offsets (ikt Offsets.hpp):** `fMass`@0x0C, `fDriveBiasFront`@0x48, `m_acceleration`@0x4C (live accel value menus poke), `nInitialDriveGears`@0x50, `fDriveInertia`@0x54, `fInitialDriveForce`@0x60, `fDriveMaxFlatVel`@0x64 (top speed), `fInitialDriveMaxFlatVel`@0x68, `fBrakeForce`@0x6C, `fSteeringLock`@0x80, `fTractionCurveMax`@0x88, `fTractionCurveMin`@0x90, `fSuspensionForce`@0xBC, `fSuspensionUpper/LowerLimit`@0xC8/0xCC, **`fSuspensionRaise`@0xD0**, `fSuspensionBiasFront/Rear`@0xD4/0xD8.
- Refs: https://github.com/ikt32/GTAVManualTransmission (Offsets.hpp), https://gtaforums.com/topic/869574-chandlingdata-in-memory/ , field docs https://docs.dwnstr.com/data-files/handling
- **ExtendedLSC Vehicle Tuning sliders → map to:** `fInitialDriveForce(0x60)`, `fDriveMaxFlatVel(0x64)`, `nInitialDriveGears(0x50)`, `fTractionCurveMax(0x88)`, `fBrakeForce(0x6C)`, `fSuspensionRaise(0xD0)`. (Model-shared caveat — clone for per-car.)
- Live-editor UX reference: https://www.gta5-mods.com/tools/real-time-handling-editor

### Mod kits & carcols (LSC mapping)
Native flow: `SET_VEHICLE_MOD_KIT(veh,0)` first → `GET_NUM_VEHICLE_MODS(veh,type)` → `SET_VEHICLE_MOD(veh,type,index,customTires)` (-1=stock). **Mod type IDs:** 0 spoiler, 1/2 bumper F/R, 3 skirt, 4 exhaust, 5 chassis, 6 grille, 7 bonnet, 8/9 wing L/R, 10 roof, 11 engine, 12 brakes, 13 gearbox, 14 horn, 15 suspension, 16 armour, 18 nitrous, 19 turbo, 22 xenon, **23 wheels**, 24 rear wheels/hydraulics, 38/48 livery. Performance slots (11,12,13,15,16,18,19) scale handling values → relevant to ExtendedLSC NoStockOption mods.
- **Wheels:** call `SET_VEHICLE_WHEEL_TYPE(veh, type)` first (0 Sport,1 Muscle,2 Lowrider,3 SUV,4 Offroad,5 Tuner,6 Bike,7 High End) — switching type changes which set `GET_NUM_VEHICLE_MODS(veh,23)` enumerates. **(Matches this project's b3788 wheel work.)**
- Files behind it: `carcols.meta` (mod kit id + visibleMods), `carvariations.meta` (links model→kit), `vehicles.meta` (handlingId). Kit ID must be unique/<~6 digits. Edit with CodeWalker. Kit-id getter: https://www.gta5-mods.com/tools/vehicle-kit-id-getter

### VStancer / wheel memory — **do it via natives, no offsets needed**
R* exposed exactly what VStancer pokes:
- `SET_VEHICLE_WHEEL_X_OFFSET(veh, wheelIndex, offset)` / `GET_..` → **track width** (mirror models → one side negative).
- `SET_VEHICLE_WHEEL_Y_ROTATION(veh, wheelIndex, value)` / `GET_..` → **camber**.
- `GET_VEHICLE_NUMBER_OF_WHEELS(veh)`.
- Open-source reference: https://github.com/carmineos/fivem-vstancer (persists per-vehicle via decorators; ExtendedLSC uses its own save). **Cleanest path for ExtendedLSC wheel fitment.** Deeper ride-height/hydraulics still need memory (`CVehicleWheel+0x148/0x14C` per community notes — but this project already RE'd the full CWheel offsets on b3788).

### Save-game editing (single-player)
SP saves at `Documents\Rockstar Games\GTA V\Profiles\<id>\SGTA5####` (#### = slot-1), **encrypted block/checksummed binary** (no clean public spec). Editable via tools: **X3T-Infinity Save Editor** https://x3t-infinity.com/GTAV_SE (money, health/armor/abilities, the 7 driving skills, vehicles, properties, unlocks, stats, progress); open-source **SyDevTeam/gta5view** https://github.com/SyDevTeam/gta5view . **For ExtendedLSC:** persist car configs via your own JSON (`VehicleSaveData.cs`), not binary save edits.

### Player stats / money
- **SP money is a stat per character:** `SP0/SP1/SP2_TOTAL_CASH`. **`Game.Player.Money` (SHVDN) already does the right thing for the active character** (`STAT_GET_INT`/`STAT_SET_INT` on the right name) — use it directly for LSC charges/refunds.
- Skills/unlocks: `STAT_SET_INT(GET_HASH_KEY("SP0_STAMINA"), value, true)` etc.

---

## 6. RE Tooling, World Editing & Misc Capabilities

### Pattern scanning & offset repos
Best practice: scan a stable *code* pattern with wildcards over the rel32/immediates, then `target = match + instr_len + rel32`; Boyer-Moore-Horspool for speed; cache resolved pointers by build; fail loud if a critical pattern is missing.
- **SHVDN `NativeMemory.cs`** https://github.com/scripthookvdotnet/scripthookvdotnet/blob/main/source/core/NativeMemory.cs — the best annotated, maintained pattern collection (entity/player funcs, all pools incl. blip/checkpoint/projectile/interior/camera, model hash table, weapon/ammo arrays, handling lookup, Euphoria messages). Ships `FindPatternBmh()` + `FindPatternNaive()`.
- YimMenu `pointers.cpp` / YimMenuV2 (Enhanced). Menyoo SP (single-player-safe examples). Methodology tool: https://github.com/MikaCybertron/Get-New-Offset-with-Pattern-Scan
- **Python prior art:** scripthookvpy3k https://github.com/lgrahl/scripthookvpy3k (binds the full native namespace tree into Python — mine for arg marshalling/threading).

### CodeWalker / OpenIV / data
- **CodeWalker (dexyfex)** https://github.com/dexyfex/CodeWalker — views/edits YDR/YDD/YFT/YBN/YNV/YPT/YTD/GXT2/REL/AWC/YCD, edits **YMAP** (entity placement), YTYP, YND paths, scenarios. R30+ does **XML import/export of nearly any RAGE format**. **`CodeWalker.Core.dll` is a headless library** (RpfManager, meta/XML converters) — driveable from PowerShell/.NET (as we already do for `.rel`). No CLI, but the Core DLL *is* the headless API.
- **OpenIV** https://openiv.com — RSC extraction, batch texture export, ASI loading; **OpenRPF** for Enhanced. RPF7 magic `0x52504637`, AES TOC + per-game NG encryption (both implement).

### Script decompilation & globals
GTA V logic = compiled `.ysc`. Decompile to find script global indices behind any world state.
- Decompiler: **njames93/GTA-V-Script-Decompiler** https://github.com/njames93/GTA-V-Script-Decompiler
- Pre-decompiled corpora: YimMenu/GTA-V-Decompiled-Scripts, **calamity-inc/GTA-V-Decompiled-Scripts** https://github.com/calamity-inc/GTA-V-Decompiled-Scripts, root-cause/v-decompiled-scripts (recent builds).
- GXT2 text editing: lollolong/gxt2 https://github.com/lollolong/gxt2 .

### World / IPL / interiors (all native-driven)
- **Persistent object spawn:** `REQUEST_MODEL`→`CREATE_OBJECT(hash,x,y,z,isNet,scriptCheck,dynamic)`→`SET_ENTITY_AS_MISSION_ENTITY(obj,true,true)`; persist the list yourself + re-create on load (Menyoo Spooner model).
- **IPL toggling:** `REQUEST_IPL(name)`/`REMOVE_IPL`/`IS_IPL_ACTIVE` — open interiors, swap map states, load furniture/collision. Lists: https://altv.stuyk.com/docs/articles/tables/ipls.html
- **Interiors:** `GET_INTERIOR_AT_COORDS`, `PIN_INTERIOR_IN_MEMORY`, `ENABLE/DISABLE_INTERIOR_PROP`, `REFRESH_INTERIOR`. **Doors:** `ADD_DOOR_TO_SYSTEM`, `DOOR_SYSTEM_SET_DOOR_STATE`. **Blips:** `ADD_BLIP_FOR_COORD/ENTITY/RADIUS` + `SET_BLIP_SPRITE/COLOUR`, custom name via `BEGIN_TEXT_COMMAND_SET_BLIP_NAME`.

### Peds / AI behavior
- **Relationship groups:** `ADD_RELATIONSHIP_GROUP(name,&hash)`, `SET_PED_RELATIONSHIP_GROUP_HASH`, `SET_RELATIONSHIP_BETWEEN_GROUPS(rel, A, B)` (**both directions**). Rel values: 0 Companion,1 Respect,2 Like,3 Neutral,4 Dislike,5 Hate.
- **Combat:** `TASK_COMBAT_PED`, `TASK_COMBAT_HATED_TARGETS_AROUND_PED`, tuning `SET_PED_COMBAT_ATTRIBUTES/ABILITY/RANGE/MOVEMENT`, `SET_PED_ACCURACY`, `SET_PED_SEEING/HEARING_RANGE`. `SET_BLOCKING_OF_NON_TEMPORARY_EVENTS(ped,true)` stops random peds abandoning tasks.
- Example gang-war repo: https://github.com/David-Lor/GTAV-SimpleGangWar

### Weather / time / water / camera
- Time: `SET_CLOCK_TIME(h,m,s)`, `PAUSE_CLOCK`, `NETWORK_OVERRIDE_CLOCK_TIME`. Weather: `SET_WEATHER_TYPE_NOW/_PERSIST`, `SET_OVERRIDE_WEATHER`, `_SET_WEATHER_TYPE_TRANSITION` (blend two). Water/ocean: **`SET_DEEP_OCEAN_SCALER(f)`** (0 flat, ≥2 storm), `SET_WAVES_INTENSITY`, `SET_RAIN`, `SET_WIND`. Wanted: `SET_PLAYER_WANTED_LEVEL`+`_NOW`, `SET_MAX_WANTED_LEVEL`, `SET_POLICE_IGNORE_PLAYER`.
- **Scripted cams:** `CREATE_CAM_WITH_PARAMS`→`SET_CAM_COORD/ROT/FOV`→`POINT_CAM_AT_*`→`SET_CAM_ACTIVE(true)`→**`RENDER_SCRIPT_CAMS(true, ease, easeTime, ..)`**; interp via two cams + `SET_CAM_ACTIVE_WITH_INTERP`.
- **Render targets** (draw cams/models onto in-world screens): `REGISTER_NAMED_RENDERTARGET`→`LINK_NAMED_RENDERTARGET(modelHash)`→`GET_NAMED_RENDERTARGET_RENDER_ID`→`SET_TEXT_RENDER_ID`. Example: https://github.com/throwarray/gtav-rendertarget

### Text / textures
- **Runtime labels (no GXT rebuild):** `ADD_TEXT_ENTRY(labelKey, text)` then display via `BEGIN_TEXT_COMMAND_DISPLAY_TEXT(label)`. Notifications: `BEGIN_TEXT_COMMAND_THEFEED_POST`/`END_TEXT_COMMAND_THEFEED_POST_TICKER`.
- **Custom PNGs from disk:** ScriptHookV C exports **`createTexture(fileName)`** + **`drawTexture(id, index, level, time, sizeX, sizeY, centerX, centerY, posX, posY, rotation, aspect, r,g,b,a)`** (up to 64 instances; native-thread only). SDK header: https://github.com/Abel-Liu/GTA5-ScriptHook/blob/master/inc/main.h . **For the ctypes bridge: bind these C exports directly** — plain C, not natives → full custom-image rendering. (.NET equivalent `GTA.UI.CustomSprite` is what ExtendedLSC's drag HUD uses.)

### Swiss-army repos
SHVDN (NativeMemory.cs = canonical memory map), CodeWalker (asset/world + headless Core), YimMenu/V2 (feature breadth), Menyoo SP (persistent XML spawning), citizenfx/natives (native DB for ctypes bindings), scripthookvpy3k (Python prior art), njames93 decompiler + decompiled corpora (script globals).

---

## 7. ExtendedLSC — consolidated direct wins

1. **Money:** `Game.Player.Money` already reads/writes the active character's SP cash correctly (`SP0/1/2_TOTAL_CASH`) — use directly for LSC purchase/refund. No memory needed.
2. **Wheel fitment (stance):** fully achievable via `SET_VEHICLE_WHEEL_X_OFFSET` (track) + `SET_VEHICLE_WHEEL_Y_ROTATION` (camber) + `GET_VEHICLE_NUMBER_OF_WHEELS` — **no memory scan**. (We currently do CWheel memory writes on b3788; the natives are a more update-proof alternative worth A/B-ing.)
3. **Vehicle Tuning side-course:** map sliders to `CHandlingData` floats (`fInitialDriveForce`, `fDriveMaxFlatVel`, `nInitialDriveGears`, `fTractionCurveMax`, `fBrakeForce`, `fSuspensionRaise`) at `CVehicle+0x960`. **Model-shared** — clone handling for per-car (ikt Handling Replacement Library pattern). Live-editor UX: real-time-handling-editor.
4. **Wheel enumeration:** call `SET_VEHICLE_WHEEL_TYPE` before `GET_NUM_VEHICLE_MODS(veh,23)` to get each category's wheel set (already used in this project).
5. **Custom HUD/art:** SHVDN `CustomSprite` (tintable PNG layers) — what the NFS drag gauge uses; respray = `Color` tint. For the bridge equivalent, ScriptHookV `createTexture/drawTexture`.
6. **Custom camera side-course:** CAM natives (`CREATE_CAM_WITH_PARAMS`, `RENDER_SCRIPT_CAMS`, `POINT_CAM_AT_ENTITY`).
7. **Mod-kit data editing (B-Rims-style):** CodeWalker for carcols/carvariations/vehicles.meta; keep kit IDs unique.

---

## 8. Master repo & tool index
| Purpose | Repo / URL |
|---|---|
| Native DB (canonical) | https://github.com/alloc8or/gta5-nativedb-data · https://alloc8or.re/gta5/nativedb/ |
| Native docs (semantics) | https://github.com/citizenfx/natives · https://docs.fivem.net/natives/ |
| Struct/offset headers | https://github.com/Mr-X-GTA/GTAV-Classes-1 |
| Live Enhanced offsets/patterns | https://github.com/YimMenu/YimMenuV2 |
| Memory pattern map (maintained) | SHVDN `NativeMemory.cs` |
| SP-safe trainer source | https://github.com/MeyhoMeyho/Menyoo.SP-1 · https://github.com/gtav-ent/GTAV-EnhancedNativeTrainer |
| Script globals semantics | https://github.com/Gogsi/GTAV-Research |
| Decompiled scripts | https://github.com/calamity-inc/GTA-V-Decompiled-Scripts · decompiler https://github.com/njames93/GTA-V-Script-Decompiler |
| Animations list (seed) | https://alexguirre.github.io/animations-list/ · emotes https://github.com/alberttheprince/rpemotes-reborn |
| Scenarios list | https://github.com/DioneB/gtav-scenarios |
| PTFX list | https://gist.github.com/alexguirre/af70f0122957f005a5c12bef2618a786 |
| Asset/world editor (+headless Core) | https://github.com/dexyfex/CodeWalker |
| Manual transmission (live drivetrain) | https://github.com/ikt32/GTAVManualTransmission |
| VStancer (native wheel offset) | https://github.com/carmineos/fivem-vstancer |
| Save editor | https://x3t-infinity.com/GTAV_SE · https://github.com/SyDevTeam/gta5view |
| Python-in-GTAV prior art | https://github.com/lgrahl/scripthookvpy3k |
| Cheat Engine table (offset verify) | https://github.com/dr-NHA/NHA_GTA_CT |

---
*Generated by 6 parallel research agents. Re-verify every offset/pattern against the live build before use.*
