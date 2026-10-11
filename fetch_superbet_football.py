#!/usr/bin/env python3
"""Superbet FUDBAL (Premier liga, prematch) -> data/epl/odds_superbet.json
Isti javni offer feed i isti detalj meca kao fetch_superbet.py (uvozi njegove funkcije, ne menja kosarku).
Fajl uzima get/events_in/line_of/push iz fetch_superbet.py, pa ga drzi u istom folderu.

Koraci:
  1) python fetch_superbet_football.py --probe        # ispise sport id -> primeri mecova (potvrdi koji je id za fudbal, podrazumevano 5)
  2) python fetch_superbet_football.py                # jedan prolaz (Premier ligu prepoznaje po klubovima, ili --comp <tournament_id>)
  3) python fetch_superbet_football.py --loop 900 --push
Ako dobijes 403/429: STANI, ne zaobilazi. Razmak >= 5 min, uslove koriscenja proveri sam.
Trzista: 1X2 (Konacan ishod), ukupno golova (linije .5), oba tima daju gol. Ostala imena trzista ispisuje kao 'nepoznato'.
Izlaz po mecu: {"h","a","start","comp","x12":[1,X,2],"ou":[[linija,vise,manje],...],"btts":[da,ne]}"""
import argparse, collections, datetime, glob, json, os, random, re, sys, time, unicodedata, urllib.error
from football_poisson import canon, nrm, load as load_results
from fetch_superbet import get, events_in, num, line_of, event_id, push, DETAIL, URL

OUT = os.path.join("data", "epl", "odds_superbet.json")
FEED = URL.replace("sports=4", "sports={sp}")
SKIP = ("poluvreme", "1. pol", "2. pol", "half", "&", " i ", "kombin", "tim ", "tima", "domaćin", "domacin", "gost", "korner", "karton",
        "penal", "azijsk", "strelac", "igrač", "igrac", "tačan rezultat", "tacan rezultat", "dupla", "hendikep", "bez remi", "minut")
STRUCT = "https://production-superbet-offer-rs.freetls.fastly.net/sb-rs/api/v2/sr-Latn-RS/struct"
BIG = ("mancester", "liverpul", "totenhem", "njukasl", "vest hem", "brajton", "kristal palas", "aston vila", "vulverhempton", "arsenal", "chelsea", "liverpool", "manchester city", "manchester united", "man city", "man utd", "man united", "tottenham", "newcastle", "aston vil", "west ham",
       "everton", "brighton", "crystal palace", "brentford", "fulham")


def low(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")


M12 = {"1": "1", "home": "1", "x": "x", "0": "x", "draw": "x", "remi": "x", "2": "2", "away": "2"}
HALF = ("poluvreme", "1. pol", "2. pol", "half", "prvo ", "drugo ")
MAX_OTHER, MAX_OUTS = 60, 40


def kind_of(name):
    m = low(name)
    half = any(s in m for s in HALF)
    if re.fullmatch(r"oba tima daju gol ili \d\+", m.strip()): return "bttsor"
    if m.strip() == "oba tima daju gol u bilo kom poluvremenu": return "bttsh"
    if m.strip() == "tacan rezultat": return "cs"
    if "oba tima daju gol" in m:
        if half: return None
        if re.search(r"&\s*\d\s*\+", m): return "bttsx"
        return None if ("&" in m or " i " in m) else "btts"
    if m.strip() == "poluvreme/kraj": return "htft"
    if half: return None
    if m.strip() == "dupla sansa": return "dc"
    if "ukupno golova" in m and "azijsk" in m: return "ouah"
    if m.strip() == "azijski hendikep": return "ah"
    if m.strip() == "hendikep 1x2": return "eh"
    if m.strip() == "hendikep": return "hcp"
    if m.strip() == "raspon golova": return "range"
    if any(low(s) in m for s in SKIP): return None
    if "konacan ishod" in m: return "x12"
    if "ukupno golova" in m or "ukupno broj golova" in m: return "ou"
    return None


def outs(mk):
    r = []
    for x in mk.get("odds") or []:
        p = num(x.get("price"))
        if p is None or not 1.01 < p <= 500 or x.get("status", 1) != 1 or x.get("display") is False: continue
        md = x.get("metadata") or {}
        r.append({"c": str(md.get("code", "")).strip().lower(), "n": low(md.get("name", "")).strip(), "i": str(md.get("info", ""))[:70], "p": p, "x": x, "g": md.get("market_line_uuid")})
    return r


def lines_of(x, mname):
    """Linija izlaza; cetvrtinske linije ("-0.5,-1" ili "-0,5/-1") vraca kao prosek (-0.75)."""
    md = x.get("metadata") or {}
    for k in ("special_bet_value", "spec_bet_value", "specialBetValue", "handicap", "line", "param", "points"):
        v = md.get(k)
        if v in (None, ""): continue
        t = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(v))
        if len(t) == 2 and ":" not in str(v): return (num(t[0]) + num(t[1])) / 2
        if t: return num(t[0])
    return line_of(x, mname)


