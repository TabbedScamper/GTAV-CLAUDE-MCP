# GTAV-Claude-MCP — How to use it

Once installed (see `INSTALL.md`) and the bridge is loaded (**F9** in-game), you drive everything by
**talking to Claude** — either in the in-game **F10** panel or in your Claude client. You don't write
code; you ask for outcomes.

---

## The mental model
```
You  →  Claude  →  MCP tools  →  socket 127.0.0.1:27015  →  bridge in GTA  →  the game
```
Claude is set up as a **GTA coding genius**: it grounds every action in verified, harvested techniques
(the `Examples/PATTERNS` library + `recipe_search`), calls natives **by name** (never a raw hash, which
would crash), validates memory before touching it, and tells you what's **verified** vs **needs an
in-game test**.

---

## Just talk to it
Examples of things to say (in the F10 panel or your Claude client):
- *"Where am I? What's around me?"* — Claude reads live world state.
- *"Give me a fully-tuned Adder and put it in front of me."*
- *"Spawn two friendly bodyguards that follow me."*
- *"Make it night and start raining."*
- *"Play some synthwave on Claude FM."* — fetches, loudness-matches, and indexes the track.

## Slash commands (Claude Code)
These ship in `.claude/` and work the moment you open the project:
- **`/make <thing>`** — build something in-game, grounded + crash-safe. *e.g.* `/make a getaway car
  idling outside the Fleeca bank`.
- **`/endless <theme or place>`** — generate grounded, varied content/scenarios from real landmarks.
  *e.g.* `/endless a heist around Vinewood`.
- **`/harvest <technique>`** — when something isn't in the pattern library yet, Claude studies real
  reference mods and distills the technique before building.
- **`/crash-audit <code/feature>`** — safety review for game-crash / memory-corruption risks.

## What Claude can do for you
- **Read/write live game state** — position, health, weather, time, nearby entities, vehicle stats.
- **Spawn & compose** — vehicles, peds, props, with AI tasks (follow, guard, drive, attack).
- **Build scenarios** — chases, heists, ambushes, races, assembled from verified mission/objective
  patterns and placed at real coordinates (no pop-in; cops via the wanted system).
- **Reverse-engineer safely** — pattern-scan, identify structures, patch bytes (with an undo registry).
- **Run Claude FM** — your own music as an in-game radio station.

---

## Good habits
- **Believe the honesty flags.** When Claude says a piece "needs an in-game test," test it before
  trusting it — live natives/memory can't be fully verified offline.
- **Start small, then compose.** Ask for one vehicle, confirm it worked, then ask for the whole scene.
- **It gets better as you use it.** Every `/harvest` widens the library, so the set of things Claude
  can reliably build keeps growing — that's the "endless" part.
- **Single-player, offline, only.** Never in GTA Online.

## If something doesn't respond
1. Is GTA running and did you press **F9**?
2. In your Claude client, ask it to read the player position — that pings the whole chain.
3. See **Troubleshooting** in `INSTALL.md`.
