# ClaudeBridge — Tool / Command Reference

Every command the in-game C# bridge (`ClaudeBridge.dll`) understands, over `127.0.0.1:27015`
(4-byte little-endian length prefix + JSON `{command, params}`; reply is the same framing,
`{result: ...}` or `{error: ...}`). Pure C#/SHVDN — works on GTA V **Legacy and Enhanced**, no PyLoaderV.

Ask the bridge itself for this list at runtime with **`commands`**.

---

## 🗨️ In-game chat (command Claude from inside GTA)
| Command | Params | Does |
|---|---|---|
| `queue_user_message` | `text` | enqueue a message (what F10 does) for the host to pick up |
| `get_pending_messages` | — | drain all queued user messages |
| `has_pending_messages` | — | are any queued? (non-draining) |
| `await_user_message` | `timeout_ms` | long-poll: block until a message or timeout (the host uses this) |
| `chat_post` | `message` | append an assistant line to the panel transcript |
| `get_chat_history` | `limit` | recent transcript lines (role/text/time) |
| `hud_message` | `message` | append a system line to the transcript |
| `set_overlay` | `text,state` | set an overlay string (searching/ok/error…) |
| `set_status` | `state` | host status the panel shows: `thinking`/`ready`/`error` |
| `panel` | `show` | open/close the in-game chat panel without a keypress |

## 🌍 World & player actions
| Command | Params | Does |
|---|---|---|
| `get_world_state` / `get_context` | — | player pos/heading/health/armor/wanted, vehicle, weather, time |
| `teleport` | `x,y,z` or `forward` | move the player (or their vehicle) |
| `heal` | — | full health + armor (+ repair current vehicle) |
| `set_invincible` | `on` | god mode on/off |
| `set_wanted` | `level` | set wanted stars 0–5 |
| `give_weapon` | `name,ammo` | give a weapon (e.g. `carbinerifle`, `rpg`) |
| `set_weather` | `weather` | e.g. `Clear`, `Thunder`, `ExtraSunny`, `Foggy` |
| `set_time` | `hour,minute` | set the in-game clock |
| `explosion` | `forward` | spawn an explosion in front of the player |

## 🚗 Vehicles
| Command | Params | Does |
|---|---|---|
| `spawn_vehicle` | `model,distance` | spawn a vehicle (deferred model-load), e.g. `adder`, `rhino` |
| `spawn_ped` | `model,distance` | spawn a ped |
| `enter_vehicle` | `handle?` | warp the player into the nearest (or given) vehicle |
| `is_in_vehicle` | — | bool |
| `get_vehicle_info` | — | name/plate/speed/health/engine of the current vehicle |
| `repair_vehicle` | — | repair + wash the current vehicle |
| `cur_veh` | — | the player's current vehicle handle |
| `nearby_vehicles` / `nearby_peds` | `radius` | nearest 20 with handles + distances |

## ⚙️ Natives (call ANY game native, safely)
| Command | Params | Does |
|---|---|---|
| `call_native_by_name` | `name,args,return_type` | call a native by SHVDN `Hash` name (auto-coerced args) |
| `call_native` | `hash,args,return_type` | call by raw hash (e.g. from `native_db.json`) |

`return_type`: `void`/`int`/`uint`/`long`/`float`/`bool`/`string`/`vector3`.

## 🧠 Memory — read / write / scan
| Command | Params | Does |
|---|---|---|
| `read` | `addr,type,count` | read memory (`int/uint/long/ptr/float/byte/bytes/floats`) |
| `write` | `addr,type,value` | write a data value |
| `entity_addr` / `veh_addr` | `handle` | resolve an entity/vehicle handle to its memory address |
| `scan` | `pattern,count` | AOB scan the main module (`48 8B ?? ...`) |
| `scan_mem` | `pattern,count,start,end` | AOB scan all committed memory (MAPPED/heap) |
| `module` | — | main module base/size/path |

## 🛠️ Memory editing (RE-grade, **reversible**)
| Command | Params | Does |
|---|---|---|
| `patch_bytes` | `addr,bytes` | write bytes over code (VirtualProtect + restore), logged for undo |
| `nop` | `addr,count` | overwrite N bytes with 0x90, reversible |
| `list_patches` | — | active patches + their original bytes |
| `restore_patch` | `addr` | undo one patch |
| `restore_all_patches` | — | undo all (also auto-done on reload) |
| `validate_address` | `addr,size` | is it readable? protect/state/region |
| `read_chain` | `base,offsets,type` | follow a pointer chain and read the final value |
| `resolve_rip_relative` | `addr,disp_offset,instruction_length` | decode a RIP-relative operand → absolute address |

## 🔍 Memory decoding — "what does this value do?" (no screen needed)
| Command | Params | Does |
|---|---|---|
| `snapshot` | `addr,size,label` | capture a region |
| `diff` | `label` | changed offsets since the snapshot (old/new as int + float) |
| `watch` | `addr,size,seconds,hz` | which offsets change over time + their float range (finds live values) |
| `correlate` | `addr,type,values,native,native_args` | write candidates → read a native → decode a field by its effect |

## 📊 Profiler — diagnose per-frame cost
| Command | Params | Does |
|---|---|---|
| `frametime` / `frametime_reset` | `window` | FPS / p95 / p99 / hitches |
| `scripts_managed` | — | every running SHVDN C# mod (name/file/state) |
| `scripts_perf` / `scripts_perf_reset` | — | per-script CPU ms/frame (always-on) |
| `natprof_start` / `natprof_stop` / `natprof_report` | `per_hash,top` | native-call counts per script (+ top hashes) |
| `methprof_start` / `methprof_stop` / `methprof_report` / `methprof_clear` | `targets,top` | per-method timing for any `Type:Method` |

## 🔧 Control / diagnostics
| Command | Params | Does |
|---|---|---|
| `ping` | — | `pong` (liveness) |
| `status` | — | bridge alive, edition (Legacy/Enhanced), queue/transcript counts |
| `commands` | — | this catalog, live |
| `profiler_status` | — | are the Harmony profiler patches active |
| `reload_scripts` | — | reload SHVDN scripts (posts Insert) — *avoid mid-session; can stick keyboard input* |
| `send_keys` | `keys` | post a key to the game window |
| `shvdn_introspect` | `types` | dump SHVDN runtime method signatures (RE helper) |

---

> Names/hashes for natives, vehicles, weapons, etc. are resolved PC-side via `pyscript/native_db.json`
> and the catalogs — keep using those to turn "police car" / "rpg" into the right model/weapon/hash.
