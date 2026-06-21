ExtendedLSC — Performance Capture Kit
=====================================

This tiny kit measures EXACTLY what ExtendedLSC is doing each frame on YOUR machine and writes a
text report you can send back, so the lag can be diagnosed instead of guessed at. It is read-only —
it does not change your game, your saves, or ExtendedLSC. Single-player only.

What's in this folder
---------------------
  run_capture.bat        <- double-click this
  Capture-ELSCPerf.ps1   (the script it runs)
  ClaudeBridge.dll       (the in-game profiler — install per step 1)
  0Harmony.dll           (its dependency — install with it)
  native_db.json         (so native names show up instead of hex numbers)
  README.txt             (this file)

Requirements
------------
You already have these if you run ExtendedLSC:
  - GTA V (Legacy build) with ScriptHookV + ScriptHookVDotNet 3 installed.

STEP 1 — install the profiler (one time)
----------------------------------------
Copy BOTH of these into your GTA V "scripts" folder (the same folder ExtendedLSC.dll is in):
      ClaudeBridge.dll
      0Harmony.dll
Typical path:  ...\Grand Theft Auto V\scripts\

STEP 2 — load it
----------------
Launch GTA V in Story Mode (offline). The profiler loads automatically.
(If the game was already running, press Insert to reload scripts.)

STEP 3 — reproduce the lag, then capture
----------------------------------------
1. Get into the exact situation where your FPS tanks (driving around, the LSC menu open,
   heavy traffic — whatever makes it bad for you).
2. While it's lagging, ALT-TAB out (leave the game running) and double-click  run_capture.bat
3. Follow the prompt. It captures for about 40 seconds — during that time, ALT-TAB BACK into the
   game and keep playing in the laggy state so it measures the real cost. (You can watch the
   countdown in the black window; just be in-game while it counts.)
4. When it finishes it writes  ELSC_perf_report.txt  in this folder.

STEP 4 — send it
----------------
Send  ELSC_perf_report.txt  to the mod author. That's it. Thank you!

Privacy: the report contains your CPU/GPU/RAM model, OS version, your installed GTA script/.asi
file names, and the performance numbers. No personal files, accounts, or save data.

Troubleshooting
---------------
"Could not connect to the profiler ...":
  - GTA isn't running, OR ClaudeBridge.dll + 0Harmony.dll aren't in the scripts\ folder,
    OR scripts haven't loaded yet. Make sure you're in Story Mode, then press Insert and retry.
"running scripts is disabled on this system":
  - Use run_capture.bat (it bypasses that). Don't run the .ps1 directly.
