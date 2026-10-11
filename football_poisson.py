#!/usr/bin/env python3
"""Premier liga: Poissonov model (Maher, opciono Dixon-Coles) + EV protiv Superbet kvota.

Podaci o rezultatima: football-data.co.uk (E0.csv, kolone Date,HomeTeam,AwayTeam,FTHG,FTAG).
  python football_poisson.py --download                 # skida tekucu i proslu sezonu u data/epl/ (ili stavi CSV sam)
  python football_poisson.py                            # predikcije za meceve iz data/epl/odds_superbet.json + EV
  python football_poisson.py --match "Arsenal,Chelsea"  # samo model, bez kvota
Model: gol-ocekivanja  lamH = mu*H*att[dom]*def[gost],  lamA = mu*att[gost]*def[dom]
  - ponderi opadaju sa vremenom (--halflife dana), a ekipe se vuku ka proseku (--k = broj 'prosecnih' utakmica),
    sto je bitno rano u sezoni; proslosezonski podaci popunjavaju prazninu, a novajlije imaju samo ovosezonske.
  - --rho je Dixon-Coles korekcija za male rezultate (0 = cist Poisson).
EV: p = w*model + (1-w)*tržište bez marže (Superbet kvote jedne kladionice nisu pouzdana referenca), EV = p*kvota-1.
Ovo je statisticki model bez povreda, sastava i rotacije; EV je procena, ne garancija."""
import argparse, csv, difflib, glob, io, json, math, os, re, sys, unicodedata, urllib.request
from datetime import date, datetime

FD = "https://www.football-data.co.uk/mmz4281/{s}/E0.csv"
D = os.path.join("data", "epl")
ODDS, OUT = os.path.join(D, "odds_superbet.json"), os.path.join(D, "predictions.json")
# kanonska imena = imena iz football-data (bez dijakritika, malim slovima, bez apostrofa); levo su varijante iz drugih izvora
ALIAS = {"manchester city": "man city", "mancester siti": "man city", "manchester united": "man united", "man utd": "man united",
         "mancester junajted": "man united", "tottenham hotspur": "tottenham", "spurs": "tottenham", "totenhem": "tottenham",
         "newcastle united": "newcastle", "njukasl": "newcastle", "wolverhampton wanderers": "wolves", "wolverhampton": "wolves",
         "vulverhempton": "wolves", "west ham united": "west ham", "vest hem": "west ham", "brighton hove albion": "brighton",
         "brighton and hove albion": "brighton", "brajton": "brighton", "leeds united": "leeds", "nottingham forest": "nottm forest",
         "nottingham": "nottm forest", "notingem forest": "nottm forest", "crystal palace": "crystal palace", "kristal palas": "crystal palace",
         "aston vila": "aston villa", "afc bournemouth": "bournemouth", "bornmut": "bournemouth", "leicester city": "leicester",
         "liverpul": "liverpool", "fulam": "fulham", "west bromwich albion": "west brom", "ipswich town": "ipswich"}


def nrm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"\b(fc|afc|cf)\b", " ", re.sub(r"[^a-z0-9 ]", "" if "'" in s else " ", s.replace("'", "")))).strip()


def canon(name, known):
    n = nrm(name)
    n = ALIAS.get(n, n)
    if n in known: return n
    m = difflib.get_close_matches(n, list(known), 1, 0.75)
    return m[0] if m else None


def season_code(d):
    y = d.year if d.month >= 8 else d.year - 1
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def parse_date(s):
    for f in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try: return datetime.strptime(s.strip(), f).date()
        except ValueError: pass


def load(files):
    M = []
    for fp in files:
        for r in csv.DictReader(open(fp, encoding="utf-8-sig")):
            d = parse_date(r.get("Date", ""))
            try: hg, ag = int(r["FTHG"]), int(r["FTAG"])
            except (KeyError, ValueError, TypeError): continue
            if d and r.get("HomeTeam"): M.append({"d": d, "h": nrm(r["HomeTeam"]), "a": nrm(r["AwayTeam"]), "hg": hg, "ag": ag})
    return M


