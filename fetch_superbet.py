#!/usr/bin/env python3
"""Superbet (kosarka, prematch) -> data/odds_superbet.json   (bez browsera, obican GET na njihov javni offer feed)

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
import argparse, collections, datetime, glob, json, os, random, re, subprocess, sys, time, urllib.request, urllib.error

URL = ("https://production-superbet-offer-rs.freetls.fastly.net/sb-rs/api/v3/sr-Latn-RS/events"
       "?startDate={s}&endDate={e}&index=active-prematch&sports=4")
DETAIL = ("https://production-superbet-offer-rs.freetls.fastly.net/sb-rs/api/v3/sr-Latn-RS/events"
          "?events={id}&includeOnly=fixture,inPlayStats,inPlayStatsMetadata,markets,priceboosts,results,superbets")
OUT = os.path.join("data", "odds_superbet.json")
SITE = os.path.join("data", "site.json")
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


def tokens(n):
    return {w for w in re.split(r"[^a-z0-9]+", n.lower()) if len(w) >= 4 and w not in STOP}


_clubs = None


def is_euroleague(r):
    """Oba tima moraju da lice na klubove iz data/site.json (isto pravilo kao na sajtu: zajednicka rec od 4+ slova)."""
    global _clubs
    if _clubs is None:
        _clubs = []
        for fp in [SITE] + glob.glob(os.path.join("data", "*", "site.json")):   # Evroliga + sve lige koje imas u repou
            try:
                c = json.load(open(fp, encoding="utf-8")).get("clubs", {})
                _clubs += [tokens(f"{v.get('name', '')} {v.get('short', '')}") for v in c.values()]
            except (OSError, ValueError):
                pass
    hit = lambda n: any(tokens(n) & t for t in _clubs)
    return hit(r["h"]) and hit(r["a"])


def run(a=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    s = now.strftime("%Y-%m-%dT00:00:00.000Z")
    e = (now + datetime.timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000Z")
    j = get(URL.format(s=s, e=e))
    diag, out = collections.Counter(), []
    for ev in events_in(j):
        diag["mecevi"] += 1
        r = parse_event(ev, diag)
        if r:
            r["eid"] = event_id(ev)
            out.append(r)
    if a and a.detail:      # detalj (hendikep, ukupno) samo za evroligaske mecove, maks. 30 po prolazu, sa razmakom
        pick = [x for x in out if (x["comp"] == f"t{a.comp}" if a.comp else is_euroleague(x))]
        pick.sort(key=lambda x: x["start"] or "")
        diag["za_detalj"] = len(pick)
        diag["bez_id"] = sum(1 for x in pick if x.get("eid") in (None, ""))
        for r in [x for x in pick if x.get("eid") not in (None, "")][:40]:
            time.sleep(random.uniform(1.5, 3))
            jd = get(a.detail.format(id=r["eid"], s=s, e=e))      # 403/429 prekida ceo prolaz
            if a.dump and not diag["dump"]:
                diag["dump"] = 1
                json.dump(jd, open(os.path.join("data", "superbet_dump.json"), "w", encoding="utf-8"), ensure_ascii=False)
                names = sorted({m.get("name", "") for x in events_in(jd) for m in x.get("markets") or []})
                print(f"DUMP {r['h']} - {r['a']}: {len(names)} trzista:\n  " + "\n  ".join(names))
            ds = [parse_event(x, diag) for x in (events_in(jd) or [])]
            ds = [x for x in ds if x]
            if ds:
                new = merge(r, ds[0]); new["eid"] = r["eid"]; r.update(new); diag["detalj_ok"] += 1
            else:
                diag["detalj_prazan"] += 1
    return out, diag


def write(events):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched": int(time.time()), "source": "superbet.rs", "events": events}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)


def push():
    run_ = lambda *c: subprocess.run(c, capture_output=True, text=True)
    run_("git", "add", OUT)
    if run_("git", "diff", "--cached", "--quiet", "--", OUT).returncode == 0:
        return
    run_("git", "commit", "-m", "Superbet odds", "--", OUT)
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
    print(f"{datetime.datetime.now():%H:%M:%S} mecevi u feedu {diag['mecevi']}, sa prepoznatim kvotama {len(ev)}")
    for e in ev[:40]:
        b = e["b"][0]
        print(f"  {e['h']} - {e['a']} | {e['start']} | 1/2 {b['h2h']} | hend {b['sp']} | ukupno {b['tt']} | igraci {len(e.get('player_props', []))}")
    bad = [(k, v) for k, v in diag.most_common(10) if k != "mecevi"]
    if bad:
        print("Neprepoznato:", bad)
    if not ev:
        print("Nista prepoznato: ne pisem fajl.")
        return
    write(ev)
    if a.push:
        push()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="sekundi izmedju prolaza (min 300)")
    ap.add_argument("--detail", default=DETAIL, help="URL detalja meca sa {id}; '' = iskljuceno")
    ap.add_argument("--comp", default="", help="opciono: tournament_id Evrolige (broj iz polja comp); bez toga se Evroliga prepoznaje po data/site.json")
    ap.add_argument("--dump", action="store_true", help="snimi sirov detalj prvog meca (data/superbet_dump.json) i ispisi imena svih trzista")
    ap.add_argument("--push", action="store_true", help="git commit+push samo data/odds_superbet.json")
    a = ap.parse_args()
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
