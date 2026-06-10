"""
re_tools_pardump.py  -  PARSER-DUMP IMPORTER ("the Rosetta Stone")   [Tier A knowledge layer]
================================================================================================
Ingests alexguirre/rage-parser-dumps JSON (MIT) -> a flat (structHash, offset) -> field-name map so
`inspect` labels real field names (CHandlingData.fInitialDriveForce, etc.) instead of anonymous slots.
Also yields every struct/field NAME to seed the JOAAT dictionary for free.

USE (on the home machine):
  1. Download the JSON-tree dump for YOUR build from
       https://alexguirre.github.io/rage-parser-dumps/   (game=gta5, build=<your GTA5.exe build>)
     -- the JSON, NOT the HTML (HTML omits offsets). Save as e.g. par_gta5_<build>.json
  2. Pre-flatten once:   python re_tools_pardump.py par_gta5_<build>.json par_index.json
  3. Wire into bridge.py (after COMMANDS):
       import re_tools_pardump as pardump
       pardump.load_index("par_index.json")          # or load_pardump() on the raw dump
       # in handle_inspect, when a struct hint / class is known:
       #   label = pardump.label(struct_hash_or_name, offset)
     and seed JOAAT:  for n in pardump.NAMES: _joaat_dict_add(n)   # if you keep a name dict

Robust to schema variants (key casing, list vs {structures:[...]}, nested children).
"""
import json
import os

# ---- JOAAT (Jenkins one-at-a-time over the lowercased string) ----
def joaat(s: str) -> int:
    h = 0
    for c in s.lower():
        h = (h + ord(c)) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= (h >> 6)
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= (h >> 11)
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h

# module-level loaded index (populated by load_pardump / load_index)
INDEX = {}     # "0xHASH" -> {"name": str, "fields": {"0xOFF": {name,type,size}}}
BY_NAME = {}   # lower-name -> "0xHASH"
NAMES = set()  # all struct + field names (for JOAAT seeding)
ENUMS = {}     # lower-enumname -> {"_name": str, "values": {int_value: member_name}}

def _first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default

def _to_int(v):
    if isinstance(v, str):
        return int(v, 16) if v.lower().startswith("0x") else int(v)
    return int(v)

def _extract_structs(data):
    """Yield struct dicts from the various shapes the dump can take."""
    if isinstance(data, list):
        src = data
    elif isinstance(data, dict):
        src = (_first(data, "structures", "structs", "types", "Structures")
               or list(data.values()))
    else:
        return
    for s in src:
        if not isinstance(s, dict):
            continue
        yield s
        for child in (_first(s, "children", "Children", default=[]) or []):
            if isinstance(child, dict):
                yield child

