# Findings — wheel fitment & live handling, read from real source

> Two reference mods, pulled as **clean source** (no decompiler) into `_sources/`, read end-to-end.
> This is the "exactly how they work" writeup — the proven call sequences, mapped to bridge commands.
> Source dirs are git-ignored; this writeup (our own words + attribution) is committed.

| Mod | Repo | Lang | License | What it teaches |
|---|---|---|---|---|
| VStancer | carmineos/fivem-vstancer | C# 99.9% | MIT (©2018 Carmine Giugliano) | wheel fitment via **natives only**, no memory |
| RT Handling Editor | ikt32/GTAVHandlingEditor | C++ | none stated → reference-only | live `CHandlingData` edit + **pattern-scanned** offset |

---

## A. Wheel fitment (stance) — VStancer — *natives only, zero memory*

**The entire effect is four natives.** No struct, no offset, no pattern scan. Track width and camber
are set per-wheel by index. (Source: `VStancer.Client/Scripts/WheelScript.cs`.)

```
SET_VEHICLE_WHEEL_X_OFFSET(vehicle, wheelIndex, offset)   → track width (how far the wheel sticks out)
GET_VEHICLE_WHEEL_X_OFFSET(vehicle, wheelIndex)
SET_VEHICLE_WHEEL_Y_ROTATION(vehicle, wheelIndex, value)  → camber (lean of the wheel)
GET_VEHICLE_WHEEL_Y_ROTATION(vehicle, wheelIndex)
GET_VEHICLE_NUMBER_OF_WHEELS(vehicle)                     → loop bound
```

**Exactly how they drive it (the non-obvious parts that make it look right):**

1. **Mirror left/right by parity.** Wheels alternate L,R,L,R by index. So one side gets `+value`, the
   other `-value` — otherwise the car shifts sideways instead of widening its stance:
   ```
   for index in range(wheelCount):
       SET_VEHICLE_WHEEL_X_OFFSET(veh, index, value if index % 2 == 0 else -value)
   ```
   Same parity trick for camber via `_Y_ROTATION`. (`WheelData.cs` getters/setters do exactly this.)

2. **Front vs rear are separate ranges.** Front wheels are indices `0 .. frontCount-1`, rear are
   `frontCount .. wheelCount-1`. `frontCount` comes from a helper (`CalculateFrontWheelsCount`) — for a
   normal 4-wheel car it's 2. So "front track width" only writes indices 0–1, "rear" writes 2–3.

3. **Capture defaults BEFORE editing, to support reset.** On first touch they read
   `GET_VEHICLE_WHEEL_X_OFFSET`/`_Y_ROTATION` for wheel 0 and the first rear wheel and stash them; reset
   = write those back. (No "stock" native — you must remember the originals yourself.)

4. **Re-assert every tick.** The game overwrites these as the vehicle streams/physics updates, so a
   `Tick` handler re-writes the edited values continuously while the value is non-default. (This matches
   our own CLAUDE.md "a value reverting later = re-assert, don't re-guess" rule.)

5. **Persistence = decorators** (`DECOR_SET_FLOAT`/`DECOR_GET_FLOAT`) keyed `vstancer_trackwidth_f` etc.
   That's a FiveM-network detail; **for our single-player bridge we'd persist to our own JSON instead**
   (same pattern ExtendedLSC already uses), and just keep the per-wheel write loop.

**→ Bridge mapping (all via `call_native`, no memory writes):**
```python
n = call_native("GET_VEHICLE_NUMBER_OF_WHEELS", [veh])
for i in range(n):
    call_native("SET_VEHICLE_WHEEL_X_OFFSET",  [veh, i, +tw if i % 2 == 0 else -tw])
    call_native("SET_VEHICLE_WHEEL_Y_ROTATION",[veh, i, +cam if i % 2 == 0 else -cam])
# re-assert in a loop; save {model, tw_f, tw_r, cam_f, cam_r} to JSON for persistence
```
This is the **update-proof path for ExtendedLSC wheel fitment** STUDIES.md §7.2 flagged — confirmed
against real shipping code. No offsets to break on a game patch.

---

## B. Live handling — RT Handling Editor — *memory, but offset is pattern-scanned*