def hc3_of(x, mname):
    """3-way hendikep: '1:0' = domacin vodi 1:0 na startu => domacin +1; '0:1' => -1. Ili oznacen broj (+1/-1)."""
    md = x.get("metadata") or {}
    for v in [md.get(k) for k in ("special_bet_value", "spec_bet_value", "specialBetValue", "handicap", "line", "param")] + [md.get("info")]:
        m = re.search(r"(\d+)\s*:\s*(\d+)", str(v or ""))
        if m: return int(m.group(1)) - int(m.group(2))
    return lines_of(x, mname)


def pair_key(c, n, i):
    t = re.sub(r"[^a-z0-9]", "", c or n)
    return {"1x": "1x", "10": "1x", "12": "12", "x2": "x2", "02": "x2", "20": "x2"}.get(t)


def htft_key(o):
    for t in (o["c"], o["n"], o["i"]):
        m = re.search(r"([12x0])\s*[/\-]?\s*([12x0])", str(t))
        if m: return "/".join("x" if ch == "0" else ch for ch in m.groups())
    return None


def rng_of(o):
    for t in (o["n"], o["i"], o["c"]):
        t = str(t)
        m = re.search(r"(\d+)\s*[-–]\s*(\d+)", t)
        if m: return int(m.group(1)), int(m.group(2))
        m = re.search(r"(\d+)\s*\+", t)
        if m: return int(m.group(1)), 99
        m = re.fullmatch(r"\s*(\d+)\s*", t)
        if m: return int(m.group(1)), int(m.group(1))
    return None


def by_group(L, mname, fn):
    g = collections.defaultdict(list)
    for o in L:
        g[o["g"] or ("l", fn(o["x"], mname))].append(o)
    return g.values()


def ids_of(ev):
    """Svi id-jevi turnira/kategorije/lige u dogadjaju, npr. {"tournament_id": "106", "category_id": "45"}."""
    out = {}
    for src in ((ev.get("fixture") or {}), ev):
        for k, v in src.items():
            if re.search(r"(tournament|categor|league|competition)[a-z]*_?id$", k, re.I) and not isinstance(v, (dict, list)) and v not in (None, ""):
                out.setdefault(k, str(v))
            elif isinstance(v, dict) and re.search(r"tournament|categor|league|competition", k, re.I) and v.get("id") not in (None, ""):
                out.setdefault(k + ".id", str(v["id"]))
    return out


