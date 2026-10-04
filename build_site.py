"""
Korak 2: pretvara sirove podatke iz data/ u jedan kompaktan fajl data/site.json
koji cita sajt (index.html). Pokrece se posle fetch_data.py.

Ulaz:  data/results.json, data/raw/boxscore_*.json, data/raw/recon_v2_clubs.txt,
       data/raw/recon_v2_people.txt, data/raw/recon_v2_games.txt
Izlaz: data/site.json
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data")
RAW = DATA / "raw"
SEASON = "E2026"

# redosled kolona u linijama igraca (isti redosled koristi index.html)
COLS = ["id", "name", "no", "gs", "min", "pts", "fg2m", "fg2a", "fg3m", "fg3a",
        "ftm", "fta", "or", "dr", "tr", "ast", "stl", "tov", "blk", "pf", "pir", "pm"]
SRC = {"fg2m": "FieldGoalsMade2", "fg2a": "FieldGoalsAttempted2", "fg3m": "FieldGoalsMade3",
       "fg3a": "FieldGoalsAttempted3", "ftm": "FreeThrowsMade", "fta": "FreeThrowsAttempted",
       "or": "OffensiveRebounds", "dr": "DefensiveRebounds", "tr": "TotalRebounds",
       "ast": "Assistances", "stl": "Steals", "tov": "Turnovers", "blk": "BlocksFavour",
       "pf": "FoulsCommited", "pir": "Valuation", "pts": "Points"}
TOT_KEYS = ["pts", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "or", "dr", "tr", "ast",
            "stl", "tov", "blk", "pf", "pir"]


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def load_array(text, key="data"):
    """Cita niz iz JSON-a. Ako je fajl odsecen (stari limit od 400 KB), spasi cele objekte."""
    if not text.strip():
        return []
    try:
        obj = json.loads(text)
        return obj[key] if isinstance(obj, dict) else obj
    except json.JSONDecodeError:
        pass
    i = text.find(f'"{key}":[')
    if i < 0:
        return []
    i = text.find("[", i) + 1
    dec, out = json.JSONDecoder(), []
    while i < len(text):
        while i < len(text) and text[i] in " \n\r\t,":
            i += 1
        try:
            o, j = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        out.append(o)
        i = j
    return out


def pretty(name):
    """'SMITH JR, NICK' -> 'Nick Smith Jr'"""
    name = (name or "").strip()
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        name = f"{first} {last}".strip()
    def cap(w):
        if w.upper() in {"II", "III", "IV", "V"}:
            return w.upper()
        return re.sub(r"(^|[-'’])([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), w.lower())
    return " ".join(cap(w) for w in name.split())


def minutes(s):
    m = re.match(r"^(\d+):(\d+)$", str(s or "").strip())
    return round(int(m.group(1)) + int(m.group(2)) / 60, 2) if m else 0.0


def pid(raw):
    return str(raw or "").strip().lstrip("P")


def build_clubs():
    out = {}
    for c in load_array(read(RAW / "recon_v2_clubs.txt")):
        out[c["code"]] = {
            "name": c.get("name"), "short": c.get("abbreviatedName") or c.get("name"),
            "city": c.get("city"), "country": (c.get("country") or {}).get("name"),
            "crest": (c.get("images") or {}).get("crest"), "president": c.get("president"),
            "website": c.get("website"), "venue": c.get("venueCode"),
        }
    return out


def build_rosters():
    ros = {}
    for p in load_array(read(RAW / "recon_v2_people.txt")):
        if not p.get("active"):
            continue
        club = (p.get("club") or {}).get("code")
        if not club:
            continue
        per = p.get("person") or {}
        r = ros.setdefault(club, {"coach": None, "assistants": [], "players": []})
        t = p.get("type")
        if t == "J":
            r["players"].append({
                "id": per.get("code"), "name": pretty(per.get("name")), "no": p.get("dorsal"),
                "pos": p.get("positionName"), "h": per.get("height") or None,
                "w": per.get("weight") or None, "country": (per.get("country") or {}).get("name"),
                "born": (per.get("birthDate") or "")[:10] or None,
                "photo": (p.get("images") or {}).get("headshot"), "from": p.get("lastTeam") or None,
            })
        elif t == "E":
            r["coach"] = {"name": pretty(per.get("name")), "country": (per.get("country") or {}).get("name"),
                          "born": (per.get("birthDate") or "")[:10] or None}
        elif t == "A":
            r["assistants"].append(pretty(per.get("name")))
    for r in ros.values():
        r["players"].sort(key=lambda x: (int(x["no"]) if str(x["no"] or "").isdigit() else 999))
    return ros


def side_lines(team_stats):
    lines = []
    for p in team_stats.get("PlayersStats", []):
        mins = minutes(p.get("Minutes"))
        if mins <= 0:
            continue
        row = []
        for c in COLS:
            if c == "id":
                row.append(pid(p.get("Player_ID")))
            elif c == "name":
                row.append(pretty(p.get("Player")))
            elif c == "no":
                row.append(p.get("Dorsal"))
            elif c == "gs":
                row.append(int(p.get("IsStarter") or 0))
            elif c == "min":
                row.append(mins)
            elif c == "pm":
                row.append(p.get("Plusminus"))
            else:
                row.append(p.get(SRC[c], 0) or 0)
        lines.append(row)
    tr = team_stats.get("totr") or {}
    tot = {k: tr.get(SRC[k], 0) or 0 for k in TOT_KEYS}
    return lines, tot, pretty(team_stats.get("Coach"))


def parse_dt(date, time):
    try:
        return datetime.strptime(f"{date} {time}", "%b %d, %Y %H:%M").strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def build_games():
    results = json.loads(read(DATA / "results.json") or "[]")
    games = []
    for g in results:
        if not (g.get("homescore") and g.get("awayscore")):
            continue
        num = g.get("gamenumber") or g.get("gamecode", "").split("_")[-1]
        item = {"n": int(num), "round": int(g.get("gameday") or 0),
                "dt": parse_dt(g.get("date"), g.get("time")),
                "h": g["homecode"].strip(), "a": g["awaycode"].strip(),
                "hs": int(g["homescore"]), "as": int(g["awayscore"]), "q": None, "box": None}
        bpath = RAW / f"boxscore_{num}.json"
        if bpath.exists():
            try:
                box = json.loads(bpath.read_text(encoding="utf-8"))
                item["live"] = bool(box.get("Live"))
                by_name = {}
                for t in box.get("Stats", []):
                    by_name[(t.get("Team") or "").strip().upper()] = t
                hn, an = g["hometeam"].strip().upper(), g["awayteam"].strip().upper()
                th, ta = by_name.get(hn), by_name.get(an)
                if not (th and ta) and len(box.get("Stats", [])) == 2:
                    th, ta = box["Stats"][0], box["Stats"][1]
                if th and ta:
                    hl, ht, hc = side_lines(th)
                    al, at, ac = side_lines(ta)
                    item["box"] = {"h": {"coach": hc, "tot": ht, "p": hl},
                                   "a": {"coach": ac, "tot": at, "p": al}}
                qs = {(q.get("Team") or "").strip().upper(): q for q in box.get("ByQuarter", [])}
                if hn in qs and an in qs:
                    def qv(q):
                        return [v for k, v in sorted(q.items()) if k.startswith("Quarter") or k.startswith("Extra")]
                    item["q"] = {"h": qv(qs[hn]), "a": qv(qs[an])}
                item["att"] = box.get("Attendance")
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                print("boxscore", num, "preskocen:", exc)
        games.append(item)
    games.sort(key=lambda x: (x["dt"] or "", x["n"]))
    return games


def build_fixtures():
    out = []
    for g in load_array(read(RAW / "recon_v2_games.txt")):
        if g.get("played"):
            continue
        try:
            out.append({"n": g["gameCode"], "round": g["round"], "utc": g.get("utcDate"),
                        "local": g.get("localDate"), "h": g["local"]["club"]["code"],
                        "a": g["road"]["club"]["code"], "venue": (g.get("venue") or {}).get("name")})
        except (KeyError, TypeError):
            continue
    out.sort(key=lambda x: (x["utc"] or "", x["n"]))
    return out


def main():
    games = build_games()
    if not games:
        print("Nema odigranih utakmica u data/results.json, site.json nije menjan.")
        sys.exit(0)
    site = {
        "season": SEASON,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cols": COLS,
        "clubs": build_clubs(),
        "rosters": build_rosters(),
        "games": games,
        "fixtures": build_fixtures(),
    }
    (DATA / "site.json").write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    nb = sum(1 for g in games if g["box"])
    print(f"site.json: {len(site['clubs'])} klubova, {sum(len(r['players']) for r in site['rosters'].values())} igraca, "
          f"{len(games)} utakmica ({nb} sa boxscore), {len(site['fixtures'])} predstojecih")


if __name__ == "__main__":
    main()
