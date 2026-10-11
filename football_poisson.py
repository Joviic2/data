#!/usr/bin/env python3
"""Premier liga: Poissonov model (Maher, opciono Dixon-Coles) + EV protiv Superbet kvota.

IZVOR (podrazumevano): Statshub preko statshub_football.py -> data/epl/statshub_data.json (rezultati svih takmicenja, xG, povrede, odmor, forma, stil, H2H).
  python statshub_football.py ; python football_poisson.py --backtest 80 (izaberi --beta) ; python football_poisson.py --beta 0.5
Stari izvor (--source csv): football-data.co.uk (E0.csv, kolone Date,HomeTeam,AwayTeam,FTHG,FTAG).
  python football_poisson.py --download                 # skida tekucu i proslu sezonu u data/epl/ (ili stavi CSV sam)
  python football_poisson.py                            # predikcije za meceve iz data/epl/odds_superbet.json + EV
  python football_poisson.py --match "Arsenal,Chelsea"  # samo model, bez kvota
Model: gol-ocekivanja  lamH = mu*H*att[dom]*def[gost],  lamA = mu*att[gost]*def[dom]
  - ponderi opadaju sa vremenom (--halflife dana), a ekipe se vuku ka proseku (--k = broj 'prosecnih' utakmica),
    sto je bitno rano u sezoni; proslosezonski podaci popunjavaju prazninu, a novajlije imaju samo ovosezonske.
  - --rho je Dixon-Coles korekcija za male rezultate (0 = cist Poisson).
EV: p = w*model + (1-w)*tržište bez marže (Superbet kvote jedne kladionice nisu pouzdana referenca), EV = p*kvota-1.
Trzista: samo 1X2, Golovi 1.5/2.5/3.5 i GG/NG (sve ostalo se ignorise).
Ovo je statisticki model bez povreda, sastava i rotacije; EV je procena, ne garancija."""
import argparse, csv, difflib, glob, io, json, math, os, re, sys, unicodedata, urllib.request
from fb_leagues import CUR
try:
    import fb_factors as FX
except ImportError:
    FX = None
from datetime import date, datetime

FD = "https://www.football-data.co.uk/mmz4281/{s}/" + CUR["csv"] + ".csv"
D = os.path.join("data", CUR["dir"])
OU_LINES = (1.5, 2.5, 3.5)
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


ALIAS.update(CUR["alias"])


def nrm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"\b(fc|afc|cf)\b", " ", re.sub(r"[^a-z0-9 ]", "" if "'" in s else " ", s.replace("'", "")))).strip()


def canon(name, known):
    n = nrm(name)
    n = ALIAS.get(n, n)
    if n in known: return n
    pre = sorted((k for k in known if n.startswith(k + " ") or k.startswith(n + " ")), key=len, reverse=True)   # "hull city" -> "hull", "ipswich town" -> "ipswich"
    if pre and (len(pre) == 1 or len(pre[0]) > len(pre[1])): return pre[0]
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


def lam_of(mod, h, a):
    """Osnovna gol-ocekivanja (bez multiplikatora): domacin, gost."""
    return mod["mu"] * mod["H"] * mod["att"].get(h, 1.0) * mod["def"].get(a, 1.0), mod["mu"] * mod["att"].get(a, 1.0) * mod["def"].get(h, 1.0)


def predict(mod, h, a, rho, mh=1.0, ma=1.0):
    lh = mod["mu"] * mod["H"] * mod["att"].get(h, 1.0) * mod["def"].get(a, 1.0) * mh
    la = mod["mu"] * mod["att"].get(a, 1.0) * mod["def"].get(h, 1.0) * ma
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


# ======================= STATSHUB: ucitavanje, xG, kontekst (povrede, odmor, forma, stil, H2H), backtest =======================
SH = os.path.join(D, "statshub_data.json")


def load_statshub(path=SH):
    """(M, SD): M = utakmice u istom formatu kao load() + xh/xa (xG) + ts; SD = ceo JSON (profili timova)."""
    SD = json.load(open(path, encoding="utf-8"))
    M = [{"d": datetime.strptime(m["d"], "%Y-%m-%d").date(), "ts": m["ts"], "h": m["h"], "a": m["a"], "hg": m["hg"], "ag": m["ag"], "xh": m.get("xh"), "xa": m.get("xa")} for m in SD["matches"]]
    return M, SD


