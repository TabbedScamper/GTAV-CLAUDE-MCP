# world_data — the spatial database for world-sensing

Files here feed `world_sense.py` so Claude can say *where* it is in human terms and place things sensibly.

- **`landmarks.json`** — named POIs (banks, customs, hospitals, landmarks) with coords. The seed is
  APPROXIMATE; the authoritative way to populate it is a **CodeWalker map export** (below).
- **`roadgraph.json`** *(optional, future)* — road/path nodes for offline route planning. In-game you
  can already get the nearest road node via the native (`nearest_road_node`); this file is for planning
  without the game running.

## Populate/expand from CodeWalker (the map IS in the game — pull it out)
CodeWalker can lay out the whole map and export its data; that's the real source for accurate landmarks
+ the road network:

1. **POIs / placements (landmarks):** In CodeWalker, browse the world or the YMAP entities; the 3D map
   view lists labelled locations. Export the entities you want (name + position) and convert to the
   `landmarks.json` shape: `{"name","kind","x","y","z"}`. (Or script it via **CodeWalker.Core** /
   **CodeWalker.API** — search/extract the relevant YMAPs, read entity positions, dump to JSON.)
2. **Road network (roadgraph):** GTA's vehicle path nodes live in `.ynd` files. CodeWalker reads/edits
   `.ynd` (node positions + links). Export the node graph → `roadgraph.json` for offline "what road is
   near / route from A to B". (In-game, `GET_CLOSEST_VEHICLE_NODE_WITH_HEADING` gives the nearest drivable
   node directly — that's the on-road-spawn fix; the exported graph is only needed for offline planning.)
3. **Zones:** GTA's zone names come from the game's `GET_NAME_OF_ZONE` native at runtime — no export
   needed; `world_sense` reads them live (if the bridge supports string returns).

## Why landmarks are only an enrichment
`world_sense` is authoritative from the game itself: exact coords (memory), exact heading/health (natives),
exact zone (native), and the nearest drivable road node (native). Landmarks just turn coords into a
friendly phrase ("~40m NE of Maze Bank"). So an approximate seed is fine — and gets better as you import
real CodeWalker data. The bearing/distance/compass math is exact regardless.
