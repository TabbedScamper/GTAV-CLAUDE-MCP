# Play and watch — the real-time agent

> "Tell me to play and just watch." The full loop on top of world-sensing: Claude sets goals, GTA's task
> system executes at 60fps, and a reflex policy keeps it responsive without an LLM in the per-frame path.
> Built to the **Hierarchical Language Agent (HLA)** pattern from current real-time-LLM-agent research.

## The three tiers (why it's fast)
The hard problem is latency: Claude thinks in seconds, the game runs at 60fps. The fix is hierarchy —
Claude is the strategist, **not** the reflexes (this is HLA: Slow Mind / Fast Mind / Executor, reported
"an order of magnitude faster" than putting the LLM in the loop):

| Tier | Who | Rate | Role |
|---|---|---|---|
| **Executor** | GTA's own task system + `pyscript/agent_actions.py` verbs | 60fps | runs the current verb frame-accurately. We issue a task once; the engine executes it. We never touch the per-frame loop. |
| **Fast Mind (reflex)** | `RuleDecider` in `tools/play_agent.py` | ~2 Hz, **no LLM** | picks a sensible verb from world-state instantly — flee when hurt, engage threats, head to the goal, else cruise/wander. Handles safety + continuity + the common case with zero tokens. |
| **Slow Mind (strategist)** | `ClaudeDecider` → your Agent SDK | on events / cooldown | sets the high-level GOAL for novel situations. Called rarely, so it never bottlenecks the action. |

## Performance techniques (from the research, applied here)
- **Reflex/cache policy** — most ticks need no LLM; the rule policy resolves them instantly. (Cache-driven
  async planning reports ~65% latency / ~50% token reduction from exactly this.)
- **Event-driven escalation** — Claude is invoked only on a *significant event* (`took_damage`,
  `wanted_changed`, `new_threat`, `threat_closing`, `exited_vehicle`) or after a cooldown — never per tick.
- **Thinking while moving** — the Executor keeps running the standing task while the Slow Mind deliberates,
  so inference latency is hidden inside the action time (no freeze waiting on a decision).
- **Intent dedup, two layers** — `play_agent` skips the bridge round-trip when the verb is unchanged
  (client-side), and `agent_actions.act` no-ops a duplicate task (bridge-side). No task spam, minimal IPC.
- **Off-thread sensing** — memory-based world reads run on the bridge's socket thread, so sensing doesn't
  stall the game tick.

## The loop (`tools/play_agent.py`)
```
each tick (~2 Hz):
  state  = bridge.get_world_state()        # the semantic snapshot (world_sense)
  events = bridge.world_events()           # deltas since last tick
  if significant(events) or cooldown:      # SLOW MIND — rare
      goal = ClaudeDecider.revise_goal(state, events, goal, verbs)
  verb  = RuleDecider.decide(state, events, goal)   # FAST MIND — every tick, instant
  if verb changed: bridge.act(verb)        # EXECUTOR — engine runs it at 60fps
  narrate(verb, reason, state.summary)     # "watch"
```

## What's built vs what you wire
- **Self-tested offline** (`python tools/play_agent.py --self-test`, `python pyscript/agent_actions.py`):
  the reflex policy, the loop, the dedup, the event-driven Claude cadence, and the verb dispatch / intent
  state / arg-building. The whole control structure is verified.
- **Runs in-game once the bridge is loaded** (F9): `agent_actions` verbs + `world_sense` sensing. Acting
  is gated behind `--go` (default dry-run narrates decisions, issues nothing). Verbs call task natives by
  name through the allowlist (wrong name → safe error, never a crash) — **verify they drive the player as
  expected in-game.**
- **The real Slow Mind is WIRED** (`tools/claude_strategist.py`): a persistent `ClaudeSDKClient` on a
  background loop (same Agent SDK mechanism as `gtav_host.py`), exposed as a sync `decide_goal(...)` the
  loop calls on events/cooldown. Claude gets READ-ONLY sensing tools (it can look closer) but NOT the
  action verbs — acting stays in the Executor, so the strategist only steers. The prompt-build + goal-parse
  are self-tested; the SDK call needs the Agent SDK installed + `claude /login` (home machine).