def parse_fb(ev, diag):
    f = ev.get("fixture") or {}
    parts = re.split(r"\s*[·•]\s*", f.get("event_name", ""), maxsplit=1)
    if len(parts) != 2:
        diag["bez_imena"] += 1
        return None
    x12, ou, btts = {}, collections.defaultdict(dict), {}
    ou_prio = {}
    tm_tok = {w for p_ in parts for w in re.split(r"[^a-z0-9]+", low(p_)) if len(w) >= 4}     # reci iz imena klubova
    X = {"dc": {}, "ah": {}, "ouah": {}, "eh": {}, "range": {}, "bttsx": {}, "htft": {}, "bttsor": {}, "bttsh": None, "cs": {}}
    other = []
    for mk in ev.get("markets") or []:
        mname = mk.get("name", "")
        kind = kind_of(mname)
        L = outs(mk)
        if kind is None:
            diag["u_other:" + mname[:45]] += 1
            if L and len(L) <= MAX_OUTS and len(other) < MAX_OTHER:
                other.append({"m": mname, "o": [[o["c"], o["n"], o["i"][:50], o["p"], lines_of(o["x"], mname)] for o in L]})
            continue
        if kind == "btts":
            # Glavno trziste je tacno "Oba tima daju gol". Varijante ("ili preko 3.5", "2+ gola svaki"...) imaju drugacije kvote i ne smeju da ga prepisu.
            if not re.fullmatch(r"oba tima daju gol\W*(\(\s*gg\s*(/?\s*ng)?\s*\))?", low(mname).strip()):
                diag["btts_odbaceno:" + mname[:45]] += 1
                continue
            if "y" in btts and "n" in btts:
                continue
            diag["btts_koristi:" + mname[:45]] += 1
        prio = 0
        if kind == "ou":
            # Glavno trziste je "Ukupno golova" (tacno ime). Sve ostalo sa tim recima (timski ukupni, "ukljucujuci produzetke"...) nije glavno:
            # ako sadrzi ime kluba odbacujemo ga, a inace moze samo da popuni linije kojih glavno trziste nema.
            ml = low(mname).strip()
            exact = bool(re.fullmatch(r"ukupno (broj )?golova( ?\(.*\))?", ml)) and "produz" not in ml
            if not exact and any(t in ml for t in tm_tok):
                diag["ou_timski:" + mname[:45]] += 1
                continue
            prio = 0 if exact else 1
        if kind in ("x12", "btts", "ou"):
            groups = collections.defaultdict(dict)
            pos = []
            for o in L:
                code, nm, p = o["c"], o["n"], o["p"]
                if kind == "x12":
                    info = low(o["i"])
                    s_ = M12.get(code) or M12.get(nm) or ("x" if ("remi" in info or "neresen" in info or "draw" in info) else None)
                    pos.append(p)
                    if s_: x12[s_] = p
                elif kind == "btts":
                    s_ = "y" if code in ("gg", "da", "yes", "y") or nm in ("da", "gg", "yes") else "n" if code in ("ng", "ne", "no", "n") or nm in ("ne", "ng", "no") else None
                    if s_: btts[s_] = p
                else:
                    ln = line_of(o["x"], mname)
                    s_ = "o" if code in ("+", "o", "over") or nm.startswith(("vise", "preko", "over")) else "u" if code in ("-", "u", "under") or nm.startswith(("manje", "ispod", "under")) else None
                    if s_ and ln is not None:
                        groups[o["g"] or ("l", ln)][s_] = (p, ln)
            if kind == "x12" and not all(k in x12 for k in "1x2") and len(pos) == 3:
                x12 = dict(zip("1x2", pos))
                diag["x12_po_redosledu"] += 1
            for g in groups.values():
                if "o" in g and "u" in g:
                    ln = g["o"][1]
                    if 0.5 <= ln <= 7.5 and (ln * 2) % 1 == 0 and 1.0 <= 1 / g["o"][0] + 1 / g["u"][0] <= 1.2:   # marza van 0-20% = pogresno uparene kvote
                        if ln not in ou_prio or prio < ou_prio[ln]:
                            ou[ln] = {"o": g["o"][0], "u": g["u"][0]}
                            ou_prio[ln] = prio
                            diag[f"ou_koristi:{mname[:40]}"] += 1
        elif kind == "dc":
            d = {}
            for o in L:
                k = pair_key(o["c"], o["n"], o["i"])
                if k: d[k] = o["p"]
            if len(d) < 3 and len(L) == 3:
                d = dict(zip(("1x", "12", "x2"), [o["p"] for o in L])); diag["dc_po_redosledu"] += 1
            if len(d) == 3: X["dc"] = d
        elif kind in ("ah", "hcp", "eh"):
            for grp in by_group(L, mname, hc3_of if kind == "eh" else lines_of):
                three = kind == "eh" or (kind == "hcp" and len(grp) == 3)
                if three:
                    d = {M12.get(o["c"]) or M12.get(o["n"]): o for o in grp}
                    if not all(k in d for k in "1x2"):
                        if len(grp) == 3: d = dict(zip("1x2", grp))
                        else: continue
                    hc = hc3_of(d["1"]["x"], mname)
                    if hc is not None and abs(hc) <= 5 and hc == int(hc):
                        X["eh"][int(hc)] = [d["1"]["p"], d["x"]["p"], d["2"]["p"]]
                else:
                    d = {o["c"]: o for o in grp}
                    if "1" not in d or "2" not in d: continue
                    l1, l2 = lines_of(d["1"]["x"], mname), lines_of(d["2"]["x"], mname)
                    hl = l1 if l1 is not None else (-l2 if l2 is not None else None)
                    if hl is None or abs(hl) > 6 or (hl * 4) % 1: continue
                    if l1 is not None and l2 is not None and abs(l1 - l2) < 1e-9 and l1 != 0: diag["ah_zajednicka_linija"] += 1
                    X["ah"][hl] = [d["1"]["p"], d["2"]["p"]]
        elif kind == "ouah":
            for grp in by_group(L, mname, lines_of):
                d = {}
                for o in grp:
                    nm = o["n"]; c = o["c"]
                    s_ = "o" if c in ("+", "o", "over") or nm.startswith(("vise", "preko", "over")) else "u" if c in ("-", "u", "under") or nm.startswith(("manje", "ispod", "under")) else None
                    ln = lines_of(o["x"], mname)
                    if s_ and ln is not None: d[s_] = (o["p"], ln)
                if "o" in d and "u" in d and 0.25 <= d["o"][1] <= 8 and (d["o"][1] * 4) % 1 == 0:
                    X["ouah"][d["o"][1]] = [d["o"][0], d["u"][0]]
        elif kind == "range":
            for o in L:
                r_ = rng_of(o)
                if r_: X["range"][r_] = o["p"]
        elif kind in ("bttsor", "bttsh"):
            y = n_ = None
            for o in L:
                if o["c"] in ("da", "gg", "yes", "y") or o["n"] in ("da", "gg", "yes"): y = o["p"]
                elif o["c"] in ("ne", "ng", "no", "n") or o["n"] in ("ne", "ng", "no"): n_ = o["p"]
            if y is not None and n_ is not None and 1.0 <= 1 / y + 1 / n_ <= 1.2:
                if kind == "bttsh": X["bttsh"] = [y, n_]
                else: X["bttsor"][int(re.search(r"(\d)\+", low(mname)).group(1))] = [y, n_]
        elif kind == "cs":
            for o in L:
                for t in (o["n"], o["c"], o["i"]):
                    m_ = re.fullmatch(r"\s*(\d+)\s*[:\-]\s*(\d+)\s*", str(t))
                    if m_:
                        X["cs"][(int(m_.group(1)), int(m_.group(2)))] = o["p"]
                        break
        elif kind == "bttsx":
            m_ = re.search(r"&\s*(\d)\s*\+", mname)
            y = n_ = None
            for o in L:
                if o["c"] in ("da", "gg", "yes", "y") or o["n"] in ("da", "gg", "yes"): y = o["p"]
                elif o["c"] in ("ne", "ng", "no", "n") or o["n"] in ("ne", "ng", "no"): n_ = o["p"]
            if m_ and y is not None and n_ is not None: X["bttsx"][int(m_.group(1))] = [y, n_]
            elif m_ and y is not None: X["bttsx"][int(m_.group(1))] = [y, None]
        elif kind == "htft":
            for o in L:
                k = htft_key(o)
                if k: X["htft"][k] = o["p"]
    Xr = [x12["1"], x12["x"], x12["2"]] if all(k in x12 for k in "1x2") else None
    OU = [[l, v["o"], v["u"]] for l, v in sorted(ou.items())]
    B = [btts["y"], btts["n"]] if "y" in btts and "n" in btts and 1.0 <= 1 / btts["y"] + 1 / btts["n"] <= 1.2 else None
    ex = {"dc": [X["dc"]["1x"], X["dc"]["12"], X["dc"]["x2"]] if X["dc"] else None,
          "ah": [[l] + v for l, v in sorted(X["ah"].items())],
          "ouah": [[l] + v for l, v in sorted(X["ouah"].items())],
          "eh": [[l] + v for l, v in sorted(X["eh"].items())],
          "range": [[lo, hi, p] for (lo, hi), p in sorted(X["range"].items())],
          "bttsx": [[t] + v for t, v in sorted(X["bttsx"].items())],
          "htft": X["htft"] or None, "bttsor": [[t] + v for t, v in sorted(X["bttsor"].items())], "bttsh": X["bttsh"],
          "cs": [[h_, a_, p_] for (h_, a_), p_ in sorted(X["cs"].items())], "other": other}
    if not (Xr or OU or B or any(ex[k] for k in ex if k != "other")):
        diag["bez_kvota"] += 1
        return None
    ids = ids_of(ev)
    tid = next((v for k, v in ids.items() if re.search("tournament", k, re.I)), "")
    return {"h": parts[0].strip(), "a": parts[1].strip(), "start": f.get("utc_date"), "comp": f"t{tid}", "ids": ids,
            "x12": Xr, "ou": OU, "btts": B, "eid": event_id(ev), **ex}