ikt edits `CHandlingData` floats directly in memory. The valuable lessons are **how to reach the
struct without a hardcoded offset**, and **the exact verified field layout**.

### B1. Reaching `CHandlingData*` from a vehicle handle — *scan, don't hardcode*

(Source: `Memory/VehicleExtensions.cpp`.) The `CVehicle → CHandlingData*` offset is found by scanning a
**code pattern** at load, then reading the displacement out of the instruction — so it self-heals across
builds instead of breaking:

```cpp
addr = FindPattern("\x3C\x03\x0F\x85\x00\x00\x00\x00\x48\x8B\x41\x20\x48\x8B\x88", "xxxx????xxxxxxx");
handlingOffset = *(int*)(addr + 0x16);          // pull the disp32 straight out of the matched code
// then:  CHandlingData* = *(uint64*)(vehicleAddr + handlingOffset)
```

This is the single most important technique in the repo: **never trust a hardcoded `+0x960`.** Our RE
toolkit already has the scanner — wiring a pattern like this to resolve the handling offset at bridge
load is the right move (and reconciles the +0x918 vs +0x960 ambiguity in STUDIES.md §5 — let the scan
decide on the live build).

### B2. The verified `CHandlingData` field map (b-recent, alignment 16)

(Source: `Memory/HandlingInfo.h` — cross-checked against alexguirre's rage-parser-dumps.) Offsets are
**from the start of `CHandlingData`**, all `float` unless noted. The performance-relevant ones:

| Offset | Field | What it does |
|---|---|---|
| 0x0C | fMass | mass |
| 0x48 / 0x4C | fDriveBiasFront / Rear | power split |
| 0x50 | nInitialDriveGears | **uint8** — gear count |
| 0x54 | fDriveInertia | rev/accel feel |
| 0x60 | fInitialDriveForce | **acceleration / power** ⭐ |
| 0x64 | fDriveMaxFlatVel | **top speed** ⭐ |
| 0x6C | fBrakeForce | braking |
| 0x80 | fSteeringLock | max steering angle |
| 0x88 | fTractionCurveMax | **grip** ⭐ |
| 0x90 | fTractionCurveMin | grip at the limit |
| 0xBC | fSuspensionForce | suspension stiffness |
| 0xC8 / 0xCC | fSuspensionUpper / LowerLimit | travel |
| 0xD0 | fSuspensionRaise | **ride height** ⭐ (this project's b3788 work confirmed) |
| 0xD4 / 0xD8 | fSuspensionBiasFront / Rear | F/R bias |
| 0x158 | m_subHandlingData | `atArray<CBaseSubHandlingData*>` → car/bike/flying sub-structs |

**Camber/toe live in a SUB-struct, not here:** `CCarHandlingData` (reached via `m_subHandlingData`
@0x158) holds `fToeFront`@0x14, `fToeRear`@0x18, `fCamberFront`@0x1C, `fCamberRear`@0x20, `fCastor`@0x24.
The header also fully maps boat/bike/flying/submarine/trailer/seaplane/special-flight sub-structs (see
`HandlingInfo.h` if we ever tune those).

### B3. The catch (also from this project's experience)

`CHandlingData` is **shared by every vehicle of that model** — editing it changes all instances. For
per-car edits you clone the handling first (ikt's "Handling Replacement Library" pattern). STUDIES.md
§5 notes the same.

**→ Bridge mapping:** `read_float`/`write_float` at `handlingPtr + <offset>` using B1 to get
`handlingPtr` and B2 for the field. We already have `read_float`/`write_float`; the add is a
pattern-scan for `handlingOffset` so it survives patches. Sub-struct (camber) = deref the `atArray` at
+0x158, take element 0, add the `CCarHandlingData` field offset.

---

## Bottom line

- **Wheels:** copy VStancer's approach wholesale — 4 natives + parity mirroring + re-assert + your own
  JSON persistence. No memory, nothing to break on a patch. Best path for ExtendedLSC stance.
- **Handling:** use ikt's **pattern-scan-the-offset** discipline (not a hardcoded `+0x960`), then the
  verified field map. Remember it's model-shared — clone for per-car.
- **Meta-lesson proven:** reading *clean source* gave us exact, named, working call sequences in
  minutes — no Ghidra, no guessing. This is why the "harvest a reference first" rule pays off.
