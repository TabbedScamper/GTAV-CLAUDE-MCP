"""
gen_catalogs.py — build name->hash catalogs from ScriptHookVDotNet's public hash enums.

Source: Examples/_sources/shvdn-api/source/scripting_v2/GTA.Native/{VehicleHash,PedHash,WeaponHash}.cs
(zlib-licensed; the hashes are public JOAAT values). Output is OUR derived name->hash table — the
catalog JSON is committed; the SHVDN clone is git-ignored. Re-run after re-cloning to refresh.

    python tools/gen_catalogs.py

Writes pyscript/catalogs/{vehicles,peds,weapons}.json  (each: {"name_lower": hash_uint, ...})
plus catalogs/aliases.json (hand-curated natural-language -> canonical name) which is NOT regenerated.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
SHVDN = os.path.join(PROJ, "Examples", "_sources", "shvdn-api", "source", "scripting_v2", "GTA.Native")
OUT = os.path.join(PROJ, "pyscript", "catalogs")

# `        Adder = 3078201489u,`  or  `Name = unchecked((int)0x...)` — capture name + numeric value.
_DEC = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)u?\s*,")
_HEX = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:unchecked\(\(int\)\s*)?(0x[0-9A-Fa-f]+)")

def parse_enum(path):
    out = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _DEC.match(line)
            if m:
                out[m.group(1).lower()] = int(m.group(2)) & 0xFFFFFFFF
                continue
            m = _HEX.match(line)
            if m:
                out[m.group(1).lower()] = int(m.group(2), 16) & 0xFFFFFFFF
    return out

def main():
    os.makedirs(OUT, exist_ok=True)
    summary = {}
    for kind, fname in (("vehicles", "VehicleHash.cs"), ("peds", "PedHash.cs"), ("weapons", "WeaponHash.cs")):
        src = os.path.join(SHVDN, fname)
        if not os.path.exists(src):
            print(f"SKIP {kind}: {src} not found (clone the SHVDN repo first)"); continue
        data = parse_enum(src)
        data.pop("invalid", None)  # drop the enum's sentinel
        with open(os.path.join(OUT, f"{kind}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=0, sort_keys=True)
        summary[kind] = len(data)
        print(f"{kind}: {len(data)} -> catalogs/{kind}.json")
    print("done:", summary)

if __name__ == "__main__":
    main()
