# Tools Claude can drive to UNDERSTAND internals & INJECT assets/code

> Compiled 2026-06-12. External tools (beyond what the bridge already does in-process) that extend
> GTAV-CLAUDE-MCP, each rated on whether Claude can drive it **headlessly** and how it wires into the
> bridge. The bridge already owns: in-process memory R/W, native calls (allowlist), pattern-scan,
> RTTI/identify, capstone disasm, value-scan, code-patch/NOP, par-dump labels, **runtime RPF/DLC
> changeset reload**, and CodeWalker.Core `.rel`/`.meta` reads. Everything below ADDS to that.

## The headless "edit asset → hot-load" loop (the key architecture)
**CodeWalker.API (write the asset, over HTTP) → OpenRPF (mounts the mods RPF) → bridge
`reload_content_changeset` (live swap, no restart).** That chain is the whole "inject anything" story,
and every piece except OpenRPF is Claude-drivable.

---

## Tool map

### CodeWalker.API (flobros) — ⭐ integrate first
- **Does:** .NET 9 headless web API (port 5555, Swagger at `/`) wrapping CodeWalker.Core — search/extract/convert/import/replace RPF assets over HTTP.
- **Understand / Inject:** Both. **Claude-drivable: YES (pure HTTP).**
- **Repo/license:** https://github.com/flobros/CodeWalker.API — MIT (prebuilt releases).
- **Endpoints:** `GET /api/search-file?filename=` · `GET /api/download-files?fullPaths=&xml=true` (auto YDR/YTD→XML) · `POST /api/import` (XML/raw → RPF) · `POST /api/replace-file` · `GET /api/hash/jenkins?input=` · `POST /api/set-config` · `POST /api/reload-services` · `GET /api/service-status`.
- **Bridge wiring:** Claude does offline asset surgery over HTTP (`download-files?xml=true` → edit XML → `import`/`replace-file` into a mods RPF), then the bridge calls `reload_content_changeset` to hot-load it. CodeWalker.API = "build the asset," bridge = "load it live." Use `/api/hash/jenkins` to resolve archive/model hashes.

### GhidraMCP (LaurieWired) — ⭐ integrate for "understand"
- **Does:** Ghidra (NSA decompiler) exposed over MCP — autonomous decompile, list functions/imports/classes, rename, set types.
- **Understand. Claude-drivable: YES (native MCP, ~7.8k★).**
- **Repo/license:** https://github.com/LaurieWired/GhidraMCP — Apache-2.0. (Alt: starsong-consulting/GhydraMCP, REST.)
- **Bridge wiring:** Static↔dynamic loop. Ghidra recovers named functions + struct offsets from GTA5.exe/.asi/.dll; Claude feeds those to the bridge to pattern-scan/verify at runtime, read live values, and confirm patch sites before NOPing. Ghidra = "what/where," bridge = "prove it live." Stronger than the bridge's live capstone for whole-binary structure.

### Cheat Engine tables — NHA_GTA_CT (dr-NHA) — ⭐ integrate for durability
- **Does:** Actively-maintained GTA V table with auto offset/global update, RTTI classname resolution, script-global notation. Companion `GTAV-C-Source-Scanner` (headless CLI for globals/hashes).
- **Understand + Inject. Claude-drivable: PARTIAL — a `.CT` is XML Claude reads directly; the scanner is CLI.**
- **Repo/license:** https://github.com/dr-NHA/NHA_GTA_CT · https://github.com/dr-NHA/GTAV-C-Source-Scanner.
- **Bridge wiring:** Highest value as a **data source, not a runtime peer.** Claude parses the `.CT` XML for maintained AOB signatures + script-global offsets, hands them to the bridge's pattern-scan/memory-read → the toolkit auto-tracks game updates instead of breaking on patch day. The cheapest durability win.

### CodeWalker (dexyfex)
- **Does:** The canonical RAGE asset explorer/editor (RPF, YDR/YTD/YMAP/YCD/… ↔ XML, 3D map, Ped Viewer).
- **Both. Claude-drivable: PARTIAL** — GUI not headless, but CodeWalker.Core is the embeddable lib (already used) and the headless HTTP surface is CodeWalker.API above.
- **Repo/license:** https://github.com/dexyfex/CodeWalker — custom permissive (credit required).
- **Bridge wiring:** Upstream lib for CodeWalker.API; fills the write/convert gap (YDR↔XML, texture pack, YMAP edits) that feeds `reload_content_changeset`.

