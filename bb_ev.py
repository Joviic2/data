#!/usr/bin/env python3
"""Kosarka: verovatnoce i EV (Expected Value) racuna BACKEND, sajt samo prikazuje.   ->  data/[<liga>/]odds_ev.json

  python bb_ev.py                    # sve lige koje imaju data/[<liga>/]odds_superbet.json i site.json
  python bb_ev.py --league euroleague --show 12     # i ispisi najbolje izbore (EV > 0) te lige
  python bb_ev.py --check            # proveri kalibraciju sd (koliko odigranih utakmica upada u +-1 sd; treba ~68%)
Poziva se i iz fetch_superbet.py posle svakog upisa kvota (ako je bb_ev.py u istom folderu).

STA RACUNA (za svaki mec, za svaku liniju: pobednik, hendikep, ukupno; glavne i alternativne linije):
 1) MODEL: ocekivani poeni oba tima iz rezultata odigranih utakmica lige (site.json): napad/odbrana svakog tima + domaci teren,
    ridge (skuplja ka proseku, --lam), vremenski ponderi (--half dana). Iz toga: ocekivana razlika (margin) i ukupno poena.
    Raspodela: normalna sa sd iz stvarnih reziduala lige (podrazumevano razlika ~11-12, ukupno ~16-17), a na to se dodaje NESIGURNOST procene
    ekipa (sd^2 + se^2, se raste kad tim ima malo odigranih utakmica). Zato su ekstremne alternativne linije ispravno 'siroke', a ne skoro sigurne.
 2) TRZISTE BEZ MARZE: svaka linija se ocisti od marze (1/o + 1/u = overround), pa se kroz sve linije istog tipa provuce JEDNA konzistentna
    normalna kriva (probit regresija po linijama). Alternativne linije dobijaju fer verovatnocu sa te krive, ne iz sopstvene (jace) marze.
 3) KONACNA SANSA  p = w*model + (1-w)*trziste,  w = tezina modela (raste sa brojem utakmica, najvise 0.40).  EV = p*kvota - 1 (kvota sa marzom koju stvarno igras).
    Pun Kelly = (p*kvota-1)/(kvota-1); sajt ga mnozi sa tvojom Kelly frakcijom i ogranicava na tvoj max % bankrolla.
 4) Zastavice: 'ok' je False (nije preporuka) ako model nije dostupan (ekipa nije prepoznata ili <MIN_G utakmica), ako se model i trziste razilaze
    za vise od 20 p.p. ili je EV preko 15% (skoro uvek znaci povredu ili pogresan model, ne priliku).
Ovo je statisticki model bez sastava i povreda; EV je procena, ne garancija."""
import argparse, collections, json, math, os, re, sys, time
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_superbet import LEAGUES, tokens, share, to_ms

ND = NormalDist()
MIN_G = 4                      # najmanje odigranih utakmica po timu da se model koristi
W_MAX, W_K = 0.40, 5.0         # tezina modela = W_MAX * n/(n+W_K)
SD_M, SD_T = (10.0, 16.0, 11.5), (13.0, 22.0, 16.5)   # (min, max, podrazumevano) sd razlike i ukupnog
MKT_SD_M, MKT_SD_T = (8.0, 17.0), (11.0, 26.0)        # granice sd kriva trzista
OK_DIV, OK_EV = 0.20, 0.15


