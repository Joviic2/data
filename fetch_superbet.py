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
Svaki prolaz cuva i odds_history.jsonl po ligi; za automatsko osvezavanje lokalno koristi --loop 600 --push.
Ako dobijes 403/429 ili prazan odgovor: STANI, ne zaobilazi. Drzi razmak >= 5 min i proveri uslove koriscenja Superbet-a.
Hendikep i ukupno se citaju iz detalja meca (events?events=<id>), samo za mecove Evrolige (prepoznaju se po data/site.json).
Ovaj feed (index=active-prematch) vraca samo "preselected" trziste (pobednik). Hendikep i ukupno su u odgovoru
za pojedinacni mec; parser ih vec cita ako stignu, a ako ne, ispisuje imena trzista koja nije prepoznao.

PLAYER PROPS (poeni / skokovi / asistencije): citaju se iz istog detalja meca, cim se pojave u ponudi (~48h pre meca).
Upisuju se u svaki mec kao  "player_props":[{"p":"Ime Prezime","s":"pts|reb|ast","l":linija,"o":over,"u":under,"main":true}]
("main" = linija najblizа 50/50 za tog igraca i statistiku, ostale su alternativne). Kombinovana trzista (poeni+skokovi...) se preskacu.
Ako Superbet imenuje trzista drugacije nego sto parser ocekuje, pokreni:  python fetch_superbet.py --dump
(snimi sirov odgovor detalja prvog meca u data/superbet_dump.json i ispise sva imena trzista)."""
import argparse, collections, datetime, json, math, os, random, re, subprocess, sys, time, unicodedata, urllib.request, urllib.error

import quant_model as Q

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
# regex po CELIM recima: ranije je "ast"/"reb" kao podniz pogadjao imena igraca (npr. Rebic, Castro) i trziste se odbacivalo
PROP_STATS = (("pts", r"\b(poen\w*|points?|pts)\b"), ("reb", r"\b(skok\w*|rebounds?|rebs?)\b"), ("ast", r"\b(asist\w*|assists?|asts?)\b"))
PROP_HINT = ("igrac", "player")
PROP_NAME_KEYS = ("player", "player_name", "playername", "competitor", "participant", "athlete", "name")
PROP_STRIP = re.compile(r"(?i)\b(ukupno|ukupan|broj|vise|više|manje|over|under|preko|ispod|iznad|igrac|igrač|igraca|igrača|player|poena|poeni|poen|points?|pts|"
                        r"skokova|skokovi|skok|rebounds?|rebs?|asistencija|asistencije|asist|assists?|asts?|na meču|na mecu|meču|mecu|utakmici|utakmica|za|na|u|i)\b")
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
    for src in (str(md.get("info", "")), str(md.get("name", "")), str(odd.get("name", "")), mname):   # linija zna da bude u imenu ishoda ("Više 24.5")
        nums = re.findall(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?![\w])", src)
        if nums:
            return num(nums[-1])
    return None


def prop_stat(mname):
    """'pts'/'reb'/'ast' ako je trziste igraca za TACNO jednu statistiku, inace None (kombinacije i timska trzista se preskacu)."""
    m = norm(mname)
    if any(x in m for x in ("poluvreme", "half", "cetvrt", "quarter", "1. pol", "2. pol", "prvi ", "drugi ")):
        return None
    hit = [k for k, rx in PROP_STATS if re.search(rx, m)]
    if len(hit) != 1 or re.search(r"\+|\band\b|dabl|double|triple", m):      # kombinovana trzista (poeni + skokovi...) se preskacu
        return None
    if re.search(r"\btim\b|\btima\b|\bteam\b|ukupno poena (na|u)\b", m) and not any(h in m for h in PROP_HINT):
        return None
    return hit[0]


def clean_name(s):
    n = PROP_STRIP.sub(" ", str(s or ""))
    n = re.sub(r"[·•:|\-–—()/\d.,+_]+", " ", n)
    n = " ".join(n.split())
    return n if len(n) > 3 and re.search(r"[^\W\d_]{2}", n) else None


def prop_player(mk, o, mname):
    """Ime igraca: specifier/metadata kosa -> ime trzista bez reci o statistici -> info."""
    md = o.get("metadata") or {}
    sp_ = md.get("specifiers") or {}
    for src in (sp_, md, mk.get("metadata") or {}, mk):
        for k in PROP_NAME_KEYS[:6]:                       # "name" iz odds/metadata se ne uzima direktno (to je "Više"/"Manje"), samo ostali kljucevi
            v = src.get(k) if isinstance(src, dict) else None
            if isinstance(v, str) and len(v) > 3 and ":" not in v and re.search(r"[^\W\d_]{2}", v) and v.strip().lower() not in ("over", "under", "više", "vise", "manje"):
                return v.strip()
    for cand in (mname, md.get("name"), o.get("name"), md.get("info")):      # ime igraca ugradjeno u tekst trzista/ishoda
        n = clean_name(cand)
        if n:
            return n
    return None


def parse_props(ev, diag):
    """[{p,s,l,o,u,main}] iz trzista igraca jednog meca."""
    got = {}
    ms_got = {}
    for mk in ev.get("markets") or []:
        mname = mk.get("name", "")
        stat = prop_stat(mname)
        if stat is None:
            continue
        for o in (mk.get("odds") or mk.get("outcomes") or []):
            p = num(o.get("price", o.get("odds")))
            if p is None or not 1.01 < p <= 30 or o.get("status", 1) != 1 or o.get("display") is False:
                continue
            md = o.get("metadata") or {}
            code = norm(md.get("code", md.get("name", ""))).strip()
            ms = (md.get("specifiers") or {}).get("milestone")
            if ms is not None:                    # Superbet: "Ime 5+" = N ili vise (samo Over strana) -> linija N-0.5
                try:
                    mv = float(str(ms).replace(",", "."))
                except ValueError:
                    mv = None
                who = prop_player(mk, o, mname)
                if mv is None or not who:
                    diag["prop_neprepoznat:" + mname[:45]] += 1
                    continue
                ms_got.setdefault((who.lower(), stat), {})[mv - 0.5] = (who, p)
                continue
            nm = norm(str(md.get("name", "")) + " " + str(md.get("info", "")) + " " + str(o.get("name", "")))
            side = ("o" if code in ("+", "o", "over") or re.search(r"\b(vise|over|preko|iznad)\b", nm)
                    else "u" if code in ("-", "u", "under") or re.search(r"\b(manje|under|ispod)\b", nm) else None)
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
    for (_, stat), lines in ms_got.items():     # milestone linije: Under se procenjuje (book ~106%), main = Over najblizi 2.00
        best = sorted(lines.items(), key=lambda kv: abs(kv[1][1] - 2.0))[:4]
        best += [kv for kv in sorted(lines.items(), reverse=True)[:2] if kv not in best]      # + 2 najvise linije (za Under vrednost na visokim pragovima)
        for i, (ln, (who, po)) in enumerate(best):
            pu = 1.06 - 1 / po
            if pu <= 0.05:
                continue
            out.append({"p": who, "s": stat, "l": ln, "o": po, "u": round(1 / pu, 2), "main": i == 0, "u_est": True})
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
MKT = collections.Counter()


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
    cpath = os.path.abspath(os.path.join("data", "superbet_comps.json"))
    try:
        for lg, ids in json.load(open(cpath, encoding="utf-8")).items():
            for i in (ids if isinstance(ids, list) else [ids]):
                overrides["t" + str(i).strip().lstrip("t")] = lg
        print(f"mapiranje turnira iz {cpath}: " + ", ".join(f"{v}<-{k}" for k, v in overrides.items()))
    except OSError:
        print(f"UPOZORENJE: nema {cpath} (mapiranje LNB/BBL po tournament_id se ne ucitava; skripta trazi data/ u folderu iz kog je pokrenuta)")
    except (ValueError, AttributeError) as ex:
        print(f"UPOZORENJE: {cpath} nije ispravan JSON ({ex}); ocekujem npr. {{\"lnb\":[217],\"bbl\":[350]}}")
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
    hit = collections.Counter(r["comp"] for r in out)
    for cid, lg in overrides.items():
        print(f"  mapiranje {lg} <- tournament_id {cid[1:]}: {hit.get(cid, 0)} mecova sa prepoznatim kvotama")
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
            for x in events_in(jd):
                for m in x.get("markets") or []:
                    MKT[(r["lg"], m.get("name", ""))] += 1
            if a.dump and not diag["dump"]:
                diag["dump"] = 1
                json.dump(jd, open(os.path.join("data", "superbet_dump.json"), "w", encoding="utf-8"), ensure_ascii=False)
                names = sorted({m.get("name", "") for x in events_in(jd) for m in x.get("markets") or []})
                print(f"DUMP {r['h']} - {r['a']} ({r['lg']}): {len(names)} trzista:\n  " + "\n  ".join(names))
            if a.dump and r["lg"] != "euroleague" and diag["dump_" + r["lg"]] == 0 and any(prop_stat(m.get("name", "")) for x in events_in(jd) for m in x.get("markets") or []):
                diag["dump_" + r["lg"]] = 1          # po jedan dump sa player marketima za svaku ligu
                json.dump(jd, open(os.path.join("data", f"superbet_dump_{r['lg']}.json"), "w", encoding="utf-8"), ensure_ascii=False)
            ds = [parse_event(x, diag) for x in (events_in(jd) or [])]
            ds = [x for x in ds if x]
            if ds:
                keep = {k: r[k] for k in ("lg", "_how", "_text")}
                new = merge(r, ds[0]); new["eid"] = r["eid"]; r.update(new); r.update(keep); diag["detalj_ok"] += 1
            else:
                diag["detalj_prazan"] += 1
    for r in out:
        r.pop("_how", None), r.pop("_text", None)
    try:
        annotate(out, a.analytics if a else AN_DEFAULT, diag)
    except Exception as ex:                          # analitika nikad ne sme da obori preuzimanje kvota
        print("UPOZORENJE: odmor/rotacija preskocena:", ex)
    for lg in LEAGUES:
        league_events = [event for event in out if event.get("lg") == lg]
        if not league_events:
            continue
        try:
            model_diag = Q.annotate_events(league_events, lg, root=".")
            diag["quant_mecevi"] += model_diag.get("mecevi_sa_modelom", 0)
            diag["quant_tipovi"] += model_diag.get("tipova", 0)
            if model_diag.get("premalo_utakmica"):
                print(f"  {lg}: quant model ceka najmanje 8 zavrsenih utakmica (ima {model_diag.get('utakmica_u_modelu', 0)})")
        except Exception as ex:
            print(f"UPOZORENJE: quant model preskocen za {lg}: {ex}")
    if a and a.detail and MKT:                      # sva imena trzista iz detalja po ligi: vidi se sta Superbet zaista nudi za igrace
        os.makedirs("data", exist_ok=True)
        with open(os.path.join("data", "superbet_trzista.txt"), "w", encoding="utf-8") as fh:
            for (lg, nm_), n in sorted(MKT.items(), key=lambda x: (x[0][0] or "", x[0][1])):
                fh.write(f"{lg}\t{n}\t{'PROP:' + prop_stat(nm_) if prop_stat(nm_) else ''}\t{nm_}\n")
        if a.dump:
            print("\nTRZISTA IGRACA koja parser prepoznaje po ligama:", dict(collections.Counter(lg for (lg, n_) in MKT if prop_stat(n_))))
            print("Sva imena trzista: data/superbet_trzista.txt (liga, broj mecova, prepoznato, ime)")
    return out, diag


# ===================== ANALITIKA (faza 1: umor/odmor + rotacije/minutaza) =====================
# Sve konstante su HEURISTIKE (nisu kalibrisane na istoriji klada); menjaj ih ovde pa proveri na backtestu.
AN_DEFAULT = "aba"                              # lige za koje se racuna (--analytics aba,acb ; "" = iskljuceno)
GAME_H = 2.0                                    # trajanje meca (sati) pri racunanju odmora
REST_PEN = ((24, 3.0), (36, 2.2), (48, 1.5), (60, 0.8), (72, 0.4))      # odmor ispod X sati -> penal u poenima razlike
AWAY_X_PEN = {"euroleague": 0.8}                # prethodni mec bio GOSTOVANJE u drugom takmicenju (Evroliga) -> dodatni penal
AWAY_X_DEFAULT = 0.5
DENSE_PEN = 0.8                                 # 3+ meca u 7 dana
MAX_PEN = 4.0
WINP_PER_PT = 0.03                              # ~3% verovatnoce pobede po poenu razlike (oko 50:50)
CONG_H = 72                                     # "dva fronta": mec u drugom takmicenju ranije/kasnije od N sati
ROT_PRIOR = 0.92                                # prior: nosioci igre igraju ~8% manje minuta uz Evropu
ROT_PRIOR_BENCH = 0.97
SIG_EDGE = 0.04                                 # prag signala: model mora biti bar 4 p.p. iznad kladionicke verovatnoce (bilo 10 p.p. za OVER)
SIG_EDGE_UNDER = 0.08                           # UNDER bez "dva fronta" trazi veci prag (model je tu sumovitiji)
ROT_K = 3                                       # tezina priora (u "mecevima")
MIN_STARTER = 22                                # prosek minuta od kojeg je igrac "nosilac"
SD_MIN = {"pts": 2.5, "reb": 1.3, "ast": 1.2}
SD_REL = {"pts": 0.32, "reb": 0.50, "ast": 0.55}
BOX_IDX = {"min": 4, "pts": 5, "reb": 14, "ast": 15}      # indeksi u box[side].p (isti kao u index.htmlu)


def _site(lg):
    try:
        return json.load(open(LEAGUES[lg]["site"], encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_context(feed):
    """Raspored svih timova iz svih site.json + feeda (sve lige i takmicenja) i istorija igraca iz boxscore-a."""
    sched, hist = [], {}
    for lg in LEAGUES:
        d = _site(lg)
        if not d:
            continue
        clubs = {k: (v.get("name") or "") + " " + (v.get("short") or "") for k, v in (d.get("clubs") or {}).items()}
        for g in (d.get("games") or []) + (d.get("fixtures") or []):
            ms = to_ms(g.get("utc") or g.get("dt"))
            if not ms or g.get("h") not in clubs or g.get("a") not in clubs:
                continue
            for me, op, home in ((g["h"], g["a"], True), (g["a"], g["h"], False)):
                sched.append({"tok": tokens(clubs[me]), "ms": ms, "home": home, "lg": lg, "opp": clubs[op].strip()})
        teams = {}
        for g in d.get("games") or []:
            box, ms = g.get("box"), to_ms(g.get("utc") or g.get("dt"))
            if not box or not ms:
                continue
            for side, code in (("h", g.get("h")), ("a", g.get("a"))):
                if code not in clubs or not (box.get(side) or {}).get("p"):
                    continue
                t = teams.setdefault(code, {"tok": tokens(clubs[code]), "pl": {}})
                for p in box[side]["p"]:
                    try:
                        row = tuple(float(p[BOX_IDX[k]] or 0) for k in ("min", "pts", "reb", "ast"))
                    except (TypeError, ValueError, IndexError):
                        continue
                    pl = t["pl"].setdefault(str(p[0]), {"name": str(p[1]), "tk": {w for w in re.split(r"[^a-z]+", norm(p[1])) if len(w) >= 2}, "g": []})
                    pl["g"].append((ms,) + row)
        hist[lg] = list(teams.values())
    for r in feed:                                  # feed: i takmicenja koja nemamo u site.json (EuroCup, ...)
        ms = to_ms(r.get("start"))
        if not ms:
            continue
        for me, op, home in ((r["h"], r["a"], True), (r["a"], r["h"], False)):
            tk = tokens(me)
            if tk and not any(share(tk, e["tok"]) and abs(e["ms"] - ms) < 6 * 3600e3 for e in sched):
                sched.append({"tok": tk, "ms": ms, "home": home, "lg": r.get("lg") or "ostalo", "opp": op})
    return {"sched": sched, "hist": hist}


def team_entries(ctx, tk):
    return sorted((e for e in ctx["sched"] if share(tk, e["tok"])), key=lambda e: e["ms"])


def rest_pen(h):
    for lim, pen in REST_PEN:
        if h < lim:
            return pen
    return 0.0


def team_load(ctx, tk, start, lg):
    """Odmor/umor tima pred mecem u ligi lg."""
    H = 3.6e6
    es = team_entries(ctx, tk)
    prev = [e for e in es if e["ms"] <= start - 3 * H]
    nxt = [e for e in es if e["ms"] >= start + 3 * H]
    p, n = (prev[-1] if prev else None), (nxt[0] if nxt else None)
    out = {"rest_h": None, "pen": 0.0, "prev": None, "next": None, "cong": False, "dbl": any(e["lg"] != lg for e in es), "n7": 1 + sum(1 for e in prev if e["ms"] > start - 7 * 24 * H)}
    if p:
        rest = round((start - p["ms"]) / H - GAME_H, 1)
        pen = rest_pen(rest)
        if p["lg"] != lg and not p["home"]:
            pen += AWAY_X_PEN.get(p["lg"], AWAY_X_DEFAULT)
        out.update(rest_h=rest, prev={"lg": p["lg"], "opp": p["opp"], "away": not p["home"], "ago_h": round((start - p["ms"]) / H, 1)})
        out["pen"] = pen
    if out["n7"] >= 3:
        out["pen"] += DENSE_PEN
    out["pen"] = round(min(MAX_PEN, out["pen"]), 2)
    if n:
        out["next"] = {"lg": n["lg"], "opp": n["opp"], "away": not n["home"], "in_h": round((n["ms"] - start) / H, 1)}
    out["cong"] = bool((p and p["lg"] != lg and (start - p["ms"]) / H <= CONG_H) or (n and n["lg"] != lg and (n["ms"] - start) / H <= CONG_H))
    return out


def _cong_at(es, ms, lg):
    H = 3.6e6
    return any(e["lg"] != lg and abs(e["ms"] - ms) <= CONG_H * H and abs(e["ms"] - ms) > 3 * H for e in es)


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def find_player(team, name):
    tk = {w for w in re.split(r"[^a-z]+", norm(name)) if len(w) >= 2}
    best, score = None, 0
    for pl in team["pl"].values():
        sc = len(tk & pl["tk"])
        long_hit = any(len(w) >= 5 and w in pl["tk"] for w in tk)
        if (sc >= 2 or (sc >= 1 and long_hit)) and sc > score:
            best, score = pl, sc
    return best


def rotation(pl, es, lg, cong_now, stat, line, o, u):
    rows = [(ms, m, pts, reb, ast, _cong_at(es, ms, lg)) for ms, m, pts, reb, ast in pl["g"] if m >= 5]
    if len(rows) < 4:
        return None
    nc = [r[1] for r in rows if not r[5]]
    cg = [r[1] for r in rows if r[5]]
    base = sum(nc) / len(nc) if len(nc) >= 3 else sum(r[1] for r in rows) / len(rows)
    ratio = (sum(cg) / len(cg) / base) if cg and base else 1.0
    prior = ROT_PRIOR if base >= MIN_STARTER else ROT_PRIOR_BENCH
    f = (len(cg) * ratio + ROT_K * prior) / (len(cg) + ROT_K)
    exp_min = base * (f if cong_now else 1.0)
    i = {"pts": 2, "reb": 3, "ast": 4}[stat]
    tm = sum(r[1] for r in rows)
    rate = sum(r[i] for r in rows) / tm if tm else 0
    proj = rate * exp_min
    sd = max(SD_MIN[stat], SD_REL[stat] * proj)
    po = 1 - _phi((line - proj) / sd)
    book_o = (1 / o) / 1.06
    book_u = (1 / u) / 1.06
    sig = None
    if po > book_o + SIG_EDGE:
        sig = "OVER"
    elif (1 - po) > book_u + (SIG_EDGE if cong_now else SIG_EDGE_UNDER):
        sig = "UNDER"
    return {"min": round(exp_min, 1), "base": round(base, 1), "f": round(f, 2), "n": len(cg), "proj": round(proj, 1), "po": round(po, 3),
            "ev_o": round(po * o - 1, 3), "ev_u": round((1 - po) * u - 1, 3), "cong": bool(cong_now), "sig": sig}


def annotate(feed, leagues, diag):
    """Dodaje svakom meču iz lige 'an' (odmor/umor) i svakoj prop liniji 'rot' (minutaza/projekcija)."""
    leagues = [x.strip() for x in str(leagues or "").split(",") if x.strip() in LEAGUES]
    if not leagues:
        return
    ctx = build_context(feed)
    diag["an_raspored"] = len(ctx["sched"])
    for r in feed:
        lg = r.get("lg")
        start = to_ms(r.get("start"))
        if lg not in leagues or not start:
            continue
        th, ta = tokens(r["h"]), tokens(r["a"])
        L = [team_load(ctx, th, start, lg), team_load(ctx, ta, start, lg)]
        if L[0]["prev"] is None and L[1]["prev"] is None:
            diag["an_bez_rasporeda"] += 1
            continue
        edge = round(L[1]["pen"] - L[0]["pen"], 2)         # + = prednost domacina (gost umorniji)
        r["an"] = {"rest_h": [L[0]["rest_h"], L[1]["rest_h"]], "pen": [L[0]["pen"], L[1]["pen"]], "edge_pts": edge, "dp_home": round(edge * WINP_PER_PT, 3),
                   "dbl": [L[0]["dbl"], L[1]["dbl"]], "cong": [L[0]["cong"], L[1]["cong"]], "prev": [L[0]["prev"], L[1]["prev"]], "next": [L[0]["next"], L[1]["next"]]}
        diag["an_mecevi"] += 1
        teams = [next((t for t in ctx["hist"].get(lg, []) if share(tk, t["tok"])), None) for tk in (th, ta)]
        for p in r.get("player_props") or []:
            for i, tk in enumerate((th, ta)):
                pl = find_player(teams[i], p["p"]) if teams[i] else None
                if pl:
                    rot = rotation(pl, team_entries(ctx, tk), lg, L[i]["cong"], p["s"], p["l"], p["o"], p["u"])
                    if rot:
                        rot["team"] = "h" if i == 0 else "a"
                        p["rot"] = rot
                        diag["an_rot"] += 1
                        diag["an_sig_" + str(rot["sig"])] += 1
                    break


def write(events):
    """Pise aktuelne kvote i append-only Superbet snimke za istorijski ROI replay."""
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
        history = os.path.join(os.path.dirname(c["out"]), "odds_history.jsonl")
        stamp = int(time.time())
        with open(history, "a", encoding="utf-8") as fh:
            for e in by.get(lg, []):
                if not e.get("b") and not e.get("player_props"):
                    continue
                row = {"ts": stamp, "lg": lg, "h": e.get("h"), "a": e.get("a"),
                       "start": e.get("start"), "b": e.get("b") or [],
                       "pp": [[p.get("p"), p.get("s"), p.get("l"), p.get("o"), p.get("u"), bool(p.get("main"))]
                              for p in e.get("player_props") or []]}
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        files.append(history)
        print(f"  {lg}: {len(by.get(lg, []))} meceva, {sum(len(e.get('player_props', [])) for e in by.get(lg, []))} prop linija -> {c['out']} + istorija")
    return files


def git(*c):
    return subprocess.run(("git",) + c, capture_output=True, text=True)


def clear_stuck_state(files):
    """Ako je ostao nedovrsen merge/rebase, commit pada sa 'fatal: cannot do a partial commit during a merge'.
    Fajlove sa kvotama prvo sacuvamo u memoriji (abort ume da ih vrati na staro), pa ih upisemo nazad posle aborta."""
    def active(name):
        p = git("rev-parse", "--git-path", name).stdout.strip()
        return bool(p) and os.path.exists(p)
    merging, rebasing = active("MERGE_HEAD"), active("rebase-merge") or active("rebase-apply")
    if not (merging or rebasing):
        return True
    saved = {}
    for f in files:
        try:
            saved[f] = open(f, "rb").read()
        except OSError:
            pass
    if merging:
        print("push: nasao nedovrsen merge, radim git merge --abort")
        if git("merge", "--abort").returncode:
            git("reset", "--merge")
    if rebasing:
        print("push: nasao nedovrsen rebase, radim git rebase --abort")
        git("rebase", "--abort")
    for f, data in saved.items():
        os.makedirs(os.path.dirname(f) or ".", exist_ok=True)
        open(f, "wb").write(data)
    if active("MERGE_HEAD") or active("rebase-merge") or active("rebase-apply"):
        print("push: merge/rebase se nije ugasio; resi rucno (git status), pa pokreni ponovo")
        return False
    return True


def push(files):
    """commit samo fajlova sa kvotama + pull --rebase (nasi fajlovi pobedjuju u konfliktu) + push; ispisuje pravi razlog ako ne uspe."""
    if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        print("push: ovaj folder nije git repo (pokreni skriptu iz korena repoa)")
        return False
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not branch or branch == "HEAD":
        branch = "main"
    if not clear_stuck_state(files):
        return False
    git("add", *files)
    if git("diff", "--cached", "--quiet", "--", *files).returncode == 0:
        print("push: nema promena u fajlovima sa kvotama")
        return True
    r = git("commit", "-m", "Superbet odds", "--", *files)
    if r.returncode:
        print("push: commit nije uspeo:", (r.stderr or r.stdout).strip()[-400:])
        return False
    err = ""
    for i in range(3):
        r = git("pull", "--rebase", "--autostash", "-X", "theirs", "origin", branch)    # u rebase-u "theirs" = nasi novi fajlovi
        if r.returncode:
            err = "pull --rebase: " + (r.stderr or r.stdout).strip()[-400:]
            git("rebase", "--abort")
        else:
            r = git("push", "origin", "HEAD:" + branch)
            if r.returncode == 0:
                print(f"push: ok ({branch})")
                return True
            err = "push: " + (r.stderr or r.stdout).strip()[-400:]
        time.sleep(5 * (i + 1))
    print("push nije uspeo.", err)
    print("Provera: git status ; git log origin/" + branch + "..HEAD ; da li je grana '" + branch + "' i da li imas pravo upisa (git push --dry-run).")
    return False


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
        if e.get("an"):
            x = e["an"]
            print(f"      odmor h {x['rest_h']} | penal {x['pen']} | prednost domacina {x['edge_pts']:+} poena | signali: "
                  + (", ".join(f"{p['p']} {p['s']} {p['l']} {p['rot']['sig']}" for p in e.get("player_props", []) if p.get("rot", {}).get("sig") and p.get("main")) or "-"))
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
    ap.add_argument("--analytics", default=AN_DEFAULT, help="lige za koje se racuna odmor/umor i rotacije (npr. aba,acb); '' = iskljuceno")
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
