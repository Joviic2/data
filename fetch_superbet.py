#!/usr/bin/env python3
"""Superbet (kosarka, prematch) -> kvote po ligama   (bez browsera, obican GET na njihov javni offer feed)

IZLAZ (isto pravilo kao sajt, funkcija lp()): Evroliga data/odds_superbet.json, a ostale lige data/<liga>/odds_superbet.json
  (data/aba/, data/acb/, data/lnb/, data/bbl/). Svaki fajl ima samo mecove te lige, u istom obliku kao do sada, uz polje "lg".
Liga meca se odredjuje redom: (1) ime turnira u feedu, (2) mec postoji u rasporedu te lige (data/<liga>/site.json, +-36h),
  (3) samo ako feed ne daje ime turnira: oba kluba pripadaju tacno jednoj ligi, (4) vecinski glas iste "comp" oznake.
  Python fetch_superbet.py --leagues  ispisuje svaki turnir iz feeda i u koju ligu je upao (za podesavanje kljucnih reci ispod).

Pokretanje (iz korena repoa):
  python fetch_superbet.py                  # jedan prolaz
  python fetch_superbet.py --loop 600 --push  # na svojoj masini: svakih ~10 min + git push samo tog fajla
Moze i u GitHub Action-u (korak 'python fetch_superbet.py'), ako te njihov feed ne blokira sa tih adresa.
Ako dobijes 403/429 ili prazan odgovor: STANI, ne zaobilazi. Drzi razmak >= 5 min i proveri uslove koriscenja Superbet-a.
Hendikep i ukupno se citaju iz detalja meca (events?events=<id>), samo za mecove Evrolige (prepoznaju se po data/site.json).
Ovaj feed (index=active-prematch) vraca samo "preselected" trziste (pobednik). Hendikep i ukupno su u odgovoru
za pojedinacni mec; parser ih vec cita ako stignu, a ako ne, ispisuje imena trzista koja nije prepoznao.

PLAYER PROPS (poeni / skokovi / asistencije): citaju se iz istog detalja meca, cim se pojave u ponudi (~48h pre meca).
Upisuju se u svaki mec kao  "player_props":[{"p":"Ime Prezime","s":"pts|reb|ast","l":linija,"o":over,"u":under,"main":true}]
("main" = linija najblizа 50/50 za tog igraca i statistiku, ostale su alternativne). Kombinovana trzista (poeni+skokovi...) se preskacu.
Ako Superbet imenuje trzista drugacije nego sto parser ocekuje, pokreni:  python fetch_superbet.py --dump
(snimi sirov odgovor detalja prvog meca u data/superbet_dump.json i ispise sva imena trzista)."""
import argparse, collections, datetime, json, os, random, re, subprocess, sys, time, unicodedata, urllib.request, urllib.error

URL = ("https://production-superbet-offer-rs.freetls.fastly.net/sb-rs/api/v3/sr-Latn-RS/events"
       "?startDate={s}&endDate={e}&index=active-prematch&sports=4")
DETAIL = ("https://production-superbet-offer-rs.freetls.fastly.net/sb-rs/api/v3/sr-Latn-RS/events"
          "?events={id}&includeOnly=fixture,inPlayStats,inPlayStatsMetadata,markets,priceboosts,results,superbets")
# kljucne reci traze se u imenu turnira iz feeda (bez dijakritika, malim slovima). Element moze biti i tuple = SVE reci moraju biti prisutne.
# Kratke reci (do 3 slova: lnb, bbl, acb, aba) traze se kao cela rec. "no" iskljucuje (zenske, druge divizije, kupovi...).
# Dodatne reci bez menjanja koda:  --alias lnb="lnb pro a" --alias bbl="nemacka 1"
LEAGUES = {
    "euroleague": {"site": "data/site.json", "out": "data/odds_superbet.json",
                   "names": ["euroleague", "euroliga", "evroliga"], "no": ["eurocup", "evrokup", "zensk", "women", "u18", "u20"]},
    "aba": {"site": "data/aba/site.json", "out": "data/aba/odds_superbet.json",
            "names": ["aba liga", "aba league", "aba 1", "jadransk", "adriatic", ("aba", "liga")], "no": ["aba 2", "aba2", "zensk", "women", "u19", "u21"]},
    "acb": {"site": "data/acb/site.json", "out": "data/acb/odds_superbet.json",
            "names": ["acb", "liga endesa", "spanija", "spain", "spanish"], "no": ["leb", "copa", "kup", "cup", "supercopa", "zensk", "women", "u22"]},
    "lnb": {"site": "data/lnb/site.json", "out": "data/lnb/odds_superbet.json",
            "names": ["lnb", "betclic elite", "francuska", "france", "french", ("pro a", "franc")],
            "no": ["pro b", "espoirs", "zensk", "women", "u21", "kup", "cup", "coupe", "leaders"]},
    "bbl": {"site": "data/bbl/site.json", "out": "data/bbl/odds_superbet.json",
            "names": ["bbl", "bundesliga", "nemacka", "germany", "german", "easycredit"],
            "no": ["pro a", "pro b", "2. ", "zensk", "women", "u19", "nbbl", "kup", "pokal", "cup", "regionalliga", "franc"]},
}


