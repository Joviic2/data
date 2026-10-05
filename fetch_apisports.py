#!/usr/bin/env python3
"""API-Sports Basketball + The Odds API -> data/site.json, data/<liga>/site.json, data/odds.json
Samo standardna biblioteka. Ključevi iz env: APISPORTS_KEY (obavezno), ODDS_API_KEY (opciono)."""
import json, os, re, sys, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KEY = os.environ.get("APISPORTS_KEY", "").strip()
ODDS_KEY = os.environ.get("ODDS_API_KEY", "").strip()
SEASON = os.environ.get("SEASON", "2026-2027")
LEAGUES = [x for x in os.environ.get("LEAGUES", "aba,acb,lkl,lnb,bbl").split(",") if x]
MAX_BOX = int(os.environ.get("MAX_BOX", "6"))         # novih boxscore poziva po pokretanju (ukupno)
MIN_AGE = int(os.environ.get("MIN_AGE_MIN", "180"))   # liga se ne osvežava češće od ovoga (minuta)
BASE = "https://v1.basketball.api-sports.io"
TZ = ZoneInfo("Europe/Belgrade")

LG = {  # (id ili None = nađi po imenu, pretraga, regex naziva, zemlja)
    "euroleague": (120, "Euroleague", r"^euroleague$", None),
    "acb": (117, "ACB", r"^(liga )?acb$", "Spain"),
    "aba": (None, "ABA", r"aba", None),
    "lkl": (None, "LKL", r"lkl|lietuvos", "Lithuania"),
    "lnb": (None, "LNB", r"lnb|betclic|pro a|elite", "France"),
    "bbl": (None, "BBL", r"bbl|basketball bundesliga", "Germany"),
}
NAME_OVR = {"ULK": "Fenerbahče", "RED": "Crvena zvezda", "MIL": "Armani Milano", "BES": "Beşiktaş", "PAN": "Panathinaikos",
            "IST": "Anadolu Efes", "MAD": "Real Madrid", "HTA": "Hapoel Tel Aviv", "TEL": "Maccabi Tel Aviv", "PAM": "Valencia",
            "PAR": "Partizan", "PRS": "Paris", "MUN": "Bayern", "BAS": "Baskonia", "ASV": "ASVEL", "VIR": "Virtus Bologna",
            "ZAL": "Žalgiris", "OLY": "Olympiacos", "BAR": "Barcelona", "DUB": "Dubai"}
ALIASES = {"MIL": ["armani", "milan"], "MUN": ["bayern"], "IST": ["efes"], "ULK": ["fenerbahce"], "BES": ["besiktas"],
           "ZAL": ["zalgiris"], "RED": ["crvena", "red star"], "ASV": ["asvel", "villeurbanne"], "TEL": ["maccabi"], "PAM": ["valencia"]}
