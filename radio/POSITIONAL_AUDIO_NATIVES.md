# Positional / Muffled Radio - Native Reference

Goal (from user): while the car is ON, the radio keeps playing; from OUTSIDE the car it sounds
MUFFLED; with a DOOR OPEN it's slightly LOUDER; and it gets QUIETER the further you walk away.

## Architecture caveat (important)
`ClaudeRadio.cs` plays local files through **NAudio straight to the PC output device** and forces the
in-game radio to OFF (`SET_VEH_RADIO_STATION veh, "OFF"`). So GTA's own audio engine does NOT
spatialize our stream - there is no built-in muffle/falloff for it. We must compute the
gain + low-pass ourselves each tick from native data, and apply it in the NAudio DSP chain:
  - Gain  -> `reader.Volume` (already wired) scaled by a positional factor.
  - Muffle -> insert a low-pass filter (e.g. NAudio `BiQuadFilter` lowpass as an `ISampleProvider`)
              between the reader and the output; lower the cutoff as the player goes outside/away.
Right now `Effective()` = calibration trim x Music slider. We add a third multiplier: `posGain`,
and drive a `lowpassCutoff` alongside it.

## Natives to read each tick (all verified callable, Legacy + Enhanced)

### Is the car on? (keep playing while engine runs)
| Native | Sig | Returns | Use |
|--------|-----|---------|-----|
| `GET_IS_VEHICLE_ENGINE_RUNNING` | (vehicle) | BOOL | Keep audio alive when player exits but engine is still running |
| `IS_VEHICLE_RADIO_ON` | (vehicle) | BOOL | Sanity / parity with vanilla behavior |

### Inside vs outside (full fidelity vs muffled)
| Native | Sig | Returns | Use |
|--------|-----|---------|-----|
| `IS_PED_IN_VEHICLE` | (ped, vehicle, atGetIn) | BOOL | Inside = no muffle, full gain. Outside = apply falloff + low-pass |

### Distance (listener -> car) for volume falloff
| Native | Sig | Returns | Use |
|--------|-----|---------|-----|
| `GET_GAMEPLAY_CAM_COORD` | () | Vector3 | Listener/"ear" position (camera). Better than ped coords for 3rd person |
| `GET_ENTITY_COORDS` | (entity, alive) | Vector3 | Vehicle position. dist = `|cam - veh|` |

Falloff: `posGain = clamp01( 1 - (dist - dInner) / (dMax - dInner) )`. Suggested dInner ~2m
(full just outside), dMax ~18-25m (silent). Tune in-game.

### Door open = slightly louder + less muffled
| Native | Sig | Returns | Use |
|--------|-----|---------|-----|
| `GET_NUMBER_OF_VEHICLE_DOORS` | (vehicle) | int | Loop doors |
| `GET_VEHICLE_DOOR_ANGLE_RATIO` | (vehicle, doorId) | float 0..1 | PROPORTIONAL openness - best signal. Take max across doors -> `doorOpen01` |
| `IS_VEHICLE_DOOR_FULLY_OPEN` | (vehicle, doorId) | BOOL | Simpler boolean alt |
| `GET_IS_DOOR_VALID` | (vehicle, doorId) | BOOL | Skip invalid door ids |

Apply: `posGain *= 1 + 0.25 * doorOpen01` (cap at 1.0 inside); raise low-pass cutoff by `doorOpen01`.

### Windows (optional - broken/down window leaks more sound)
| Native | Sig | Returns | Use |
|--------|-----|---------|-----|
| `ARE_ALL_VEHICLE_WINDOWS_INTACT` | (vehicle) | BOOL | If false, reduce muffle a bit |
| `IS_VEHICLE_WINDOW_INTACT` | (vehicle, windowIndex) | BOOL | Per-window detail |

## Muffle model (low-pass cutoff)
- Inside vehicle: cutoff ~= full (e.g. 20000 Hz = bypass).
- Outside, doors shut: cutoff ~700-1200 Hz (heavy muffle, "thump through the door").
- Outside, door fully open: cutoff ~3000-5000 Hz (much clearer).
- Blend by `doorOpen01`; optionally also lower cutoff slightly with distance.

## Door id reference (GTA standard)
0 front-left, 1 front-right, 2 rear-left, 3 rear-right, 4 hood, 5 trunk, 6/7 extra.
For "a door is open" use doors 0-3 (and maybe 5) - ignore hood.

## GTA-native positional radio (alternative path - NOT what we use, for reference)
These spatialize GTA's OWN radio, not our NAudio stream. Could matter if we ever switch to driving a
real station via `SET_VEH_RADIO_STATION` instead of NAudio:
- `SET_VEHICLE_RADIO_LOUD` (vehicle, toggle) - makes a vehicle's radio audible from outside
- `SET_POSITIONED_PLAYER_VEHICLE_RADIO_EMITTER_ENABLED` (p0)
- `SET_RADIO_POSITION_AUDIO_MUTE` (p0)
- `SET_EMITTER_RADIO_STATION` (emitterName, radioStation, p2) - static world emitters (e.g. club speakers)

## Handle hygiene (from CLAUDE.md)
Get a FRESH vehicle handle each tick (`ped.CurrentVehicle` / last-known when on foot). Stale
handles crash. When the player is on foot, cache the last vehicle handle so we can still read its
coords/doors for the outside-the-car effect.
