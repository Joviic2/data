"""
Korak 4: play-by-play -> petorke (lineups), on/off i sudije.
Ulaz:  data/site.json (posle build_site.py)
Izlaz: data/an/<utakmica>.json (mali fajl po utakmici, ne ponavlja se) i data/analytics.json
Napomena: u svakoj utakmici se proverava da zbir poena iz PBP-a daje konacan rezultat, inace se ona ne racuna.
"""
import json, re, time
from pathlib import Path
import requests
from build_site import pretty

DATA = Path("data"); AN = DATA / "an"
SEASON = "E2026"; MAX_NEW = 40          # najvise novih utakmica po pokretanju (ostale stizu u sledecim)
API = "https://live.euroleague.net/api/"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get(name, n):
    for _ in range(3):
        try:
            r = requests.get(API + name, params={"gamecode": n, "seasoncode": SEASON}, headers=HDR, timeout=30)
            if r.status_code == 200 and r.text.strip():
                return r.json()
        except Exception as e:
            print(name, n, e)
        time.sleep(2)
    return None


def pid(x): return str(x or "").strip().lstrip("P")
def num(v):
    try: return int(v)
    except (TypeError, ValueError): return None
def sec(s):
    m = re.match(r"^(\d+):(\d+)$", str(s or "").strip())
    return int(m[1]) * 60 + int(m[2]) if m else None
PL = lambda p: 600 if p <= 4 else 300
PS = lambda p: 600 * (p - 1) if p <= 4 else 2400 + 300 * (p - 5)


def events(d):
    ev = []
    for v in (d.values() if isinstance(d, dict) else []):
        if isinstance(v, list) and v and isinstance(v[0], dict) and "PLAYTYPE" in v[0]:
            ev += v
    ev.sort(key=lambda e: num(e.get("NUMBEROFPLAY")) or 0)
    return ev


def refs_of(o, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            if "referee" in k.lower():
                items = v if isinstance(v, list) else [v]
                for x in items:
                    if isinstance(x, dict):
                        x = next((y for y in x.values() if isinstance(y, str) and y.strip()), "")
                    for part in str(x or "").split(";"):
                        if part.strip(): acc.append(pretty(part))
            else:
                refs_of(v, acc)
    elif isinstance(o, list):
        for x in o: refs_of(x, acc)
    return acc


def proc(ev, g, start, swap):
    on = {s: set(start[s]) for s in "ha"}
    lu = {"h": {}, "a": {}}; po = {"h": {}, "a": {}}; tot = {"h": [0, 0, 0], "a": [0, 0, 0]}
    st = {"t0": 0, "bad": 0}; sc = [0, 0]; per = 0

    def add(dt, d):
        for s, i in (("h", 0), ("a", 1)):
            if not dt and not d[0] and not d[1]: continue
            if len(on[s]) != 5: st["bad"] += dt; continue
            pf, pa = d[i], d[1 - i]
            for tg in (lu[s].setdefault(tuple(sorted(on[s])), [0, 0, 0]), tot[s],
                       *[po[s].setdefault(p, [0, 0, 0]) for p in on[s]]):
                tg[0] += dt; tg[1] += pf; tg[2] += pa

    for e in ev:
        pt = (e.get("PLAYTYPE") or "").strip()
        if pt == "BP": per += 1
        p = max(per, 1); rem = sec(e.get("MARKERTIME"))
        if pt == "BP" or rem is None: rem = PL(p)
        t = PS(p) + PL(p) - rem; dt = max(0, t - st["t0"]); st["t0"] = max(t, st["t0"])
        a, b = num(e.get("POINTS_A")), num(e.get("POINTS_B")); d = [0, 0]
        if a is not None and b is not None:
            if swap: a, b = b, a
            d = [max(0, a - sc[0]), max(0, b - sc[1])]; sc = [a, b]
        add(dt, d)
        if pt in ("IN", "OUT"):
            code = (e.get("CODETEAM") or "").strip()
            s = "h" if code == g["h"] else "a" if code == g["a"] else None; i = pid(e.get("PLAYER_ID"))
            if s and i: (on[s].add if pt == "IN" else on[s].discard)(i)
    return lu, po, tot, sc, st["bad"]


def one(g):
    start = {s: [p[0] for p in g["box"][s]["p"] if p[3]] for s in "ha"}
    d = get("PlayByPlay", g["n"])
    if d is None: return None
    out = {"n": g["n"], "ok": False, "refs": refs_of(get("Header", g["n"]) or {}, [])}
    ev = events(d)
    if ev and all(len(v) == 5 for v in start.values()):
        for swap in (False, True):
            lu, po, tot, sc, bad = proc(ev, g, start, swap)
            if sc == [g["hs"], g["as"]] and bad < 90:
                out.update(ok=True, tot=tot, po=po,
                           lu={s: [[list(k), *v] for k, v in lu[s].items() if v[0] > 0] for s in "ha"})
                break
    return out


def aggregate(site):
    gm = {g["n"]: g for g in site["games"]}; teams, refs = {}, {}
    for f in AN.glob("*.json"):
        x = json.loads(f.read_text(encoding="utf-8")); g = gm.get(x["n"])
        if not g: continue
        if x["refs"] and g.get("box"):
            ht, at = g["box"]["h"]["tot"], g["box"]["a"]["tot"]
            for r in x["refs"]:
                e = refs.setdefault(r, {"g": 0, "pf": 0, "fta": 0, "pts": 0, "tov": 0}); e["g"] += 1
                for k in ("pf", "fta", "pts", "tov"): e[k] += ht[k] + at[k]
        if not x["ok"]: continue
        for s in "ha":
            T = teams.setdefault(g[s], {"g": 0, "tot": [0, 0, 0], "lu": {}, "onoff": {}}); T["g"] += 1
            for i in range(3): T["tot"][i] += x["tot"][s][i]
            for k, *v in x["lu"][s]:
                e = T["lu"].setdefault(tuple(k), [0, 0, 0])
                for i in range(3): e[i] += v[i]
            for p, v in x["po"][s].items():
                e = T["onoff"].setdefault(p, [0, 0, 0])
                for i in range(3): e[i] += v[i]
    for T in teams.values():
        T["lineups"] = [{"p": list(k), "s": v[0], "pf": v[1], "pa": v[2]}
                        for k, v in sorted(T.pop("lu").items(), key=lambda kv: -kv[1][0]) if v[0] >= 120][:15]
    return {"updated": site.get("updated"), "teams": teams, "refs": refs}


def main():
    site = json.loads((DATA / "site.json").read_text(encoding="utf-8")); AN.mkdir(parents=True, exist_ok=True); new = 0
    for g in site["games"]:
        f = AN / f"{g['n']}.json"
        if f.exists() or not g.get("box") or g.get("live") or new >= MAX_NEW: continue
        new += 1; r = one(g)
        if r is not None: f.write_text(json.dumps(r, separators=(",", ":")), encoding="utf-8")
        time.sleep(1)
    res = aggregate(site)
    (DATA / "analytics.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"analytics.json: {len(res['teams'])} timova sa petorkama, {len(res['refs'])} sudija, novih utakmica {new}")
    if site["games"] and not res["refs"]: print("UPOZORENJE: nema sudija u Header odgovoru, proveri polje.")


if __name__ == "__main__":
    main()
