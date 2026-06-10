"""
predecode.py - decode every audio .rel in the extracted tree to cached XML (hashes resolved),
so in-game deep-dive lookups are pure cache reads. Config rels (radio/game defs) go first.
Re-runnable: skips files already cached. Run from the project root.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server import gtadata as g

rels = g.find(r"\.rel$", limit=100000, regex=True)
# config rels (radio/game/ambient definitions) first, then the rest (sounds.dat54 etc.)
rels.sort(key=lambda p: (0 if "config" in p.lower() else 1, "sounds" in p.lower(), p.lower()))

print(f"[predecode] {len(rels)} .rel files", flush=True)
ok = skip = fail = 0
t0 = time.time()
for i, p in enumerate(rels, 1):
    cache = g._cache_path(p)
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(p):
        skip += 1
        continue
    r = g.decode(p)
    if r.get("ok"):
        ok += 1
    else:
        fail += 1
        print(f"[predecode]   FAIL {os.path.basename(p)}: {r.get('note')}", flush=True)
    if i % 20 == 0 or i == len(rels):
        print(f"[predecode] {i}/{len(rels)}  ok={ok} skip={skip} fail={fail}  {time.time()-t0:.0f}s", flush=True)

# report cache size
total = sum(os.path.getsize(os.path.join(g.XML_CACHE, f)) for f in os.listdir(g.XML_CACHE)) if os.path.isdir(g.XML_CACHE) else 0
print(f"[predecode] DONE ok={ok} skip={skip} fail={fail}  cache={total/1e6:.0f} MB in {g.XML_CACHE}", flush=True)