def jl(p, d=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return d


def cl(x, a, b):
    return max(a, min(b, x))


# ------------------------------------------------------------------ model
def load_games(site, now):
    out = []
    for g in site.get("games") or []:
        hs, as_ = g.get("hs"), g.get("as")
        ms = to_ms(g.get("utc") or g.get("dt"))
        if hs is None or as_ is None or not g.get("h") or not g.get("a") or not ms or ms / 1000 > now:
            continue
        out.append((ms / 1000, g["h"], g["a"], float(hs), float(as_)))
    return sorted(out)


def fit(games, now, half=75.0, lam=5.0, iters=300):
    """pts_dom = base + hca/2 + off[dom] + def[gost];  pts_gost = base - hca/2 + off[gost] + def[dom]  (def > 0 = prima vise)."""
    T = sorted({g[1] for g in games} | {g[2] for g in games})
    W = [0.5 ** (max(0.0, now - g[0]) / 86400 / half) for g in games]
    o, d = dict.fromkeys(T, 0.0), dict.fromkeys(T, 0.0)
    base = sum(w * (g[3] + g[4]) for w, g in zip(W, games)) / (2 * sum(W))
    hca = 0.0
    for _ in range(iters):
        sb = sh = sw = 0.0
        for w, (_, h, a, yh, ya) in zip(W, games):
            sb += w * ((yh - hca / 2 - o[h] - d[a]) + (ya + hca / 2 - o[a] - d[h])); sw += 2 * w
        base = sb / sw
        for w, (_, h, a, yh, ya) in zip(W, games):
            sh += w * ((yh - ya) - ((o[h] + d[a]) - (o[a] + d[h])))
        hca = sh / sum(W)
        no, nd, wo, wd = ({t: 0.0 for t in T} for _ in range(4))
        for w, (_, h, a, yh, ya) in zip(W, games):
            no[h] += w * (yh - base - hca / 2 - d[a]); wo[h] += w
            no[a] += w * (ya - base + hca / 2 - d[h]); wo[a] += w
            nd[a] += w * (yh - base - hca / 2 - o[h]); wd[a] += w
            nd[h] += w * (ya - base + hca / 2 - o[a]); wd[h] += w
        o2 = {t: no[t] / (wo[t] + lam) for t in T}
        d2 = {t: nd[t] / (wd[t] + lam) for t in T}
        ch = max(abs(o2[t] - o[t]) + abs(d2[t] - d[t]) for t in T)
        o, d = o2, d2
        if ch < 1e-7:
            break
    mod = {"base": base, "hca": hca, "o": o, "d": d, "n": len(games), "W": W}
    rm, rt = [], []                                   # reziduali (u uzorku) za sd
    for w, (_, h, a, yh, ya) in zip(W, games):
        ph, pa = lam_of(mod, h, a)
        rm.append((yh - ya) - (ph - pa)); rt.append((yh + ya) - (ph + pa))
    N, Tn = len(games), len(T)
    infl = math.sqrt(N / max(N - min(0.6 * (2 * Tn + 2), 0.5 * N), 1.0)) if N else 1.0      # reziduali u uzorku potcenjuju pravu gresku
    sd = lambda r, lim: cl(math.sqrt(sum(x * x for x in r) / N) * infl, lim[0], lim[1]) if N >= 20 else lim[2]
    mod["sd_m"], mod["sd_t"] = sd(rm, SD_M), sd(rt, SD_T)
    mod["ng"] = collections.Counter(t for g in games for t in (g[1], g[2]))
    mod["cover"] = (sum(1 for x in rm if abs(x) <= mod["sd_m"]) / N if N else None, sum(1 for x in rt if abs(x) <= mod["sd_t"]) / N if N else None)
    return mod


def lam_of(mod, h, a):
    return (mod["base"] + mod["hca"] / 2 + mod["o"].get(h, 0.0) + mod["d"].get(a, 0.0),
            mod["base"] - mod["hca"] / 2 + mod["o"].get(a, 0.0) + mod["d"].get(h, 0.0))


def predict(mod, h, a):
    ph, pa = lam_of(mod, h, a)
    nh, na = mod["ng"].get(h, 0), mod["ng"].get(a, 0)
    k = math.sqrt(0.5 / max(nh, 1) + 0.5 / max(na, 1)) * 0.8
    se_m, se_t = mod["sd_m"] * k, mod["sd_t"] * k
    return {"margin": ph - pa, "total": ph + pa, "pts": [ph, pa], "sd_m": math.hypot(mod["sd_m"], se_m), "sd_t": math.hypot(mod["sd_t"], se_t),
            "se_m": se_m, "se_t": se_t, "ng": [nh, na], "hca": mod["hca"], "n": mod["n"],
            "w": W_MAX * min(nh, na) / (min(nh, na) + W_K) if min(nh, na) > 0 else 0.0}


# ------------------------------------------------------------------ trziste bez marze
def probit(p):
    return ND.inv_cdf(cl(p, 0.02, 0.98))


def fit_curve(pts, lim, default_sd):
    """pts: [(x, p)], z = probit(p) = alfa + beta*x, beta = 1/sd. Vraca (mu_param alfa*sd, sd, n_linija). Jedna linija: sd = default."""
    P = [(x, cl(p, 0.02, 0.98)) for x, p in pts]
    ws = [ND.pdf(probit(p)) ** 2 / (p * (1 - p)) for _, p in P]
    zs = [probit(p) for _, p in P]
    xs = [x for x, _ in P]
    sw = sum(ws)
    mx, mz = sum(w * x for w, x in zip(ws, xs)) / sw, sum(w * z for w, z in zip(ws, zs)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    beta = None
    if len({round(x, 3) for x in xs}) >= 2 and sxx > 1e-9:
        beta = sum(w * (x - mx) * (z - mz) for w, x, z in zip(ws, xs, zs)) / sxx
    if beta is None or beta <= 0:
        beta = 1.0 / default_sd
    beta = cl(beta, 1.0 / lim[1], 1.0 / lim[0])
    alfa = mz - beta * mx
    return alfa, beta


def nv2(a, b):
    """(p_prve strane bez marze, overround) za dvosmernu kvotu."""
    ia, ib = 1 / a, 1 / b
    return ia / (ia + ib), ia + ib


def outcome(kind, mu, sd, L):
    """(win, push, lose) za 'kind' = 'o' (ukupno vise), 'u' (manje), 'h' (domacin sa linijom L), 'a' (gost) pri N(mu, sd). Cele linije imaju push (diskretni rezultat)."""
    cdf = ND.cdf
    if kind in ("o", "u"):
        if L == int(L):
            above, push = 1 - cdf((L + .5 - mu) / sd), cdf((L + .5 - mu) / sd) - cdf((L - .5 - mu) / sd)
        else:
            above, push = 1 - cdf((L - mu) / sd), 0.0
        return (above, push, 1 - above - push) if kind == "o" else (1 - above - push, push, above)
    # hendikep: domacin pokriva ako margin + L > 0
    if L == int(L):
        win, push = 1 - cdf((-L + .5 - mu) / sd), cdf((-L + .5 - mu) / sd) - cdf((-L - .5 - mu) / sd)
    else:
        win, push = 1 - cdf((-L - mu) / sd), 0.0
    return (win, push, 1 - win - push) if kind == "h" else (1 - win - push, push, win)


def event_evs(e, pred, mod_ok, mkt_flag=True):
    b = e.get("b") or []
    tt = [(x["tt"], i == 0) for i, x in enumerate(b) if x.get("tt")]
    sp = [(x["sp"], i == 0) for i, x in enumerate(b) if x.get("sp")]
    h2h = b[0].get("h2h") if b else None
    meta = {"model": bool(mod_ok)}
    # kriva trzista: ukupno (x = -L, jer P(over) pada sa linijom), razlika (x = +L, pobeda domacina je L=0)
    ct = cm = None
    if tt:
        pts = [(-L, nv2(o, u)[0]) for (L, o, u), _ in tt if o > 1 and u > 1]
        if pts:
            al, be = fit_curve(pts, MKT_SD_T, pred["sd_t"] if pred else SD_T[2]); ct = (al / be, 1 / be)
    mpts = [(L, nv2(o, a)[0]) for (L, o, a), _ in sp if o > 1 and a > 1]
    if h2h and h2h[0] and h2h[1]:
        mpts.append((0.0, nv2(h2h[0], h2h[1])[0]))
    if mpts:
        al, be = fit_curve(mpts, MKT_SD_M, pred["sd_m"] if pred else SD_M[2]); cm = (al / be, 1 / be)
    meta["mkt_t"] = {"mu": round(ct[0], 2), "sd": round(ct[1], 2)} if ct else None
    meta["mkt_m"] = {"mu": round(cm[0], 2), "sd": round(cm[1], 2)} if cm else None
    out = []
    w = pred["w"] if (pred and mod_ok) else 0.0

    def add(k, mk, pick, side, L, odds, other, main, mkt_p, mdl):
        """mkt_p/mdl = (win, push, lose) trzisna (kriva) i modelska raspodela za TAJ izbor."""
        if not odds or odds <= 1.01:
            return
        mw, mp, ml = mdl if mdl else (None, None, None)
        kw, kp, kl = mkt_p
        pm = mw / (mw + ml) if mdl else None                  # sansa pobede kad se ulog vrati na push
        pf = kw / (kw + kl)
        p = w * pm + (1 - w) * pf if pm is not None and w > 0 else pf
        push = (w * mp + (1 - w) * kp) if (mdl and w > 0) else kp
        ev = (1 - push) * (p * odds - 1)
        evm = (1 - mp) * (pm * odds - 1) if pm is not None else None
        raw, orr = (nv2(odds, other) if other else (None, None))
        row = {"k": k, "mk": mk, "pick": pick, "side": side, "line": L, "o": odds, "alt": not main, "p": round(p, 4), "pm": None if pm is None else round(pm, 4),
               "pf": round(pf, 4), "pf_raw": None if raw is None else round(raw, 4), "vig": None if orr is None else round(orr - 1, 4), "push": round(push, 4),
               "ev": round(ev, 4), "ev_m": None if evm is None else round(evm, 4), "fair": round(1 / p, 3) if p > 0 else None,
               "kelly": round(max(0.0, (p * odds - 1) / (odds - 1)), 4)}
        why = None
        if pm is None:
            why = "nema modela (tim nije prepoznat ili premalo odigranih utakmica)"
        elif abs(pm - pf) > OK_DIV:
            why = f"model i tržište se razilaze {abs(pm - pf) * 100:.0f} p.p."
        elif ev > OK_EV:
            why = f"EV {ev * 100:.0f}% je neverovatno visok (verovatno povreda/pogrešan model)"
        row["ok"] = why is None
        if why:
            row["why"] = why
        out.append(row)

    for (L, o, u), main in tt:
        if not (o > 1 and u > 1) or not ct:
            continue
        for side, od, ot in (("o", o, u), ("u", u, o)):
            add("t", f"Ukupno {L:g}", "Više" if side == "o" else "Manje", side, L, od, ot, main,
                outcome(side, ct[0], ct[1], L), outcome(side, pred["total"], pred["sd_t"], L) if mod_ok else None)
    for (L, o, a), main in sp:
        if not (o > 1 and a > 1) or not cm:
            continue
        for side, od, ot in (("h", o, a), ("a", a, o)):
            ln = L if side == "h" else -L
            add("s", f"Hendikep {ln:+g}", e["h"] if side == "h" else e["a"], side, ln, od, ot, main,
                outcome(side, cm[0], cm[1], L), outcome(side, pred["margin"], pred["sd_m"], L) if mod_ok else None)
    if h2h and h2h[0] and h2h[1] and cm:
        for side, od, ot in (("h", h2h[0], h2h[1]), ("a", h2h[1], h2h[0])):
            add("h", "Pobednik", e["h"] if side == "h" else e["a"], side, 0, od, ot, True,
                _ml(side, cm), _ml(side, (pred["margin"], pred["sd_m"])) if mod_ok else None)
    return out, meta


def _ml(side, c):
    """pobednik (bez produzetka remi ne postoji): P(margin > 0) normalno."""
    p = 1 - ND.cdf(-c[0] / c[1])
    return (p, 0.0, 1 - p) if side == "h" else (1 - p, 0.0, p)


# ------------------------------------------------------------------ liga
def club_codes(site):
    return {k: tokens(f"{v.get('name', '')} {v.get('short', '')}") for k, v in (site.get("clubs") or {}).items()}


def code_of(name, clubs):
    t = tokens(name)
    hit = [c for c, tk in clubs.items() if share(t, tk)]
    if len(hit) == 1:
        return hit[0]
    if len(hit) > 1:
        sc = sorted(((len(t & clubs[c]), c) for c in hit), reverse=True)
        if sc[0][0] > sc[1][0]:
            return sc[0][1]
    return None


def run_league(lg, a):
    c = LEAGUES[lg]
    od = jl(c["out"])
    if not od or not od.get("events"):
        return None
    site = jl(c["site"]) or {}
    now = time.time()
    games = load_games(site, now)
    clubs = club_codes(site)
    mod = fit(games, now, a.half, a.lam) if len(games) >= 12 else None
    res, nok = [], 0
    for e in od["events"]:
        if not e.get("b"):
            continue
        h, aw = code_of(e["h"], clubs), code_of(e["a"], clubs)
        pred, why = None, None
        if mod and h and aw and h != aw:
            pred = predict(mod, h, aw)
            if min(pred["ng"]) < MIN_G:
                why = f"premalo odigranih utakmica ({pred['ng'][0]}/{pred['ng'][1]}, treba {MIN_G}+)"
        else:
            why = "nema rezultata lige u site.json" if not mod else "tim nije prepoznat u site.json"
        ok = bool(pred) and why is None
        pr = pred or {"w": 0, "sd_m": SD_M[2], "sd_t": SD_T[2], "margin": 0, "total": 0}
        evs, meta = event_evs(e, pr, ok)
        if pred:
            meta["q"] = {"margin": round(pred["margin"], 2), "total": round(pred["total"], 2), "pts": [round(x, 1) for x in pred["pts"]], "sd_m": round(pred["sd_m"], 2),
                         "sd_t": round(pred["sd_t"], 2), "se_m": round(pred["se_m"], 2), "se_t": round(pred["se_t"], 2), "w": round(pred["w"], 3), "n": pred["n"],
                         "ng": pred["ng"], "hca": round(pred["hca"], 2), "simple": True}
        if why:
            meta["reason"] = why
        res.append({"h": e["h"], "a": e["a"], "start": e.get("start"), "evs": evs, "meta": meta})
        nok += sum(1 for x in evs if x["ok"] and x["ev"] > 0)
    p = os.path.join(os.path.dirname(c["out"]), "odds_ev.json")
    json.dump({"built": int(now), "fetched": od.get("fetched"), "league": lg, "model": {"games": len(games), "sd_m": mod and round(mod["sd_m"], 2), "sd_t": mod and round(mod["sd_t"], 2),
              "hca": mod and round(mod["hca"], 2), "half": a.half, "lam": a.lam}, "events": res}, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)
    msg = f"  {lg}: {len(res)} mecova, {sum(len(r['evs']) for r in res)} izbora, {nok} sa EV>0 i ok -> {p}"
    if mod:
        msg += f" | model: {len(games)} utakmica, sd razlike {mod['sd_m']:.1f}, sd ukupno {mod['sd_t']:.1f}, domaci teren {mod['hca']:+.1f}"
    print(msg)
    if a.check and mod and mod["cover"][0] is not None:
        print(f"    kalibracija: u +-1 sd upada {mod['cover'][0] * 100:.0f}% razlika i {mod['cover'][1] * 100:.0f}% ukupnih (ocekivano ~68%)")
    if a.show and (a.league in (None, lg)):
        rows = sorted(((x["ev"], r["h"], r["a"], x) for r in res for x in r["evs"] if x["ok"] and x["ev"] > 0), key=lambda t: -t[0])
        for ev, h, aw, x in rows[:a.show]:
            print(f"    {h} - {aw}: {x['mk']} {x['pick']} @ {x['o']:.2f}{' (alt)' if x['alt'] else ''} | model {x['pm'] * 100:.0f}% trziste {x['pf'] * 100:.0f}% mesano {x['p'] * 100:.0f}% | EV {ev * 100:+.1f}%")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default=None, help="euroleague | aba | acb | lnb | bbl (podrazumevano sve)")
    ap.add_argument("--half", type=float, default=75.0, help="poluzivot ponderisanja utakmica u danima")
    ap.add_argument("--lam", type=float, default=5.0, help="koliko 'prosecnih utakmica' vuce tim ka proseku (ridge)")
    ap.add_argument("--show", type=int, default=0); ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    done = 0
    for lg in ([a.league] if a.league else list(LEAGUES)):
        if lg in LEAGUES and run_league(lg, a) is not None:
            done += 1
    if not done:
        print("nema nijednog data/[<liga>/]odds_superbet.json sa mecevima (prvo pokreni fetch_superbet.py)")


if __name__ == "__main__":
    main()
