#!/usr/bin/env python3
"""Tabela lige za sajt: data/<liga>/statshub_data.json (+ keš zvanicne Statshub tabele) -> data/<liga>/table.json

  python fb_table.py --league laliga        # jedna liga
  python fb_table.py --all                  # epl, laliga, l1, bl, sa
  python fb_table.py --league laliga --check   # ispisi i razlike izmedju izracunate i zvanicne tabele

Ne zove mrezu: koristi ono sto je statshub_football.py vec skinuo (rezultate svih mecova i, ako je u kesu, zvanicnu tabelu
/api/unique-tournament/<ut>/<sezona>/standing). Redovi tabele:
  - ZVANICNA tabela (ako je u kesu i slaze se sa rezultatima: nijedan tim nema manje utakmica nego sto smo videli) daje U/P/N/I/golove/bodove,
    pa tabela ne zavisi od toga koliko meceva je Statshub vratio po timu;
  - inace se tabela racuna iz rezultata tekuce sezone (isti rezultat kao pre) i oznaci se kao izracunata.
  - forma (poslednjih 5, najnoviji prvi, sa rezultatom i protivnikom za 'hover') uvek dolazi iz rezultata.
Zone (Liga sampiona / Evropa / Konferencija / baraz / ispadanje) su po ligama u BANDS; promeni ih ovde kad se promene pravila."""
import argparse, glob, json, os, re, sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# a..b = mesta (ukljucivo), c = klasa boje na sajtu, t = opis u legendi
BANDS = {
    "epl":    [(1, 4, "cl", "Liga šampiona"), (5, 5, "el", "Liga Evrope"), (18, 20, "rel", "Ispadanje (Championship)")],
    "laliga": [(1, 4, "cl", "Liga šampiona"), (5, 5, "el", "Liga Evrope"), (6, 6, "co", "Liga konferencije (kvalifikacije)"), (18, 20, "rel", "Ispadanje (Segunda)")],
    "l1":     [(1, 3, "cl", "Liga šampiona"), (4, 4, "clq", "Liga šampiona (kvalifikacije)"), (5, 5, "el", "Liga Evrope"), (6, 6, "co", "Liga konferencije (kvalifikacije)"),
               (16, 16, "po", "Baraž za ispadanje"), (17, 18, "rel", "Ispadanje (Ligue 2)")],
    "bl":     [(1, 4, "cl", "Liga šampiona"), (5, 5, "el", "Liga Evrope"), (6, 6, "co", "Liga konferencije (kvalifikacije)"), (16, 16, "po", "Baraž za ispadanje"), (17, 18, "rel", "Ispadanje (2. Bundesliga)")],
    "sa":     [(1, 4, "cl", "Liga šampiona"), (5, 5, "el", "Liga Evrope"), (6, 6, "co", "Liga konferencije (kvalifikacije)"), (18, 20, "rel", "Ispadanje (Serie B)")],
}
SIZE = {"epl": 20, "laliga": 20, "l1": 18, "bl": 18, "sa": 20}
ALIASES = {"pl": ("matches", "played", "gamesPlayed", "games", "mp", "gp"), "w": ("wins", "won", "w"), "d": ("draws", "draw", "drawn", "ties", "d", "t"),
           "l": ("losses", "lost", "l", "defeats"), "gf": ("scoresFor", "goalsFor", "gf", "goalsScored", "scored"),
           "ga": ("scoresAgainst", "goalsAgainst", "ga", "goalsConceded", "conceded"), "pts": ("points", "pts")}


