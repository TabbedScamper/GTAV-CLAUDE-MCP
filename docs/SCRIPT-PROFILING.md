# Script & Performance Profiling (Layer 1 + ETW)

Diagnose what scripts are running and which mod is eating frames. Three complementary probes, because
"scripts" in GTA V means three different runtimes:

| Runtime | Examples | Probe |
|---|---|---|
| RAGE `.ysc` scripts | game scripts + add-on `.ysc` mods (carmod_shop, freemode, a trainer's `.ysc`) | **Layer 1** (`scripts_list`) |
| SHVDN C# scripts | ELSC, ClaudeChatUI, other `.dll` mods | profiler `managed/JIT` bucket; ETW per-assembly |
| ASI / native plugins | ScriptHookV, native `.asi` mods | profiler per-module; ETW |

## Layer 1 — live `.ysc` inventory  (`pyscript/re_tools_scripts.py`)

Read-only, paused-safe. Enumerates every live `GtaThread` (RAGE script thread) with its name, state,
thread id, and script hash.

- `scripts_list` — the inventory. Auto-locates the `GtaThread` vtable + name offset on first call
  (cached after), then lists every running script.
- `scripts_discover` — just the locate step, with the candidate scoring (for verification).
- `scripts_inspect {address}` — hexdump a thread struct to confirm the context sub-offsets in-game.
- `scripts_reset` — drop the cached vtable/offset and re-learn.

Each entry: `{name, known, address, thread_id, state, state_label, script_hash, program_counter}`.
`known=true` means the name matches a real `.ysc` (high confidence); `known=false` is an add-on
script or noise. `state_label` distinguishes `running` (ticking this frame) from idle/blocked.

### How it finds threads without a fabricated signature (the RE finding)
We do **not** hard-code a build-specific AOB for the thread collection (project rule). Instead it
learns the layout from the running process and validates every step:

1. Regex-scan the game heap for null-terminated lowercase identifiers; keep only those in the
   **known `.ysc` name whitelist** (`pyscript/ysc_script_names.txt`, 1100+ names harvested from the
   extracted game files). This is the decisive discriminator — a real `GtaThread.m_Name` is a known
   script name; look-alike rage objects (texture/index arrays) are not.
2. For each known-name buffer at address `S`, vote every 8-aligned `B` in `[S-0x300, S-0x10]` whose
   first qword points into `GTA5.exe` **and** whose `+0x10` context has a sane state — i.e. every
   thread-shaped struct. Every running thread votes for the same `(vtable, name_offset)` pair, so it
   dominates; unrelated member pointers scatter.
3. Enumerate every heap struct sharing that vtable; decode the name strictly at the learned offset.

**Verified (Legacy `GTA5.exe`, this session):** vtable found, `name_offset = 0x14C`; enumerated real
threads — `animal_controller, cellphone_controller, flow_controller, friends_controller,
social_controller, context_controller, family_scene_f0, ...`. The offset is **not hard-coded** — it
is re-learned per process, so this works on Enhanced too (names are stable across builds).
`state/thread_id` use the documented rage context offsets and are best-effort — confirm with
`scripts_inspect` and override via the `ctx_offset` / `name_offset` params if a build differs.

## Profiler — per-mod CPU  (`pyscript/re_tools_profile.py`)

In-process sampling profiler, dependency-free, crash-safe (suspends ONE native thread at a time,
reads its RIP, resumes immediately; never touches interpreter threads — that would deadlock the GIL).

- `module_ranges` — every loaded module with base+size (the address→mod map).
- `profile_cpu {seconds=5, hz=1000}` — samples for N seconds and attributes each RIP to the owning
  module. Returns CPU% per module, hot-first, plus a `managed/JIT (SHVDN)` bucket = **all** C# mods
  combined, and the busiest threads.

Native `.asi`/`.dll` mods attribute cleanly to their module. All SHVDN C# mods land in JIT code
outside any module range → they share the `managed/JIT` bucket. To split that per C# assembly, use ETW.

## ETW profiler — kernel-grade, per-assembly  (`tools/etw_profiler.py`)

Out-of-process (cannot perturb/crash the game), kernel sampling, and with the .NET provider it splits
managed time **per C# assembly** (ELSC vs LemonUI vs another SHVDN mod).

```
python tools/etw_profiler.py            # 15s capture of the running GTA
python tools/etw_profiler.py 30         # 30s
python tools/etw_profiler.py --modules  # just dump the module map (no admin)
```

- Always writes `tools/gta_modules.json` (the address→mod map; admin-free).
- Records a CPU `.etl` via PerfView (if `PerfView.exe` is in `tools/` or PATH) else built-in
  `wpr.exe`. **Needs an elevated terminal** for the capture.
- Open the `.etl` in PerfView/WPA → CPU Stacks → group by module/assembly. PerfView keeps .NET
  symbols so SHVDN mods split per assembly.

## Recommended flow to diagnose a frame-rate problem
1. `scripts_list` — is some script stuck `running` that shouldn't be? Any unexpected add-on `.ysc`?
2. `profile_cpu 5` — which module dominates? If it's `managed/JIT`, a C# mod is hot.
3. `python tools/etw_profiler.py 20` (elevated) — split the managed bucket per assembly to name the mod.