def load_pardump(path):
    """Parse a raw par-dump JSON into the module index. Returns a summary dict."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    INDEX.clear(); BY_NAME.clear(); NAMES.clear(); ENUMS.clear()
    nfields = 0
    for s in _extract_structs(data):
        name = _first(s, "name", "Name")
        if not name:
            continue
        h = _first(s, "hash", "Hash")
        h = _to_int(h) if h is not None else joaat(name)
        hx = f"0x{h:X}"
        NAMES.add(name)
        fields = _first(s, "fields", "Fields", "members", "Members", default=[]) or []
        fmap = {}
        for fl in fields:
            fname = _first(fl, "name", "Name")
            off = _first(fl, "offset", "Offset")
            if fname is None or off is None:
                continue
            NAMES.add(fname)
            fmap[f"0x{_to_int(off):X}"] = {
                "name": fname,
                "type": _first(fl, "type", "Type"),
                "size": _first(fl, "size", "Size"),
            }
            nfields += 1
        INDEX[hx] = {"name": name, "fields": fmap}
        BY_NAME[name.lower()] = hx
    for e in (_first(data, "enums", "Enums", default=[]) or []):
        en = _first(e, "name", "Name")
        if not en:
            continue
        members = _first(e, "members", "Members", "values", "Values", default=[]) or []
        vmap = {}
        items = members.items() if isinstance(members, dict) else \
                ((_first(m, "name", "Name"), _first(m, "value", "Value")) for m in members)
        for mn, mv in items:
            if mn is None or mv is None:
                continue
            try:
                vmap[_to_int(mv)] = mn; NAMES.add(mn)
            except (ValueError, TypeError):
                pass
        ENUMS[en.lower()] = {"_name": en, "values": vmap}
        NAMES.add(en)
    return {"structs": len(INDEX), "fields": nfields, "enums": len(ENUMS), "names": len(NAMES)}

def save_index(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"index": INDEX, "enums": ENUMS, "names": sorted(NAMES)}, f)
    return {"saved": path, "structs": len(INDEX), "enums": len(ENUMS)}

def load_index(path):
    """Load a pre-flattened index (faster than re-parsing the raw dump)."""
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    INDEX.clear(); INDEX.update(d.get("index", {}))
    NAMES.clear(); NAMES.update(d.get("names", []))
    ENUMS.clear()
    for k, e in d.get("enums", {}).items():     # JSON int keys come back as strings -> re-int them
        ENUMS[k] = {"_name": e.get("_name"),
                    "values": {int(vv): mm for vv, mm in e.get("values", {}).items()}}
    BY_NAME.clear()
    for hx, s in INDEX.items():
        BY_NAME[s["name"].lower()] = hx
    return {"structs": len(INDEX), "enums": len(ENUMS), "names": len(NAMES)}

def decode_enum(enum, value):
    """Enum value -> member name (or '|'-joined flag names for bitflags), via the imported dump."""
    e = ENUMS.get(str(enum).lower())
    if not e:
        return None
    v = _to_int(value)
    vals = e["values"]
    if v in vals:
        return vals[v]
    flags = [vals[b] for b in vals if b and (b & (b - 1)) == 0 and (v & b)]   # power-of-2 bits set
    return "|".join(flags) if flags else None

def _hash_for(struct):
    """Accept a struct hash (int/hex) or a class name -> '0xHASH' key, or None."""
    if struct is None:
        return None
    if isinstance(struct, int):
        return f"0x{struct:X}"
    s = str(struct)
    if s.lower().startswith("0x"):
        return f"0x{int(s,16):X}"
    if s.isdigit():
        return f"0x{int(s):X}"
    return BY_NAME.get(s.lower())            # class name

def label(struct, offset):
    """(struct hash|name, offset) -> field name, or None. The inspect-labeling entry point."""
    hx = _hash_for(struct)
    if not hx or hx not in INDEX:
        return None
    off = f"0x{_to_int(offset):X}"
    fld = INDEX[hx]["fields"].get(off)
    return fld["name"] if fld else None

def struct_fields(struct):
    """All fields of a struct as [{offset,name,type,size}] sorted by offset."""
    hx = _hash_for(struct)
    if not hx or hx not in INDEX:
        return {"error": f"struct '{struct}' not in dump"}
    flds = [{"offset": o, **v} for o, v in INDEX[hx]["fields"].items()]
    flds.sort(key=lambda x: int(x["offset"], 16))
    return {"success": True, "struct": INDEX[hx]["name"], "hash": hx, "fields": flds}

# ---- bridge handlers ----
def handle_par_label(p):
    n = label(p.get("struct"), p.get("offset"))
    return {"success": True, "field": n} if n else {"error": "no field at that offset"}
def handle_par_struct(p):
    return struct_fields(p.get("struct"))
def handle_enum_decode(p):
    n = decode_enum(p.get("enum"), p.get("value"))
    return {"success": True, "enum": p.get("enum"), "value": p.get("value"), "name": n} \
        if n is not None else {"error": "enum/value not found in dump (is a dump loaded?)"}

PAR_COMMANDS = {"par_label": handle_par_label, "par_struct": handle_par_struct,
                "enum_decode": handle_enum_decode}

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        print(load_pardump(sys.argv[1]))
        if len(sys.argv) >= 3:
            print(save_index(sys.argv[2]))