def jl(p, dflt=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return dflt


def season_start(today):
    return f"{today.year if today.month >= 7 else today.year - 1}-07-01"


def num(o, names):
    for k in names:
        v = o.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
            return int(v)
    return None


# ------------------------------------------------------------------ iz rezultata
def computed(SD, ut, today):
    T = SD.get("teams") or {}
    start = season_start(today)
    R = {k: {"key": k, "name": v.get("name") or k, "pl": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0, "res": []} for k, v in T.items()}
    seen = set()
    for m in sorted(SD.get("league_matches") or SD.get("matches") or [], key=lambda m: m.get("ts") or 0):
        sig = (m.get("d"), m.get("h"), m.get("a"))
        if m.get("ut") != ut or (m.get("d") or "") < start or m["h"] not in R or m["a"] not in R or sig in seen:
            continue
        seen.add(sig)
        for t, o, gf, ga, home in ((m["h"], m["a"], m["hg"], m["ag"], True), (m["a"], m["h"], m["ag"], m["hg"], False)):
            r = R[t]
            k = "w" if gf > ga else "d" if gf == ga else "l"
            r["pl"] += 1; r["gf"] += gf; r["ga"] += ga; r[k] += 1; r["pts"] += 3 if k == "w" else 1 if k == "d" else 0
            r["res"].append({"r": k.upper(), "s": f"{gf}:{ga}", "o": R[o]["name"], "h": home, "d": m["d"]})
    return R


def sort_rows(rows):
    rows.sort(key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"], r["name"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


# ------------------------------------------------------------------ zvanicna tabela iz kesa
def _is_row(o):
    return isinstance(o, dict) and ("team" in o or "teamId" in o) and num(o, ALIASES["pts"]) is not None


def _lists(o, parent=None):
    if isinstance(o, dict):
        for v in o.values():
            yield from _lists(v, o)
    elif isinstance(o, list):
        if sum(1 for x in o if _is_row(x)) >= 8:
            yield parent, o
        for v in o:
            yield from _lists(v, parent)


def official(SD, d, ut):
    """{key_tima: {rank, pl, w, d, l, gf, ga, pts}} iz kesirane zvanicne tabele, ili None. Ako u kesu ima vise tabela (ukupno/kuci/gost) uzima onu sa najvise bodova."""
    T = SD.get("teams") or {}
    byid = {str(v.get("id")): k for k, v in T.items() if v.get("id") is not None}
    byname = {str(v.get("name") or "").lower(): k for k, v in T.items()}
    files = sorted(glob.glob(os.path.join(d, "statshub", "*standing*.json")), key=lambda p: (f"unique_tournament_{ut}_" not in p, p))
    best = None
    for fp in files:
        for parent, lst in _lists(jl(fp, {})):
            out = {}
            for i, x in enumerate(lst):
                if not _is_row(x):
                    continue
                t = x.get("team") if isinstance(x.get("team"), dict) else {}
                tid = str(t.get("id") if t.get("id") is not None else x.get("teamId"))
                k = byid.get(tid) or byname.get(str(t.get("name") or "").lower())
                vals = {f: num(x, ALIASES[f]) for f in ALIASES}
                if not k or any(vals[f] is None for f in ("pl", "pts")):
                    continue
                for f in ("w", "d", "l", "gf", "ga"):
                    vals[f] = vals[f] or 0
                out[k] = {**vals, "rank": num(x, ("position", "rank")) or i + 1}
            tp = str((parent or {}).get("type") or (parent or {}).get("name") or "").lower()
            if len(out) >= 8 and not any(w in tp for w in ("home", "away")):
                score = sum(v["pts"] for v in out.values())
                if best is None or score > best[0]:
                    best = (score, out)
    return best[1] if best else None


# ------------------------------------------------------------------ sklapanje
def build(code, today=None, SD=None, check=False):
    from fb_leagues import LEAGUES
    cfg = LEAGUES[code]
    d = os.path.join("data", cfg["dir"])
    SD = SD or jl(os.path.join(d, "statshub_data.json"))
    if not SD or not SD.get("teams"):
        print(f"[{code}] nema {os.path.join(d, 'statshub_data.json')} (pokreni statshub_football.py --league {code})")
        return None
    today = today or date.today()
    R = computed(SD, cfg.get("ut"), today)
    off = official(SD, d, cfg.get("ut"))
    src, note = "computed", ""
    rows = [r for r in R.values() if r["pl"] > 0]
    if off:
        bad = [k for k, v in off.items() if k in R and v["pl"] < R[k]["pl"]]
        if bad:
            note = "zvanicna tabela se ne slaze sa rezultatima (" + ", ".join(bad[:3]) + "): koristim izracunatu"
            off = None
    if check and off:
        for k, v in sorted(off.items(), key=lambda kv: kv[1]["rank"]):
            c = R.get(k)
            if c and (c["pl"], c["pts"], c["gf"], c["ga"]) != (v["pl"], v["pts"], v["gf"], v["ga"]):
                print(f"  RAZLIKA {k}: zvanicna U{v['pl']} B{v['pts']} {v['gf']}:{v['ga']}  |  izracunata U{c['pl']} B{c['pts']} {c['gf']}:{c['ga']}")
    if off:
        src = "official"
        rows = []
        for k, v in off.items():
            r = R.get(k) or {"key": k, "name": SD["teams"][k].get("name") or k, "res": []}
            rows.append({**{x: r[x] for x in ("key", "name", "res")}, **{x: v[x] for x in ("pl", "w", "d", "l", "gf", "ga", "pts")}, "orank": v["rank"]})
        rows.sort(key=lambda r: r["orank"])
        for i, r in enumerate(rows):
            r["rank"] = i + 1; r.pop("orank")
    else:
        sort_rows(rows)
    for r in rows:
        res = r.pop("res")
        r["gd"] = r["gf"] - r["ga"]
        r["form"] = "".join(x["r"] for x in res[-5:])
        r["fl"] = [{**x, "d": x["d"]} for x in reversed(res[-5:])]
    pls = sorted(r["pl"] for r in rows)
    size = SIZE.get(code, len(R))
    partial = src == "computed" and (len(rows) < size or (bool(pls) and (pls[-1] - pls[0] > 3 or pls[0] < 1)))
    try:
        import fb_factors as FX
        zones = FX.ZONES.get(code, FX.ZONES["epl"])
    except Exception:
        zones = {"cl": 4, "eu": 7, "rel": 3}
    out = {"updated": datetime.now().isoformat(timespec="seconds"), "league": code, "source": src, "rows": rows, "n": len(rows), "partial": partial,
           "season_from": season_start(today), "zones": zones, "bands": [{"a": a, "b": b, "c": c, "t": t} for a, b, c, t in BANDS.get(code, [])]}
    if note:
        out["note"] = note
    return out


def write(code, today=None, SD=None, check=False):
    tb = build(code, today, SD, check)
    if not tb or not tb["rows"]:
        return None
    p = os.path.join("data", __import__("fb_leagues").LEAGUES[code]["dir"], "table.json")
    json.dump(tb, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)
    print(f"[{code}] tabela: {tb['n']} timova, izvor {'zvanicna Statshub tabela' if tb['source'] == 'official' else 'izracunato iz rezultata'} -> {p}"
          + ("  (NEPOTPUNA: neki timovi imaju premalo mečeva u podacima)" if tb["partial"] else "") + (f"  [{tb['note']}]" if tb.get("note") else ""))
    return tb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="epl"); ap.add_argument("--all", action="store_true"); ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    from fb_leagues import LEAGUES
    for c in (list(BANDS) if a.all else [a.league]):
        if c in LEAGUES:
            write(c, check=a.check)


if __name__ == "__main__":
    main()