def kw_hit(text, kw):
    if isinstance(kw, tuple):
        return all(kw_hit(text, k) for k in kw)
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text)) if len(kw) <= 3 and kw.isalpha() else kw in text
PROP_STATS = (("pts", ("poen", "point", "pts")), ("reb", ("skok", "rebound", "reb")), ("ast", ("asist", "assist", "ast")))
PROP_HINT = ("igrač", "igrac", "player")
PROP_NAME_KEYS = ("player", "player_name", "playername", "competitor", "participant", "name")
SKIP = ("poluvreme", "half", "četvrt", "cetvrt", "quarter", "tim ", "tima", "igrač", "igrac", "1. pol", "2. pol")


def num(x):
    try:
        return float(str(x).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; personal-odds-tracker/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def events_in(j, out=None):
    """Svi dict-ovi koji imaju 'fixture' i 'markets' (bez obzira na omotac)."""
    out = [] if out is None else out
    if isinstance(j, dict):
        if "fixture" in j and "markets" in j:
            out.append(j)
        else:
            for v in j.values():
                events_in(v, out)
    elif isinstance(j, list):
        for v in j:
            events_in(v, out)
    return out


def kind_of(name):
    m = name.lower()
    if any(s in m for s in SKIP):
        return None
    if "hendikep" in m or "handicap" in m:
        return "sp"
    if any(s in m for s in ("ukupno", "total", "više/manje", "vise/manje")):
        return "tt"
    if "pobednik" in m or "konačan ishod" in m or "konacan ishod" in m:
        return "h2h"
    return None


def line_of(odd, mname):
    md = odd.get("metadata") or {}
    sp_ = md.get("specifiers") or {}
    for k in ("total", "hcp", "handicap", "line", "points"):
        v = num(sp_.get(k))
        if v is not None:
            return v
    for k in ("special_bet_value", "spec_bet_value", "specialBetValue", "handicap", "line", "param", "points"):
        v = num(md.get(k))
        if v is not None:
            return v
    nums = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(md.get("info", "")) + " " + mname)
    return num(nums[-1]) if nums else None


def prop_stat(mname):
    """'pts'/'reb'/'ast' ako je trziste igraca za TACNO jednu statistiku, inace None (kombinacije i timska trzista se preskacu)."""
    m = mname.lower()
    if any(x in m for x in ("poluvreme", "half", "četvrt", "cetvrt", "quarter", "1. pol", "2. pol", "prvi ", "drugi ")):
        return None
    hit = [k for k, ws in PROP_STATS if any(w in m for w in ws)]
    if len(hit) != 1 or re.search(r"\+|\bi\b|\band\b|dabl|double|triple", m):
        return None
    if re.search(r"\btim\b|\btima\b|\bteam\b|ukupno poena na|ukupno poena u", m) and not any(h in m for h in PROP_HINT):
        return None
    return hit[0]


def prop_player(mk, o, mname):
    """Ime igraca: specifier/metadata kosa -> ime trzista bez reci o statistici -> info."""
    md = o.get("metadata") or {}
    sp_ = md.get("specifiers") or {}
    for src in (sp_, mk.get("metadata") or {}, mk):
        for k in PROP_NAME_KEYS[:5]:
            v = src.get(k) if isinstance(src, dict) else None
            if isinstance(v, str) and len(v) > 3:
                return v.strip()
    n = re.sub(r"(?i)\b(ukupno|ukupan|broj|više|vise|manje|over|under|igrač|igrac|player|poena|poeni|points?|skokova|skokovi|skok|rebounds?|asistencija|asistencije|asists?|assists?|igrača|igraca|na meču|na mecu)\b", " ", mname)
    n = re.sub(r"[·•:|\-–—()/\d.,+]+", " ", n)
    n = " ".join(n.split())
    if len(n) > 3:
        return n
    info = str(md.get("info", ""))
    info = " ".join(re.sub(r"[·•:|\d.,+()\-]+", " ", re.sub(r"(?i)više|vise|manje|over|under|preko|ispod", " ", info)).split())
    return info if len(info) > 3 else None


