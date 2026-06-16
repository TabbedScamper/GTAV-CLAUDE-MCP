# 02 — Vehicle dynamics (drivetrain, wheels, handling, fitment)

Mined from **ikt's GTAVManualTransmission** (the richest live-drivetrain memory source),
**ikt's HandlingEditor**, and **VStancer**. See also `../FINDINGS-wheels-and-handling.md` for the
wheel-fitment + handling deep-dive. Offsets here are read-anchors — **pattern-scan, don't hardcode**.

---

### Resolve CVehicle base address from a handle, every tick (don't cache)
**Category:** memory
**Problem:** Turn a vehicle handle into the `CVehicle*` that all offsets are relative to.
**Method:** AOB-scan `GetAddressOfEntity` once: `83 F9 FF 74 31 4C 8B 0D ?? ?? ?? ?? 44 8B C1 49 8B 41 08` — the match IS the function entry; cast to `fn(int)->ptr` and call with the handle. Then every getter is `*(T*)(base + offset)`.
**Gotcha:** The pattern resolves a *function you call*, not a data offset — don't read a displacement out of it. Entities get re-pooled/moved, so **re-resolve from the handle each tick; never cache the pointer**. Null-check base before deref. (SHVDN's GUID resolver in file 01 is the C# equivalent.)
**Source:** manualtransmission-ikt/Gears/Memory/NativeMemory.cpp

### Read a field offset out of the matched instruction's disp32
**Category:** memory
**Problem:** Get a field's numeric offset (gears/RPM/fuel) without hardcoding, so it survives patches.
**Method:** AOB-match an instruction that touches the field, then `offset = *(int*)(addr + N)` where N points at the disp32 inside the opcode. E.g. RPM `76 03 0F 28 F0 F3 44 0F 10 93` → `*(int*)(addr+10)`; next-gear `48 8D 8F ?? ?? ?? ?? 4C 8B C3` → `*(int*)(addr+3)`; turbo (b1604+) `F3 0F 10 9F D4 08 00 00` → `*(int*)(addr+4)`.
**Gotcha:** N is per-pattern — it must land on the `??` disp bytes, not the opcode start. **Sibling fields are derived by arithmetic off ONE anchor**, not separate scans: `currentGear=nextGear+2`, `topGear=nextGear+6`, `gearRatios=nextGear+8`; `clutch=rpm+0xC`, `throttle=rpm+0x10`; `oil=fuel+4`. If the anchor scan fails, the whole derived group is silently wrong — guard on the anchor being nonzero.
**Source:** manualtransmission-ikt/Gears/Memory/VehicleExtensions.cpp

### Branch the AOB on game build before scanning version-sensitive fields
**Category:** memory
**Problem:** Turbo, drive-force, handbrake, traction-vector offsets moved between builds; one pattern won't cover all.
**Method:** Call `SetVersion(shvVersion)` first (also bumps gear count 8→11 at ≥b1604). In `Init()`, gate the pattern by version: e.g. handbrake `8A C2 24 01 C0 E0 04 08 81` (→ `*(int*)(addr+19)`) on b2060+ vs `44 88 A3 ?? ?? ?? ?? 45 8A F4` (→ `*(int*)(addr+3)`) below; arenaBoost on b1604+ has no pattern — hardcoded `turboOffset + 0x30`.
**Gotcha:** SHV's reported version can disagree with the actual EXE — ikt cross-checks `getGameVersion()` against `getExeInfo()` and prefers the EXE-derived version when SHV reports lower. SetVersion MUST precede Init() (gear count + branch decisions read the version at scan time).
**Source:** manualtransmission-ikt/Gears/Memory/VehicleExtensions.cpp, Gears/main.cpp