SUMK = ["pts", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "or", "dr", "tr", "ast", "stl", "tov", "blk", "pf", "pir"]
IDX = dict(zip(SUMK, [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]))
FIN, LIVE = {"FT", "AOT"}, {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT"}

class QuotaOut(Exception): pass
stat = {"remaining": None}

def norm(s): return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()
def n0(v):
    try: return float(v) if v not in (None, "") else 0
    except (TypeError, ValueError): return 0
def pk(o, *ns):
    for n in ns:
        if isinstance(o, dict) and o.get(n) is not None: return o[n]
def http(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=30) as r:
        return json.load(r), r.headers

def api(path, **params):
    data, h = http(BASE + path + "?" + urllib.parse.urlencode(params), {"x-apisports-key": KEY})
    rem = h.get("x-ratelimit-requests-remaining")
    if rem is not None: stat["remaining"] = rem
    err = data.get("errors")
    if err:
        msg = json.dumps(err)
        if re.search(r"limit|quota|request", msg, re.I): raise QuotaOut(msg)
        raise RuntimeError(path + ": " + msg)
    return data.get("response") or []

def read(p):
    try:
        with open(p, encoding="utf8") as f: return json.load(f)
    except Exception: return None
def write(p, obj):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf8") as f: json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

def parse_min(m):
    if m in (None, ""): return 0
    if isinstance(m, (int, float)): return m
    a, _, b = str(m).partition(":")
    return n0(a) + n0(b) / 60

def local_dt(ts): return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%dT%H:%M")

def league_id(lg):
    lid, search, rx, country = LG[lg]
    if lid: return lid
    found = api("/leagues", search=search)
    for x in found:
        if re.search(rx, x["name"], re.I) and (not country or country.lower() in norm((x.get("country") or {}).get("name"))):
            print(f"  {lg}: liga '{x['name']}' id={x['id']}"); return x["id"]
    raise RuntimeError(f"Liga nije pronađena u API-ju (nađeno: {[x['name'] for x in found][:8]})")

def make_coder(teams, existing_clubs):
    used, by_id = set(), {}
    toks = lambda s: {w for w in re.split(r"[^a-z0-9]+", norm(s)) if len(w) >= 4}
    for t in sorted(teams, key=lambda x: x["id"]):
        nn, tt, code = norm(t["name"]), toks(t["name"]), None
        for c, cl in existing_clubs.items():          # 1) postojeći kodovi iz tvog site.json
            if c not in used and tt & (toks(cl.get("name", "")) | toks(cl.get("short", ""))): code = c; break
        if not code:                                   # 2) poznati kodovi + aliasi
            for c in NAME_OVR:
                if c not in used and any(x in nn for x in [norm(NAME_OVR[c])] + ALIASES.get(c, [])): code = c; break
        if not code:                                   # 3) novi kod
            base = (re.sub(r"[^a-z0-9]", "", nn)[:3].upper()).ljust(3, "X"); code = base; i = 1
            while code in used or code in NAME_OVR: code = base[:2] + str(i); i += 1
        used.add(code); by_id[t["id"]] = code
    return by_id.get

def map_player(e):
    fg, tp = e.get("field_goals") or {}, e.get("threepoint_goals") or e.get("three_points") or {}
    ft, rb = e.get("freethrows_goals") or e.get("free_throws") or {}, e.get("rebounds") or {}
    fgm, fga = n0(pk(fg, "total", "made")), n0(pk(fg, "attempts", "attempted"))
    m3, a3 = n0(pk(tp, "total", "made")), n0(pk(tp, "attempts", "attempted"))
    ftm, fta = n0(pk(ft, "total", "made")), n0(pk(ft, "attempts", "attempted"))
    orb, drb = n0(pk(rb, "offence", "offensive", "offense")), n0(pk(rb, "defense", "defensive", "defence"))
    trb = n0(rb.get("total")) or orb + drb
    pts, ast, stl, blk = n0(e.get("points")), n0(e.get("assists")), n0(e.get("steals")), n0(e.get("blocks"))
    tov, pf = n0(e.get("turnovers")), n0(pk(e, "fouls", "personal_fouls"))
    pir = pts + trb + ast + stl + blk - (fga - fgm) - (fta - ftm) - tov - pf
    pl = e.get("player") or {}
    row = [pl.get("id"), pl.get("name"), pl.get("number") or "", 1 if (e.get("type") == "starters" or e.get("starter")) else 0,
           parse_min(e.get("minutes")), pts, fgm - m3, fga - a3, m3, a3, ftm, fta, orb, drb, trb, ast, stl, tov, blk, pf, pir,
           e.get("plus_minus") if e.get("plus_minus") is not None else ""]
    return [int(x) if isinstance(x, float) and x == int(x) and i not in (4,) else x for i, x in enumerate(row)]

def map_side(entries):
    p = [map_player(e) for e in entries]
    return {"coach": "", "p": p, "tot": {k: sum(n0(r[IDX[k]]) for r in p) for k in SUMK}}

def get_box(g, budget):
    cp = f"data/cache/box/{g['id']}.json"
    c = read(cp)
    if c: return c
    if budget["left"] <= 0 or budget["broken"]: return None
    budget["left"] -= 1
    try:
        rows = api("/games/statistics/players", id=g["id"])
    except QuotaOut: raise
    except Exception as ex:
        print("  boxscore nedostupan:", ex); budget["broken"] = True; return None
    h = [r for r in rows if (r.get("team") or {}).get("id") == g["teams"]["home"]["id"]]
    a = [r for r in rows if (r.get("team") or {}).get("id") == g["teams"]["away"]["id"]]
    if not h or not a: return None
    box = {"h": map_side(h), "a": map_side(a)}; write(cp, box); return box

def quarters(s):
    q = [s.get(f"quarter_{i}") for i in (1, 2, 3, 4)]
    if s.get("over_time") is not None: q.append(s["over_time"])
    return [int(n0(x)) for x in q]

def build_site(lg, out, budget):
    old = read(out) or {}
    raw = [g for g in api("/games", league=league_id(lg), season=SEASON)
           if g.get("teams", {}).get("home", {}).get("id") and g["teams"]["away"].get("id")]
    raw.sort(key=lambda g: g["timestamp"])
    teams = {}
    for g in raw:
        for s in ("home", "away"): teams[g["teams"][s]["id"]] = g["teams"][s]
    code = make_coder(list(teams.values()), old.get("clubs", {}))
    clubs = dict(old.get("clubs", {}))
    for t in teams.values():
        c = clubs.setdefault(code(t["id"]), {"name": t["name"], "short": t["name"]})
        if t.get("logo") and not c.get("crest"): c["crest"] = t["logo"]
    cnt, games, fixtures = {}, [], []
    for g in raw:
        h, a = code(g["teams"]["home"]["id"]), code(g["teams"]["away"]["id"])
        w = re.sub(r"\D", "", str(g.get("week") or ""))
        r = int(w) if w and int(w) > 0 else max(cnt.get(h, 0), cnt.get(a, 0)) + 1
        cnt[h] = cnt[a] = r
        st, sc = (g.get("status") or {}).get("short"), g["scores"]
        hs, as_ = sc["home"].get("total"), sc["away"].get("total")
        if st in FIN and hs is not None and as_ is not None:
            game = {"n": g["id"], "round": r, "dt": local_dt(g["timestamp"]), "h": h, "a": a, "hs": int(hs), "as": int(as_)}
            if sc["home"].get("quarter_1") is not None: game["q"] = {"h": quarters(sc["home"]), "a": quarters(sc["away"])}
            box = get_box(g, budget)
            if box: game["box"] = box
            games.append(game)
        elif st not in LIVE and st not in ("CANC", "PST"):
            fixtures.append({"n": g["id"], "round": r, "h": h, "a": a,
                             "utc": datetime.fromtimestamp(g["timestamp"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")})
    rosters = old.get("rosters") or {}                 # sačuvaj postojeće (slike, pozicije)
    for g in games:
        for sd in ("h", "a"):
            if "box" not in g: continue
            R = rosters.setdefault(g[sd], {"players": [], "assistants": []})
            for p in g["box"][sd]["p"]:
                if not any(str(x.get("id")) == str(p[0]) for x in R["players"]):
                    R["players"].append({"id": p[0], "name": p[1], "no": p[2], "pos": ""})
    site = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "clubs": clubs, "games": games,
            "fixtures": fixtures, "rosters": rosters}
    for k, v in old.items(): site.setdefault(k, v)     # ostala polja iz starog fajla
    write(out, site)
    print(f"{lg}: {len(games)} utakmica, {len(fixtures)} predstojećih -> {out}")

def build_odds():
    O = "https://api.the-odds-api.com/v4"
    sports, _ = http(f"{O}/sports?apiKey={ODDS_KEY}")
    sk = next((s["key"] for s in sports if re.search("euroleague", s["key"] + s["title"], re.I)), None)
    if not sk: print("Odds: nema Euroleague sporta"); return
    ev, h = http(f"{O}/sports/{sk}/odds?regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal&apiKey={ODDS_KEY}")
    out = []
    for e in ev:
        bs = []
        for bk in e.get("bookmakers", []):
            M = {m["key"]: m for m in bk.get("markets", [])}
            price = lambda m, n: next((o["price"] for o in m["outcomes"] if o["name"] == n), None)
            sp = M.get("spreads"); spH = sp and next((o for o in sp["outcomes"] if o["name"] == e["home_team"]), None)
            tt = M.get("totals"); ov = tt and next((o for o in tt["outcomes"] if o["name"] == "Over"), None)
            bs.append({"key": bk["key"], "title": bk["title"],
                       "h2h": [price(M["h2h"], e["home_team"]), price(M["h2h"], e["away_team"])] if "h2h" in M else None,
                       "sp": [spH["point"], spH["price"], price(sp, e["away_team"])] if spH else None,
                       "tt": [ov["point"], ov["price"], price(tt, "Under")] if ov else None})
        out.append({"id": e["id"], "h": e["home_team"], "a": e["away_team"], "start": e["commence_time"], "b": bs})
    write("data/odds.json", {"fetched": int(datetime.now().timestamp()), "remaining": h.get("x-requests-remaining") or "", "events": out})
    print(f"Kvote: {len(out)} događaja, preostalo kredita: {h.get('x-requests-remaining')}")

def fresh(path):
    d = read(path)
    try: age = (datetime.now(timezone.utc) - datetime.strptime(d["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 60
    except Exception: return False
    return age < MIN_AGE

def main():
    if not KEY: sys.exit("APISPORTS_KEY nije podešen (GitHub Secret).")
    ok, budget = 0, {"left": MAX_BOX, "broken": False}
    try:
        for lg in LEAGUES:
            out = "data/site.json" if lg == "euroleague" else f"data/{lg}/site.json"
            if fresh(out): print(f"{lg}: osveženo pre manje od {MIN_AGE} min, preskačem"); ok += 1; continue
            try: build_site(lg, out, budget); ok += 1
            except QuotaOut: raise
            except Exception as ex: print(f"{lg}: GREŠKA {ex}")
    except QuotaOut as ex:
        print("API-Sports dnevni limit potrošen:", ex)
    print("API-Sports preostalo poziva:", stat["remaining"])
    if ODDS_KEY:
        try: build_odds()
        except Exception as ex: print("Kvote GREŠKA:", ex)
    if not ok: sys.exit("NIJEDNA liga nije uspela, pogledaj greške iznad.")

if __name__ == "__main__": main()

