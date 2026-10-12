#!/usr/bin/env python3
"""Automatski popunjava data/<liga>/referees.json i data/<liga>/stadiums.json (epl, laliga, l1, sa), bez rucnog unosa.

  python fb_autodata.py --league laliga            # sudije + stadioni
  python fb_autodata.py --league epl --only stad   # samo stadioni (offline, bez Statshub poziva)
  python fb_autodata.py --league epl --offline     # sve iz kesa data/<liga>/statshub/

Redosled: pokreni PRE football_poisson.py, posle statshub_football.py (koristi isti kes pa skoro ne pravi nove pozive).

SUDIJE: iz Statshub upcoming-events svakog kluba uzima refereeId (ili ugnjezdeni 'referee' objekat) za sledeci meč;
  ime trazi (1) u samom dogadjaju, (2) u sitemap-referees.xml (/referee/<slug>/<id>), (3) u /api/event/<id>.
  Sudija se obicno objavi 1-3 dana pre meca; do tada nema unosa (model onda radi bez faktora sudije, ne izmislja ga).
  Kljuc je "domacin|gost" u istim kljucevima klubova kao statshub_data.json; rucni unosi za mecove koji tek predstoje ostaju.
STADIONI: ugradjena tabela (priblizne koordinate, dovoljno za prognozu vremena) za klubove sve 4 lige; za klub koji nije u tabeli
  proba OpenStreetMap Nominatim ("<klub> stadium", 1 poziv/s, uz tvoj User-Agent) i to prijavi da proveris; bez rezultata ostaje prazno.
Isti stil kao ostali skriptovi: jedan pregledac, pauze 1.5-3 s, staje na 403/429, bez zaobilazenja."""
import argparse, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fb_leagues import CUR
import statshub_football as S
from fb_factors import STADIUMS as BASE_ST

D = os.path.join("data", CUR["dir"])
SH = os.path.join(D, "statshub_data.json")

# priblizne koordinate (lat, lon); EPL/Serie A/Ligue 1 top klubovi su vec u fb_factors.STADIUMS, ovde dopuna
EXTRA = {
    # La Liga
    "alaves": (42.837, -2.688), "almeria": (36.841, -2.435), "celta": (42.212, -8.740), "cadiz": (36.503, -6.273), "elche": (38.267, -0.663),
    "espanyol": (41.348, 2.075), "getafe": (40.325, -3.714), "girona": (41.961, 2.829), "granada": (37.153, -3.596), "las palmas": (28.100, -15.456),
    "leganes": (40.340, -3.760), "levante": (39.495, -0.364), "mallorca": (39.590, 2.631), "oviedo": (43.365, -5.870), "rayo": (40.392, -3.659),
    "valladolid": (41.644, -4.761), "racing": (43.473, -3.793), "deportivo": (43.369, -8.417), "gijon": (43.536, -5.637), "zaragoza": (41.636, -0.901),
    "malaga": (36.734, -4.426), "tenerife": (28.463, -16.257), "eibar": (43.182, -2.476), "huesca": (42.133, -0.416),
    # Serie A
    "cagliari": (39.200, 9.137), "como": (45.810, 9.080), "cremonese": (45.139, 10.020), "empoli": (43.726, 10.953), "genoa": (44.416, 8.952),
    "udinese": (46.082, 13.200), "lecce": (40.365, 18.208), "verona": (45.435, 10.969), "parma": (44.795, 10.338), "sassuolo": (44.714, 10.650),
    "pisa": (43.725, 10.401), "venezia": (45.491, 12.363), "monza": (45.583, 9.304), "salernitana": (40.617, 14.815), "frosinone": (41.630, 13.340),
    "sampdoria": (44.416, 8.952), "palermo": (38.152, 13.343), "spezia": (44.104, 9.813),
    # Ligue 1
    "angers": (47.461, -0.531), "auxerre": (47.786, 3.589), "brest": (48.403, -4.462), "le havre": (49.499, 0.169), "lens": (50.433, 2.815),
    "lorient": (47.748, -3.368), "metz": (49.109, 6.159), "montpellier": (43.622, 3.812), "nantes": (47.256, -1.525), "paris fc": (48.821, 2.346),
    "rennes": (48.107, -1.713), "reims": (49.247, 4.025), "strasbourg": (48.560, 7.755), "toulouse": (43.583, 1.434), "st etienne": (45.461, 4.390),
    "troyes": (48.307, 4.095), "clermont": (45.816, 3.121), "bordeaux": (44.829, -0.562), "ajaccio": (41.927, 8.775),
    # dopune (kratki nazivi, promovisani klubovi)
    "man city": (53.483, -2.200), "man united": (53.463, -2.291), "spurs": (51.604, -0.066), "forest": (52.940, -1.133),
}
ST = {**BASE_ST, **EXTRA}


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def jl(p, dflt):
    try: return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError): return dflt