### Walk vehicle → wheel-array → individual wheel pointers
**Category:** vehicle
**Problem:** Read/write per-wheel data (rotation, steering angle, brake/power, traction, health, radius).
**Method:** `wheelsPtr = *(uint64*)(base + wheelsPtrOffset)`, `numWheels = *(int*)(base + numWheelsOffset)` — both from ONE scan `3B B7 48 0B 00 00 7D 0D` → `numWheelsOffset=*(int*)(addr+2)`, `wheelsPtrOffset=that-8`. Each wheel = `*(uint64*)(wheelsPtr + 0x08*i)`. Per-wheel sub-offsets: tyre radius `0x110`, rim `0x114`, tyre width `0x118`, world velocity `0xB0`, traction vector `0xC0`, wheel-id `0x108`, handling-ptr `0x120`.
**Gotcha:** `wheelsPtr` is a pointer to an ARRAY OF POINTERS — double deref, stride 0x08 (not wheel-struct size). Steering/brake/power anchored to one pattern by ±arithmetic. Rotation speed + traction-vector lengths are stored **negated** (getters flip sign). Bounds check is off-by-one (`index > GetNumWheels` allows ==) — validate yourself.
**Source:** manualtransmission-ikt/Gears/Memory/VehicleExtensions.cpp

### Reach CHandlingData through the pattern-scanned handling offset
**Category:** vehicle
**Problem:** Read/write handling values (drive bias, drive force, steering lock, suspension, camber/toe).
**Method:** Scan `3C 03 0F 85 ?? ?? ?? ?? 48 8B 41 20 48 8B 88` → `handlingOffset = *(int*)(addr+0x16)`. Then `handlingPtr = *(uint64*)(base + handlingOffset)`. Field map (constants, from `HandlingInfo.h`): `fInitialDriveForce@0x60` (power), `fDriveMaxFlatVel@0x64` (top speed), `fTractionCurveMax@0x88` (grip), `fSuspensionRaise@0xD0` (ride height), `fBrakeForce@0x6C`, `fSteeringLock@0x80`. **Camber/toe are in a SUB-struct:** `CCarHandlingData` via `m_subHandlingData@0x158` → `fToeFront@0x14`, `fCamberFront@0x1C`, `fCamberRear@0x20`, `fCastor@0x24`.
**Gotcha:** The scanned `handlingOffset` self-heals across builds — **this resolves the +0x918 vs +0x960 ambiguity in STUDIES.md §5: let the live scan decide.** Two "drive force" concepts: live `driveForceOffset` on the vehicle (upgrade-affected, mutable) vs static `fInitialDriveForce@0x60` in handling. `CHandlingData` is **model-shared** — editing changes every instance of that model; clone for per-car.
**Source:** manualtransmission-ikt/Gears/Memory/VehicleExtensions.cpp, handling-editor-ikt/RTHandlingEditor/Memory/HandlingInfo.h

### Reach CVehicleModelInfo for model flags + steering-wheel angle
**Category:** vehicle
**Problem:** Read class-level model data (vehicle flag bitsets, max steering-wheel display angle).
**Method:** `pModelInfo = *(uint64*)(base + 0x020)`. Flags: scan `48 85 C0 74 3C 8B 80 ?? ?? ?? ?? C1 E8 0F` → `flagsOffset = *(int*)(addr+7)`, read 6 consecutive uint32 at `pModelInfo + flagsOffset + 4*i`. Max steering-wheel angle = `*(float*)(pModelInfo + 0x54C)`.
**Gotcha:** `0x020` and `0x54C` are hardcoded (version-fragile). The flags scan returns an offset into *modelinfo*, so do the modelinfo indirection first — reading `base + flagsOffset` points into the wrong struct.
**Source:** manualtransmission-ikt/Gears/Memory/VehicleExtensions.cpp

