#!/usr/bin/env python3
"""Pronalazi u snimljenom JSON-u (raw.json / telo_*.json) odgovor sa utakmicama i kvotama.
Pokretanje:  python find_odds.py putanja/do/raw.json [rec1 rec2 ...]   (dodatne reci menjaju pretragu, npr. hendikep ukupno)
Ispisuje: koji URL-ovi su najveci i u kojima se pominju evroligaski timovi, plus skracen primer jednog meca.
Posalji mi taj ispis (ne ceo fajl). Vrednosti sa kljucevima tipa token/cookie/secret se skrivaju."""
import json, sys, re

NEEDLES = ("evroliga", "euroleague", "real madrid", "olympiacos", "olimpijakos", "partizan", "crvena zvezda", "zvezda", "fenerbahce", "panathinaikos", "barcelona", "baskonia")
REDACT = ("token", "authorization", "cookie", "secret", "password", "session")


def redact(o):
    if isinstance(o, dict):
        return {k: ("<skriveno>" if any(r in k.lower() for r in REDACT) else redact(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(x) for x in o]
    return o


def cands(node, out, depth=0):
    """Elementi liste (>=3 'brace') koji sadrze ime tima i nisu ogromni = verovatni mecevi."""
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if len(dicts) >= 3:
            for x in dicts:
                s = json.dumps(x, ensure_ascii=False).lower()
                if len(s) < 40000 and any(n in s for n in NEEDLES):
                    out.append(x)
        for x in node:
            cands(x, out, depth + 1)
    elif isinstance(node, dict) and depth < 12:
        for v in node.values():
            cands(v, out, depth + 1)


def main(path):
    global NEEDLES
    if len(sys.argv) > 2:
        NEEDLES = tuple(x.lower() for x in sys.argv[2:])
    j = json.load(open(path, encoding="utf-8"))
    items = j if isinstance(j, list) and j and isinstance(j[0], dict) and "url" in j[0] else [{"url": path, "data": j}]
    rows = []
    for it in items:
        c = []
        cands(it.get("data"), c)
        rows.append((len(json.dumps(it.get("data"), ensure_ascii=False)), len(c), it.get("url", "?"), c))
    print("Najveci odgovori (bajtova | kandidata za mec | url):")
    for size, n, url, _ in sorted(rows, key=lambda r: -r[0])[:12]:
        print(f"  {size:>9} | {n:>4} | {url[:140]}")
    best = max(rows, key=lambda r: r[1])
    if best[1] == 0:
        print("\nNijedan odgovor ne pominje evroligaske timove: kvote za kosarku nisu snimljene. Ponovi snimanje sa otvorenom kosarkom/Evroligom.")
        return
    print(f"\nNajvise meceva u: {best[2]}  ({best[1]} kandidata). Primer jednog meca (skraceno):")
    print(json.dumps(redact(best[3][0]), ensure_ascii=False, indent=1)[:3500])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Upotreba: python find_odds.py putanja/do/raw.json")
    main(sys.argv[1])