def merge(r, d):
    ou = {x[0]: x for x in r["ou"] + d["ou"]}
    r.update(x12=r["x12"] or d["x12"], btts=r["btts"] or d["btts"], ou=[ou[k] for k in sorted(ou)])
    for k in ("dc", "ah", "ouah", "eh", "range", "bttsx", "htft", "bttsor", "bttsh", "cs", "other"):   # detalj ima sva trzista, pa ima prednost
        if d.get(k): r[k] = d[k]
    return r


BAD = re.compile(r"\((ž|z|r|w)\)|\bu\d\d\b|\bwomen\b|\bladies\b|\bzene\b", re.I)
_known = None


def epl_teams():
    """Klubovi tekuce Premier lige iz data/epl/E0_*.csv (football_poisson.py --download); prazan skup ako fajla nema."""
    global _known
    if _known is None:
        sh = os.path.join("data", "epl", "statshub_data.json")      # klubovi tekuce PL iz Statshub-a (statshub_football.py); inace stari CSV
        if os.path.exists(sh):
            _known = set(json.load(open(sh, encoding="utf-8")).get("teams", {}))
        else:
            fs = sorted(glob.glob(os.path.join("data", "epl", "E0_*.csv")))
            _known = {m[k] for m in load_results(fs[-1:]) for k in ("h", "a")} if fs else set()
    return _known