def parse_props(ev, diag):
    """[{p,s,l,o,u,main}] iz trzista igraca jednog meca."""
    got = {}
    for mk in ev.get("markets") or []:
        mname = mk.get("name", "")
        stat = prop_stat(mname)
        if stat is None:
            continue
        for o in mk.get("odds") or []:
            p = num(o.get("price"))
            if p is None or not 1.01 < p <= 30 or o.get("status", 1) != 1 or o.get("display") is False:
                continue
            md = o.get("metadata") or {}
            code = str(md.get("code", md.get("name", ""))).strip().lower()
            nm = (str(md.get("name", "")) + " " + str(md.get("info", ""))).lower()
            side = ("o" if code in ("+", "o", "over") or any(x in nm for x in ("više", "vise", "over", "preko"))
                    else "u" if code in ("-", "u", "under") or any(x in nm for x in ("manje", "under", "ispod")) else None)
            ln = line_of(o, mname)
            who = prop_player(mk, o, mname)
            if not side or ln is None or not who or not 0.5 <= ln <= 60:
                diag["prop_neprepoznat:" + mname[:45]] += 1
                continue
            gk = (who.lower(), stat, md.get("market_line_uuid") or ln)
            got.setdefault(gk, {"p": who, "s": stat, "l": ln})[side] = p
    byk = collections.defaultdict(list)
    for g in got.values():
        if "o" in g and "u" in g:
            byk[(g["p"].lower(), g["s"])].append(g)
    out = []
    for lst in byk.values():
        lst.sort(key=lambda g: abs(1 / g["o"] - 1 / g["u"]))
        for i, g in enumerate(lst[:4]):
            out.append({"p": g["p"], "s": g["s"], "l": g["l"], "o": g["o"], "u": g["u"], "main": i == 0})
    out.sort(key=lambda g: (g["p"], g["s"], not g["main"], g["l"]))
    diag["prop_linija"] += len(out)
    return out


def parse_event(ev, diag):
    f = ev.get("fixture") or {}
    parts = re.split(r"\s*[·•]\s*", f.get("event_name", ""), maxsplit=1)
    if len(parts) != 2:
        diag["bez_imena"] += 1
        return None
    h2h, sp, tt = {}, collections.defaultdict(dict), collections.defaultdict(dict)
    for mk in ev.get("markets") or []:
        mname = mk.get("name", "")
        kind = kind_of(mname)
        if kind is None:
            if prop_stat(mname) is None:
                diag["nepoznato:" + mname[:45]] += 1
            continue
        groups = collections.defaultdict(dict)
        for o in mk.get("odds") or []:
            p = num(o.get("price"))
            if p is None or not 1.01 < p <= 60 or o.get("status", 1) != 1 or o.get("display") is False:
                continue
            md = o.get("metadata") or {}
            code = str(md.get("code", md.get("name", ""))).strip().lower()
            if kind == "h2h":
                if code in ("1", "home"):
                    h2h["h"] = p
                elif code in ("2", "away"):
                    h2h["a"] = p
                continue
            ln = line_of(o, mname)
            gk = md.get("market_line_uuid") or ("l", abs(ln) if ln is not None else None)
            if kind == "sp":
                side = "h" if code in ("1", "home") else "a" if code in ("2", "away") else None
            else:
                nm = (str(md.get("name", "")) + " " + str(md.get("info", ""))).lower()
                side = ("o" if code in ("+", "o", "over") or any(x in nm for x in ("više", "vise", "over", "preko"))
                        else "u" if code in ("-", "u", "under") or any(x in nm for x in ("manje", "under", "ispod")) else None)
            if side:
                groups[gk][side] = (p, ln)
        for g in groups.values():
            if kind == "sp" and "h" in g and "a" in g:       # linija domacina = granica ishoda "1"
                ln = g["h"][1]
                if ln is not None and abs(ln) <= 40 and (ln * 2) % 1 == 0:
                    sp[ln] = {"h": g["h"][0], "a": g["a"][0]}
            elif kind == "tt" and "o" in g and "u" in g:
                ln = g["o"][1] if g["o"][1] is not None else g["u"][1]
                if ln is not None and 100 <= ln <= 260 and (ln * 2) % 1 == 0:
                    tt[ln] = {"o": g["o"][0], "u": g["u"][0]}
    pairs = lambda d, a, b: sorted(((l, v[a], v[b]) for l, v in d.items() if a in v and b in v), key=lambda x: abs(1 / x[1] - 1 / x[2]))
    SP, TT = pairs(sp, "h", "a"), pairs(tt, "o", "u")
    H = [h2h["h"], h2h["a"]] if "h" in h2h and "a" in h2h else None
    if not (H or SP or TT or parse_props(ev, collections.Counter())):
        diag["bez_kvota"] += 1
        return None
    r = compose(parts[0].strip(), parts[1].strip(), f.get("utc_date"), f"t{f.get('tournament_id', '')}", H, SP, TT)
    pp = parse_props(ev, diag)
    if pp:
        r["player_props"] = pp
    return r


