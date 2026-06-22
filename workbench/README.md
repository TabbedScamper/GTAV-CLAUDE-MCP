# workbench/ — dev / RE / mission-builder scratch (NOT the shipping product)

This folder holds code that is **not part of the player-facing GTAV-Claude-MCP bridge**: experiments,
reverse-engineering scratch, and the older "mission-builder" prototypes. It's kept for reference and
future mining, but it is **not** required to build or run the bridge.

- `mission-builder/` — heist/scenario/agent prototypes (`heist_beats.py`, `vault_heist_slice.py`,
  `lifeinvader_breakin.py`, `play_heist.py`, `play_agent.py`, `endless_missions.py`, `mission_runner.py`,
  `scenario_gen.py`, `claude_strategist.py`, `author_animation.py`, `scenarios/`, session reconstructions).
  These were an exploration of using the bridge to script full missions; the bridge itself is a *tool*,
  not a mission engine (see the project notes), so they live here, out of the product tree.
- `experimental/` — one-off spikes (e.g. `auto_drive`).

The shipping product is: `csharp_bridge/` (the in-game DLL), `mcp_server/` + `gtav_host.py` (PC-side
brain), `pyscript/` data (`native_db.json`, catalogs, recipes), and `tools/` build/RE utilities.
See the root `README.md` and `TOOLS.md`.