def club_ok(r):
    """True/False: oba kluba su iz Premier lige (i nisu zenski/U21 tim). None ako nemamo spisak klubova."""
    known = epl_teams()
    if not known: return None
    if BAD.search(r["h"]) or BAD.search(r["a"]): return False
    return bool(canon(r["h"], known) and canon(r["a"], known))


def show_sample(raw):
    print(f"\nFeed ima {len(raw)} dogadjaja, ali nijedan nema prepoznatu kvotu. Uzorak prva 2 (trzista i kodovi):")
    for ev in raw[:2]:
        print(" ", (ev.get("fixture") or {}).get("event_name"))
        for mk in (ev.get("markets") or [])[:6]:
            od = [(o.get("price"), o.get("status"), o.get("display"), {k: v for k, v in (o.get("metadata") or {}).items() if k in ("code", "name", "info")}) for o in (mk.get("odds") or [])[:3]]
            print("    trziste:", mk.get("name"), "|", od)
        if not ev.get("markets"):
            print("    (nema polja markets; kljucevi:", list(ev)[:12], ")")


def diag_ids(allv):
    print(f"\nNema mecova za taj filter. Id polja u feedu ({len(allv)} fudbalskih mecova):")
    fields = collections.defaultdict(collections.Counter)
    ex = {}
    for r in allv:
        for k, v in r["ids"].items():
            fields[k][v] += 1
            ex.setdefault((k, v), f"{r['h']} - {r['a']}")
    if not fields: print("  nijedan mec nema polje sa id-jem turnira/kategorije; pokreni --tournaments ili posalji mi jedan sirov dogadjaj.")
    for k, c in fields.items():
        print(f"  {k}: " + "; ".join(f"{v} ({n}x, npr. {ex[(k, v)]})" for v, n in c.most_common(6)))


def detect_comp(evs):
    c, ex = collections.Counter(), {}
    for r in evs:
        if any(k in low(r["h"]) or k in low(r["a"]) for k in BIG):
            c[r["comp"]] += 1
            ex.setdefault(r["comp"], f"{r['h']} - {r['a']}")
    top = c.most_common(3)
    for k, n in top:
        print(f"  kandidat za Premier ligu: {k}  ({n} mecova s velikim klubovima, npr. {ex[k]})")
    return top[0][0] if top and top[0][1] >= 3 else None


def window(days):
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT00:00:00.000Z"), (now + datetime.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")