def closeness(x):
    return abs(1 / x[1] - 1 / x[2])


def compose(h, a, start, comp, H, SP, TT):
    SP, TT = sorted(SP, key=closeness), sorted(TT, key=closeness)
    b = [{"h2h": H, "sp": list(SP[0]) if SP else None, "tt": list(TT[0]) if TT else None}]
    b += [{"h2h": None, "sp": list(x), "tt": None} for x in SP[1:6]] + [{"h2h": None, "sp": None, "tt": list(x)} for x in TT[1:6]]
    return {"h": h, "a": a, "start": start, "comp": comp, "b": b}


def merge(r, d):
    """Spaja kvote iz liste (pobednik) i iz detalja meca (hendikep, ukupno); linije se ne dupliraju."""
    H = r["b"][0]["h2h"] or d["b"][0]["h2h"]
    sp = {x["sp"][0]: tuple(x["sp"]) for x in r["b"] + d["b"] if x["sp"]}
    tt = {x["tt"][0]: tuple(x["tt"]) for x in r["b"] + d["b"] if x["tt"]}
    m = compose(r["h"], r["a"], r["start"], r["comp"], H, list(sp.values()), list(tt.values()))
    pp = d.get("player_props") or r.get("player_props")      # igraci dolaze iz detalja meca
    if pp:
        m["player_props"] = pp
    return m


def event_id(ev):
    f = ev.get("fixture") or {}
    for src in (f, ev):
        for k in ("match_id", "matchId", "event_id", "eventId", "id"):
            if src.get(k) not in (None, ""):
                return src[k]
    return None


STOP = {"basket", "basketball", "club", "team", "baloncesto", "kosarka", "kosarkaski"}
WIN_MS = 36 * 3600 * 1000


def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode().lower()


def tokens(n):
    return {w for w in re.split(r"[^a-z0-9]+", norm(n)) if len(w) >= 4 and w not in STOP}


def share(a, b):
    """Dva skupa reci se poklapaju ako dele rec ili imaju isti pocetak od 4+ slova (Barca ~ Barcelona)."""
    return any(x == y or (x[:4] == y[:4] and len(x) >= 4 and len(y) >= 4 and x[:4] not in ("real", "club")) for x in a for y in b)


def to_ms(x):
    try:
        return datetime.datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp() * 1000
    except (TypeError, ValueError):
        return None


_data = None


def league_data():
    """Za svaku ligu: tokeni klubova (po kodu) i raspored/odigrane utakmice iz njenog site.json (ako postoji u repou)."""
    global _data
    if _data is None:
        _data = {}
        for lg, c in LEAGUES.items():
            try:
                d = json.load(open(c["site"], encoding="utf-8"))
            except (OSError, ValueError):
                continue
            clubs = {k: tokens(f"{v.get('name', '')} {v.get('short', '')}") for k, v in (d.get("clubs") or {}).items()}
            games = [(g.get("h"), g.get("a"), to_ms(g.get("utc") or g.get("dt"))) for g in (d.get("fixtures") or []) + (d.get("games") or [])]
            _data[lg] = {"clubs": clubs, "games": games}
    return _data


def tour_text(ev):
    """Imena turnira/kategorije iz fixture-a (polja cije ime sadrzi tournament/competition/league/category)."""
    out = []

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k.lower())
        elif isinstance(o, str) and any(w in key for w in ("tournament", "competition", "league", "category")) and "id" not in key:
            out.append(o)
    walk(ev.get("fixture") or {})
    return norm(" | ".join(dict.fromkeys(out)))