def eff(M, beta):
    """Efektivni golovi = beta*xG + (1-beta)*golovi (samo gde xG postoji): xG je manje sumovit pokazatelj snage od golova."""
    if not beta: return M
    return [{**m, "hg": beta * m["xh"] + (1 - beta) * m["hg"], "ag": beta * m["xa"] + (1 - beta) * m["ag"]} if m.get("xh") is not None else m for m in M]


def h2h(M, h, a, n=6):
    r = sorted((m for m in M if {m["h"], m["a"]} == {h, a}), key=lambda m: m["ts"])[-n:]
    if not r: return None
    gh = sum(m["hg"] if m["h"] == h else m["ag"] for m in r); ga = sum(m["ag"] if m["h"] == h else m["hg"] for m in r)
    return {"n": len(r), "h_win": sum(1 for m in r if (m["hg"] > m["ag"]) == (m["h"] == h) and m["hg"] != m["ag"]), "draw": sum(1 for m in r if m["hg"] == m["ag"]),
            "gh": gh, "ga": ga, "avg_goals": round((gh + ga) / len(r), 2), "last": [f"{m['d']} {m['h']} {m['hg']}:{m['ag']} {m['a']}" for m in r[-3:]]}


def team_ctx(SD, key_, start_ms, att_k, def_k, rest_pen, inj_min=450.0):
    """Multiplikatori za ocekivane golove ekipe: napad (povrede igraca koji nose xG+xA, kratak odmor) i odbrana (nedostupni defanzivci/golman)."""
    t = (SD.get("teams") or {}).get(key_)
    if not t: return {"att": 1.0, "conc": 1.0, "rest": None, "out": [], "games": 0}
    byid = {p["id"]: p for p in t.get("players") or []}
    att_s = def_s = 0.0; out = []
    for u in t.get("unav") or []:
        st = str(u.get("status") or "").lower()
        w = 1.0 if st == "out" else 0.4 if st in ("doubtful", "questionable", "uncertain") else 0.0
        p = byid.get(u.get("id"))
        if not w or not p: continue
        att_s += w * p["att"] * (min(1.0, p["min10"] / inj_min) if inj_min else 1.0)   # dugorocno povredjeni (nisu igrali poslednjih 10) vec su u formi tima: ne racunaj dvaput
        if p["pos"] in ("D", "G"): def_s += w * min(1.0, p["min10"] / 900.0)
        out.append({"n": u.get("n"), "pos": u.get("pos"), "status": st, "att": p["att"], "min10": p["min10"], "reason": u.get("reason")})
    rest = round((start_ms / 1000 - t["last"]["ts"]) / 86400, 1) if t.get("last") and start_ms else None
    att = 1 - att_k * min(att_s, 0.35)
    if rest is not None and 0 <= rest < 3.5: att *= 1 - rest_pen
    return {"att": round(att, 4), "conc": round(1 + def_k * min(def_s, 2.0), 4), "rest": rest, "out": out, "games": t.get("games", 0),
            "form": t.get("form"), "style": (t.get("style") or {}).get("labels"), "xg": (t.get("style") or {}).get("xg"), "next": t.get("next")}


def backtest(M, K, betas, a):
    """Walk-forward: za svaku od poslednjih K utakmica uci samo iz ranijih; log-loss za 1X2 i preko 2.5. Manje = bolje."""
    ms = sorted(M, key=lambda m: m["ts"]); test = ms[-K:]
    print(f"\nBACKTEST: {len(test)} utakmica, halflife {a.halflife}, k {a.k}, rho {a.rho}")
    for beta in betas:
        l12 = lo = n = 0
        for m in test:
            tr = [x for x in ms if x["ts"] < m["ts"] - 3600]
            if len(tr) < 40: continue
            mod = fit(eff(tr, beta), m["d"], a.halflife, a.k)
            if m["h"] not in mod["att"] or m["a"] not in mod["att"]: continue
            _, _, G, P = predict(mod, m["h"], m["a"], a.rho)
            res = 0 if m["hg"] > m["ag"] else 1 if m["hg"] == m["ag"] else 2
            l12 -= math.log(max(1e-9, P["x12"][res]))
            po = P["over"](2); lo -= math.log(max(1e-9, po if m["hg"] + m["ag"] > 2 else 1 - po)); n += 1
        if n: print(f"  beta (udeo xG) {beta:<4}: log-loss 1X2 {l12 / n:.4f} | preko/ispod 2.5 {lo / n:.4f}   (n={n})")
    print("  Nasumicno (1/3,1/3,1/3) daje 1.0986 za 1X2; trziste obicno ~0.97-1.00. Uzmi beta sa najmanjim log-lossom, ali samo ako je razlika veca od sumova (n < 100 je mali uzorak).")