def probe(a):
    s, e = window(2)
    for sp in range(1, 16):
        try:
            evs = events_in(get(FEED.format(s=s, e=e, sp=sp)))
        except urllib.error.HTTPError as ex:
            sys.exit(f"HTTP {ex.code}: feed odbija zahtev, stajem.")
        names = [(x.get("fixture") or {}).get("event_name", "?") for x in evs[:3]]
        print(f"sports={sp:<3} mecova {len(evs):<4} {names}")
        time.sleep(random.uniform(2, 3))


def names_in(o, pre=""):
    """Sva tekstualna polja ciji naziv lici na turnir/ligu/kategoriju/zemlju (da vidimo kako feed imenuje turnir)."""
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)): out += names_in(v, pre + k + ".")
            elif isinstance(v, str) and re.search(r"tournament|league|competition|categor|country", k, re.I) and "id" not in k.lower().split("_")[-1:]:
                out.append(f"{pre}{k}={v}")
    elif isinstance(o, list):
        for x in o[:3]: out += names_in(x, pre)
    return out


def tournaments(a):
    s, e = window(a.days)
    evs = events_in(get(FEED.format(s=s, e=e, sp=a.sports)))
    by = collections.defaultdict(list)
    for ev in evs:
        by[(ev.get("fixture") or {}).get("tournament_id")].append(ev)
    print(f"sports={a.sports}, {a.days} dana: {len(evs)} dogadjaja u {len(by)} turnira\n  tournament_id | mecova | primeri | imena polja turnira")
    for tid, L in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ex = "; ".join((x.get("fixture") or {}).get("event_name", "?") for x in L[:3])
        nm = sorted(set(names_in(L[0].get("fixture") or {})))[:3]
        print(f"  {str(tid):<13} | {len(L):<6} | {ex[:80]} | {nm}")
    print("\nPremijer ligu prepoznas po Arsenal/Chelsea/Liverpool... (oko 10 mecova po kolu). Upisi: --comp <tournament_id>")


def struct(a):
    j = get(STRUCT)
    rx = re.compile(a.struct, re.I)
    def walk(o, path=""):
        if isinstance(o, dict):
            ln = o.get("localNames")
            if isinstance(ln, dict):
                nm = " / ".join(str(v) for v in ln.values())
                if rx.search(low(nm)) or rx.search(nm): yield path, o.get("id"), nm
            for k, v in o.items(): yield from walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o): yield from walk(v, f"{path}[{i}]")
    n = 0
    for path, i, nm in walk(j):
        print(f"  id={i}  {nm}   ({path[-70:]})"); n += 1
    print(f"{n} pogodaka. id ovde je tournament/kategorija/sport iz stabla; proveri da se isti broj javlja u --tournaments.")


