# 17 — HKHModHelper toolkit + 13 reusable hacking minigames

Mined from **HKHModHelperNew.dll** (~52k lines — the shared library EVERY HKH191 mod depends on) +
**Hacking Minigames in SP** (~28k lines). 271 public helpers; the headline is that the HARD multi-call UI
sequences (mission-pass/heist screens, scaleform, the 13 GTAO hacking minigames) are pre-solved. Re-decompile
from `Examples/_sources/HKH191/*/scripts/HKHModHelperNew.dll`.

> For us: these are C#-side capabilities (scaleform + minigames need the SHVDN context — PATTERNS/15). The
> heist-interaction module (HeistInteractions.cs) can call these to add real hacking beats + real win/lose
> screens. Completion contract below is the integration glue.

---

### Real mission-pass / heist-complete / heist-failed scaleform screens (one call each) ⭐
**Category:** hud
**Problem:** The authentic R* "Mission Passed" / "HEIST COMPLETE — $X — GOLD" / "HEIST FAILED" end screens.
**Method:** HKHModHelper wraps the multi-scaleform composition:
`ShowMissionName(header, text, walltype, wallcolour, uptoptext)` = the mission-complete stat-wall screen
(stateful 3-stage); `HeistCompleteScaleform(heistName, medal, take)` = heist pass (medal GOLD/SILVER/BRONZE,
take=$); `HeistFailedScaleform(heistName)` = fail screen; `MissionName_RequestMissionPassScaleform()` preloads
the 3 celebration movies; `PlayAnimPostFX("RAMPAGE_PASSED", dur, looped)` adds the green flash; `HudandRadar
(hud,radar)` toggles HUD during the screen. Backed by generic scaleform helpers: `RequestScaleform`/
`HasScaleformMovieLoaded`/`BeginScaleformMovieMethod`/`ScaleformMovieMethodAddParam*`/`EndScaleformMovieMethod`
(+ return-value polling) and `DrawScaleformMovieFullscreen`.
**Gotcha:** scaleform is a precise state machine with NO error on a wrong sequence (silent fail) — use the
wrappers, don't hand-roll. These render correctly because they run in SHVDN (script-thread context), same
reason our objective text moved to C# (PATTERNS/15).
**Source:** HKHModHelper ShowMissionName L10624, HeistComplete L11138, HeistFailed L11269, postfx L11360

### 13 GTAO hacking minigames as drop-in beats — real scaleform UI + a clean completion contract ⭐
**Category:** interaction
**Problem:** Player-driven hacking beats (drill/keypad/fingerprint/voltage/beam/circuit) with real R* UIs.
**Method:** Each is one entry call that drives the actual GTAO minigame: `RunFingerPrintHack` (casino dots),
`RunKeypadHack`, `RunCopyPrintHack` (Cayo fingerprint clone, BINK video), `RunVoltageHack` (Cayo voltage,
BINK intro + `mpisland_voltage`), `RunCircuitHack` (Fleeca snake via the real **HACKING_MESSAGE** scaleform),
`RunBeamHack` (Doomsday laser, `MPBeamHack_lvl1-5`), `RunHotwireHack`, `RunPCBHack`, `RunPasscodeHack`,
`RunBruteforceHack_Numbers/_Password/_Both`, `RunDataCrack`, `RunFIBHack`, `RunSecuroServPhoneHack`.
**Completion contract (uniform):** the entry call sets a start flag (e.g. `CircuitHack=1`); the helper's tick
loop runs it; on finish it sets `HackFinishedID` = 1 (success) / 2 (partial) / 3 (fail). Poll
`IsHackSuccessful()` (HackFinishedID==1) then `RefreshHackFinishedID()` to reset. Buttons via the real
`instructional_buttons` scaleform (CLEAR_ALL → SET_DATA_SLOT(i, GET_CONTROL_INSTRUCTIONAL_BUTTONS_STRING…) →
DRAW_INSTRUCTIONAL_BUTTONS). Pass/fail via `HACKING_MESSAGE` `SET_DISPLAY`(type, title, subtitle, R,G,B,
showSuccess) — success green (45,203,134) "CIRCUIT COMPLETE", fail red (188,49,43).
**Gotcha:** some need an audio bank first (`RequestScriptAudioBank("DLC_HEIST_HACKING_SNAKE_SOUNDS"…)`) +
texture dicts (`mporderunlock`, `mphackinggame`, `MPCircuitHack`); the helper handles most. Drive these from
the C# beat (HeistInteractions.cs) — kind `minigame` → call Run*, watch HackFinishedID, report done. Controls:
172/173 up/down, 174/175 left/right, 201 select.
**Source:** HackingMinigames RunCircuitHack L9857 + HACKING_MESSAGE L1466-1599; CopyPrint L9248; Voltage L9049;
contract IsHackSuccessful L321; HKHModHelper minigame wrappers L22142-23959

### Money / funds with optional Dirty-Money integration (graceful mod detection)
**Category:** reward
**Problem:** Pay/charge the player, supporting the Dirty-Money mod if present, else vanilla.
**Method:** `AddFunds(int/float)`, `RemoveFunds(int/float)` (RemoveFunds pops an account-vs-dirty choice),
`AreFundsHigher/Lower/Equal(value)` checks; Dirty-Money path via `AddDirtyMoney`/`DirtyMoneyWalletValue`/
`DeductDirtyMoney`, gated on `File.Exists("scripts/Dirty Money.dll")`.
**Gotcha:** the "feature-detect an optional mod by DLL presence, fall back gracefully" pattern is reusable for
ANY integration (our manual-transmission / VStancer tie-ins do the same).
**Source:** HKHModHelper funds L40727-41137

### The reusable utility belt (streaming, control, geometry, attach, audio, player-lock)
**Category:** shared-base
**Problem:** The everyday building blocks every mission/heist needs, pre-wrapped with the right native order.
**Method:** Streaming: `RequestModel(string/VehicleHash/PedHash/int/uint)` (polls until loaded),
`LoadTexureDict`/`SetStreamedTextureDictAsNoLongerNeeded`, `RequestScriptAudioBank`. Control: `IsControlJust
Pressed`/`IsControlPressed`/`GetControlNormal`/`DisableControlAction` (Control or int). Geometry:
`GetVector2Offset(coord, heading, offset)`, `GetDistance`/`GetDistance_VDIST2(a,b,sign,radius)`,
`RotationToDirection`, 2D vector + AABB/ray helpers (`Intersects`, `DO_RECTANGLES_OVERLAP`,
`IsBeamIntersectingEdge`). Attach: `ATTACH_ENTITY_TO_ENTITY(e1,e2,bone, pos,rot, flags…)` (Vector3 overload).
Audio scenes: `StartAudioScene`/`StopAudioScene(s)`/`IsAudioSceneActive`, `PlaySoundFromCoord`/`Frontend`.
Player: `SetPlayerControl(hasControl, PlayerControlFlags)` (SPC_* flag combos). HUD: `DisplayHelpTextThisFrame
(msg, formal)`, `TextNotification(avatar,author,title,msg)`, `DrawRect`/`DrawSprite`, `ShowTimeLeft`/`ShowLives`.
**Gotcha:** `DisplayHelpTextThisFrame(msg, formal:true)` is the mission-styled help; `GetDistance_VDIST2` takes
a comparison sign string ("<"/">") + radius and returns a bool (squared-distance, cheap). Mirror these as our
canonical wrappers so generated beats use consistent, correct native ordering.
**Source:** HKHModHelper L436-12154 (streaming/control/geometry/attach/audio/player) — full catalog in FINDINGS
