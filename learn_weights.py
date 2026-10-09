"""Uci tezine iz istorije: (1) Ridge regresija rezidualne razlike (stvarna razlika minus Pinnacle zatvaranje)
na faktore (rejting, forma, odmor), (2) LOO unakrsna validacija kaze da li faktori uopste dodaju nesto tržištu.
Tezina modela raste samo ako faktori dokazano pomazu. Izlaz: data/model_weights.json. Treba bar 40 utakmica."""
import json, re, time, unicodedata, difflib
import numpy as np
def load(p, d):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return d
norm = lambda s: re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(s or "").lower()).encode("ascii", "ignore").decode())
day = lambda s: re.sub(r"\D", "", str(s or ""))[:8]
clv, site = load("data/clv.json", {}), load("data/site.json", {})
games, clubs = site.get("games", []), site.get("clubs", {})
names = {}
for c, v in clubs.items():
    for k in (c, v.get("short"), v.get("name")):
        if k: names[norm(k)] = c
def code(n):
    n = norm(n)
    if n in names: return names[n]
    m = difflib.get_close_matches(n, list(names), 1, 0.75)
    return names[m[0]] if m else None
G = sorted([g for g in games if g.get("hs") is not None and g.get("as") is not None], key=lambda g: day(g.get("dt")))
def prior(t, d):
    return [g for g in G if (g["h"] == t or g["a"] == t) and day(g.get("dt")) < d]
def marg(g, t): return g["hs"] - g["as"] if g["h"] == t else g["as"] - g["hs"]
def feats(h, a, d):
    ph, pa = prior(h, d), prior(a, d)
    if len(ph) < 3 or len(pa) < 3: return None
    m = lambda P, t: np.mean([marg(g, t) for g in P])
    rest = lambda P: min(6, max(0, (int(d) - int(day(P[-1].get("dt")))))) if P else 3
    return [m(ph, h) - m(pa, a), m(ph[-5:], h) - m(pa[-5:], a), max(-4, min(4, rest(ph) - rest(pa)))]
X, Y, used = [], [], set()
for r in clv.values():
    c = r.get("close") or {}
    if "sp" not in c: continue
    h, a, d = code(r["h"]), code(r["a"]), day(r["start"])
    cand = [g for g in G if g["h"] == h and g["a"] == a and id(g) not in used]
    if not h or not a or not cand: continue
    g = min(cand, key=lambda g: abs(int(day(g.get("dt")) or 0) - int(d)))
    f = feats(h, a, day(g.get("dt")))
    if f is None: continue
    used.add(id(g)); X.append(f); Y.append((g["hs"] - g["as"]) + c["sp"][0])  # rezidual: stvarna razlika - ocekivana (-linija)
n = len(Y)
out = {"updated": int(time.time()), "n": n, "names": ["razlika rejtinga", "razlika forme (5)", "razlika odmora"]}
if n < 40:
    out["status"] = f"nedovoljno podataka ({n}/40)"
else:
    X, Y = np.array(X, float), np.array(Y, float)
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd; yc = Y - Y.mean(); best = None
    for lam in (1, 3, 10, 30, 100):
        A = np.linalg.inv(Z.T @ Z + lam * np.eye(Z.shape[1])); H = Z @ A @ Z.T
        beta = A @ Z.T @ yc; e = yc - Z @ beta
        loo = e / (1 - np.diag(H)); mse = float(np.mean(loo ** 2))
        if best is None or mse < best[0]: best = (mse, lam, beta)
    mse_cv, lam, beta = best; mse_mkt = float(np.mean(yc ** 2)); gain = 1 - mse_cv / mse_mkt
    game = float(min(.45, max(.10, .15 + 2 * max(gain, 0))))
    out.update(status="ok", weights={"game": round(game, 3), "sharp": round(game * .75, 3)},
               ridge={"lambda": lam, "coef": [round(float(x), 3) for x in beta / sd], "mean": [round(float(x), 3) for x in mu]},
               metrics={"mse_market": round(mse_mkt, 2), "mse_cv": round(mse_cv, 2), "gain": round(float(gain), 3)})
json.dump(out, open("data/model_weights.json", "w", encoding="utf-8"), ensure_ascii=False)
print(out["status"], "n=", n)
