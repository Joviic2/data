#!/usr/bin/env python3
"""backtest.py - testiranje quant_model.py na istorijskim podacima (bez buducnosti: svaki dan se model ponovo fituje samo na ranijim mecevima).

  python backtest.py --league aba                       # 1) walk-forward tacnost meca (MAE, Brier, log-loss, kalibracija) + poredjenje sa naivnim modelom
  python backtest.py --league aba --grid                # isto, za vise kombinacija parametara (HL_TEAM, RIDGE, FF_BLEND)
  python backtest.py --league aba --props               # 2) props: da li model bolje procenjuje igrace od naivnog proseka (+ pseudo-ROI)
  python backtest.py --league aba --odds data/aba/odds_history.jsonl   # 3) PRAVI ROI: replay snimljenih Superbet kvota (fetch_superbet.py ih belezi)

Vazno o iskrenosti rezultata:
 - (1) i (2) NE koriste prave kvote, pa ne mogu da kazu ROI; mere samo da li je model precizniji od naivne procene.
   "Pseudo-ROI" u (2) koristi liniju = zaokruzen prosek zadnjih 10 utakmica i kvotu 1.91; prava linija je ostrija, pa je to gornja granica.
 - Jedini pravi ROI daje (3), i to tek kad skupi dovoljno snimljenih kvota (stotine opklada). Mali uzorak = sum, ne dokaz."""
import argparse, collections, itertools, json, math, os, sys, time

import quant_model as Q


def _mean(x):
    x = list(x)
    return sum(x) / len(x) if x else float("nan")