def once(a):
    s, e = window(a.days)
    try:
        j = get(FEED.format(s=s, e=e, sp=a.sports))
    except urllib.error.HTTPError as ex:
        sys.exit(f"HTTP {ex.code}: feed odbija zahtev. Ne zaobilazim; pokusaj kasnije.")
    diag = collections.Counter()
    raw = events_in(j)
    allv = [r for r in (parse_fb(ev, diag) for ev in raw) if r]
    if not allv:
        show_sample(raw)
        print("Neprepoznato:", diag.most_common(8))
        return
    explicit = a.tournament or a.category or a.comp
    if explicit:
        if a.tournament: hit = lambda r: any(v == str(a.tournament) for k, v in r["ids"].items() if re.search("tournament", k, re.I))
        elif a.category: hit = lambda r: any(v == str(a.category) for k, v in r["ids"].items() if re.search("categor", k, re.I))
        else: hit = lambda r: str(a.comp) in r["ids"].values()
        evs = [r for r in allv if hit(r)]
        if a.category or a.clubs:       # kategorija "Engleska" ima i Championship, kupove, zenske... pa dodatno filtriramo po klubovima
            if not epl_teams(): print("Upozorenje: nema data/epl/statshub_data.json (pokreni statshub_football.py), pa nema filtera po klubovima.")
            evs = [r for r in evs if club_ok(r) is not False]
    elif epl_teams():
        evs = [r for r in allv if club_ok(r)]
    else:
        c = detect_comp(allv)
        evs = [r for r in allv if c and r["comp"] == c]
    comp = ",".join(sorted({r["comp"] for r in evs})) or "-"
    if not evs:
        diag_ids(allv)
        return
    limit = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=a.hours)).strftime("%Y-%m-%dT%H:%M")
    todo = [r for r in evs if r.get("eid") not in (None, "") and (r["start"] or "") <= limit][:a.max]
    for r in todo:
        time.sleep(random.uniform(1.5, 3))
        ds = [x for x in (parse_fb(x, diag) for x in events_in(get(DETAIL.format(id=r["eid"])))) if x]
        diag["detalj_ok" if ds else "detalj_prazan"] += 1
        if ds: merge(r, ds[0])
    for r in evs:
        r.pop("eid", None); r.pop("ids", None)
    print(f"{datetime.datetime.now():%H:%M:%S} comp {comp}: {len(evs)} mecova (od {len(allv)} fudbalskih), detalj {len(todo)}")
    for r in evs[:20]:
        print(f"  {r['h']} - {r['a']} | {r['start']} | 1X2 {r['x12']} | ukupno {len(r['ou'])} lin | GG/NG {r['btts']} | DC {bool(r.get('dc'))} AH {len(r.get('ah') or [])} AU {len(r.get('ouah') or [])} EH {len(r.get('eh') or [])} rasp {len(r.get('range') or [])} G&{len(r.get('bttsx') or [])} HT/FT {len(r.get('htft') or {})} ostalo {len(r.get('other') or [])}")
    bad = [(k, v) for k, v in diag.most_common(12) if k not in ("detalj_ok",)]
    if bad: print("Neprepoznato:", bad)
    if not evs: return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    unk = {}
    for r in evs:                                   # sva trzista koja jos ne razumemo: ime, broj mecova, primer izlaza (kod, ime, info, kvota, linija)
        for o in r.get("other") or []:
            u = unk.setdefault(o["m"], {"mecova": 0, "primer": o["o"][:12]})
            u["mecova"] += 1
    if unk:
        with open(os.path.join(os.path.dirname(OUT), "unknown_markets.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(sorted(unk.items(), key=lambda kv: -kv[1]["mecova"])), fh, ensure_ascii=False, indent=1)
        print(f"Nepoznatih trzista: {len(unk)} (sva imena + primer izlaza: {os.path.join(os.path.dirname(OUT), 'unknown_markets.json')})")
    with open(OUT + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"fetched": int(time.time()), "source": "superbet.rs", "league": "epl", "comp": comp, "events": evs}, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(OUT + ".tmp", OUT)
    if a.push: push([OUT])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="sekundi izmedju prolaza (min 300)")
    ap.add_argument("--probe", action="store_true", help="ispisi sports id -> primeri mecova (nadji id za fudbal)")
    ap.add_argument("--tournaments", action="store_true", help="spisak svih turnira (tournament_id, broj mecova, primeri) za izabrani sport")
    ap.add_argument("--struct", nargs="?", const="premier|premijer|engleska|england|fudbal|football", default="", help="trazi turnire po imenu u stablu (regex), npr. --struct \"premier|engleska\"")
    ap.add_argument("--sports", default="5", help="id sporta u feedu (fudbal = 5, proveri sa --probe)")
    ap.add_argument("--tournament", default="", help="tournament_id Premier lige, npr. 106 (iz --struct: .data.tournaments)")
    ap.add_argument("--category", default="", help="id kategorije, npr. 45 = cela Engleska; automatski dodaje filter po klubovima Premier lige")
    ap.add_argument("--comp", default="", help="bilo koji id turnira/kategorije/lige koji se pojavljuje u dogadjaju (kao --tournament ili --category, bez razlike)")
    ap.add_argument("--clubs", action="store_true", help="dodatno zadrzi samo mecove izmedju klubova iz data/epl/E0_*.csv")
    ap.add_argument("--days", type=int, default=14, help="koliko dana unapred u listi")
    ap.add_argument("--hours", type=int, default=240, help="detalj (ukupno golova, GG/NG) samo za mecove u narednih N sati")
    ap.add_argument("--max", type=int, default=24, help="najvise detalj-poziva po prolazu")
    ap.add_argument("--push", action="store_true", help="git commit+push samo data/epl/odds_superbet.json")
    a = ap.parse_args()
    if a.probe: probe(a); sys.exit()
    if a.tournaments: tournaments(a); sys.exit()
    if a.struct: struct(a); sys.exit()
    if not a.loop: once(a)
    else:
        while True:
            try: once(a)
            except SystemExit as e: print(e); break
            except Exception as e: print("greska:", e)
            time.sleep(max(300, a.loop) * random.uniform(.9, 1.2))
