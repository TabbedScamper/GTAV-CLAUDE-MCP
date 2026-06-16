# Examples — reference-mod harvesting & decompilation

> **The rule:** when we're about to build *anything*, first go find mods that already do
> something similar, pull them in here, decompile them to readable source, and **read the real
> code** instead of guessing native sequences, arg orders, or offsets. Real shipped mods are the
> best documentation that exists for GTA V — every native call in them is proven to work.

This folder is the project's **reference library of other people's mods**, decompiled into source we
can read. It is **machine-local and git-ignored** — see "Legal / git" below. Nothing here ships.

---

## When to use this (the trigger)

Before implementing a feature, ask: *"has someone already shipped a mod that does this?"* If yes
(almost always), harvest 1–3 of the closest examples **first**. Examples:

| If we're building… | Search for… |
|---|---|
| wheel fitment / stance | "vstancer", "stance", "wheel offset" |
| live handling tuning | "handling editor", "real time handling" |
| a scene/spawn system | "menyoo", "object spooner", "trainer spawn" |
| particles / FX | "ptfx", "particle", "vfx" |
| a custom HUD | "customsprite", "scaleform hud", "nativeui" |
| radio / audio | "custom radio", "add-on radio", "naudio" |
| anything LSC / mod-shop | "extended los santos customs", "vehicle mod menu" |

## Where to look (priority order — best reference value first)

1. **GitHub, source-available** — the gold standard. Full source, license visible, history.
   Search `github.com` for the topic + `gta5` / `scripthookvdotnet` / `fivem`.
2. **gta5-mods.com** — biggest catalog. Most *script* mods are **SHVDN .NET DLLs** that decompile
   back to near-perfect C# (see below). Many also link their GitHub source — prefer that link.
3. **FiveM / cfx forums, gist, gitlab** — Lua/JS resources are plain source already.

## What each file type decompiles to, and with what

| You downloaded | It is | Decompile with | Result |
|---|---|---|---|
| `.dll` (SHVDN script) | .NET / C# | **ILSpy** (`ilspycmd`) | **near-perfect C#** ⭐ best |
| `.asi` / native `.dll` | C++ (ScriptHookV) | **Ghidra headless** | decompiled C (lossy) |
| `.lua` `.js` `.ts` | source already | — (just read) | original source ⭐ |
| `.cs` `.vb` | source already | — | original source ⭐ |
| `.xml` `.ini` `.meta` | config | — | read directly |
| `.rpf` bundled assets | RAGE archive | CodeWalker.Core / GTAUtil | extract → XML |

**Why SHVDN DLLs are a goldmine:** they're managed .NET, so ILSpy reconstructs the original C# almost
exactly — variable names, native call sequences (`Function.Call(Hash.CREATE_VEHICLE, …)`), the works.
That's a *proven, working* native sequence you can transcode straight to a bridge `call_native(...)`.

## The workflow

```
1. Identify the closest 1–3 mods (prefer source-available GitHub).
2. Download the archive into  Examples/_sources/<modname>/   (keep the original zip + a SOURCE.txt:
   URL, author, license, date, why we grabbed it).
3. Decompile into            Examples/_decompiled/<modname>/
      - SHVDN .dll :  ilspycmd <file>.dll -o Examples/_decompiled/<modname>
      - .asi/.dll  :  Ghidra analyzeHeadless ... (see RE-TOOLKIT-ADVANCED.md)
      - source mods:  just copy the source in
4. Read the decompiled source for the exact native calls / offsets / arg orders we need.
5. Record the reusable finding in the relevant project doc (DISCOVERIES.md, STUDIES.md, a runbook)
   with attribution — NOT a copy-paste of their code into ours.
```

Tools: **ILSpy CLI** — `dotnet tool install -g ilspycmd` (cross-platform .NET decompiler).
**Ghidra headless** — for `.asi`/C++ (see RE-TOOLKIT-ADVANCED.md). **CodeWalker.Core / GTAUtil** —
for any `.rpf` assets bundled with a mod.

## `manifest.json`

Keep `Examples/manifest.json` as the index of what's been harvested: one entry per mod with
`name, url, author, license, type, decompiled_path, why, date, findings` — so we never re-download
the same reference twice and can cite where a technique came from.

---

## Legal / git — READ THIS

- **Everything under `_sources/` and `_decompiled/` is git-ignored and never pushed.** These are
  other people's copyrighted mods; decompiling for *private study/interop* is one thing, but
  **republishing** their source (even decompiled) in our public repo is not ours to do.
- **We extract *techniques and knowledge*, not code.** The output that lands in our repo is our own
  implementation plus a documented finding ("mod X calls `SET_VEHICLE_WHEEL_X_OFFSET` for track
  width — see STUDIES.md §5"), *with attribution* — never their files copied in.
- Respect each mod's license. Record it in `manifest.json`. If a license forbids reverse
  engineering and we only need behavior, prefer reading its docs/observing it in-game instead.
- This is single-player, personal study. Keep it that way.