### OpenIV / OpenRPF (Enhanced)
- **Does:** RPF editor/installer + ASI loader. For **Enhanced**, **OpenRPF** (dsound.dll shim) replaces OpenIV.asi — loads ASI plugins and mounts modified/unencrypted RPFs from `mods/`.
- **Inject (infrastructure). Claude-drivable: NO** (load-time ASI / GUI).
- **URL:** openiv.com · OpenRPF on GTA5-Mods.
- **Bridge wiring:** Required runtime dependency, not a scripted tool — OpenRPF is what makes the bridge's hot-reload possible on Enhanced (lets `mods/` RPFs be mounted). Prefer OpenRPF over OpenIV.asi on Enhanced.

### Sollumz (Blender) + .NET pipeline
- **Does:** Blender add-on to import/edit/export GTA V models/collisions/anims/interiors (2025 builds: direct binary, Blender 4–5).
- **Inject (authoring new geometry — a board, prop, accessory). Claude-drivable: PARTIAL** — `blender --background --python` runs Sollumz operators headlessly.
- **Repo/license:** https://github.com/Sollumz/Sollumz — MIT. Bridge: flobros/codewalker_sollumz_bridge.
- **Bridge wiring:** Content-creation front end. Claude drives headless Blender+Sollumz to export a YDR/YFT → CodeWalker.API imports into a mods RPF → bridge hot-reloads. Promote to top tier the moment original models (not edits) are needed — e.g. a custom skateboard.

### ReClass.NET
- **Does:** Live struct/class reconstructor against a running process; exports C++ headers.
- **Understand. Claude-drivable: PARTIAL/NO** (GUI; `.rcnet`/headers are readable file formats).
- **Repo/license:** https://github.com/ReClassNET/ReClass.NET — MIT.
- **Bridge wiring:** Largely redundant with the bridge's own inspect/identify (RTTI + par-dump already give named layouts live, headlessly). Use only as an **import source** for classes RTTI doesn't fully label. Don't build a runtime dependency.

### Reference data: alexguirre lists, CodeWalker Ped Viewer, Pleb Masters Forge
- **Does:** alexguirre **animations-list** (every dict+clip, patch-versioned) + **rage-parser-dumps** (par struct layouts per build — the canonical par source). Forge = huge searchable hash/flag/object DB.
- **Understand. Claude-drivable: YES for the data** (static GitHub Pages — WebFetch + parse).
- **URLs:** https://alexguirre.github.io/animations-list/ · https://alexguirre.github.io/rage-parser-dumps/ · https://forge.plebmasters.de/.
- **Bridge wiring:** Name/hash resolution. Claude pulls the exact clip dict/clip or model name, Jenkins-hashes via CodeWalker.API, passes to the bridge native path. rage-parser-dumps cross-checks the bridge's live par-dump labels per build. (animations-list is the full superset of our `catalogs/anims.json` seed.)

### ysc decompilers (njames93) + decompiled corpora
- **Does:** Turn `.ysc` game scripts into readable code; pre-decompiled corpora to grep.
- **Understand (discover script globals/mission logic). Claude-drivable: YES** (CLI tool; corpora are plain source).
- **Repo:** https://github.com/njames93/GTA-V-Script-Decompiler · corpus https://github.com/YimMenu/GTA-V-Decompiled-Scripts.
- **Bridge wiring:** Closes "what does this script global mean." Grep the corpus to find which `Global_xxxxx` controls a feature → bridge resolves its live address (global-base + index, cross-checked vs NHA_GTA_CT) → read/write.

---

## TOP 3 to integrate next
1. **CodeWalker.API** — the only fully-headless HTTP *inject* path; pairs 1:1 with the bridge's `reload_content_changeset` for a complete restart-free "edit asset → hot-load" loop. MIT, lowest friction.
2. **NHA_GTA_CT (.CT parser) + GTAV-C-Source-Scanner** — read the maintained `.CT` XML for AOBs + globals so the toolkit *survives patch days* automatically. Cheapest durability win (data ingestion, no new process).
3. **GhidraMCP** — native MCP; whole-binary decompilation recovering functions/offsets the live disasm can't, forming a static↔dynamic loop with the bridge.

*Honorable mention:* headless **Blender+Sollumz** completes the create-new-geometry pipeline into CodeWalker.API — promote when original models (e.g. a skateboard) are needed.
