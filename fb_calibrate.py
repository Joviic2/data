#!/usr/bin/env python3
"""Automatska kalibracija skale ocekivanih golova (--lam-scale) walk-forward backtestom na nasim podacima.

  python fb_calibrate.py --league epl                 # trazi najbolji lam-scale, pise data/<liga>/calibration.json
  python fb_calibrate.py --league laliga --last 250 --step 8
  python fb_calibrate.py --all                        # sve 4 lige redom

Za svaku od poslednjih --last utakmica model uci SAMO iz ranijih (isti postupak kao u football_poisson.py: beta*xG + (1-beta)*golovi,
poluzivot, k), pa se ocekivani golovi (lh, la) mnoze skalom s iz mreze. Bira se s koji daje najmanji log-loss za Preko/Ispod 1.5, 2.5 i 3.5.
Pored toga ispisuje pristrasnost: koliko cesto model kaze 'Preko' naspram stvarne ucestalosti (ako je pomereno, trziste Ispod/Preko je lazno +EV).

Izlaz data/<liga>/calibration.json: {lam_scale, bt_grid, n, bias_before, bias_after, logloss{...}, mean_goals{model, real}, updated}.
Napomena o sigurnosti: skala se ogranicava na [0.85, 1.15], a ako je razlika u log-lossu manja od suma (n malo) ostaje 1.0."""
import argparse, json, math, os, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
LINES = (1.5, 2.5, 3.5)


def run(code, a):
    from fb_leagues import LEAGUES
    import football_poisson as FP
    d = os.path.join("data", LEAGUES[code]["dir"]); p = os.path.join(d, "statshub_data.json")
    if not os.path.exists(p): print(f"[{code}] nema {p} (pokreni statshub_football.py --league {code})"); return None
    M, _ = FP.load_statshub(p)
    ms = sorted(M, key=lambda m: m["ts"])
    K = min(a.last, max(0, len(ms) - 60))
    if K < 40: print(f"[{code}] premalo meceva za kalibraciju ({len(ms)})"); return None
    test = ms[-K:]
    grid_s = [round(x, 3) for x in a.grid]
    ll = {s: {L: 0.0 for L in LINES} for s in grid_s}
    pov = {s: {L: 0.0 for L in LINES} for s in grid_s}
    act = {L: 0 for L in LINES}; sum_l = {s: 0.0 for s in grid_s}; sum_g = 0.0; n = 0
    mod = None
    for i, m in enumerate(test):
        if mod is None or i % a.step == 0:
            tr = [x for x in ms if x["ts"] < m["ts"] - 3600]
            if len(tr) < 40: continue
            mod = FP.fit(FP.eff(tr, a.beta), m["d"], a.halflife, a.k)
        if m["h"] not in mod["att"] or m["a"] not in mod["att"]: continue
        lh = mod["mu"] * mod["H"] * mod["att"][m["h"]] * mod["def"][m["a"]]
        la = mod["mu"] * mod["att"][m["a"]] * mod["def"][m["h"]]
        tot = m["hg"] + m["ag"]; sum_g += tot; n += 1
        for L in LINES: act[L] += tot > L
        for s in grid_s:
            P = FP.probs(FP.grid(lh * s, la * s, a.rho))
            sum_l[s] += (lh + la) * s
            for L in LINES:
                po = P["over"](int(L)); pov[s][L] += po
                ll[s][L] -= math.log(max(1e-9, po if tot > L else 1 - po))
    if n < 30: print(f"[{code}] n={n} premalo"); return None
    tl = {s: sum(ll[s].values()) / (n * len(LINES)) for s in grid_s}
    best = min(grid_s, key=lambda s: tl[s])
    base = tl.get(1.0, tl[best])
    gain = base - tl[best]
    lam = best if gain > 0.002 and 0.85 <= best <= 1.15 else 1.0
    bias = lambda s: {str(L): round(pov[s][L] / n - act[L] / n, 3) for L in LINES}
    res = {"lam_scale": lam, "best_raw": best, "bt_grid": ",".join(str(s) for s in grid_s), "n": n, "beta": a.beta, "halflife": a.halflife, "k": a.k, "rho": a.rho,
           "logloss": {str(s): round(tl[s], 4) for s in grid_s}, "bias_before": bias(1.0) if 1.0 in pov else None, "bias_after": bias(lam if lam in pov else best),
           "mean_goals": {"real": round(sum_g / n, 3), "model_1.0": round(sum_l.get(1.0, 0) / n, 3), "model_best": round(sum_l[best] / n, 3)},
           "over_actual": {str(L): round(act[L] / n, 3) for L in LINES}, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
    os.makedirs(d, exist_ok=True)
    json.dump(res, open(os.path.join(d, "calibration.json"), "w"), indent=1)
    print(f"[{code}] n={n}  najbolji s={best}  primenjen lam-scale={lam}  (log-loss O/U: s=1.0 {base:.4f} -> s={best} {tl[best]:.4f})")
    print(f"      pristrasnost Preko (model - stvarnost) pre: {res['bias_before']}  posle: {res['bias_after']}")
    print(f"      prosek golova: stvarno {res['mean_goals']['real']}, model {res['mean_goals']['model_1.0']} (s=1.0), {res['mean_goals']['model_best']} (s={best})")
    print(f"      -> python football_poisson.py --league {code} --lam-scale {lam}   (ili ga skripta cita iz calibration.json)")
    if best in (grid_s[0], grid_s[-1]) and best != 1.0: print(f"      ! najbolji s je na ivici mreze, probaj sirem --bt-grid (npr. 0.85..1.15)")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="epl"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--last", type=int, default=200, help="koliko poslednjih meceva testirati"); ap.add_argument("--step", type=int, default=10, help="model se uci iznova na svakih N meceva (brze)")
    ap.add_argument("--beta", type=float, default=0.5); ap.add_argument("--halflife", type=float, default=180.0); ap.add_argument("--k", type=float, default=4.0); ap.add_argument("--rho", type=float, default=0.0)
    ap.add_argument("--bt-grid", default="0.90,0.94,0.97,1.0,1.03,1.06,1.10", help="skale koje se isprobavaju")
    a = ap.parse_args()
    a.grid = [float(x) for x in a.bt_grid.split(",")]
    if 1.0 not in a.grid: a.grid.append(1.0); a.grid.sort()
    from fb_leagues import LEAGUES
    for c in (LEAGUES if a.all else [a.league]): run(c, a)


if __name__ == "__main__":
    main()