def backtest_cfg(M, K, a, beta):
    """Walk-forward: poredi udeo kratkog modela (recent-w) i momentum (mom-k). Ispisuje log-loss i PRISTRASNOST: srednja predvidjena verovatnoca 'preko 2.5' minus stvarna ucestalost.
    Negativna pristrasnost = model gura Under (potcenjuje golove); preporuceni --lam-scale je odnos stvarnog i predvidjenog prosecnog broja golova."""
    if not FX: print("fb_factors.py nije pored football_poisson.py"); return
    ms = sorted(M, key=lambda m: m["ts"]); test = ms[-K:]
    cfgs = [(0.0, 0.0), (0.3, 0.0), (0.0, 0.25), (0.3, 0.25), (0.5, 0.25), (0.3, 0.5)]
    print(f"\nBACKTEST KONFIGURACIJA: {len(test)} utakmica, beta {beta}, halflife {a.halflife}/{a.halflife_short}")
    print(f"  {'recent-w':>8} {'mom-k':>6} | {'LL 1X2':>7} {'LL o2.5':>8} | {'P(o2.5)':>8} {'stvarno':>8} {'bias':>7} | {'gol pred':>8} {'gol stv':>8} | n")
    base_scale = None
    for rw, mk in cfgs:
        l12 = lo = n = 0; po_s = ov_s = tp = ta = 0.0
        for m in test:
            tr = [x for x in ms if x["ts"] < m["ts"] - 3600]
            if len(tr) < 40: continue
            trE = eff(tr, beta); ml = fit(trE, m["d"], a.halflife, a.k)
            if m["h"] not in ml["att"] or m["a"] not in ml["att"]: continue
            msh = fit(trE, m["d"], a.halflife_short, a.k_short) if rw else None
            def lam(h_, g_, ml=ml, msh=msh, rw=rw):
                x, y = lam_of(ml, h_, g_)
                if msh:
                    sx, sy = lam_of(msh, h_, g_); x, y = (1 - rw) * x + rw * sx, (1 - rw) * y + rw * sy
                return x, y
            lh, la = lam(m["h"], m["a"])
            if mk:
                Pm = dict(FX.PARAMS, mom_k=mk)
                mh_, ma_ = FX.momentum(tr, beta, lam, m["h"], Pm), FX.momentum(tr, beta, lam, m["a"], Pm)
                lh *= mh_["att"] * ma_["conc"] * ((1 + Pm["collapse_k"]) if ma_["collapse"] else 1)
                la *= ma_["att"] * mh_["conc"] * ((1 + Pm["collapse_k"]) if mh_["collapse"] else 1)
            G = grid(lh * a.lam_scale, la * a.lam_scale, a.rho); Pr = probs(G)
            res = 0 if m["hg"] > m["ag"] else 1 if m["hg"] == m["ag"] else 2
            l12 -= math.log(max(1e-9, Pr["x12"][res]))
            po = Pr["over"](2); ov = m["hg"] + m["ag"] > 2
            lo -= math.log(max(1e-9, po if ov else 1 - po)); po_s += po; ov_s += ov; tp += lh * a.lam_scale + la * a.lam_scale; ta += m["hg"] + m["ag"]; n += 1
        if n:
            if base_scale is None: base_scale = ta / tp
            print(f"  {rw:>8} {mk:>6} | {l12 / n:7.4f} {lo / n:8.4f} | {po_s / n:8.3f} {ov_s / n:8.3f} {(po_s - ov_s) / n:+7.3f} | {tp / n:8.2f} {ta / n:8.2f} | {n}")
    if base_scale: print(f"\n  Osnovna konfiguracija: stvarni/predvidjeni golovi = {base_scale:.3f} -> --lam-scale {base_scale:.3f} ispravlja sistematsku pristrasnost (ako je dalje od 1.0 za vise od ~3%).")
    print("  Biraj konfiguraciju sa najmanjim log-lossom, ali samo ako je razlika veca od suma (n < 150 je mali uzorak). Stil, sudija, motivacija i vreme se ne mogu backtestovati (nema istorije).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="epl", help="epl | laliga")
    ap.add_argument("--download", action="store_true", help="skini E0.csv za tekucu i proslu sezonu u data/epl/")
    ap.add_argument("--seasons", default="", help="npr. 2627,2526 (podrazumevano tekuca + prosla)")
    ap.add_argument("--match", default="", help='"Domacin,Gost" bez kvota')
    ap.add_argument("--halflife", type=float, default=180.0); ap.add_argument("--k", type=float, default=4.0)
    ap.add_argument("--rho", type=float, default=0.0); ap.add_argument("--w", type=float, default=0.35, help="tezina modela u odnosu na trziste")
    ap.add_argument("--h1", type=float, default=0.45, help="udeo golova u 1. poluvremenu (za HT/FT)"); ap.add_argument("--min-ev", type=float, default=0.03); ap.add_argument("--all", action="store_true", help="prikazi sve izbore, ne samo EV iznad praga")
    ap.add_argument("--keep-sus", action="store_true", help="zadrzi i sumnjive izbore (model i trziste se razilaze >15 pp ili sirovi EV >25%%); podrazumevano se izbacuju")
    ap.add_argument("--source", choices=["auto", "statshub", "csv"], default="auto", help="izvor rezultata: Statshub (xG, povrede, forma...) ili stari football-data CSV")
    ap.add_argument("--refresh", action="store_true", help="prvo osvezi Statshub podatke (statshub_football.py)")
    ap.add_argument("--offline", action="store_true", help="uz --refresh: samo iz kesa")
    ap.add_argument("--beta", type=float, default=0.5, help="udeo xG u 'efektivnim golovima' pri ucenju snage ekipa (0 = samo golovi); odredi ga sa --backtest")
    ap.add_argument("--att-k", type=float, default=0.5, help="koliko se napad smanjuje po udelu xG+xA koji nose nedostupni igraci (0 = ne primenjuj povrede)")
    ap.add_argument("--def-k", type=float, default=0.06, help="povecanje primljenih golova po nedostupnom bekovi/golmanu (udeo minuta)")
    ap.add_argument("--rest-pen", type=float, default=0.02, help="umanjenje napada kad je odmor manji od 3.5 dana (0 = iskljuceno)")
    ap.add_argument("--backtest", type=int, default=0, metavar="K", help="walk-forward na poslednjih K utakmica, poredi beta vrednosti (npr. --backtest 80)")
    ap.add_argument("--betas", default="0,0.25,0.5,0.75,1")
    PD = FX.PARAMS if FX else {}
    ap.add_argument("--inj-min", type=float, default=450.0, help="minuta u poslednjih 10 mecova za koje se igracev izostanak racuna u punom iznosu (0 = stari nacin; manje = dugorocno povredjeni se ne racunaju dvaput)")
    ap.add_argument("--recent-w", type=float, default=0.30, help="udeo KRATKOG modela (skorija forma) u ocekivanim golovima; 0 = samo dugi model")
    ap.add_argument("--halflife-short", type=float, default=45.0); ap.add_argument("--k-short", type=float, default=6.0)
    ap.add_argument("--lam-scale", type=float, default=1.0, help="mnozi oba ocekivana gola; uzmi preporuku iz --backtest K --bt-grid ako model sistematski potcenjuje golove")
    ap.add_argument("--no-factors", action="store_true", help="iskljuci fb_factors.py (momentum, stil, sudija, motivacija, vreme)"); ap.add_argument("--no-weather", action="store_true")
    ap.add_argument("--mom-k", type=float, default=PD.get("mom_k", .25)); ap.add_argument("--style-k", type=float, default=PD.get("style_k", .03)); ap.add_argument("--mot-k", type=float, default=PD.get("mot_k", .04))
    ap.add_argument("--ref-k", type=float, default=PD.get("ref_k", .015)); ap.add_argument("--wx-k", type=float, default=PD.get("wx_k", .02))
    ap.add_argument("--under-rule", choices=["block", "demote", "off"], default="block", help="Under/NG na mecu gde je tim u naletu ili ima raspad odbrane: block = ne preporucuj, demote = spusti EV za --under-pen")
    ap.add_argument("--under-pen", type=float, default=0.04)
    ap.add_argument("--ref", action="append", default=[], help='sudija meca: "domacin,gost=Ime Prezime" (moze vise puta; ili data/<liga>/referees.json)')
    ap.add_argument("--bt-grid", action="store_true", help="uz --backtest K: poredi recent-w / momentum konfiguracije i ispisi pristrasnost Under/Over i preporuceni --lam-scale")
    a = ap.parse_args()
    today = date.today()
    cur = season_code(today); prev = f"{int(cur[:2]) - 1:02d}{int(cur[:2]):02d}"
    seasons = a.seasons.split(",") if a.seasons else [cur, prev]
    os.makedirs(D, exist_ok=True)
    if a.download:
        for s in seasons:
            try:
                open(os.path.join(D, f"{CUR['csv']}_{s}.csv"), "wb").write(urllib.request.urlopen(urllib.request.Request(FD.format(s=s), headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
                print("skinuto", s)
            except Exception as e: print("nije uspelo", s, e)
    if a.refresh:
        import types, statshub_football as sf
        sf.build(types.SimpleNamespace(offline=a.offline, ttl=6.0, limit=60, tournament=sf.resolve_ids(a.offline)[0], season=sf.resolve_ids(a.offline)[1], no_upcoming=False))
    Mraw = None
    SD, use_sh = None, a.source == "statshub" or (a.source == "auto" and os.path.exists(SH))
    if use_sh:
        if not os.path.exists(SH): sys.exit(f"Nema {SH}. Pokreni: python statshub_football.py (ili --refresh)")
        M, SD = load_statshub()
        print(f"Izvor: Statshub ({SD.get('updated')}), {sum(1 for m in M if m['xh'] is not None)} utakmica sa xG")
        if a.backtest:
            backtest_cfg(M, a.backtest, a, a.beta) if a.bt_grid else backtest(M, a.backtest, [float(x) for x in a.betas.split(",")], a)
            return
        Mraw = M
        M = eff(M, a.beta)
    else:
        files = [os.path.join(D, f"{CUR['csv']}_{s}.csv") for s in seasons if os.path.exists(os.path.join(D, f"{CUR['csv']}_{s}.csv"))] + glob.glob(os.path.join(D, "results*.csv"))
        M = load(files)
    if len(M) < 20: sys.exit(f"Premalo utakmica ({len(M)}). Pokreni statshub_football.py (ili --download za stari CSV izvor)")
    mod = fit(M, today, a.halflife, a.k); known = set(mod["att"])
    print(f"Model: {len(M)} utakmica, {len(known)} ekipa, mu {mod['mu']:.2f}, domaci faktor {mod['H']:.2f}" + (f", beta xG {a.beta}" if use_sh else ""))
    ctxs = []
    rows, out, skipped, dropped = [], [], [], 0
    mod_s = fit(M, today, a.halflife_short, a.k_short) if a.recent_w > 0 else None
    def blend_lam(h_, g_):
        x, y = lam_of(mod, h_, g_)
        if mod_s:
            sx, sy = lam_of(mod_s, h_, g_); x, y = (1 - a.recent_w) * x + a.recent_w * sx, (1 - a.recent_w) * y + a.recent_w * sy
        return x, y
    FP = dict(FX.PARAMS, mom_k=a.mom_k, style_k=a.style_k, mot_k=a.mot_k, ref_k=a.ref_k, wx_k=a.wx_k) if FX else None
    fcfg = {"dir": D, "csv": CUR["csv"], "league": CUR["code"], "ut": CUR.get("ut"), "today": today, "ref": {}, "wx_off": a.no_weather}
    for r_ in a.ref:
        k_, _, nm_ = r_.partition("=")
        pr_ = [canon(x.strip(), known) for x in k_.split(",")]
        if len(pr_) == 2 and all(pr_) and nm_.strip(): fcfg["ref"][f"{pr_[0]}|{pr_[1]}"] = nm_.strip()
    if a.match:
        h, g = [canon(x, known) for x in a.match.split(",")]
        todo = [{"h": h, "a": g, "H": h, "A": g, "start": None, "x12": None, "ou": [], "btts": None}]
    else:
        try: ev = json.load(open(ODDS, encoding="utf-8"))["events"]
        except OSError: sys.exit(f"Nema {ODDS}. Pokreni fetch_superbet_football.py ili koristi --match.")
        todo = []
        for e in ev:
            h, g = canon(e["h"], known), canon(e["a"], known)
            if not (h and g): print("  ne prepoznajem ekipu:", e["h"], "-", e["a"], "(dodaj u ALIAS)"); skipped.append({"h": e["h"], "a": e["a"], "start": e.get("start")}); continue
            todo.append({**e, "H": e["h"], "A": e["a"], "h": h, "a": g})
    for e in todo:
        mh = ma = 1.0; cx = None
        if SD:
            try: st_ms = datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00")).timestamp() * 1000
            except (TypeError, ValueError): st_ms = None
            ch, ca = team_ctx(SD, e["h"], st_ms, a.att_k, a.def_k, a.rest_pen, a.inj_min), team_ctx(SD, e["a"], st_ms, a.att_k, a.def_k, a.rest_pen, a.inj_min)
            mh, ma = ch["att"] * ca["conc"], ca["att"] * ch["conc"]
            cx = {"h": e["H"], "a": e["A"], "start": e.get("start"), "mult": [round(mh, 3), round(ma, 3)], "home": ch, "away": ca, "h2h": h2h(Mraw, e["h"], e["a"]) if Mraw else None}
        F = None
        if cx and FX and not a.no_factors:
            F = FX.all_factors(SD, Mraw, a.beta, blend_lam, {**e, "h": e["h"], "a": e["a"]}, ch, ca, fcfg, FP)
            mh *= F["mult"][0] * F["total"]; ma *= F["mult"][1] * F["total"]; cx["factors"] = F
        lh0, la0 = blend_lam(e["h"], e["a"])
        lh, la = lh0 * mh * a.lam_scale, la0 * ma * a.lam_scale
        G = grid(lh, la, a.rho); P = probs(G)
        if cx:
            cx["lam"] = [round(lh, 3), round(la, 3)]; ctxs.append(cx)
            if F:
                for n_ in F["notes"][:8]: print("   *", n_)
                if F["under_block"]: print("   ! Under/NG:", a.under_rule, "-", "; ".join(F["under_block"]))
            print(f"\n{e['H']} - {e['A']}: forma {ch['form']['str'] if ch.get('form') else '?'} / {ca['form']['str'] if ca.get('form') else '?'} | odmor {ch['rest']}d / {ca['rest']}d"
                  f" | nedostupni {[u['n'] for u in ch['out']]} / {[u['n'] for u in ca['out']]} | stil {ch.get('style')} / {ca.get('style')}")
        print(f"\n{e['H']} - {e['A']}  {e.get('start') or ''}\n  ocekivani golovi {lh:.2f}:{la:.2f} | 1X2 {P['x12'][0]*100:.0f}/{P['x12'][1]*100:.0f}/{P['x12'][2]*100:.0f}% | GG {P['btts']*100:.0f}% | preko 2.5 {P['over'](2)*100:.0f}%")
        best = max(((i, j, G[i][j]) for i in range(len(G)) for j in range(len(G))), key=lambda t: t[2])
        print(f"  najverovatniji rezultat {best[0]}:{best[1]} ({best[2]*100:.1f}%)")
        sel = []   # (trziste, izbor, kvota, p_model, p_trziste)
        if e.get("x12") and len(e["x12"]) == 3 and all(o and o > 1.01 for o in e["x12"]) and 1.0 <= sum(1 / o for o in e["x12"]) <= 1.25:
            f = fair(e["x12"])
            sel += [("1X2", n, e["x12"][i], P["x12"][i], f[i]) for i, n in enumerate(("1", "X", "2"))]
        for L, o, u in e.get("ou") or []:
            if L not in OU_LINES or not (o and u and 1.0 <= 1 / o + 1 / u <= 1.2): continue      # samo linije 1.5/2.5/3.5; nelogicna marza = pogresno uparene kvote
            f = fair([o, u]); po = P["over"](L)
            sel += [(f"Golovi {L}", "Vise", o, po, f[0]), (f"Golovi {L}", "Manje", u, 1 - po, f[1])]
        if e.get("btts") and len(e["btts"]) == 2 and all(o and o > 1.01 for o in e["btts"]) and 1.0 <= sum(1 / o for o in e["btts"]) <= 1.2:
            f = fair(e["btts"]); sel += [("GG/NG", "GG", e["btts"][0], P["btts"], f[0]), ("GG/NG", "NG", e["btts"][1], 1 - P["btts"], f[1])]
        # Namerno SAMO 1X2, Golovi 1.5/2.5/3.5 i GG/NG. Kombinovane i specijalne igre (GG ili N+, GG&N+, HT/FT, DC, AH, EH, raspon, tacan rezultat...)
        # iz Superbet JSON-a se ignorisu: njihovo mapiranje izlaza je nepouzdano.
        for t_ in sel:
            mk, pick, o, pm, pf = t_[:5]; pp = t_[5] if len(t_) > 5 else 0.0
            p = a.w * pm + (1 - a.w) * pf; ev_ = (1 - pp) * (p * o - 1); evr = (1 - pp) * (pm * o - 1)
            row = {"h": e["H"], "a": e["A"], "start": e.get("start"), "market": mk, "pick": pick, "odds": o, "p_model": round(pm, 4), "p_market": round(pf, 4), "p": round(p, 4), "push": round(pp, 4), "ev": round(ev_, 4), "ev_raw": round(evr, 4), "sus": abs(pm - pf) > 0.15 or evr > 0.25}
            if row["sus"] and not a.keep_sus:         # model i trziste se previse razilaze (npr. novajlije bez istorije): nepouzdano, ne izbacujemo tip
                dropped += 1
                continue
            if F and F["under_block"] and pick in ("Manje", "NG") and a.under_rule != "off":      # "ne diraj hot tim": ekipa u naletu lomi statistiku
                row["block_reason"] = "; ".join(F["under_block"])
                if a.under_rule == "block": row["blocked"] = True
                else: row["ev"] = round(row["ev"] - a.under_pen, 4); row["demoted"] = True
            out.append(row)
            if a.all or ev_ >= a.min_ev: print(f"  {mk:<12} {pick:<6} kvota {o:<5.2f} model {pm*100:4.1f}% trziste {pf*100:4.1f}%{f' vracen ulog {pp*100:.0f}%' if pp else ''} -> EV {ev_*100:+5.1f}% (sirovi model {evr*100:+.1f}%){'  (PROVERI liniju/kvotu)' if evr > 0.25 else ''}")
    if not a.match:
        try:                                          # tekstualno objasnjenje svakog tipa (label, meaning, why, risk) za "Zasto?" prozorcic
            from fb_explain import explain_row, ctx_for
            for r_ in out:
                r_.update(explain_row(r_, r_["h"], r_["a"], ctx_for({"context": ctxs}, r_["h"], r_["a"], r_.get("start"))))
        except ImportError:
            print("UPOZORENJE: fb_explain.py nije pored football_poisson.py - tipovi ce biti bez objasnjenja iz JSON-a (sajt koristi rezervno objasnjenje)")
        json.dump({"updated": datetime.now().isoformat(timespec="seconds"), "w": a.w, "source": "statshub" if SD else "csv", "beta": a.beta if SD else None, "rows": out, "skipped": skipped, "context": ctxs}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"\nupisano {len(out)} izbora u {OUT}" + (f" (izbaceno {dropped} sumnjivih: model i trziste se previse razilaze; --keep-sus ih vraca)" if dropped else ""))


if __name__ == "__main__":
    main()
