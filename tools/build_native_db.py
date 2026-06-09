#!/usr/bin/env python3
"""
build_native_db.py - Generate the unified GTA V native database for the Claude MCP bridge.

Merges alloc8or's Legacy (natives.json) + Enhanced (natives_gen9.json) into ONE
name-keyed database used as the bridge's VERIFIED-HASH ALLOWLIST.

Why this exists:
  A wrong 64-bit hash passed to ScriptHookV CRASHES the game (no error - instant CTD).
  The alloc8or hashes are the AUTHORITATIVE canonical hashes (ScriptHookV translates
  them to the live build at runtime), so "any native present in this DB" is the safe,
  complete set Claude is allowed to call. Guessed / typo / known-bad hashes are rejected.

Research finding (verified by parsing both files):
  Canonical native hashes are IDENTICAL across Legacy and Enhanced - every shared
  native matches, and the Enhanced "crossmap" is all-identity. We still store hashes
  per-edition (cheap insurance) + an availability flag for the ~50 edition-exclusive ones.

Run:  python tools/build_native_db.py
Out:  pyscript/native_db.json   (+ a build report to stdout)
"""
import json
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, "sources")
LEGACY_SRC = os.path.join(SRC, "natives.json")
ENHANCED_SRC = os.path.join(SRC, "natives_gen9.json")
CLAUDE_MD = os.path.join(ROOT, "CLAUDE.md")
OUT = os.path.join(ROOT, "pyscript", "native_db.json")

HASH_RE = re.compile(r"0x[0-9A-Fa-f]{6,16}")


def load(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def flatten(doc):
    """namespace -> hash -> entry   =>   name -> {namespace, hash, ...}"""
    out = {}
    for ns, natives in doc.items():
        for hash64, e in natives.items():
            name = e.get("name")
            if not name:
                continue
            out[name] = {
                "namespace": ns,
                "hash": hash64,
                "jhash": e.get("jhash", ""),
                "params": e.get("params", []),
                "return_type": e.get("return_type", "void"),
                "comment": e.get("comment", ""),
                "build": e.get("build", ""),
            }
    return out


def parse_claude_md(path):
    """Extract working (name -> hash) and known-bad (hash -> reason) from CLAUDE.md tables."""
    working, bad = {}, {}
    if not os.path.exists(path):
        return working, bad
    text = open(path, "r", encoding="utf-8").read()
    section = None
    for line in text.splitlines():
        low = line.lower()
        if line.lstrip().startswith("#"):
            section = None  # heading ends a table section
        if "known bad native" in low:
            section = "bad"
            continue
        if "working native" in low:
            section = "working"
            continue
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if not cells or cells[0].lower() in ("hash", ""):
            continue
        if set("".join(cells)) <= set("-: "):  # markdown separator row
            continue
        hashes = HASH_RE.findall(line)
        if section == "bad" and hashes:
            bad[hashes[0].lower()] = cells[-1] if len(cells) > 1 else "known crash"
        elif section == "working" and len(cells) >= 2 and hashes:
            name = cells[1]
            bad_name = name.lower() in ("function", "")
            if not bad_name:
                working[name] = hashes[0]
    return working, bad


def main():
    legacy = flatten(load(LEGACY_SRC))
    enhanced = flatten(load(ENHANCED_SRC))
    working, known_bad = parse_claude_md(CLAUDE_MD)

    names = sorted(set(legacy) | set(enhanced))
    natives = {}
    hash_differs = legacy_only = enhanced_only = 0

    for name in names:
        L, E = legacy.get(name), enhanced.get(name)
        base = E or L
        rec = {
            "namespace": base["namespace"],
            "jhash": base["jhash"],
            "params": base["params"],
            "return_type": base["return_type"],
            "comment": base["comment"],
            "hashes": {
                "legacy": L["hash"] if L else None,
                "enhanced": E["hash"] if E else None,
            },
            "build_added": {
                "legacy": L["build"] if L else None,
                "enhanced": E["build"] if E else None,
            },
            "flags": [],
            "verified": {"legacy": False, "enhanced": False},
            "notes": "",
        }
        if not L:
            rec["flags"].append("not_in_legacy")
            enhanced_only += 1
        if not E:
            rec["flags"].append("not_in_enhanced")
            legacy_only += 1
        if L and E and L["hash"].lower() != E["hash"].lower():
            rec["flags"].append("hash_differs")
            hash_differs += 1
        natives[name] = rec

    # Fold in CLAUDE.md tested hashes (raises confidence; not the gate)
    tested, mismatches, unknown_working = 0, [], []
    for name, h in working.items():
        rec = natives.get(name)
        if not rec:
            unknown_working.append((name, h))
            continue
        lh = (rec["hashes"]["legacy"] or "").lower()
        if lh and lh == h.lower():
            rec["verified"]["legacy"] = True
            tested += 1
        else:
            mismatches.append((name, h, rec["hashes"]["legacy"]))

    # known_bad: warn if a banned hash is actually a real native (would over-ban)
    all_hashes = set()
    for r in natives.values():
        for hh in r["hashes"].values():
            if hh:
                all_hashes.add(hh.lower())
    kb_collisions = [h for h in known_bad if h in all_hashes]

    out = {
        "$schema_version": "1.0",
        "$meta": {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "source": "alloc8or/gta5-nativedb-data (natives.json + natives_gen9.json)",
            "legacy_natives": len(legacy),
            "enhanced_natives": len(enhanced),
            "total_natives": len(natives),
            "note": "callable = present here AND not in known_bad. Hashes are canonical "
                    "(same on Legacy+Enhanced); ScriptHookV translates to the live build.",
        },
        "known_bad": known_bad,
        "natives": natives,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), sort_keys=True)

    size_mb = round(os.path.getsize(OUT) / (1024 * 1024), 2)
    print(f"[build_native_db] wrote {OUT}  ({size_mb} MB)")
    print(f"  total natives : {len(natives)}")
    print(f"  legacy        : {len(legacy)}")
    print(f"  enhanced      : {len(enhanced)}")
    print(f"  hash_differs  : {hash_differs}")
    print(f"  legacy_only   : {legacy_only}")
    print(f"  enhanced_only : {enhanced_only}")
    print(f"  verified(legacy) from CLAUDE.md : {tested}")
    print(f"  known_bad     : {len(known_bad)}  -> {list(known_bad.keys())}")
    if mismatches:
        print("  !! WORKING-HASH MISMATCHES (tested hash != DB hash) - REVIEW:")
        for name, th, dh in mismatches:
            print(f"     {name}: claude={th} db={dh}")
    if unknown_working:
        print(f"  !! working hashes for natives not in DB: {unknown_working}")
    if kb_collisions:
        print(f"  !! known_bad hashes that ARE real natives (over-ban?): {kb_collisions}")


if __name__ == "__main__":
    main()