def fit(M, today, halflife=180.0, k=4.0):
    w = [0.5 ** (max(0, (today - m["d"]).days) / halflife) for m in M]
    T = sorted({m["h"] for m in M} | {m["a"] for m in M})
    W, Gh, Ga = sum(w), sum(x * m["hg"] for x, m in zip(w, M)), sum(x * m["ag"] for x, m in zip(w, M))
    l0, mu, H = (Gh + Ga) / (2 * W), Ga / W, Gh / Ga
    a, d = dict.fromkeys(T, 1.0), dict.fromkeys(T, 1.0)
    for _ in range(500):
        na, ea, nd, ed = ({t: k * l0 for t in T} for _ in range(4))
        for x, m in zip(w, M):
            h, g = m["h"], m["a"]
            na[h] += x * m["hg"]; ea[h] += x * mu * H * d[g]
            na[g] += x * m["ag"]; ea[g] += x * mu * d[h]
            nd[g] += x * m["hg"]; ed[g] += x * mu * H * a[h]
            nd[h] += x * m["ag"]; ed[h] += x * mu * a[g]
        A, B = {t: na[t] / ea[t] for t in T}, {t: nd[t] / ed[t] for t in T}
        sa, sb = sum(A.values()) / len(T), sum(B.values()) / len(T)
        A, B = {t: v / sa for t, v in A.items()}, {t: v / sb for t, v in B.items()}
        mu = sum(x * m["ag"] for x, m in zip(w, M)) / sum(x * A[m["a"]] * B[m["h"]] for x, m in zip(w, M))
        H = Gh / sum(x * mu * A[m["h"]] * B[m["a"]] for x, m in zip(w, M))
        ch = max(abs(A[t] - a[t]) + abs(B[t] - d[t]) for t in T)
        a, d = A, B
        if ch < 1e-9: break
    return {"att": a, "def": d, "mu": mu, "H": H}


def grid(lh, la, rho=0.0, n=12):
    pm = lambda l, i: math.exp(-l + i * math.log(l) - math.lgamma(i + 1))
    def tau(i, j):
        if i == 0 and j == 0: return 1 - lh * la * rho
        if i == 0 and j == 1: return 1 + lh * rho
        if i == 1 and j == 0: return 1 + la * rho
        if i == 1 and j == 1: return 1 - rho
        return 1.0
    G = [[pm(lh, i) * pm(la, j) * tau(i, j) for j in range(n)] for i in range(n)]
    s = sum(map(sum, G))
    return [[v / s for v in r] for r in G]


def probs(G):
    n = len(G)
    p1 = sum(G[i][j] for i in range(n) for j in range(n) if i > j)
    px = sum(G[i][i] for i in range(n))
    over = lambda L: sum(G[i][j] for i in range(n) for j in range(n) if i + j > L)
    return {"x12": [p1, px, 1 - p1 - px], "over": over, "btts": sum(G[i][j] for i in range(1, n) for j in range(1, n))}


def predict(mod, h, a, rho):
    lh = mod["mu"] * mod["H"] * mod["att"].get(h, 1.0) * mod["def"].get(a, 1.0)
    la = mod["mu"] * mod["att"].get(a, 1.0) * mod["def"].get(h, 1.0)
    G = grid(lh, la, rho)
    return lh, la, G, probs(G)


def comps(L):
    """Cetvrtinske linije (.25/.75) = po pola uloga na dve susedne linije."""
    return [L - 0.25, L + 0.25] if round(L * 4) % 2 else [L]


def wpl(G, L, kind):
    """(pobeda, vracen ulog, poraz) za domacina (kind='ah': i-j+L) ili za 'vise' (kind='ou': i+j-L), uprosecno po komponentama linije."""
    n, tot, cs = len(G), [0.0, 0.0, 0.0], comps(L)
    for c in cs:
        for i in range(n):
            for j in range(n):
                d = (i - j + c) if kind == "ah" else (i + j - c)
                tot[0 if d > 1e-9 else 1 if abs(d) <= 1e-9 else 2] += G[i][j]
    return [t / len(cs) for t in tot]


def htft_probs(lh, la, f1=0.45, n=9):
    """HT/FT iz dva nezavisna Poissona (udeo golova u 1. poluvremenu f1). Priblizno: ne uzima u obzir korelaciju poluvremena."""
    pm = lambda l, i: math.exp(-l + i * math.log(l) - math.lgamma(i + 1))
    r = lambda i, j: "1" if i > j else "x" if i == j else "2"
    h1 = [[pm(f1 * lh, i) * pm(f1 * la, j) for j in range(n)] for i in range(n)]
    h2 = [[pm((1 - f1) * lh, i) * pm((1 - f1) * la, j) for j in range(n)] for i in range(n)]
    out = {}
    for i1 in range(n):
        for j1 in range(n):
            for i2 in range(n):
                for j2 in range(n):
                    k = r(i1, j1) + "/" + r(i1 + i2, j1 + j2)
                    out[k] = out.get(k, 0.0) + h1[i1][j1] * h2[i2][j2]
    t = sum(out.values())
    return {k: v / t for k, v in out.items()}


