"""
Uvoz rezultata za dodatnu ligu u isti format koji cita sajt: data/<liga>/site.json.
Radi bez boxscore-a (tabela, forma, rejting, model, zamor, forma preko liga rade; igracki tabovi ostaju prazni dok se ne doda boxscore).

Ulaz:  data/<liga>/results.csv   kolone: date,time,round,home,away,home_score,away_score   (date YYYY-MM-DD, time HH:MM)
       data/<liga>/fixtures.csv  kolone: date,time,round,home,away                         (opciono, predstojece)
Pokretanje:  python import_results.py aba
"""
import csv, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

lg = sys.argv[1] if len(sys.argv) > 1 else "aba"
D = Path("data") / lg
clubs, codes = {}, {}


def code(name):
    name = name.strip()
    if name in codes: return codes[name]
    slug = re.sub(r"[^A-Za-z0-9]", "", name).upper()
    for n in range(3, len(slug) + 1):
        c = slug[:n]
        if c not in clubs: break
    clubs[c] = {"name": name, "short": name}; codes[name] = c
    return c


def rows(f):
    p = D / f
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


games = []
for i, r in enumerate(rows("results.csv")):
    games.append({"n": i + 1, "round": int(r.get("round") or 0), "dt": f"{r['date']}T{r.get('time') or '00:00'}",
                  "h": code(r["home"]), "a": code(r["away"]), "hs": int(r["home_score"]), "as": int(r["away_score"]),
                  "q": None, "box": None})
games.sort(key=lambda g: (g["dt"], g["n"]))
fixtures = [{"n": 100000 + i, "round": int(r.get("round") or 0), "utc": f"{r['date']}T{r.get('time') or '00:00'}:00",
             "local": f"{r['date']}T{r.get('time') or '00:00'}:00", "h": code(r["home"]), "a": code(r["away"]), "venue": None}
            for i, r in enumerate(rows("fixtures.csv"))]
fixtures.sort(key=lambda x: (x["utc"], x["n"]))
site = {"season": lg, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cols": [],
        "clubs": clubs, "rosters": {}, "games": games, "fixtures": fixtures}
(D / "site.json").write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{D/'site.json'}: {len(clubs)} klubova, {len(games)} utakmica, {len(fixtures)} predstojecih")