### Re-assert gear/RPM/clutch/throttle writes EVERY tick
**Category:** vehicle
**Problem:** The drivetrain sim overwrites your memory writes each frame; a one-shot write is lost.
**Method:** In the per-frame loop, write fresh each tick: to lock a gear set BOTH `SetGearCurr` AND `SetGearNext` to the same value (gear is a uint16 pair); `SetCurrentRPM(clamp(rpm,0,1))`; `SetClutch`; set BOTH `SetThrottle` AND `SetThrottleP`. Values are normalized floats [0,1] (throttleP can go negative).
**Gotcha:** `SetThrottle` reportedly affects engine *sound* only; actual drive is `SetThrottleP`/wheel power — set both. There is no set-and-forget. (This is the memory-side proof of our CLAUDE.md "value reverting = re-assert, not re-guess" rule.)
**Source:** manualtransmission-ikt/Gears/script.cpp

### Patch the game's WRITER instruction when re-asserting isn't enough
**Category:** memory
**Problem:** Some fields fail even with per-tick writes because a specific game instruction actively decays/resets them (throttle decay, brake decay, auto-shift, ABS, steering assist).
**Method:** AOB-scan the writer instruction and NOP it (`0x90` fill) or jump over it; save original bytes for restore. Gate the patch set by version. Examples: brake decay `EB 05 F3 0F 10 40 78`, ABS `0B 45 04 89 86 ?? 02 00 00`, steering control `F3 0F 11 8B FC 08 00 00`.
**Gotcha:** Apply the patch BEFORE/with the write each tick or the un-NOPed instruction wins the race. Patches are **process-global** (all vehicles) — enable sparingly, restore when done (esp. input-hijacking ones). Patch bytes are version-specific and tracked separately from read offsets. Use a small retry budget (scans transiently fail). **Our `re_tools_patch` (nop/patch_bytes/restore) already does this — reversible via registry + WAL.**
**Source:** manualtransmission-ikt/Gears/Memory/MemoryPatcher.cpp

### Wheel fitment / stance — natives only, no memory (VStancer)
**Category:** vehicle
**Problem:** Adjust track width + camber (the ExtendedLSC stance feature) update-proof.
**Method:** `n = GET_VEHICLE_NUMBER_OF_WHEELS(veh)`; for each wheel i: `SET_VEHICLE_WHEEL_X_OFFSET(veh, i, +tw if i%2==0 else -tw)` (track) and `SET_VEHICLE_WHEEL_Y_ROTATION(veh, i, +cam if i%2==0 else -cam)` (camber). Front = indices `0..frontCount-1`, rear = the rest. Capture stock via the `GET_` variants before editing (no reset native). Re-assert each tick; persist to your own JSON.
**Gotcha:** **Mirror L/R by index parity** or the car slides sideways instead of widening. Front/rear are separate index ranges. No memory, no offsets → nothing to break on a patch. **Best path for ExtendedLSC** (STUDIES.md §7.2). Seen in 1 mod, but corroborated by the natives' documented purpose.
**Source:** vstancer/VStancer.Client/Scripts/WheelScript.cs (full writeup in ../FINDINGS-wheels-and-handling.md)

### Derive on-ground / blinker / fuel from non-obvious encodings
**Category:** vehicle
**Problem:** Some "states" aren't stored as the obvious flag.
**Method:** **On-ground per wheel** = suspension compression `!= 0.0` (scan `45 0F 57 C9 F3 0F 11 83 60 01 00 00 F3 0F 5C` → `*(int*)(addr+8)`); a wheel in the air reads exactly 0.0; use last-contact coords at wheel `+0x40` (stable). **Blinker phase** = `((*(uint32*)(base+indicatorTimingOffset) + gameTime) >> 9) & 1` — a function of stored counter + live clock, changes every frame. **Oil** = `fuel+4` (not separately scanned).
**Gotcha:** None of these read as a plain bool/value — they're derived. A wheel's "next-probable" contact coord (`+0x60`) flutters to (0,0,0) when contact is lost; use `+0x40`.
**Source:** manualtransmission-ikt/Gears/Memory/VehicleExtensions.cpp