# ------------------------------------------------------------------ 1) mecevi
def walk_forward(L, min_train=40, **kw):
    games, rows = L.games, []
    for d in sorted({int(g["t"] // 86400) for g in games}):
        start = d * 86400
        past = [g for g in games if g["t"] < start]
        if len(past) < min_train:
            continue
        M = L.fit(start, **kw)
        if not M.ok:
            continue
        hm = _mean(g["hs"] - g["as"] for g in past)
        tm = _mean(g["hs"] + g["as"] for g in past)
        pf, pa, n = collections.defaultdict(float), collections.defaultdict(float), collections.Counter()
        for g in past:
            for t, a, b in ((g["h"], g["hs"], g["as"]), (g["a"], g["as"], g["hs"])):
                pf[t] += a
                pa[t] += b
                n[t] += 1
        for i, g in enumerate(games):
            if int(g["t"] // 86400) != d:
                continue
            p = M.predict(g["h"], g["a"], M._rd(i, g))
            if not p or n[g["h"]] < 3 or n[g["a"]] < 3:
                continue
            nh, na = (pf[g["h"]] + pa[g["a"]] * 0 + 0) / n[g["h"]], (pf[g["a"]]) / n[g["a"]]
            nm = ((pf[g["h"]] - pa[g["h"]]) / n[g["h"]] - (pf[g["a"]] - pa[g["a"]]) / n[g["a"]]) + hm
            nt = (pf[g["h"]] / n[g["h"]] + pa[g["a"]] / n[g["a"]]) / 2 + (pf[g["a"]] / n[g["a"]] + pa[g["h"]] / n[g["h"]]) / 2
            rows.append({"m": g["hs"] - g["as"], "t": g["hs"] + g["as"], "pm": p["margin"], "pt": p["total"], "sd": M.sd_m,
                         "nm": nm, "nt": nt, "base_m": hm, "base_t": tm})
    return rows


def summarize(rows):
    if not rows:
        return None
    n = len(rows)
    ll = lambda p, y: -math.log(clamp(p) if y else clamp(1 - p))
    clamp = lambda p: min(1 - 1e-6, max(1e-6, p))
    out = {"n": n}
    for name, pm, pt in (("model", "pm", "pt"), ("naivni (prosek tima)", "nm", "nt"), ("samo liga", "base_m", "base_t")):
        mae_m = _mean(abs(r["m"] - r[pm]) for r in rows)
        mae_t = _mean(abs(r["t"] - r[pt]) for r in rows)
        if pm == "pm":
            pw = [Q.phi(r["pm"] / r["sd"]) for r in rows]
        else:
            sdv = math.sqrt(_mean((r["m"] - r[pm]) ** 2 for r in rows))
            pw = [Q.phi(r[pm] / sdv) for r in rows]
        y = [1 if r["m"] > 0 else 0 for r in rows]
        out[name] = {"MAE razlika": mae_m, "MAE zbir": mae_t, "tacnost pobednika": _mean((p > .5) == bool(yy) for p, yy in zip(pw, y)),
                     "Brier": _mean((p - yy) ** 2 for p, yy in zip(pw, y)), "log-loss": _mean(ll(p, yy) for p, yy in zip(pw, y))}
        if pm == "pm":
            bins = collections.defaultdict(list)
            for p, yy in zip(pw, y):
                bins[min(9, int(p * 10))].append((p, yy))
            out["kalibracija"] = [(b, len(v), _mean(p for p, _ in v), _mean(yy for _, yy in v)) for b, v in sorted(bins.items())]
    return out


def show(res, title):
    print(f"\n=== {title} (n={res['n']}) ===")
    keys = ["MAE razlika", "MAE zbir", "tacnost pobednika", "Brier", "log-loss"]
    print(f"{'':24s}" + "".join(f"{k:>20s}" for k in keys))
    for nm in ("model", "naivni (prosek tima)", "samo liga"):
        print(f"{nm:24s}" + "".join(f"{res[nm][k]:20.3f}" for k in keys))
    print("kalibracija modela (predvidjena verovatnoca pobede domacina -> stvarna):")
    for b, n, p, y in res["kalibracija"]:
        print(f"   {b * 10:3d}-{b * 10 + 10:<3d}%  n={n:4d}  pred {p * 100:5.1f}%  stvarno {y * 100:5.1f}%")


# ------------------------------------------------------------------ 2) props
def props_bt(L, min_train=40, delta=0.55, odds=1.91, max_days=None):
    games = L.games
    days = sorted({int(g["t"] // 86400) for g in games})
    res = collections.defaultdict(list)
    for d in days:
        start = d * 86400
        if sum(1 for g in games if g["t"] < start) < min_train:
            continue
        M = L.fit(start)
        if not M.ok:
            continue
        PR = Q.Props(L, M, start)
        for i, g in enumerate(games):
            if int(g["t"] // 86400) != d:
                continue
            mp = M.predict(g["h"], g["a"], M._rd(i, g))
            if not mp:
                continue
            for r in L.prec:
                if r["i"] != i or r["min"] < 10:
                    continue
                home = r["tm"] == g["h"]
                pr = PR.project(r["id"], r["tm"], r["opp"], home, mp, M.sd_m, ())
                if not pr:
                    continue
                hist = [x for x in PR.byp.get(r["id"], []) if x["t"] < start][-10:]
                if len(hist) < 5:
                    continue
                for s in Q.STATS:
                    naive = _mean(x[s] for x in hist)
                    line = math.floor(naive) + 0.5
                    S = pr["stats"][s]
                    po, pu, _ = Q.over_under(line, S["mu"], S["sd"], "norm" if s == "pts" else "count")
                    res[s].append({"y": r[s], "mu": S["mu"], "naive": naive, "line": line, "po": po, "pu": pu, "grp": "st" if pr["base_min"] >= 20 else "bn"})
    return res


def show_props(res, odds=1.91, edge=0.05):
    print("\n=== PROPS: model vs naivni prosek zadnjih 10 (MAE = manje je bolje) ===")
    for s, rows in res.items():
        mm, mn = _mean(abs(r["y"] - r["mu"]) for r in rows), _mean(abs(r["y"] - r["naive"]) for r in rows)
        bets = [(r, "O") for r in rows if r["po"] - 0.5 >= edge and r["y"] != r["line"]] + [(r, "U") for r in rows if r["pu"] - 0.5 >= edge and r["y"] != r["line"]]
        hit = [(r["y"] > r["line"]) if side == "O" else (r["y"] < r["line"]) for r, side in bets]
        roi = (sum(odds - 1 if h else -1 for h in hit) / len(hit)) if hit else float("nan")
        print(f"  {s}: n={len(rows):5d} | MAE model {mm:.3f} vs naivno {mn:.3f} ({(1 - mm / mn) * 100:+.1f}%) | "
              f"pseudo-opklade (edge>={edge * 100:.0f}% vs 50/50) n={len(hit)}, pogodak {_mean(hit) * 100 if hit else float('nan'):.1f}%, pseudo-ROI {roi * 100:+.1f}% (breakeven {100 / odds:.1f}%)")
    print("  NAPOMENA: linija = naivni prosek, nije prava Superbet linija. Ovo meri informaciju koju model dodaje, ne zaradu.")


# ------------------------------------------------------------------ 3) pravi ROI iz snimljenih kvota
def sel_odds(snap, tip):
    b = snap.get("b") or []
    b0 = b[0] if b else {}
    k = tip["k"]
    if k == "h2h" and b0.get("h2h"):
        return b0["h2h"][0 if tip["team"] == "h" else 1]
    if k == "sp":
        for x in b:
            sp = x.get("sp")
            if sp and abs(sp[0] - (tip["line"] if tip["team"] == "h" else -tip["line"])) < 1e-9:
                return sp[1 if tip["team"] == "h" else 2]
    if k == "tt":
        for x in b:
            tt = x.get("tt")
            if tt and abs(tt[0] - tip["line"]) < 1e-9:
                return tt[1 if tip["side"] == "OVER" else 2]
    if k == "prop":
        for p in snap.get("player_props") or []:
            if p["p"] == tip["pname"] and p["s"] == tip["stat"] and abs(p["l"] - tip["line"]) < 1e-9:
                return p["o"] if tip["side"] == "OVER" else p["u"]
    return None


def settle(tip, g, L, i):
    """1 = dobitak, 0 = gubitak, None = push/nepoznato."""
    m = g["hs"] - g["as"]
    k = tip["k"]
    if k == "h2h":
        return int((m > 0) == (tip["team"] == "h")) if m != 0 else None
    if k == "sp":
        v = (m + tip["line"]) if tip["team"] == "h" else (-m + tip["line"])
        return None if v == 0 else int(v > 0)
    if k == "tt":
        t = g["hs"] + g["as"]
        return None if t == tip["line"] else int((t > tip["line"]) == (tip["side"] == "OVER"))
    if k == "prop":
        r = next((x for x in L.prec if x["i"] == i and x["id"] == tip["pid"]), None)
        if not r:
            return None
        y = r[tip["stat"]]
        return None if y == tip["line"] else int((y > tip["line"]) == (tip["side"] == "OVER"))
    return None


def odds_replay(L, path, lg, bet_at="first", min_ev=None):
    snaps = collections.defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            snaps[(r.get("h"), r.get("a"), str(r.get("start"))[:16])].append(r)
    bets = []
    cfg = dict(Q.CFG)
    if min_ev is not None:
        cfg["MIN_EV"] = min_ev
    for key, ss in snaps.items():
        ss.sort(key=lambda r: r.get("ts", 0))
        start = Q.parse_ts(ss[0].get("start"))
        if not start or start > time.time() - 3 * 3600:
            continue
        h, a = L.team_code(key[0]), L.team_code(key[1])
        gi = next((i for i, g in enumerate(L.games) if g["h"] == h and g["a"] == a and abs(g["t"] - start) < 12 * 3600), None)
        if gi is None:
            continue
        ss = [s for s in ss if s.get("ts", 0) < start]
        if not ss:
            continue
        snap, close = (ss[0] if bet_at == "first" else ss[-1]), ss[-1]
        ev = {"lg": lg, "h": key[0], "a": key[1], "start": ss[0].get("start"), "b": snap.get("b") or [],
              "player_props": [{"p": x[0], "s": x[1], "l": x[2], "o": x[3], "u": x[4], "main": bool(x[5])} for x in snap.get("pp") or []]}
        snap_full = {"b": snap.get("b"), "player_props": ev["player_props"]}
        close_full = {"b": close.get("b"), "player_props": [{"p": x[0], "s": x[1], "l": x[2], "o": x[3], "u": x[4]} for x in close.get("pp") or []]}
        Q.annotate_events([ev], lg, cfg=cfg, asof=start - 1, league=L)
        for t in ev.get("tips") or []:
            res = settle(t, L.games[gi], L, gi)
            co = sel_odds(close_full, t)
            bets.append({"t": start, "tip": t, "res": res, "clv": (t["odds"] / co - 1) if co else None})
    return bets


def show_bets(bets):
    done = [b for b in bets if b["res"] is not None]
    print(f"\n=== ROI iz snimljenih kvota: {len(bets)} tipova, {len(done)} resenih (bez push-a) ===")
    if not done:
        print("  Nema resenih opklada. Pusti fetch_superbet.py da radi nekoliko dana (belezi data/<liga>/odds_history.jsonl).")
        return
    pr = lambda b: (b["tip"]["odds"] - 1) if b["res"] else -1
    def line(name, bs):
        if not bs:
            return
        roi = _mean(pr(b) for b in bs)
        se = math.sqrt(_mean((pr(b) - roi) ** 2 for b in bs) / len(bs))
        print(f"  {name:22s} n={len(bs):4d}  pogodak {_mean(b['res'] for b in bs) * 100:5.1f}%  ROI {roi * 100:+6.1f}% (+-{se * 100:.1f})  prosecan predvidjeni EV {_mean(b['tip']['ev'] for b in bs) * 100:+5.1f}%")
    line("SVE", done)
    for k in ("h2h", "sp", "tt", "prop"):
        line("trziste " + k, [b for b in done if b["tip"]["k"] == k])
    for lo, hi in ((0.03, 0.05), (0.05, 0.08), (0.08, 9)):
        line(f"EV {lo * 100:.0f}-{min(hi, 0.99) * 100:.0f}%+", [b for b in done if lo <= b["tip"]["ev"] < hi])
    cl = [b["clv"] for b in done if b["clv"] is not None]
    if cl:
        print(f"  CLV (kvota pri opkladi / zatvarajuca - 1): prosek {_mean(cl) * 100:+.2f}% na {len(cl)} opklada (pozitivan CLV = model hvata vrednost pre tržista)")
    bank = 1.0
    for b in sorted(done, key=lambda x: x["t"]):
        k = b["tip"]["kelly"]
        bank *= 1 + k * (pr(b))
    print(f"  Kelly simulacija (cetvrtina Kelly-ja, max 2%): bankroll x{bank:.3f}")
    print(f"  UPOZORENJE: sa n<300 standardna greska ROI-ja je desetak procenata, pa je ovo indikacija, ne dokaz.")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="aba")
    ap.add_argument("--root", default=".")
    ap.add_argument("--min-train", type=int, default=40)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--props", action="store_true")
    ap.add_argument("--odds", default="")
    ap.add_argument("--bet-at", default="first", choices=["first", "last"])
    ap.add_argument("--min-ev", type=float, default=None)
    a = ap.parse_args()
    L = Q.League(a.league, a.root)
    print(f"{a.league}: {len(L.games)} odigranih utakmica sa boxscore-om")
    if len(L.games) < a.min_train + 10:
        sys.exit("Premalo utakmica za backtest (smanji --min-train ili sacekaj vise kola).")
    if a.grid:
        out = []
        for hl, rg, fb in itertools.product((40, 75, 120), (2.0, 4.0, 8.0), (0.0, 0.3, 0.5)):
            r = summarize(walk_forward(L, a.min_train, HL_TEAM=hl, RIDGE=rg, FF_BLEND=fb))
            if r:
                out.append((r["model"]["log-loss"], r["model"]["MAE razlika"], r["model"]["MAE zbir"], hl, rg, fb))
        print("\nHL_TEAM RIDGE FF_BLEND |  log-loss  MAE razlika  MAE zbir   (sortirano po log-loss)")
        for ll, mm, mt, hl, rg, fb in sorted(out)[:12]:
            print(f"{hl:7d} {rg:5.1f} {fb:8.1f} | {ll:8.4f} {mm:11.3f} {mt:9.3f}")
        print("Oprez: biranje najboljeg parametra na istom uzorku daje optimisticku sliku; menjaj CFG samo ako je razlika jasna i stabilna.")
        return
    r = summarize(walk_forward(L, a.min_train))
    if r:
        show(r, "walk-forward predikcija meceva")
    if a.props:
        show_props(props_bt(L, a.min_train))
    if a.odds:
        show_bets(odds_replay(L, a.odds, a.league, a.bet_at, a.min_ev))


if __name__ == "__main__":
    main()
