#!/usr/bin/env python3
"""
fetch_aba.py - ABA liga (aba-liga.com) -> JSON fajlovi koje cita Value Analyzer.

POKRETANJE
  python fetch_aba.py                 # preuzme sta fali (kalendar uvek, boxscore samo novih utakmica)
  python fetch_aba.py --force         # ponovo parsira sve utakmice
  python fetch_aba.py --offline       # ne ide na net, koristi samo raw/ kes (za testiranje)
  python fetch_aba.py --season 26 --league-id 1 --out data/aba

STRUKTURA IZLAZA (sve za jednu ligu je u jednom folderu: data/<liga>/)
  data/aba/
    site.json            <- JEDINO ovo cita aplikacija (sastavlja se iz ostalog)
    calendar.json        <- ceo raspored + rezultati, kolo po kolo (parsirano iz kalendara)
    positions.json       <- OPCIONO, ti ga pises: {"5648":"Guard"}  (pozicije igraca)
    games/<broj>.json    <- jedna utakmica = jedan fajl (rezultat po cetvrtinama + ceo boxscore)
    teams/<KOD>.json     <- jedan tim = jedan fajl (bilans, utakmice, roster sa prosecima)
    raw/                 <- originalni HTML kako je preuzet (kes, moze u .gitignore)
"""
import argparse, json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

BASE = "https://www.aba-liga.com"
LEAGUE = {"key": "aba", "name": "ABA League", "season_label": "2026/27"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; ValueAnalyzerBot/1.0; +personal use)"}
TZ = ZoneInfo("Europe/Belgrade")

# redosled kolona u boxscore redu posle broja i imena (ABA tabela)
KEYS = ["min", "pts", "pct", "fg2m", "fg2a", "fg2p", "fg3m", "fg3a", "fg3p", "ftm", "fta", "ftp",
        "dr", "or", "tr", "ast", "stl", "tov", "blk", "blka", "pf", "pfd", "paint", "sec", "fb", "pm", "val"]
ADV = {"paint", "sec", "fb"}          # kolone "Points from" - na starim mecevima mogu da fale
SUMK = ["pts", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "or", "dr", "tr", "ast", "stl", "tov", "blk", "pf", "pir"]
# kolone niza igraca u site.json (isto kao Euroleague: indeksi 0-21 su fiksni)
PCOLS = ["id", "name", "no", "starter", "min", "pts", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "or", "dr", "tr",
         "ast", "stl", "tov", "blk", "pf", "pir", "pm", "blk_against", "fouls_drawn", "pts_paint", "pts_2nd", "pts_fb"]
SHORT = {"BOR": "Borac", "BOS": "Bosna", "BUD": "Budućnost", "CIB": "Cibona", "CLU": "U-BT Cluj", "COL": "Cedevita Olimpija",
         "CZV": "Crvena zvezda", "DUB": "Dubai", "FMP": "FMP", "IGO": "Igokea", "ILI": "Ilirija", "KRK": "Krka",
         "MEG": "Mega", "PAR": "Partizan", "SBR": "Slovan", "SCD": "SC Derby", "SIR": "Široki", "SPA": "Spartak",
         "VIE": "Vienna", "ZAD": "Zadar"}

S = requests.Session(); S.headers.update(UA)
def log(*a): print(*a, flush=True)


# ---------------------------------------------------------------- mreza / kes
def http_get(url):
    last = None
    for i in range(4):
        try:
            r = S.get(url, timeout=30)
            if r.status_code == 200:
                time.sleep(0.6)  # ljubazno prema sajtu
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code == 404: break
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url} -> {last}")

def load_html(url, cache, offline, refresh):
    if cache.exists() and (offline or not refresh):
        return cache.read_text("utf-8")
    if offline: raise FileNotFoundError(f"nema u kesu: {cache}")
    return http_get(url)

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")


# ---------------------------------------------------------------- pomocne
def num(s, d=0):
    s = (s or "").strip().replace(",", ".")
    try: return float(s) if "." in s else int(s)
    except ValueError: return d

def minutes(s):
    m = re.match(r"\s*(\d+):(\d+)", s or "")
    return round(int(m[1]) + int(m[2]) / 60, 2) if m else 0