def jw(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(o, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True); os.replace(p + ".tmp", p)


def lookup(key, name):
    """koordinate po kljucu kluba; pa po imenu; pa po sadrzavanju (npr. 'brighton hove albion' ~ 'brighton')."""
    for k in (key, norm(name)):
        if k in ST: return ST[k]
    n = norm(name) + " " + key
    hits = [v for k, v in ST.items() if len(k) >= 4 and re.search(r"\b" + re.escape(k) + r"\b", n)]
    return hits[0] if len(set(hits)) == 1 else None


def nominatim(name, ua):
    q = urllib.parse.quote(f"{name} stadium")
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1", headers={"User-Agent": ua}), timeout=10)
        j = json.loads(r.read())
        time.sleep(1.2)
        return (round(float(j[0]["lat"]), 3), round(float(j[0]["lon"]), 3)) if j else None
    except Exception:
        return None


def stadiums(teams, a):
    p = os.path.join(D, "stadiums.json")
    cur = {k: tuple(v) for k, v in jl(p, {}).items()}
    out, new, miss = dict(cur), [], []
    for k, t in teams.items():
        if k in out: continue
        xy = lookup(k, t.get("name", k))
        src = "tabela"
        if not xy and not a.no_net:
            xy, src = nominatim(t.get("name", k), a.ua), "OSM Nominatim"
        if xy: out[k] = tuple(xy); new.append(f"{k} ({src})")
        else: miss.append(k)
    jw(p, {k: list(v) for k, v in out.items()})
    print(f"stadiums.json: {len(out)} klubova ({len(new)} novih)" + (f"; PROVERI: {', '.join(n for n in new if 'OSM' in n)}" if any("OSM" in n for n in new) else ""))
    if miss: print("  bez koordinata (dodaj rucno u stadiums.json): " + ", ".join(miss))


# ------------------------------------------------------------------ sudije
def find_ref(o, depth=0):
    """(id, ime) iz dogadjaja: refereeId / referee{id,name,slug} bilo gde u prvih nekoliko nivoa."""
    rid = rname = None
    if isinstance(o, dict) and depth < 4:
        for k, v in o.items():
            lk = k.lower()
            if lk == "refereeid" and v: rid = v
            elif lk in ("refereename", "referee_name") and isinstance(v, str): rname = v
            elif lk == "referee":
                if isinstance(v, dict):
                    rid = rid or v.get("id"); rname = rname or v.get("name") or (v.get("slug") or "").replace("-", " ").title() or None
                elif isinstance(v, str): rname = rname or v
                elif isinstance(v, (int,)) and v: rid = rid or v
            elif isinstance(v, (dict, list)) and not (rid and rname):
                r2 = find_ref(v if isinstance(v, dict) else {"x": v}, depth + 1)
                rid, rname = rid or r2[0], rname or r2[1]
    elif isinstance(o, list) and depth < 4:
        for x in o[:5]:
            r2 = find_ref(x, depth + 1); rid, rname = rid or r2[0], rname or r2[1]
    return rid, rname


def sitemap_names(cl):
    fn = os.path.join(S.CACHE, "referee_names.json")
    c = jl(fn, None)
    if c and (cl.offline or time.time() - os.path.getmtime(fn) < 7 * 86400): return c
    if cl.offline: return c or {}
    cl._open()
    js = "async u => { const r = await fetch(u); return [r.status, await r.text()] }"
    names, todo, seen = {}, [S.BASE + "/sitemap-referees.xml"], set()
    while todo and len(seen) < 6:
        u = todo.pop(0)
        if u in seen: continue
        seen.add(u); time.sleep(2)
        st, tx = cl.page.evaluate(js, u)
        if st in (403, 429): raise S.Blocked(f"HTTP {st} {u}")
        if st != 200: print(f"  ! sitemap {st} {u}"); continue
        for m in re.finditer(r"/referee/([a-z0-9\-]+)/(\d+)", tx): names[m.group(2)] = m.group(1).replace("-", " ").title()
        todo += [x for x in re.findall(r"<loc>([^<]*sitemap[^<]*referee[^<]*)</loc>", tx) if x not in seen]
    if names: jw(fn, names)
    print(f"  sitemap sudija: {len(names)} imena")
    return names


def referees(SD, a):
    teams = SD["teams"]
    byid = {str(t["id"]): k for k, t in teams.items() if t.get("id") is not None}
    cl = S.Client(a.offline, a.ttl)
    now, found, unresolved = time.time(), {}, []
    try:
        names = None
        for tid, k in byid.items():
            j = cl.get(f"/api/team/{tid}/upcoming-events?page=1&limit=10")
            for it in S.events_in(j) if j else []:
                ev = S.norm_event(it)
                if ev["fin"] or not ev["ts"] or ev["ts"] < now - 3 * 3600 or ev["ts"] > now + a.days * 86400: continue
                hk, ak = byid.get(str(ev["h"])), byid.get(str(ev["a"]))
                if not hk or not ak or ev["id"] in found: continue
                rid, rname = find_ref(it)
                if not rname and rid:
                    names = names if names is not None else sitemap_names(cl)
                    rname = names.get(str(rid))
                if not rname and (rid or a.deep) and not a.offline and len(found) + len(unresolved) < 40:
                    ej = cl.get(f"/api/event/{ev['id']}")                 # poslednja opcija: detalj meca
                    r2 = find_ref(ej) if ej else (None, None)
                    rname = r2[1] or (names or {}).get(str(r2[0]))
                if rname: found[ev["id"]] = (hk, ak, rname.strip())
                else: unresolved.append(f"{hk}|{ak}" + (f" (refereeId {rid} bez imena)" if rid else " (sudija jos nije objavljen)"))
    except S.Blocked as e:
        print("STOP:", e, "- sacuvano sto je stiglo, ne zaobilazim.")
    finally:
        cl.close()
    p = os.path.join(D, "referees.json")
    old = jl(p, {})
    fresh = {f"{h}|{g}": n for h, g, n in found.values()}
    keep = {k: v for k, v in old.items() if k not in fresh and a.keep_old}
    out = {**keep, **fresh}
    jw(p, out)
    print(f"referees.json: {len(fresh)} sudija za predstojece mecove" + (f" (+{len(keep)} ranijih)" if keep else ""))
    for k, v in sorted(fresh.items()): print(f"  {k:<34} {v}")
    if unresolved: print(f"  bez sudije ({len(unresolved)}): " + "; ".join(unresolved[:8]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="epl")
    ap.add_argument("--only", choices=["ref", "stad"], default=None)
    ap.add_argument("--offline", action="store_true"); ap.add_argument("--ttl", type=float, default=6.0)
    ap.add_argument("--days", type=float, default=7, help="sudije za mecove u narednih N dana")
    ap.add_argument("--deep", action="store_true", help="i bez refereeId probaj /api/event/<id>")
    ap.add_argument("--keep-old", action="store_true", help="ostavi ranije (rucne) unose za mecove koji nisu u novom popisu")
    ap.add_argument("--no-net", action="store_true", help="stadioni samo iz tabele (bez Nominatim)")
    ap.add_argument("--ua", default="betting-analysis-hobby/1.0 (stadium coordinates; personal use)")
    a = ap.parse_args()
    SD = jl(SH, None)
    if not SD or not SD.get("teams"): sys.exit(f"Nema {SH}: prvo pokreni statshub_football.py --league {CUR['code']}")
    if a.only != "ref": stadiums(SD["teams"], a)
    if a.only != "stad": referees(SD, a)


if __name__ == "__main__":
    main()