def classify(r, text):
    """(liga, nacin) ili (None, razlog)."""
    L = league_data()
    hit = [lg for lg, c in LEAGUES.items() if any(kw_hit(text, w) for w in c["names"]) and not any(kw_hit(text, w) for w in c["no"])]
    if len(hit) == 1:
        return hit[0], "ime"
    th, ta, t0 = tokens(r["h"]), tokens(r["a"]), to_ms(r.get("start"))
    byfix = []
    for lg, d in L.items():
        for h, a, ms in d["games"]:
            if h in d["clubs"] and a in d["clubs"] and share(th, d["clubs"][h]) and share(ta, d["clubs"][a]) and t0 and ms and abs(ms - t0) < WIN_MS:
                byfix.append(lg)
                break
    if len(byfix) == 1:
        return byfix[0], "raspored"
    if text and not hit:
        return None, "turnir_nepoznat"       # feed imenuje turnir koji nije nijedna od nasih liga (EuroCup, LBA...)
    both = [lg for lg, d in L.items() if any(share(th, t) for t in d["clubs"].values()) and any(share(ta, t) for t in d["clubs"].values())]
    if not text and len(both) == 1:
        return both[0], "klub"
    return None, "dvosmisleno"


def assign(out, diag, overrides):
    """Dodeljuje r['lg'] svakom meču; vraca {tekst turnira: Counter(liga)} za ispis."""
    seen = collections.defaultdict(collections.Counter)
    votes = collections.defaultdict(collections.Counter)
    for r in out:
        text = r.pop("_tour", "")
        lg, how = overrides.get(r["comp"]), "comp(--comp)"
        if not lg:
            lg, how = classify(r, text)
        r["lg"], r["_how"], r["_text"] = lg, how, text
        if lg and how in ("ime", "raspored", "comp(--comp)"):
            votes[r["comp"]][lg] += 1
    for r in out:                                   # preostali: vecinski glas iste comp oznake
        if not r["lg"] and r["comp"] in votes:
            lg, n = votes[r["comp"]].most_common(1)[0]
            if n >= 2 and n / sum(votes[r["comp"]].values()) >= .8:
                r["lg"], r["_how"] = lg, "comp"
        diag["liga:" + (r["lg"] or "nedodeljeno")] += 1
        seen[r["_text"] or "(feed bez imena turnira)"][r["lg"] or "-"] += 1
    return seen


HINT = ("tournament", "competition", "league", "category", "country", "region", "sport")


def raw_events(j, out=None):
    """Svaki dict sa kljucem 'fixture' (i bez markets), da vidimo i mecove koje parser odbaci."""
    out = [] if out is None else out
    if isinstance(j, dict):
        if "fixture" in j:
            out.append(j)
        else:
            for v in j.values():
                raw_events(v, out)
    elif isinstance(j, list):
        for v in j:
            raw_events(v, out)
    return out