## Usage
```
# Watch the reflex agent reason (dry-run, narrate only — safe, needs GTA+bridge for real state):
python tools/play_agent.py --goto -75 -818 44        # head to Maze Bank, react to threats en route
python tools/play_agent.py                            # freeplay: cruise/wander, fight when attacked

# Let it actually drive the player:
python tools/play_agent.py --goto -75 -818 44 --go

# Full HLA: Claude sets the goals (Slow Mind) + reflex executes (needs Agent SDK + claude /login):
python tools/play_agent.py --claude --go             # Claude decides where to go; reflex keeps it alive
```

## Mission-objective sensing (`pyscript/mission_sense.py`)
Turns "wander" into "pursue the objective". It reads the game's **blips** to find where the player is
directed: `DOES_BLIP_HAVE_GPS_ROUTE` finds the routed destination (the prize), plus the user waypoint and
hostile target blips, via verified natives (`GET_FIRST/NEXT_BLIP_INFO_ID`, `GET_BLIP_COORDS`,
`GET_BLIP_INFO_ID_ENTITY_INDEX`, `GET_MISSION_FLAG`). `get_world_state` now carries an `objective`
{kind, coords, entity, dist, compass}, and the reflex policy pursues it: an **enemy** objective → `engage`
its entity; a **destination** objective → `drive_to`/`walk_to` its coords. This unlocks the SIMPLE story
beats (reach-the-marker / go-to / kill-the-target). It reads WHERE to go, not the objective TEXT — so
timed/stealth/QTE/cutscene beats are still out of reach (those need HUD/vision + finer skills).

## Build-and-play: the combined launcher (`tools/play_heist.py`)
"Claude, build a bank heist and play through it." This is the **Director-Actor** loop (IBSEN director-actor
/ Left-4-Dead AI-Director pattern) — one tick loop, two roles, one shared world read:
- **Director (game master)** = `mission_runner.MissionRunner` — runs the scenario (objectives, routed blips,
  the timer that **calls the cops on timeout**), judges completion/pass/fail, and **logs each run** (learning).
- **Actor (player)** = `play_agent.RuleDecider` (+ optional Claude) — pursues the director's CURRENT objective
  as its goal; the reflex handles threats/safety. (`stop`/hold during a grab beat.)

The director's current objective is fed straight to the actor (no dependence on blip presentation), so
*build* (scenario + director) and *play* (actor) run as one watchable loop; after each run it prints the
`review()` — the telemetry that makes it smarter. Grounded in the real M8T heist framework (`PATTERNS/12`).
```
python tools/play_heist.py bank_heist           # dry-run: watch the director + player reason
python tools/play_heist.py bank_heist --go       # actually run + play it in-game
python tools/play_heist.py bank_heist --go --claude   # + Claude Slow Mind for adaptation
python tools/mission_runner.py bank_heist --review    # what it has learned across runs
```

## Status in the agent stack
1. **Sense** — `world_sense` (get_world_state / world_events).  ✅ built
2. **Act** — `agent_actions` verbs (Executor over GTA tasks).  ✅ built
3. **Loop** — `play_agent` (HLA: reflex + event-driven strategist).  ✅ built
4. **Strategist** — `claude_strategist` wires the Claude Slow Mind via the Agent SDK.  ✅ built (`--claude`)
5. **Objective sensing** — `mission_sense` (blip-based); reflex pursues it.  ✅ built → simple missions reachable
6. **Enrich** — objective TEXT via HUD/vision, finer mission skills + state machine, CodeWalker map import
   for routing, out-param natives (raycast/road-node/zone).  ○ later (the path to harder missions)

So "tell me to play and watch" is complete and tested end-to-end: Claude sets goals, the reflex keeps it
alive AND pursues the game's objective, GTA's tasks execute at 60fps. Free-roam + simple go-to/kill
objectives are in reach; full scripted/timed/stealth missions need the enrichment in step 6.