def fair(odds):
    s = sum(1 / o for o in odds)
    return [1 / o / s for o in odds]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="skini E0.csv za tekucu i proslu sezonu u data/epl/")
    ap.add_argument("--seasons", default="", help="npr. 2627,2526 (podrazumevano tekuca + prosla)")
    ap.add_argument("--match", default="", help='"Domacin,Gost" bez kvota')
    ap.add_argument("--halflife", type=float, default=180.0); ap.add_argument("--k", type=float, default=4.0)
    ap.add_argument("--rho", type=float, default=0.0); ap.add_argument("--w", type=float, default=0.35, help="tezina modela u odnosu na trziste")
    ap.add_argument("--h1", type=float, default=0.45, help="udeo golova u 1. poluvremenu (za HT/FT)"); ap.add_argument("--min-ev", type=float, default=0.03); ap.add_argument("--all", action="store_true", help="prikazi sve izbore, ne samo EV iznad praga")
    a = ap.parse_args()
    today = date.today()
    cur = season_code(today); prev = f"{int(cur[:2]) - 1:02d}{int(cur[:2]):02d}"
    seasons = a.seasons.split(",") if a.seasons else [cur, prev]
    os.makedirs(D, exist_ok=True)
    if a.download:
        for s in seasons:
            try:
                open(os.path.join(D, f"E0_{s}.csv"), "wb").write(urllib.request.urlopen(urllib.request.Request(FD.format(s=s), headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
                print("skinuto", s)
            except Exception as e: print("nije uspelo", s, e)
    files = [os.path.join(D, f"E0_{s}.csv") for s in seasons if os.path.exists(os.path.join(D, f"E0_{s}.csv"))] + glob.glob(os.path.join(D, "results*.csv"))
    M = load(files)
    if len(M) < 20: sys.exit(f"Premalo utakmica ({len(M)}). Pokreni sa --download ili stavi E0 CSV u {D}/ (fajlovi: {files})")
    mod = fit(M, today, a.halflife, a.k); known = set(mod["att"])
    print(f"Model: {len(M)} utakmica, {len(known)} ekipa, mu {mod['mu']:.2f}, domaci faktor {mod['H']:.2f}")
    rows, out = [], []
    if a.match:
        h, g = [canon(x, known) for x in a.match.split(",")]
        todo = [{"h": h, "a": g, "H": h, "A": g, "start": None, "x12": None, "ou": [], "btts": None}]
    else:
        try: ev = json.load(open(ODDS, encoding="utf-8"))["events"]
        except OSError: sys.exit(f"Nema {ODDS}. Pokreni fetch_superbet_football.py ili koristi --match.")
        todo = []
        for e in ev:
            h, g = canon(e["h"], known), canon(e["a"], known)
            if not (h and g): print("  ne prepoznajem ekipu:", e["h"], "-", e["a"], "(dodaj u ALIAS)"); continue
            todo.append({**e, "H": e["h"], "A": e["a"], "h": h, "a": g})
    for e in todo:
        lh, la, G, P = predict(mod, e["h"], e["a"], a.rho)
        print(f"\n{e['H']} - {e['A']}  {e.get('start') or ''}\n  ocekivani golovi {lh:.2f}:{la:.2f} | 1X2 {P['x12'][0]*100:.0f}/{P['x12'][1]*100:.0f}/{P['x12'][2]*100:.0f}% | GG {P['btts']*100:.0f}% | preko 2.5 {P['over'](2)*100:.0f}%")
        best = max(((i, j, G[i][j]) for i in range(len(G)) for j in range(len(G))), key=lambda t: t[2])
        print(f"  najverovatniji rezultat {best[0]}:{best[1]} ({best[2]*100:.1f}%)")
        sel = []   # (trziste, izbor, kvota, p_model, p_trziste)
        if e.get("x12"):
            f = fair(e["x12"])
            sel += [("1X2", n, e["x12"][i], P["x12"][i], f[i]) for i, n in enumerate(("1", "X", "2"))]
        for L, o, u in e.get("ou") or []:
            f = fair([o, u]); po = P["over"](L)
            sel += [(f"Golovi {L}", "Vise", o, po, f[0]), (f"Golovi {L}", "Manje", u, 1 - po, f[1])]
        if e.get("btts"):
            f = fair(e["btts"]); sel += [("GG/NG", "GG", e["btts"][0], P["btts"], f[0]), ("GG/NG", "NG", e["btts"][1], 1 - P["btts"], f[1])]
        nn = len(G)
        if e.get("dc"):
            o3 = e["dc"]; sm = sum(1 / o for o in o3); x = P["x12"]
            pdc = [x[0] + x[1], x[0] + x[2], x[1] + x[2]]
            sel += [("Dupla sansa", nm_, o3[i], pdc[i], 2 * (1 / o3[i]) / sm) for i, nm_ in enumerate(("1X", "12", "X2"))]
        for L, oh, oa in e.get("ah") or []:           # azijski/2-way hendikep; L = hendikep domacina; push = vracen ulog
            w_, pu_, l_ = wpl(G, L, "ah"); c = w_ / (w_ + l_) if w_ + l_ > 0 else .5; f = fair([oh, oa])
            sel += [(f"AH {L:+g}", "1", oh, c, f[0], pu_), (f"AH {L:+g}", "2", oa, 1 - c, f[1], pu_)]
        for L, ov_, un_ in e.get("ouah") or []:        # azijski ukupno golova
            w_, pu_, l_ = wpl(G, L, "ou"); c = w_ / (w_ + l_) if w_ + l_ > 0 else .5; f = fair([ov_, un_])
            sel += [(f"AU {L:g}", "Vise", ov_, c, f[0], pu_), (f"AU {L:g}", "Manje", un_, 1 - c, f[1], pu_)]
        for hc, o1, ox, o2 in e.get("eh") or []:       # 3-way hendikep: domacinu se dodaje hc golova
            pr = [0.0, 0.0, 0.0]
            for i in range(nn):
                for j in range(nn):
                    d = i - j + hc; pr[0 if d > 0 else 1 if d == 0 else 2] += G[i][j]
            f = fair([o1, ox, o2])
            sel += [(f"EH {hc:+d}", nm_, o, pr[k], f[k]) for k, (nm_, o) in enumerate(zip(("1", "X", "2"), (o1, ox, o2)))]
        R_ = e.get("range") or []
        if R_:
            f = fair([x[2] for x in R_])
            for k, (lo, hi, o) in enumerate(R_):
                sel.append(("Raspon", f"{lo}-{hi}" if hi < 99 else f"{lo}+", o, sum(G[i][j] for i in range(nn) for j in range(nn) if lo <= i + j <= hi), f[k]))
        for thr, y_, n_ in e.get("bttsx") or []:
            py = sum(G[i][j] for i in range(1, nn) for j in range(1, nn) if i + j >= thr)
            if n_: f = fair([y_, n_]); sel += [(f"GG&{thr}+", "Da", y_, py, f[0]), (f"GG&{thr}+", "Ne", n_, 1 - py, f[1])]
            else: sel.append((f"GG&{thr}+", "Da", y_, py, 1 / y_))
        if e.get("htft"):
            hp = htft_probs(lh, la, a.h1); tot_ = sum(1 / o for o in e["htft"].values())
            sel += [("HT/FT", k.upper(), o, hp.get(k, 0.0), (1 / o) / tot_) for k, o in e["htft"].items()]
        for t_ in sel:
            mk, pick, o, pm, pf = t_[:5]; pp = t_[5] if len(t_) > 5 else 0.0
            p = a.w * pm + (1 - a.w) * pf; ev_ = (1 - pp) * (p * o - 1); evr = (1 - pp) * (pm * o - 1)
            row = {"h": e["H"], "a": e["A"], "start": e.get("start"), "market": mk, "pick": pick, "odds": o, "p_model": round(pm, 4), "p_market": round(pf, 4), "p": round(p, 4), "push": round(pp, 4), "ev": round(ev_, 4), "ev_raw": round(evr, 4)}
            out.append(row)
            if a.all or ev_ >= a.min_ev: print(f"  {mk:<12} {pick:<6} kvota {o:<5.2f} model {pm*100:4.1f}% trziste {pf*100:4.1f}%{f' vracen ulog {pp*100:.0f}%' if pp else ''} -> EV {ev_*100:+5.1f}% (sirovi model {evr*100:+.1f}%){'  (PROVERI liniju/kvotu)' if evr > 0.25 else ''}")
    if not a.match:
        json.dump({"updated": datetime.now().isoformat(timespec="seconds"), "w": a.w, "rows": out}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"\nupisano {len(out)} izbora u {OUT}")


if __name__ == "__main__":
    main()