def to_utc(txt):
    """'Friday, 02.10.2026 18:30 CET' -> ('2026-10-02T16:30:00Z', True). Bez sata: 12:00, False."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", txt or "")
    if not m: return None, False
    d, mo, y, h, mi = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h or 12), int(mi or 0), tzinfo=TZ).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), h is not None


# ---------------------------------------------------------------- 1) KALENDAR
def parse_calendar(html, season, lid):
    soup = BeautifulSoup(html, "html.parser")
    ids = {}  # naziv kluba -> club_id iz menija "Teams"
    for a in soup.select('a[href*="/team/"]'):
        m = re.search(rf"/team/(\d+)/{season}/{lid}/0/", a.get("href", ""))
        if m: ids.setdefault(a.get_text(strip=True), m.group(1))
    games, clubs = [], {}
    for panel in soup.select("div.panel"):
        h = panel.select_one(".panel-title a")
        label = re.sub(r"\s+", " ", h.get_text(" ", strip=True)) if h else ""
        m = re.match(r"ROUND\s+(\d+)", label, re.I)
        rnd = int(m[1]) if m else None
        for tr in panel.select("tbody tr"):
            tds = tr.find_all("td")
            a, ax = tr.select_one("p.hidden-xs a"), tr.select_one("p.visible-xs a")
            if len(tds) < 4 or not a or not ax: continue
            names = [t for t in a.stripped_strings if t != ":"]
            codes = [t for t in ax.stripped_strings if t != ":"]
            mm = re.search(r"/match/(\d+)/\d+/\d+/\w+/q1/\d+/home/([^/]*)/", a.get("href", ""))
            if len(names) != 2 or len(codes) != 2 or not mm: continue  # TBD utakmice (plej-of) nemaju timove
            sc = re.search(r"(\d+)\s*:\s*(\d+)", tds[1].get_text())
            utc, known = to_utc(tds[2].get_text(" ", strip=True))
            for c, n in zip(codes, names):
                clubs.setdefault(c, {"name": n, "short": SHORT.get(c, n), "club_id": ids.get(n),
                                     "crest": f"{BASE}/images/club/100x100/{ids[n]}.png" if n in ids else None})
            games.append({"n": int(mm[1]), "slug": mm[2], "round": rnd, "phase": label, "h": codes[0], "a": codes[1],
                          "hs": int(sc[1]) if sc else None, "as": int(sc[2]) if sc else None,
                          "utc": utc, "time_known": known, "group": tds[-1].get_text(strip=True) or None})
    return {"games": games, "clubs": clubs}


# ---------------------------------------------------------------- 2) BOXSCORE
def parse_row(vals):
    if len(vals) == len(KEYS): keys = KEYS
    elif len(vals) == len(KEYS) - len(ADV): keys = [k for k in KEYS if k not in ADV]
    else: raise ValueError(f"neocekivan broj kolona: {len(vals)}")
    d = {k: (minutes(v) if k == "min" else num(v)) for k, v in zip(keys, vals)}
    for k in ADV: d.setdefault(k, 0)
    return d

def parse_box(html, season, lid):
    soup = BeautifulSoup(html, "html.parser")
    tabs = soup.select("table.match_boxscore_team_table")
    if len(tabs) < 2: return None  # boxscore jos nije objavljen
    q = []
    t = soup.select_one("#match_cetrtine_rezultat")
    if t and len(t.find_all("tr")) >= 2:
        for c in t.find_all("tr")[1].find_all("td"):
            m = re.match(r"\s*(\d+)\s*:\s*(\d+)", c.get_text())
            if m: q.append((int(m[1]), int(m[2])))
    box = {}
    comp = soup.select_one("table.match_boxscore_teams_compare_table")
    crow = [r for r in (comp.select("tbody tr") if comp else []) if len(r.find_all("td")) > 20]
    for side, tab, i in (("h", tabs[0], 0), ("a", tabs[1], 1)):
        players = []
        for tr in tab.select("tbody tr"):
            tds = tr.find_all("td")
            link = tds[1].find("a") if len(tds) > 1 else None
            if len(tds) < 5 or not link: continue  # DNP red ima samo 4 celije
            d = parse_row([x.get_text(strip=True) for x in tds[2:]])
            if d["min"] <= 0: continue
            pid = re.search(r"/player/(\d+)/", link["href"])[1]
            players.append([pid, link.get_text(strip=True), tds[0].get_text(strip=True),
                            1 if "*" in tds[1].get_text() else 0, d["min"], d["pts"], d["fg2m"], d["fg2a"], d["fg3m"],
                            d["fg3a"], d["ftm"], d["fta"], d["or"], d["dr"], d["tr"], d["ast"], d["stl"], d["tov"],
                            d["blk"], d["pf"], d["val"], d["pm"], d["blka"], d["pfd"], d["paint"], d["sec"], d["fb"]])
        if i < len(crow):  # ukupno tima sa stranice (ukljucuje timske skokove/izgubljene)
            tt = parse_row([x.get_text(strip=True) for x in crow[i].find_all("td")[2:]]) if False else \
                 parse_row([x.get_text(strip=True) for x in crow[i].find_all("td")[1:]])
            tot = {k: tt[k] for k in SUMK if k != "pir"}; tot["pir"] = tt["val"]
        else:
            tot = {k: sum(p[PCOLS.index(k)] for p in players) for k in SUMK}
        box[side] = {"p": players, "tot": tot}
    info = soup.select_one("#basic_match_info")
    venue = re.search(r"Venue:\s*(.+)", info.get_text("\n", strip=True)) if info else None
    people = soup.select_one(".col-md-12 .smallCaps")
    refs = re.search(r"Referees:\s*(.+)", " ".join(soup.get_text(" ", strip=True).split()))
    clubs = {}
    for a in soup.select('#match_clubs_and_results_info_table a[href^="/team/"]'):
        m = re.search(r"/team/(\d+)/", a["href"])
        if m and a.get_text(strip=True): clubs[a.get_text(strip=True)] = m[1]
    return {"q": {"h": [x[0] for x in q], "a": [x[1] for x in q]}, "box": box,
            "venue": venue[1].strip() if venue else None, "club_ids": clubs}


# ---------------------------------------------------------------- 3) SASTAVLJANJE
def team_file(code, club, games, roster):
    gl, w, l, pf, pa = [], 0, 0, 0, 0
    for g in sorted(games, key=lambda x: x["dt"] or ""):
        if code not in (g["h"], g["a"]): continue
        home = g["h"] == code
        f_, a_ = (g["hs"], g["as"]) if home else (g["as"], g["hs"])
        won = f_ > a_; w += won; l += not won; pf += f_; pa += a_
        gl.append({"n": g["n"], "round": g["round"], "dt": g["dt"], "home": home, "opp": g["a"] if home else g["h"],
                   "pf": f_, "pa": a_, "result": "W" if won else "L"})
    n = len(gl) or 1
    return {"code": code, **club, "record": {"gp": len(gl), "w": w, "l": l, "pf": pf, "pa": pa,
            "pf_avg": round(pf / n, 1), "pa_avg": round(pa / n, 1)}, "games": gl,
            "roster": sorted(roster, key=lambda p: -p["min_avg"])}

def build(out, cal, parsed, season, lid):
    pos = {}
    if (out / "positions.json").exists():
        pos = json.loads((out / "positions.json").read_text("utf-8"))
    games, fixtures, rosters, pstat = [], [], {}, {}
    for g in cal["games"]:
        p = parsed.get(g["n"])
        if g["hs"] is not None and p:
            games.append({"n": g["n"], "round": g["round"], "h": g["h"], "a": g["a"], "hs": g["hs"], "as": g["as"],
                          "dt": g["utc"], "q": p["q"], "box": p["box"]})
            for side, code in (("h", g["h"]), ("a", g["a"])):
                for r in p["box"][side]["p"]:
                    s = pstat.setdefault((code, r[0]), {"id": r[0], "name": r[1], "no": r[2], "gp": 0, "min": 0, "pts": 0, "reb": 0, "ast": 0})
                    s["gp"] += 1; s["min"] += r[4]; s["pts"] += r[5]; s["reb"] += r[14]; s["ast"] += r[15]; s["no"] = r[2]
        elif g["hs"] is None:
            fixtures.append({"n": g["n"], "round": g["round"], "h": g["h"], "a": g["a"], "utc": g["utc"]})
    for (code, pid), s in pstat.items():
        n = s["gp"]
        rosters.setdefault(code, {"players": [], "assistants": []})["players"].append(
            {"id": pid, "name": s["name"], "no": s["no"], "pos": pos.get(pid, ""),
             "photo": f"{BASE}/stats/img/foto/{pid}.png", "gp": n, "min_avg": round(s["min"] / n, 1),
             "pts_avg": round(s["pts"] / n, 1), "reb_avg": round(s["reb"] / n, 1), "ast_avg": round(s["ast"] / n, 1)})
    games.sort(key=lambda x: x["dt"] or ""); fixtures.sort(key=lambda x: x["utc"] or "")
    for code, club in cal["clubs"].items():
        save(out / "teams" / f"{code}.json", team_file(code, club, games, rosters.get(code, {}).get("players", [])))
    site_ros = {c: {"players": [{k: p[k] for k in ("id", "name", "no", "pos", "photo")} for p in r["players"]], "assistants": []}
                for c, r in rosters.items()}
    site = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "league": {**LEAGUE, "season": season, "league_id": lid, "player_columns": PCOLS},
            "clubs": cal["clubs"], "rosters": site_ros, "games": games, "fixtures": fixtures}
    save(out / "site.json", site)
    return site


def main(argv=None):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=26); ap.add_argument("--league-id", type=int, default=1)
    ap.add_argument("--out", default="data/aba"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv); out = Path(a.out)
    html = load_html(f"{BASE}/calendar/{a.season}/{a.league_id}/", out / "raw" / "calendar.html", a.offline, True)
    cal = parse_calendar(html, a.season, a.league_id)
    if not cal["games"]: sys.exit("Kalendar je prazan - proveri --season / --league-id ili je sajt promenio HTML.")
    save(out / "calendar.json", cal)
    done = [g for g in cal["games"] if g["hs"] is not None]
    log(f"Kalendar: {len(cal['games'])} utakmica, odigrano {len(done)}, klubova {len(cal['clubs'])}")
    parsed, bad = {}, 0
    for g in done:
        gp = out / "games" / f"{g['n']}.json"
        if gp.exists() and not a.force:
            parsed[g["n"]] = json.loads(gp.read_text("utf-8")); continue
        url = f"{BASE}/match/{g['n']}/{a.season}/{a.league_id}/Boxscore/q1/1/home/{g['slug']}/"
        raw = out / "raw" / f"box_{g['n']}.html"
        try:
            h = load_html(url, raw, a.offline, a.force)
            p = parse_box(h, a.season, a.league_id)
            if not p: log(f"  #{g['n']} {g['h']}-{g['a']}: boxscore jos nije objavljen"); bad += 1; continue
            hs = sum(x[5] for x in p["box"]["h"]["p"]); as_ = sum(x[5] for x in p["box"]["a"]["p"])
            if (hs, as_) != (g["hs"], g["as"]):
                log(f"  UPOZORENJE #{g['n']}: zbir poena igraca {hs}:{as_} != rezultat {g['hs']}:{g['as']}")
            raw.parent.mkdir(parents=True, exist_ok=True); raw.write_text(h, "utf-8")
            save(gp, {**{k: g[k] for k in ("n", "round", "phase", "group", "h", "a", "hs", "as")}, "dt": g["utc"],
                      "columns": PCOLS, "source": url, **p})
            parsed[g["n"]] = json.loads(gp.read_text("utf-8")); log(f"  #{g['n']} {g['h']} {g['hs']}:{g['as']} {g['a']}  OK")
        except Exception as e:
            log(f"  #{g['n']} {g['h']}-{g['a']}: GRESKA {e}"); bad += 1
    site = build(out, cal, parsed, a.season, a.league_id)
    log(f"Gotovo: {len(site['games'])} utakmica sa boxscore-om, {len(site['fixtures'])} predstojecih, {bad} problema -> {out/'site.json'}")

if __name__ == "__main__":
    main()