def flat(o, pre=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from flat(v, f"{pre}{k}.")
    elif isinstance(o, (str, int, float)) and not isinstance(o, bool):
        yield pre[:-1], o


# Podsetnik za citanje ID-jeva (feed ne salje imena turnira): reci iz imena klubova koje "provlacimo" kroz svaki turnir.
# Samo savet u ispisu, NE koristi se za automatsku dodelu lige (za to sluzi --comp ili data/superbet_comps.json).
HINTS = {
    "LNB (Francuska)": ("asvel", "villeurbanne", "monaco", "paris basket", "le mans", "nanterre", "strasbourg", "limoges", "dijon", "chalon",
                        "boulazac", "cholet", "gravelines", "portel", "bourg", "nancy", "orleans", "roanne", "levallois", "vichy", "fos provence",
                        "evreux", "rouen", "saint-chamond", "blois", "poitiers", "saint-quentin"),
    "BBL (Nemacka)": ("bayern", "alba berlin", "ulm", "telekom", "bonn", "chemnitz", "niners", "rostock", "seawolves", "ludwigsburg", "riesen",
                      "wurzburg", "gottingen", "hamburg", "towers", "heidelberg", "oldenburg", "bamberg", "brose", "braunschweig", "lowen",
                      "trier", "vechta", "crailsheim", "merlins", "skyliners", "frankfurt", "tubingen", "tigers", "bremerhaven", "bayreuth"),
    "TBSL (Turska)": ("galatasaray", "fenerbahce", "besiktas", "anadolu efes", "trabzonspor", "tofas", "bahcesehir", "turk telekom", "karsiyaka",
                      "bursaspor", "manisa", "petkim", "aliaga", "samsunspor", "gaziantep", "merkezefendi", "yalova", "mersin", "uskudar", "buyukcekmece"),
    "ACB (Spanija)": ("real madrid", "barca", "barcelona", "baskonia", "valencia", "unicaja", "joventut", "obradoiro", "manresa", "andorra", "bilbao",
                      "tenerife", "girona", "zaragoza", "murcia", "lleida", "burgos", "breogan", "coruna"),
    "ABA (Jadran)": ("partizan", "crvena zvezda", "zvezda", "buducnost", "cedevita", "olimpija", "mega", "split", "dubrava", "igokea", "borac", "spartak", "studentski"),
}


def raw_report(j, a, parsed):
    """--leagues: SVI tournament_id iz sirovog feeda (pre filtriranja): broj mecova, primeri mecova, savet lige i trenutna dodela."""
    evs = raw_events(j)
    print(f"SIROV FEED: {len(evs)} dogadjaja (sa 'fixture'); {sum(1 for e in evs if not e.get('markets'))} bez trzista\n")
    cur = collections.defaultdict(collections.Counter)
    for r in parsed:
        cur[r["comp"]][r["lg"] or "-"] += 1
    G = {}
    for e in evs:
        fx = e.get("fixture") or {}
        tid = fx.get("tournament_id", "?")
        g = G.setdefault(str(tid), {"n": 0, "ok": 0, "names": [], "ex": [], "cat": fx.get("category_id")})
        g["n"] += 1
        g["ok"] += 1 if e.get("markets") else 0
        nm_ = fx.get("event_name", "?")
        g["names"].append(nm_)
        if len(g["ex"]) < 3:
            g["ex"].append(f"{nm_} ({str(fx.get('utc_date', ''))[5:16].replace('T', ' ')})")
    rows = []
    for tid, g in G.items():
        women = all("(Ž)" in n or "(W)" in n or "(Z)" in n for n in g["names"])
        score = {}
        for lab, words in HINTS.items():
            m = sum(1 for n in g["names"] if any(w in norm(n) for w in words))
            if m:
                score[lab] = m
        best = max(score.items(), key=lambda x: x[1]) if score else None
        rows.append({"id": tid, "n": g["n"], "ok": g["ok"], "ex": g["ex"], "women": women, "cat": g["cat"], "hint": best,
                     "cur": ", ".join(f"{k}:{v}" for k, v in cur.get("t" + tid, collections.Counter()).most_common())})
    line = lambda r: (f"  id={r['id']:<7} {r['n']:3d} mec. ({r['ok']} sa trz.) kat={r['cat']} {'[ZENSKA] ' if r['women'] else ''}"
                      f"{('dodeljeno: ' + r['cur'] + ' ') if r['cur'] else ''}| " + " / ".join(r["ex"]))
    full = [line(r) for r in sorted(rows, key=lambda r: int(r["id"]) if str(r["id"]).isdigit() else 0)]
    os.makedirs("data", exist_ok=True)
    open(os.path.join("data", "superbet_turniri.txt"), "w", encoding="utf-8").write("\n".join(full) + "\n")
    print(f"SVI TURNIRI IZ FEEDA: {len(rows)} (sortirano po ID-ju; ista lista je u data/superbet_turniri.txt):")
    print("\n".join(full))
    print(f"\n{'=' * 100}\nPREDLOG PO KLUBOVIMA (samo savet; ne dodeljuje se automatski). Muski turniri, najpre oni sa najvise pogodaka:")
    shown = False
    for lab in HINTS:
        cand = sorted([r for r in rows if r["hint"] and r["hint"][0] == lab and not r["women"]], key=lambda r: -r["hint"][1])
        for r in cand[:6]:
            shown = True
            print(f"  {lab:16} id={r['id']:<7} {r['hint'][1]}/{r['n']} mecova se poklapa sa klubovima | {' / '.join(r['ex'])}")
    if not shown:
        print("  nijedan turnir nema klubove iz podsetnika.")
    print("\nKad nadjes ID:   py fetch_superbet.py --comp lnb=ID --comp bbl=ID1,ID2     (ili trajno u data/superbet_comps.json: {\"lnb\":[ID],\"bbl\":[ID]})")
    if a.grep:
        w = norm(a.grep)
        print(f"\nPRETRAGA '{a.grep}' u celom feedu (putanja = vrednost):")
        hits = []

        def walk(o, path):
            if isinstance(o, dict):
                for k, v in o.items():
                    if w in norm(k):
                        hits.append((path + "/" + str(k), "(kljuc)"))
                    walk(v, path + "/" + str(k))
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    walk(v, path + f"[{i}]")
            elif w in norm(o):
                hits.append((path, str(o)))
        walk(j, "")
        for pth, v in hits[:60]:
            print(f"  {pth[-110:]} = {v[:100]}")
        print(f"  ukupno pogodaka: {len(hits)}" + (" (prikazano 60)" if len(hits) > 60 else ""))
    if a.raw:
        json.dump(j, open(os.path.join("data", "superbet_feed.json"), "w", encoding="utf-8"), ensure_ascii=False)
        print("\nSirov feed snimljen u data/superbet_feed.json.")


def run(a=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    s = now.strftime("%Y-%m-%dT00:00:00.000Z")
    e = (now + datetime.timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000Z")
    j = get(URL.format(s=s, e=e).replace("index=active-prematch", "index=" + (a.index if a else "active-prematch")).replace("sports=4", "sports=" + str(a.sports if a else 4)))
    diag, out = collections.Counter(), []
    for ev in events_in(j):
        diag["mecevi"] += 1
        r = parse_event(ev, diag)
        if r:
            r["eid"] = event_id(ev)
            r["_tour"] = tour_text(ev)
            out.append(r)
    overrides = {}                                  # --comp lnb=123 --comp bbl=456,457  (bez "lg=" = Evroliga); + data/superbet_comps.json
    try:
        for lg, ids in json.load(open(os.path.join("data", "superbet_comps.json"), encoding="utf-8")).items():
            for i in (ids if isinstance(ids, list) else [ids]):
                overrides["t" + str(i).strip().lstrip("t")] = lg
    except (OSError, ValueError, AttributeError):
        pass
    for arg in (a.comp if a else []):
        for part in str(arg).split(";"):
            part = part.strip()
            if part:
                lg, _, ids = part.partition("=") if "=" in part else ("euroleague", "", part)
                for i in ids.split(","):
                    if i.strip():
                        overrides["t" + i.strip().lstrip("t")] = lg.strip()
    bad = [lg for lg in overrides.values() if lg not in LEAGUES]
    if bad:
        sys.exit(f"nepoznata liga u mapiranju: {sorted(set(bad))}; dozvoljene: {list(LEAGUES)}")
    seen = assign(out, diag, overrides)
    if a and (a.leagues or a.grep or a.raw):
        raw_report(j, a, out)
    if a and a.leagues:
        print("\nDODELA LIGA (samo mecevi sa prepoznatim kvotama; tekst turnira -> liga: broj mecova):")
        for t, c in sorted(seen.items(), key=lambda x: -sum(x[1].values())):
            print(f"  {t[:70]!r:75} -> " + ", ".join(f"{k}: {v}" for k, v in c.most_common()))
    if a and a.detail and not (a.leagues or a.grep or a.raw):      # detalj (hendikep, ukupno, igraci) za mecove prepoznatih liga u narednih --hours sati, maks. --max po prolazu
        lim = (now + datetime.timedelta(hours=a.hours)).timestamp() * 1000
        pick = [x for x in out if x["lg"] and (to_ms(x["start"]) or 0) <= lim and (to_ms(x["start"]) or 0) >= now.timestamp() * 1000 - 3 * 3600e3]
        pick.sort(key=lambda x: x["start"] or "")
        diag["za_detalj"] = len(pick)
        diag["bez_id"] = sum(1 for x in pick if x.get("eid") in (None, ""))
        for r in [x for x in pick if x.get("eid") not in (None, "")][:a.max]:
            time.sleep(random.uniform(1.5, 3))
            jd = get(a.detail.format(id=r["eid"], s=s, e=e))      # 403/429 prekida ceo prolaz
            if a.dump and not diag["dump"]:
                diag["dump"] = 1
                json.dump(jd, open(os.path.join("data", "superbet_dump.json"), "w", encoding="utf-8"), ensure_ascii=False)
                names = sorted({m.get("name", "") for x in events_in(jd) for m in x.get("markets") or []})
                print(f"DUMP {r['h']} - {r['a']} ({r['lg']}): {len(names)} trzista:\n  " + "\n  ".join(names))
            ds = [parse_event(x, diag) for x in (events_in(jd) or [])]
            ds = [x for x in ds if x]
            if ds:
                keep = {k: r[k] for k in ("lg", "_how", "_text")}
                new = merge(r, ds[0]); new["eid"] = r["eid"]; r.update(new); r.update(keep); diag["detalj_ok"] += 1
            else:
                diag["detalj_prazan"] += 1
    for r in out:
        r.pop("_how", None), r.pop("_text", None)
    return out, diag


def write(events):
    """Pise po jedan fajl za svaku ligu (samo za lige ciji site.json postoji ili koje imaju mecove). Vraca listu fajlova."""
    by = collections.defaultdict(list)
    for e in events:
        if e.get("lg"):
            by[e["lg"]].append(e)
    files = []
    for lg, c in LEAGUES.items():
        if not by.get(lg) and lg not in league_data():
            continue                      # liga nije u repou i nema kvota: ne pravim prazan fajl
        os.makedirs(os.path.dirname(c["out"]), exist_ok=True)
        tmp = c["out"] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched": int(time.time()), "source": "superbet.rs", "league": lg, "events": by.get(lg, [])}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, c["out"])
        files.append(c["out"])
        print(f"  {lg}: {len(by.get(lg, []))} mecova, {sum(len(e.get('player_props', [])) for e in by.get(lg, []))} prop linija -> {c['out']}")
    return files


def push(files):
    run_ = lambda *c: subprocess.run(c, capture_output=True, text=True)
    run_("git", "add", *files)
    if run_("git", "diff", "--cached", "--quiet", "--", *files).returncode == 0:
        return
    run_("git", "commit", "-m", "Superbet odds", "--", *files)
    for i in range(3):
        run_("git", "pull", "--rebase", "--autostash", "origin", "main")
        if run_("git", "push", "origin", "HEAD:main").returncode == 0:
            return
        time.sleep(5 * (i + 1))
    print("push nije uspeo")


def once(a):
    try:
        ev, diag = run(a)
    except urllib.error.HTTPError as ex:
        sys.exit(f"HTTP {ex.code}: feed odbija zahtev. Ne zaobilazim; pokusaj kasnije ili sa svoje masine.")
    if a.leagues or a.grep or a.raw:
        print("\n(dijagnostika: ne pisem fajlove sa kvotama)")
        return
    mine = [e for e in ev if e.get("lg")]
    print(f"{datetime.datetime.now():%H:%M:%S} mecevi u feedu {diag['mecevi']}, sa prepoznatim kvotama {len(ev)}, u nasim ligama {len(mine)}")
    for e in mine[:40]:
        b = e["b"][0]
        print(f"  [{e['lg']}] {e['h']} - {e['a']} | {e['start']} | 1/2 {b['h2h']} | hend {b['sp']} | ukupno {b['tt']} | igraci {len(e.get('player_props', []))}")
    bad = [(k, v) for k, v in diag.most_common(14) if k != "mecevi"]
    if bad:
        print("Neprepoznato/dijagnostika:", bad)
    if not mine:
        print("Nista prepoznato ni u jednoj nasoj ligi: ne pisem fajlove.")
        return
    files = write(mine)
    if a.push and files:
        push(files)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="sekundi izmedju prolaza (min 300)")
    ap.add_argument("--detail", default=DETAIL, help="URL detalja meca sa {id}; '' = iskljuceno")
    ap.add_argument("--comp", action="append", default=[], help="mapiranje po tournament_id: --comp lnb=123 --comp bbl=456,457 (moze vise puta); trajno u data/superbet_comps.json")
    ap.add_argument("--hours", type=int, default=96, help="detalj (hendikep/ukupno/props) samo za mecove u narednih N sati")
    ap.add_argument("--max", type=int, default=120, help="najvise detalj-poziva po prolazu (razmak 1.5-3 s izmedju)")
    ap.add_argument("--leagues", action="store_true", help="dijagnostika: ispisi SVE turnire iz sirovog feeda (ID, ime, broj mecova) i dodelu liga; ne pise fajlove")
    ap.add_argument("--grep", default="", help="dijagnostika: nadji tekst u celom sirovom feedu (npr. --grep lnb ili --grep bundesliga) i ispisi putanju polja")
    ap.add_argument("--raw", action="store_true", help="dijagnostika: snimi ceo sirov feed u data/superbet_feed.json")
    ap.add_argument("--alias", action="append", default=[], help="dodaj kljucnu rec za ligu: --alias lnb=\"lnb pro a\" (moze vise puta)")
    ap.add_argument("--sports", default="4", help="id sporta u feedu (kosarka = 4)")
    ap.add_argument("--index", default="active-prematch", help="index feeda (npr. active-prematch)")
    ap.add_argument("--dump", action="store_true", help="snimi sirov detalj prvog meca (data/superbet_dump.json) i ispisi imena svih trzista")
    ap.add_argument("--push", action="store_true", help="git commit+push samo fajlova sa kvotama")
    a = ap.parse_args()
    for al in a.alias:
        lg, _, kw = al.partition("=")
        if lg.strip() in LEAGUES and kw.strip():
            LEAGUES[lg.strip()]["names"].append(norm(kw.strip()))
        else:
            sys.exit(f"--alias {al!r}: ocekujem liga=tekst, liga je jedna od {list(LEAGUES)}")
    if not a.loop:
        once(a)
    else:
        while True:
            try:
                once(a)
            except SystemExit as e:
                print(e)
                break
            except Exception as e:
                print("greska:", e)
            time.sleep(max(300, a.loop) * random.uniform(.9, 1.2))
