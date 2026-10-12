#!/usr/bin/env python3
"""Walk-forward test Faktora ocaja (fb_factors.desperation) na nasim podacima: da li korekcija stvarno smanjuje log-loss?

  python fb_desp_test.py --league epl                 # poslednjih 200 meceva
  python fb_desp_test.py --league laliga --last 300 --step 8

Za svaku od poslednjih --last utakmica model uci SAMO iz ranijih (kao fb_calibrate.py), faktor ocaja se racuna samo iz ranijih meceva, pa se
ocekivani golovi mnoze sa att/conc. Poredi se skala s (0 = iskljuceno, 1 = podrazumevani desp_att_k=0.06 / desp_conc_k=0.04, 2 = duplo) po
log-lossu za 1X2 i preko/ispod 2.5, i posebno na mecevima gde je bar jedan tim u crvenoj zoni (tamo bi efekat trebalo da se vidi).
Tabela/motivacija se ovde ne racuna: test pretpostavlja punu potrebu (1.0) za sve timove, pa je to GORNJA granica koristi faktora; ako ni tada ne pomaze, ne pomaze.
Zakljucak se ispisuje samo ako ima dovoljno mecova u crvenoj zoni; inace pise da dokaza nema. Ako ne pomaze, postavi PARAMS desp_att_k i desp_conc_k na 0."""
import argparse, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run(code, a):
    from fb_leagues import LEAGUES
    import football_poisson as FP
    import fb_factors as FF
    d = os.path.join("data", LEAGUES[code]["dir"]); p = os.path.join(d, "statshub_data.json")
    if not os.path.exists(p): print(f"[{code}] nema {p}"); return None
    M, _ = FP.load_statshub(p)
    ms = sorted(M, key=lambda m: m["ts"]); K = min(a.last, max(0, len(ms) - 60))
    if K < 40: print(f"[{code}] premalo meceva ({len(ms)})"); return None
    grid = [float(x) for x in a.scales.split(",")]
    L = {s: [0.0, 0.0, 0.0] for s in grid}            # 1X2, O/U 2.5, brojac
    R = {s: [0.0, 0.0, 0.0] for s in grid}            # isto, samo mececi sa crvenom zonom
    MO = {"score": 1.0}                               # test uvodi punu potrebu (tabela nije uracunata): gornja granica koristi faktora
    mod, n, nred = None, 0, 0
    for i, m in enumerate(ms[-K:]):
        past = [x for x in ms if x["ts"] < m["ts"] - 3600]
        if len(past) < 40: continue
        if mod is None or i % a.step == 0:
            mod = FP.fit(FP.eff(past, a.beta), m["d"], a.halflife, a.k)
        if m["h"] not in mod["att"] or m["a"] not in mod["att"]: continue
        lh = mod["mu"] * mod["H"] * mod["att"][m["h"]] * mod["def"][m["a"]]
        la = mod["mu"] * mod["att"][m["a"]] * mod["def"][m["h"]]
        res = 0 if m["hg"] > m["ag"] else 1 if m["hg"] == m["ag"] else 2
        over = m["hg"] + m["ag"] > 2
        red = False
        for s in grid:
            P = dict(FF.PARAMS, desp_att_k=FF.PARAMS["desp_att_k"] * s, desp_conc_k=FF.PARAMS["desp_conc_k"] * s)
            dh, da = FF.desperation(past, m["h"], P, None, MO), FF.desperation(past, m["a"], P, None, MO)
            if s == 1.0: red = dh["red"] or da["red"]
            Pm = FP.probs(FP.grid(lh * dh["att"] * da["conc"], la * da["att"] * dh["conc"], a.rho))
            l12 = -math.log(max(1e-9, Pm["x12"][res])); po = Pm["over"](2); lou = -math.log(max(1e-9, po if over else 1 - po))
            L[s][0] += l12; L[s][1] += lou; L[s][2] += 1
        if red:
            nred += 1
            for s in grid:                             # drugi prolaz samo za crvene (isti racun, odvojen zbir)
                P = dict(FF.PARAMS, desp_att_k=FF.PARAMS["desp_att_k"] * s, desp_conc_k=FF.PARAMS["desp_conc_k"] * s)
                dh, da = FF.desperation(past, m["h"], P, None, MO), FF.desperation(past, m["a"], P, None, MO)
                Pm = FP.probs(FP.grid(lh * dh["att"] * da["conc"], la * da["att"] * dh["conc"], a.rho))
                R[s][0] += -math.log(max(1e-9, Pm["x12"][res])); po = Pm["over"](2); R[s][1] += -math.log(max(1e-9, po if over else 1 - po)); R[s][2] += 1
        n += 1
    if n < 30: print(f"[{code}] n={n} premalo"); return None
    print(f"[{code}] testirano {n} meceva, od toga {nred} sa timom u crvenoj zoni (skala 1.0)")
    print("   skala | 1X2 (svi) | O/U 2.5 (svi) | 1X2 (crveni) | O/U (crveni)")
    for s in grid:
        c = L[s][2] or 1; r = R[s][2]
        print(f"   {s:<5} | {L[s][0] / c:.4f}    | {L[s][1] / c:.4f}        | " + (f"{R[s][0] / r:.4f}       | {R[s][1] / r:.4f}" if r else "-            | -"))
    if 0.0 in grid and 1.0 in grid:
        dall = (L[0.0][0] + L[0.0][1]) / (2 * L[0.0][2]) - (L[1.0][0] + L[1.0][1]) / (2 * L[1.0][2])
        dred = ((R[0.0][0] + R[0.0][1]) / (2 * R[0.0][2]) - (R[1.0][0] + R[1.0][1]) / (2 * R[1.0][2])) if R[0.0][2] else None
        print(f"   razlika log-lossa (iskljuceno - ukljuceno; pozitivno = faktor pomaze): svi {dall:+.4f}" + (f", crveni {dred:+.4f}" if dred is not None else ""))
        if nred < 15:
            print("   ZAKLJUCAK: premalo meceva u crvenoj zoni (<15), nema dokaza ni za ni protiv. Ostavi faktor, ali mu ne veruj; probaj sa vise meceva (--last).")
        elif dred is not None and dred > 0.003 and dall > 0:
            print("   ZAKLJUCAK: faktor blago pomaze na ovom uzorku. I dalje je uzorak mali, proveri na drugoj ligi pre nego sto ga pojacas.")
        else:
            print("   ZAKLJUCAK: ne pomaze (ili je razlika u sumu). Postavi desp_att_k i desp_conc_k na 0 u fb_factors.PARAMS dok ne dokazemo suprotno.")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="epl"); ap.add_argument("--last", type=int, default=200); ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--beta", type=float, default=0.5); ap.add_argument("--halflife", type=float, default=180.0); ap.add_argument("--k", type=float, default=4.0); ap.add_argument("--rho", type=float, default=0.0)
    ap.add_argument("--scales", default="0,0.5,1.0,2.0", help="skale desp_att_k/desp_conc_k koje se isprobavaju (mora sadrzati 0 i 1)")
    a = ap.parse_args()
    run(a.league, a)


if __name__ == "__main__":
    main()
