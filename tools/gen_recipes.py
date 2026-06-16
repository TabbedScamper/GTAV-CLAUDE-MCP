"""
gen_recipes.py — flatten the human-written PATTERNS/*.md cards into a queryable recipes.json so the
bridge/MCP can serve them ("how do I spawn a persistent prop on the ground?" -> the verified recipe).

    python tools/gen_recipes.py

Parses each `### title` card with its **Category/Problem/Method/Gotcha/Source** lines. Output is OUR
distilled knowledge (committed), not the mods' code. Re-run after editing the PATTERNS cards.
"""
import json, os, re, glob

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PAT = os.path.join(PROJ, "Examples", "PATTERNS")
OUT = os.path.join(PROJ, "pyscript", "recipes")

_FIELD = re.compile(r"^\*\*(Category|Problem|Method|Gotcha|Source)\:\*\*\s*(.*)", re.I)

def parse_file(path):
    cards, cur = [], None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("### "):
                if cur:
                    cards.append(cur)
                title = line[4:].strip()
                cur = {"title": title, "category": "", "problem": "", "method": "", "gotcha": "", "source": ""}
                continue
            if cur is None:
                continue
            m = _FIELD.match(line)
            if m:
                key = m.group(1).lower()
                val = m.group(2).strip()
                # strip the "· *seen in N mods*" corroboration tag off Category into its own field
                if key == "category" and "·" in val:
                    val, tag = val.split("·", 1)
                    cur["corroboration"] = tag.strip().strip("*")
                    val = val.strip()
                cur[key] = val
    if cur:
        cards.append(cur)
    return cards

def main():
    os.makedirs(OUT, exist_ok=True)
    recipes = []
    for md in sorted(glob.glob(os.path.join(PAT, "*.md"))):
        if os.path.basename(md).lower() == "readme.md":
            continue
        file_tag = os.path.basename(md)
        for c in parse_file(md):
            c["file"] = file_tag
            c["id"] = re.sub(r"[^a-z0-9]+", "-", c["title"].lower()).strip("-")[:60]
            recipes.append(c)
    with open(os.path.join(OUT, "recipes.json"), "w", encoding="utf-8") as f:
        json.dump({"count": len(recipes), "recipes": recipes}, f, indent=1)
    cats = sorted({c["category"] for c in recipes if c["category"]})
    print(f"{len(recipes)} recipes -> recipes/recipes.json")
    print("categories:", ", ".join(cats))

if __name__ == "__main__":
    main()
