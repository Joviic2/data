#!/usr/bin/env python3
"""quant_model.py - kvantitativni model za evropsku kosarku (Evroliga, ABA, ACB, LNB, BBL). Samo standardna biblioteka.
"""
import collections, datetime, json, math, os, re, time, unicodedata

CFG = {
    "HL_TEAM": 75.0, "HL_PLAYER": 60.0,
    "RIDGE": 4.0, "RIDGE_PACE": 6.0, "HCA_K": 12.0,
    "GT_MARGIN": 18, "GT_W": 0.35,
    "FF_BLEND": 0.30,
    "REST_K": 60.0, "REST_CLIP": 3.0,
    "SD_M_PRIOR": 11.5, "SD_T_PRIOR": 14.0, "SD_PRIOR_N": 20,
    "W_MKT_MATCH": 0.50, "W_MKT_PROP": 0.30,
    "MIN_EV": 0.03, "MAX_ODDS_ALT": 2.6, "SUSPECT_EV": 0.35,
    "MIN_GAMES_PROP": 5, "MAXMIN": 37.0, "RETAIN": 0.65,
    "K_RATE": 150.0, "K_DVP": 5.0, "K_ZONE_P": 15.0, "K_ZONE_D": 40.0, "K_FF": 5.0, "CV_K": 6.0,
    "ELAST_PTS": 0.55, "ELAST_AST": 0.55,
    "KELLY": 0.25, "KELLY_CAP": 0.02, "USE_ALT_PROPS": False,
    "DVP_AMP": 1.5,
    "MULT_DAMP": {"pts": 0.15, "reb": 0.55, "ast": 0.55},
    "SHRINK_PTS": 0.25,
}
ZN = ("rim", "paint", "mid", "c3l", "c3r", "ab3")
STATS = ("pts", "reb", "ast")
SLAB = {"pts": "poena", "reb": "skokova", "ast": "asistencija"}
STOP = {"basket", "basketball", "club", "team", "baloncesto", "kosarka", "kosarkaski"}


def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode().lower()


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def clamp(x, a, b):
    return max(a, min(b, x))


def parse_ts(x):
    try:
        d = datetime.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError):
        return None


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _nid(x):
    return re.sub(r"^[Pp]", "", str(x if x is not None else "").strip())


def _f(p, i):
    try:
        return float(p[i] or 0)
    except (TypeError, ValueError, IndexError):
        return 0.0


def pos_class(pos):
    p = norm(pos or "").strip()
    if not p:
        return "?"
    if re.search(r"cent|^c\b|^c-|pivot", p):
        return "C"
    if re.search(r"forw|^f\b|^f-|^pf|^sf|krilo", p):
        return "F"
    if re.search(r"guard|^g\b|^g-|^pg|^sg|bek|plej", p):
        return "G"
    return "?"


def toks(n):
    return {w for w in re.split(r"[^a-z0-9]+", norm(n)) if len(w) >= 4 and w not in STOP}


def tscore(a, b):
    s = 0
    for x in a:
        for y in b:
            if x == y:
                s += 2
                break
            if x[:4] == y[:4] and x[:4] not in ("real", "club"):
                s += 1
                break
    return s


def zone_of(x, y, is3):
    if is3:
        return ("c3l" if x < 0 else "c3r") if abs(x) >= 600 and y <= 150 else "ab3"
    if math.hypot(x, y) <= 125:
        return "rim"
    return "paint" if abs(x) <= 245 and y <= 420 else "mid"


def parse_shots(j):
    A = j if isinstance(j, list) else next((v for v in (j or {}).values() if isinstance(v, list)), [])
    out = []
    for r in A:
        if not isinstance(r, dict):
            continue
        g = lambda ks: next((r[k] for k in ks if r.get(k) is not None), None)
        act = str(g(["ID_ACTION", "id_action", "action", "ACTION"]) or "").upper().strip()
        if not re.match(r"^([23]FG|FT)[MA]$", act) or act[0] == "F":
            continue
        try:
            x, y = float(g(["COORD_X", "coord_x", "x", "X"])), float(g(["COORD_Y", "coord_y", "y", "Y"]))
        except (TypeError, ValueError):
            continue
        if y < -200:
            continue
        out.append({"t": str(g(["TEAM", "team"]) or "").strip(), "p": _nid(g(["ID_PLAYER", "id_player", "player_id"])),
                    "m": act.endswith("M"), "z": zone_of(x, y, act[0] == "3")})
    return out


def poss(t):
    return t["fg2a"] + t["fg3a"] + 0.44 * t["fta"] - t.get("or", 0) + t["tov"]


def _tot_ok(t):
    return isinstance(t, dict) and all(k in t for k in ("fg2m", "fg2a", "fg3m", "fg3a", "fta", "tov", "pts"))


def solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                k = M[r][c] / M[c][c]
                for j in range(c, n + 1):
                    M[r][j] -= k * M[c][j]
    return [M[i][n] / M[i][i] for i in range(n)]


def nb_cdf(k, mu, var):
    if k < 0:
        return 0.0
    mu = max(mu, 1e-6)
    if var > mu * 1.0001:
        r = mu * mu / (var - mu)
        p = r / (r + mu)
        pm = p ** r
        s = pm
        for i in range(int(k)):
            pm *= (i + r) / (i + 1) * (1 - p)
            s += pm
        return min(1.0, s)
    pm = math.exp(-mu)
    s = pm
    for i in range(int(k)):
        pm *= mu / (i + 1)
        s += pm
    return min(1.0, s)


def over_under(line, mu, sd, kind):
    if kind == "norm":
        if abs(line - round(line)) < 1e-9:
            po = 1 - phi((line + 0.5 - mu) / sd)
            pu = phi((line - 0.5 - mu) / sd)
            return po, pu, max(0.0, 1 - po - pu)
        po = 1 - phi((line - mu) / sd)
        return po, 1 - po, 0.0
    var = max(sd * sd, mu * 1.0001)
    if abs(line - round(line)) < 1e-9:
        L = int(round(line))
        pu = nb_cdf(L - 1, mu, var)
        po = 1 - nb_cdf(L, mu, var)
        return po, pu, max(0.0, 1 - po - pu)
    po = 1 - nb_cdf(math.floor(line), mu, var)
    return po, 1 - po, 0.0


def ev_of(p, push, odds):
    return p * odds + push - 1


def kelly(p, odds, cfg=CFG):
    if odds <= 1:
        return 0.0
    return round(clamp((p * odds - 1) / (odds - 1) * cfg["KELLY"], 0, cfg["KELLY_CAP"]), 4)


class League:
    def __init__(self, lg, root=".", site=None):
        self.lg = lg
        self.dir = os.path.join(root, "data") if lg == "euroleague" else os.path.join(root, "data", lg)
        site = site if site is not None else (_load(os.path.join(self.dir, "site.json")) or {})
        self.clubs = site.get("clubs") or {}
        self.rosters = site.get("rosters") or {}
        self.fixtures = site.get("fixtures") or []
        self.inj = (_load(os.path.join(self.dir, "injuries.json")) or {}).get("teams") or {}
        self._shots = {}
        self.games = []
        for g in site.get("games") or []:
            t = parse_ts(g.get("utc") or g.get("dt"))
            b = g.get("box") or {}
            if t is None or g.get("hs") is None or g.get("as") is None or not b.get("h") or not b.get("a"):
                continue
            th, ta = b["h"].get("tot"), b["a"].get("tot")
            if not (_tot_ok(th) and _tot_ok(ta)):
                continue
            self.games.append({"n": g.get("n"), "t": t, "h": g["h"], "a": g["a"], "hs": float(g["hs"]), "as": float(g["as"]),
                               "q": g.get("q"), "box": b, "th": th, "ta": ta})
        self.games.sort(key=lambda x: x["t"])
        self._prep()

    def _prep(self):
        self.prec, self.active, self.tmgames, self.rest = [], {}, collections.defaultdict(list), {}
        for i, g in enumerate(self.games):
            q = g["q"] or {}
            qh = [float(x or 0) for x in (q.get("h") or [])]
            qa = [float(x or 0) for x in (q.get("a") or [])]
            nq = len(qh)
            g["mins"] = 40 + 5 * max(0, nq - 4) if nq >= 4 else 40
            g["P"] = (poss(g["th"]) + poss(g["ta"])) / 2
            g["Pn"] = g["P"] * 40 / g["mins"]
            g["blow"], g["m3"] = False, None
            g["seg"] = (g["hs"], g["as"], g["P"])
            if nq >= 4 and len(qa) == nq:
                m3 = sum(qh[:3]) - sum(qa[:3])
                g["m3"] = m3
                if abs(m3) >= CFG["GT_MARGIN"]:
                    g["blow"] = True
                    g["seg"] = (sum(qh[:3]), sum(qa[:3]), g["P"] * 0.75 * 40 / g["mins"])
            for s, tm, opp, home, tot in (("h", g["h"], g["a"], 1, g["th"]), ("a", g["a"], g["h"], -1, g["ta"])):
                self.tmgames[tm].append(i)
                rows = g["box"][s].get("p") or []
                tmin = sum(_f(p, 4) for p in rows) or 200.0
                den = tot["fg2a"] + tot["fg3a"] + 0.44 * tot["fta"] + tot["tov"]
                own = g["hs"] if s == "h" else g["as"]
                marg = (g["hs"] - g["as"]) * home
                act = set()
                for p in rows:
                    mn = _f(p, 4)
                    if mn <= 0:
                        continue
                    pid = _nid(p[0])
                    act.add(pid)
                    fga = _f(p, 7) + _f(p, 9)
                    usg = 100 * (fga + 0.44 * _f(p, 11) + _f(p, 17)) * (tmin / 5) / (mn * den) if den > 0 else 0.0
                    self.prec.append({"i": i, "t": g["t"], "tm": tm, "opp": opp, "home": home, "id": pid, "name": str(p[1]),
                                      "start": _f(p, 3), "min": mn, "pts": _f(p, 5), "reb": _f(p, 14), "ast": _f(p, 15),
                                      "fg3m": _f(p, 8), "usg": usg, "P": g["Pn"], "blow": g["blow"], "marg": marg,
                                      "eff": own / g["P"] * 100 if g["P"] > 0 else 100.0})
                self.active[(i, tm)] = act
        for tm, idx in self.tmgames.items():
            prev = None
            for i in idx:
                self.rest[(i, tm)] = None if prev is None else (self.games[i]["t"] - self.games[prev]["t"]) / 86400.0
                prev = i

    def shots(self, n):
        if n not in self._shots:
            j = _load(os.path.join(self.dir, f"shots_{n}.json"))
            self._shots[n] = parse_shots(j) if j is not None else None
        return self._shots[n]

    def team_code(self, name):
        tk = toks(name)
        best, sc = None, 0
        for c, v in self.clubs.items():
            s = tscore(tk, toks((v.get("name") or "") + " " + (v.get("short") or "")))
            if s > sc:
                best, sc = c, s
        return best if sc >= 1 else None

    def fit(self, asof=None, **kw):
        cfg = dict(CFG)
        cfg.update(kw)
        if asof is None:
            asof = (self.games[-1]["t"] + 1) if self.games else time.time()
        return Model(self, [(i, g) for i, g in enumerate(self.games) if g["t"] < asof], asof, cfg)


class Model:
    def __init__(self, L, gs, asof, cfg):
        self.L, self.cfg, self.asof, self.n = L, cfg, asof, len(gs)
        self.ok = self.n >= 8
        if not self.ok:
            return
        W = lambda t: 0.5 ** ((asof - t) / 86400.0 / cfg["HL_TEAM"])
        obs, pobs, T = [], [], set()
        for i, g in gs:
            sh, sa, sP = g["seg"]
            if sP <= 0:
                continue
            w = W(g["t"])
            obs.append((g["h"], g["a"], 1, sh / sP * 100, w))
            obs.append((g["a"], g["h"], -1, sa / sP * 100, w))
            pobs.append((g["h"], g["a"], g["Pn"], w))
            T.update((g["h"], g["a"]))
        self.T = sorted(T)
        self._fit_eff(obs)
        self._fit_pace(pobs)
        self._residuals(gs, W)
        self._factors(gs, W)
        self._tinfo(obs)
        self._sd(gs, W)

    def _fit_eff(self, obs):
        c, T = self.cfg, self.T
        tw = sum(o[4] for o in obs)
        lam = c["RIDGE"] * tw / len(obs)
        off, dfn, h = {t: 0.0 for t in T}, {t: 0.0 for t in T}, 0.0
        own, opp = collections.defaultdict(list), collections.defaultdict(list)
        for o in obs:
            own[o[0]].append(o)
            opp[o[1]].append(o)
        mu = sum(o[3] * o[4] for o in obs) / tw
        for _ in range(80):
            mu = sum(w * (y - h * s - off[t] + dfn[p]) for t, p, s, y, w in obs) / tw
            h = sum(w * s * (y - mu - off[t] + dfn[p]) for t, p, s, y, w in obs) / tw
            for t in T:
                a = sum(w * (y - mu - h * s + dfn[p]) for tt, p, s, y, w in own[t])
                off[t] = a / (sum(o[4] for o in own[t]) + lam)
                a = sum(w * (mu + h * s + off[tt] - y) for tt, p, s, y, w in opp[t])
                dfn[t] = a / (sum(o[4] for o in opp[t]) + lam)
        self.mu, self.h, self.off, self.dfn = mu, h, off, dfn

    def _fit_pace(self, pobs):
        T, tw = self.T, sum(o[3] for o in pobs)
        lam = self.cfg["RIDGE_PACE"] * tw / len(pobs)
        pc = {t: 0.0 for t in T}
        by = collections.defaultdict(list)
        for o in pobs:
            by[o[0]].append((o[1], o[2], o[3]))
            by[o[1]].append((o[0], o[2], o[3]))
        mp = sum(o[2] * o[3] for o in pobs) / tw
        for _ in range(60):
            mp = sum(w * (P - pc[a] - pc[b]) for a, b, P, w in pobs) / tw
            for t in T:
                num = sum(w * (P - mp - pc[o]) for o, P, w in by[t])
                pc[t] = num / (sum(x[2] for x in by[t]) + lam)
        self.mp, self.pc = mp, pc

    def _rd(self, i, g):
        a, b = self.L.rest.get((i, g["h"])), self.L.rest.get((i, g["a"]))
        return 0.0 if a is None or b is None else clamp(a - b, -self.cfg["REST_CLIP"], self.cfg["REST_CLIP"])

    def _residuals(self, gs, W):
        c = self.cfg
        self.dev, self.rest_beta = {}, 0.0
        res, hm, am = [], collections.defaultdict(lambda: [0.0, 0.0]), collections.defaultdict(lambda: [0.0, 0.0])
        for i, g in gs:
            sh, sa, sP = g["seg"]
            if sP <= 0:
                continue
            w = W(g["t"])
            pred = 2 * self.h + self.off[g["h"]] - self.dfn[g["a"]] - self.off[g["a"]] + self.dfn[g["h"]]
            r = (sh - sa) / sP * 100 - pred
            res.append((i, g, w, r))
            hm[g["h"]][0] += w * r
            hm[g["h"]][1] += w
            am[g["a"]][0] += -w * r
            am[g["a"]][1] += w
        for t in self.T:
            if hm[t][1] > 0 and am[t][1] > 0:
                n = min(hm[t][1], am[t][1])
                self.dev[t] = (hm[t][0] / hm[t][1] - am[t][0] / am[t][1]) / 2 * n / (n + c["HCA_K"])
        sxx = sum(w * self._rd(i, g) ** 2 for i, g, w, r in res)
        sxy = sum(w * self._rd(i, g) * (r - self.dev.get(g["h"], 0)) for i, g, w, r in res)
        self.rest_beta = sxy / (sxx + c["REST_K"]) if sxx > 0 else 0.0

    def _fg(self, tot, ot):
        fga = tot["fg2a"] + tot["fg3a"]
        orb = tot.get("or", 0.0)
        return {"efg": ((tot["fg2m"] + 1.5 * tot["fg3m"]), fga), "tov": (tot["tov"], fga + 0.44 * tot["fta"] + tot["tov"]),
                "orb": (orb, orb + max(0.0, ot.get("tr", 0.0) - ot.get("or", 0.0))), "ftr": (tot["fta"], fga)}

    def _factors(self, gs, W):
        K = self.cfg["K_FF"]
        FK = ("efg", "tov", "orb", "ftr")
        o_s = collections.defaultdict(lambda: {k: [0.0, 0.0] for k in FK})
        d_s = collections.defaultdict(lambda: {k: [0.0, 0.0] for k in FK})
        wt = collections.defaultdict(float)
        lg = {k: [0.0, 0.0] for k in FK}
        X, Y, Wl = [], [], []
        for i, g in gs:
            w = W(g["t"])
            for tm, op, tot, ot, y in ((g["h"], g["a"], g["th"], g["ta"], g["seg"][0]), (g["a"], g["h"], g["ta"], g["th"], g["seg"][1])):
                f = self._fg(tot, ot)
                wt[tm] += w
                wt[op] += w
                for k in FK:
                    o_s[tm][k][0] += w * f[k][0]
                    o_s[tm][k][1] += w * f[k][1]
                    d_s[op][k][0] += w * f[k][0]
                    d_s[op][k][1] += w * f[k][1]
                    lg[k][0] += w * f[k][0]
                    lg[k][1] += w * f[k][1]
                sP = g["seg"][2]
                if sP > 0 and all(f[k][1] > 0 for k in FK):
                    X.append([1.0] + [f[k][0] / f[k][1] for k in FK])
                    Y.append(y / sP * 100)
                    Wl.append(w)
        self.lgf = {k: lg[k][0] / lg[k][1] for k in FK}
        shr = lambda s, k, t: (s[k][0] + K * self.lgf[k] * (s[k][1] / wt[t])) / (s[k][1] + K * (s[k][1] / wt[t])) if wt[t] > 0 and s[k][1] > 0 else self.lgf[k]
        self.ffo = {t: {k: shr(o_s[t], k, t) for k in FK} for t in self.T}
        self.ffd = {t: {k: shr(d_s[t], k, t) for k in FK} for t in self.T}
        self.ffb = None
        if len(X) >= 30:
            n = 5
            A = [[sum(w * x[a] * x[b] for x, w in zip(X, Wl)) + (1e-3 if a == b and a else 1e-9 * (a == b)) for b in range(n)] for a in range(n)]
            bb = [sum(w * x[a] * y for x, y, w in zip(X, Y, Wl)) for a in range(n)]
            self.ffb = solve(A, bb)

    FFW = {"efg": 0.40, "tov": 0.25, "orb": 0.20, "ftr": 0.15}

    def ff_eff(self, a, d):
        if not self.ffb:
            return None
        x = [1.0]
        for k in ("efg", "tov", "orb", "ftr"):
            x.append(self.lgf[k] + (self.ffo[a][k] - self.lgf[k]) + (self.ffd[d][k] - self.lgf[k]))
        return sum(b * v for b, v in zip(self.ffb, x))

    def ff_edge(self, a, b):
        e = {}
        for k, sgn in (("efg", 1), ("tov", -1), ("orb", 1), ("ftr", 1)):
            e[k] = sgn * ((self.ffo[a][k] - self.ffd[b][k]) - (self.ffo[b][k] - self.ffd[a][k]))
        return sum(self.FFW[k] * e[k] * 100 for k in e), e

    def _tinfo(self, obs):
        K = 8.0
        raw = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))
        for t, p, s, y, w in obs:
            raw[t][("o", s)][0] += w * y
            raw[t][("o", s)][1] += w
            raw[p][("d", -s)][0] += w * y
            raw[p][("d", -s)][1] += w
        self.ti = {}
        for t in self.T:
            r = raw[t]
            oall = (r[("o", 1)][0] + r[("o", -1)][0]) / max(1e-9, r[("o", 1)][1] + r[("o", -1)][1])
            dall = (r[("d", 1)][0] + r[("d", -1)][0]) / max(1e-9, r[("d", 1)][1] + r[("d", -1)][1])
            sp = lambda key, base: (r[key][0] + K * base) / (r[key][1] + K)
            self.ti[t] = {"ortg": round(self.mu + self.off[t], 1), "drtg": round(self.mu - self.dfn[t], 1),
                          "net": round(self.off[t] + self.dfn[t], 1), "pace": round(self.mp + self.pc[t], 1),
                          "ortg_h": round(sp(("o", 1), oall), 1), "ortg_a": round(sp(("o", -1), oall), 1),
                          "drtg_h": round(sp(("d", 1), dall), 1), "drtg_a": round(sp(("d", -1), dall), 1),
                          "efg": round(self.ffo[t]["efg"] * 100, 1), "tov": round(self.ffo[t]["tov"] * 100, 1),
                          "orb": round(self.ffo[t]["orb"] * 100, 1), "ftr": round(self.ffo[t]["ftr"] * 100, 1),
                          "efg_d": round(self.ffd[t]["efg"] * 100, 1), "tov_d": round(self.ffd[t]["tov"] * 100, 1),
                          "orb_d": round(self.ffd[t]["orb"] * 100, 1), "ftr_d": round(self.ffd[t]["ftr"] * 100, 1)}

    def predict(self, h, a, rest_diff_days=0.0, use_ff=True):
        if h not in self.off or a not in self.off:
            return None
        c = self.cfg
        P = self.mp + self.pc[h] + self.pc[a]
        nh, na = self.mu + self.off[h] - self.dfn[a], self.mu + self.off[a] - self.dfn[h]
        fb = c["FF_BLEND"] if use_ff else 0.0
        fh, fa = (self.ff_eff(h, a), self.ff_eff(a, h)) if fb > 0 else (None, None)
        if fh is not None and fa is not None:
            nh, na = (1 - fb) * nh + fb * fh, (1 - fb) * na + fb * fa
        rd = clamp(rest_diff_days, -c["REST_CLIP"], c["REST_CLIP"])
        adj = self.dev.get(h, 0.0) + self.rest_beta * rd
        eh, ea = nh + self.h + adj / 2, na - self.h - adj / 2
        ph, pa = P * eh / 100, P * ea / 100
        return {"P": P, "eff_h": eh, "eff_a": ea, "pts_h": ph, "pts_a": pa, "margin": ph - pa, "total": ph + pa,
                "rest_pts": P * self.rest_beta * rd / 100, "hca_team": P * self.dev.get(h, 0.0) / 100}

    def _sd(self, gs, W):
        c, sm, st, tw = self.cfg, 0.0, 0.0, 0.0
        for i, g in gs:
            p = self.predict(g["h"], g["a"], self._rd(i, g))
            if not p:
                continue
            w = W(g["t"])
            sm += w * (g["hs"] - g["as"] - p["margin"]) ** 2
            st += w * (g["hs"] + g["as"] - p["total"]) ** 2
            tw += w
        n = len(gs)
        N0 = c["SD_PRIOR_N"]
        sm, st = (sm / tw if tw else c["SD_M_PRIOR"] ** 2), (st / tw if tw else c["SD_T_PRIOR"] ** 2)
        self.sd_m = math.sqrt((n * sm + N0 * c["SD_M_PRIOR"] ** 2) / (n + N0)) * 1.04
        self.sd_t = math.sqrt((n * st + N0 * c["SD_T_PRIOR"] ** 2) / (n + N0)) * 1.04


class Props:
    def __init__(self, L, M, asof):
        self.L, self.M, self.asof, self.c = L, M, asof, M.cfg
        c = self.c
        recs = [r for r in L.prec if r["t"] < asof and r["min"] >= 4]
        self.recs = recs
        self.byp = collections.defaultdict(list)
        for r in recs:
            self.byp[r["id"]].append(r)
        age = lambda r: (asof - r["t"]) / 86400.0
        self.w = lambda r: 0.5 ** (age(r) / c["HL_PLAYER"]) * (c["GT_W"] if r["blow"] else 1.0)
        pos_of = {}
        for tm, ro in L.rosters.items():
            for p in (ro or {}).get("players", []):
                pos_of[_nid(p.get("id"))] = pos_class(p.get("pos"))
        raw = {}
        for pid, rs in self.byp.items():
            rs.sort(key=lambda r: r["t"])
            ws = [self.w(r) for r in rs]
            Sw = sum(ws)
            Wm = sum(w * r["min"] for w, r in zip(ws, rs))
            if Sw <= 0 or Wm <= 0:
                continue
            raw[pid] = {"id": pid, "name": rs[-1]["name"], "tm": rs[-1]["tm"], "pos": pos_of.get(pid, "?"), "n": len(rs), "Sw": Sw, "Wm": Wm,
                        "mean_min": Wm / Sw, "S": {s: sum(w * r[s] for w, r in zip(ws, rs)) for s in STATS},
                        "usg": sum(w * r["usg"] * r["min"] for w, r in zip(ws, rs)) / Wm,
                        "Pbar": sum(w * r["P"] for w, r in zip(ws, rs)) / Sw, "effbar": sum(w * r["eff"] for w, r in zip(ws, rs)) / Sw,
                        "xs": {s: [r[s] for r in rs] for s in STATS}, "ws": ws,
                        "l5min": sum(r["min"] for r in rs[-5:]) / len(rs[-5:])}
        pri = {}
        for ps in {p["pos"] for p in raw.values()} | {"?"}:
            sel = [p for p in raw.values() if ps == "?" or p["pos"] == ps]
            wm = sum(p["Wm"] for p in sel) or 1.0
            pri[ps] = {"rate": {s: sum(p["S"][s] for p in sel) / wm for s in STATS}, "usg": sum(p["usg"] * p["Wm"] for p in sel) / wm}
        self.pri = pri
        cvs = {s: [] for s in STATS}
        for p in raw.values():
            pr = pri.get(p["pos"], pri["?"])
            p["rate"] = {s: (p["S"][s] + c["K_RATE"] * pr["rate"][s]) / (p["Wm"] + c["K_RATE"]) for s in STATS}
            p["cv"] = {}
            for s in STATS:
                x, ws = p["xs"][s], p["ws"]
                mu = sum(w * v for w, v in zip(ws, x)) / p["Sw"]
                var = sum(w * (v - mu) ** 2 for w, v in zip(ws, x)) / p["Sw"]
                p["cv"][s] = math.sqrt(var) / max(mu, 0.5)
                if p["n"] >= 8:
                    cvs[s].append(p["cv"][s])
        self.lg_cv = {s: (sorted(v)[len(v) // 2] if v else {"pts": 0.40, "reb": 0.55, "ast": 0.65}[s]) for s, v in cvs.items()}
        self.prof = raw
        self._dvp()
        self._blowout()
        self._spatial()
        self._ww = {}

    def _dvp(self):
        c = self.c
        acc = collections.defaultdict(lambda: [0.0, 0.0])
        for r in self.recs:
            p = self.prof.get(r["id"])
            if not p or p["pos"] == "?":
                continue
            for s in STATS:
                exp = p["rate"][s] * r["min"] * (r["P"] / p["Pbar"])
                if exp < 0.3:
                    continue
                raw = clamp(r[s] / exp, 0.1, 4.0)
                ratio = clamp(1.0 + (raw - 1.0) * c["DVP_AMP"], 0.05, 6.0)
                w = 0.5 ** ((self.asof - r["t"]) / 86400.0 / c["HL_PLAYER"] / 2) * clamp(r["min"] / 25, 0.3, 1.2)
                for key in ((r["opp"], p["pos"], s), (r["opp"], "*", s)):
                    acc[key][0] += w * ratio
                    acc[key][1] += w
        K = c["K_DVP"]
        self.dvp = {k: (v[0] + K) / (v[1] + K) for k, v in acc.items()}
        self.dvp_n = {k: v[1] for k, v in acc.items()}

    def dvp_mult(self, opp, pos, stat, usg):
        if pos == "?" or (opp, pos, stat) not in self.dvp:
            return 1.0, None
        d = self.dvp[(opp, pos, stat)]
        rel = d / self.dvp.get((opp, "*", stat), 1.0) if stat == "pts" else d
        up = self.pri.get(pos, self.pri["?"])["usg"] or 1.0
        dev = (rel - 1) * clamp(usg / up, 0.6, 1.5)
        return 1 + dev, rel

    def _blowout(self):
        xs = {"st": [], "bn": []}
        for r in self.recs:
            p = self.prof.get(r["id"])
            if not p or p["n"] < 5:
                continue
            xs["st" if p["mean_min"] >= 20 else "bn"].append((max(0.0, abs(r["marg"]) - 8) / 10, r["min"] / p["mean_min"] - 1))
        self.bw, self.ex_avg = {}, {}
        for k, v in xs.items():
            sxx = sum(x * x for x, y in v)
            self.bw[k] = sum(x * y for x, y in v) / (sxx + 30.0) if v else 0.0
            self.ex_avg[k] = sum(x for x, y in v) / len(v) if v else 0.0

    @staticmethod
    def _ex_blow(mu, sd):
        s = z = 0.0
        for m in range(-40, 41, 2):
            wgt = math.exp(-0.5 * ((m - mu) / sd) ** 2)
            s += wgt * max(0.0, abs(m) - 8) / 10
            z += wgt
        return s / z

    def _spatial(self):
        c = self.c
        self.pz = collections.defaultdict(lambda: {z: [0, 0] for z in ZN})
        self.dz = collections.defaultdict(lambda: {z: [0, 0] for z in ZN})
        self.lz = {z: [0, 0] for z in ZN}
        self.has_shots = False
        for g in self.L.games:
            if g["t"] >= self.asof or g["n"] is None:
                continue
            rows = self.L.shots(g["n"])
            if not rows:
                continue
            self.has_shots = True
            for r in rows:
                dt = g["a"] if r["t"] == g["h"] else g["h"] if r["t"] == g["a"] else None
                z = r["z"]
                for acc in (self.pz[r["p"]][z], self.lz[z]) + ((self.dz[dt][z],) if dt else ()):
                    acc[0] += 1
                    acc[1] += 1 if r["m"] else 0
        self.lfg = {z: (self.lz[z][1] / self.lz[z][0] if self.lz[z][0] else 0.4) for z in ZN}

    def spatial_mult(self, pid, opp):
        if not self.has_shots or pid not in self.pz:
            return 1.0, None
        c, pz = self.c, self.pz[pid]
        tot = sum(v[0] for v in pz.values())
        if tot < 25:
            return 1.0, None
        base = adj = 0.0
        best = (0.0, None, 1.0)
        for z in ZN:
            a, m = pz[z]
            val = 3 if z in ("c3l", "c3r", "ab3") else 2
            fgz = (m + c["K_ZONE_P"] * self.lfg[z]) / (a + c["K_ZONE_P"])
            da, dm = self.dz[opp][z] if opp in self.dz else (0, 0)
            dmul = ((dm + c["K_ZONE_D"] * self.lfg[z]) / (da + c["K_ZONE_D"])) / self.lfg[z] if self.lfg[z] else 1.0
            v = a / tot * val * fgz
            base += v
            adj += v * dmul
            if v * (dmul - 1) > best[0]:
                best = (v * (dmul - 1), z, dmul)
        if base <= 0:
            return 1.0, None
        m = clamp(adj / base, 0.88, 1.12)
        return m, (best[1], best[2])

    def inj_level(self, tm, name):
        tk = sorted([w for w in re.split(r"[^a-z0-9]+", norm(name)) if len(w) >= 3], key=len, reverse=True)
        if not tk:
            return 0
        for s in self.L.inj.get(tm) or []:
            if tk[0] in norm(str(s).split(":")[0]):
                st = norm(s)
                return 2 if re.search(r"\bvan\b|operacij|ahil|acl|\bout\b", st) else 1.5 if re.search(r"sumnjiv|doubt|questionable", st) else 0
        return 0

    def with_without(self, tm, oid, pid):
        key = (tm, oid, pid)
        if key in self._ww:
            return self._ww[key]
        L = self.L
        first = next((i for i in L.tmgames[tm] if oid in L.active.get((i, tm), ())), None)
        res = None
        if first is not None:
            idx = [i for i in L.tmgames[tm] if i >= first and L.games[i]["t"] < self.asof]
            ab = {i for i in idx if oid not in L.active[(i, tm)]}
            rs = [r for r in self.byp.get(pid, []) if r["tm"] == tm and r["i"] in set(idx)]
            ra, rw = [r for r in rs if r["i"] in ab], [r for r in rs if r["i"] not in ab]
            if len(ra) >= 3 and len(rw) >= 3:
                K = 4.0
                n = len(ra)
                ma, mw = sum(r["min"] for r in ra) / n, sum(r["min"] for r in rw) / len(rw)
                rr = {}
                for s in STATS:
                    a = sum(r[s] for r in ra) / max(1e-9, sum(r["min"] for r in ra))
                    b = sum(r[s] for r in rw) / max(1e-9, sum(r["min"] for r in rw))
                    rr[s] = (n * (a / b if b > 0 else 1.0) + K) / (n + K)
                res = ((n * (ma / mw) + K) / (n + K), rr, n)
        self._ww[key] = res
        return res

    def inj_effects(self, tm, pid, outs):
        p = self.prof[pid]
        mmult, add, rm, notes = 1.0, 0.0, {s: 1.0 for s in STATS}, []
        team = [x for x in self.prof.values() if x["tm"] == tm and x["mean_min"] >= 8 and x["n"] >= 3]
        for oid, q in outs:
            o = self.prof.get(oid)
            if not o or oid == pid or o["mean_min"] < 12:
                continue
            ww = self.with_without(tm, oid, pid)
            if ww:
                mmult *= 1 + q * (ww[0] - 1)
                for s in STATS:
                    rm[s] *= 1 + q * (ww[1][s] - 1)
                notes.append(f"{o['name']} van ({ww[2]} utakmica bez njega: min x{ww[0]:.2f})")
            else:
                elig = [x for x in team if x["id"] != oid and x["id"] not in {z for z, _ in outs}]
                wsum = sum((1.5 if x["pos"] == o["pos"] else 1.0) * x["mean_min"] for x in elig) or 1.0
                wt = (1.5 if p["pos"] == o["pos"] else 1.0) * p["mean_min"]
                share = wt / wsum if wsum > 0 else 0.0
                lost_min = o["mean_min"] * q
                mmult *= 1.0 + (lost_min * share) / max(5.0, p["mean_min"])
                notes.append(f"{o['name']} van (preraspodela minuta)")
        return {"mmult": mmult, "add": add, "rm": rm, "notes": notes}

    def project(self, pid, tm, opp, home, mp, margin_sd, outs=()):
        p = self.prof.get(pid)
        c = self.c
        if not p or p["n"] < c["MIN_GAMES_PROP"] or p["mean_min"] < 8:
            return None
        om = p["mean_min"]
        mu_margin = mp["margin"] if isinstance(mp, dict) else mp
        ex_b = self._ex_blow(mu_margin, margin_sd)
        bw_beta = self.bw["st"] if om >= 20 else self.bw["bn"]
        mins = clamp(om * (1.0 + bw_beta * ex_b), 5.0, c["MAXMIN"])
        ie = self.inj_effects(tm, pid, outs)
        adj_mins = clamp(mins * ie["mmult"], 5.0, c["MAXMIN"])
        
        stats_dict = {}
        for s in STATS:
            base_rate = p["rate"][s]
            if s == "pts":
                # Blend the stabilized season rate with recent per-minute scoring form.
                # This avoids using stale full-season rates when a player's role/scoring changes.
                recent = self.byp.get(pid, [])[-10:]
                recent_min = sum(r["min"] for r in recent)
                if recent_min > 0:
                    recent_rate = sum(r["pts"] for r in recent) / recent_min
                    base_rate = 0.5 * base_rate + 0.5 * recent_rate
            naive_val = base_rate * adj_mins
            mult = ie["rm"][s]
            dm, dvp_rel = self.dvp_mult(opp, p["pos"], s, p["usg"])
            spat_val = 1.0
            zone_info = None
            if s == "pts":
                sm, z_info = self.spatial_mult(pid, opp)
                mult *= dm * sm
                spat_val = sm
                zone_info = {"z": z_info[0], "m": z_info[1]} if z_info else None
                mult = 1.0 + (mult - 1.0) * c["MULT_DAMP"]["pts"]
                proj_raw = naive_val * mult
                proj = (1.0 - c["SHRINK_PTS"]) * proj_raw + c["SHRINK_PTS"] * naive_val
            else:
                mult *= dm
                mult = 1.0 + (mult - 1.0) * c["MULT_DAMP"][s]
                proj = naive_val * mult
                
            sd = max(0.5, p["cv"][s] * max(proj, 0.5))
            stats_dict[s] = {
                "mu": round(proj, 2), "sd": round(sd, 2), "dvp": round(dm, 2),
                "dvp_rel": round(dvp_rel, 2) if dvp_rel is not None else 1.0,
                "spat": round(spat_val, 2), "zone": zone_info, "inj": ie["notes"]
            }
        
        return {
            "min": round(adj_mins, 1), "base_min": round(om, 1), "usg": round(p["usg"], 1),
            "pace": round(mp.get("P", 75.0) if isinstance(mp, dict) else 75.0, 1),
            "n": p["n"], "pos": p["pos"], "inj_mult": round(ie["mmult"], 3), "stats": stats_dict
        }

    def match_player(self, name, tm):
        tn = norm(name)
        tk = {w for w in re.split(r"[^a-z0-9]+", tn) if len(w) >= 2}
        best, sc = None, 0
        for p in self.prof.values():
            if p["tm"] != tm:
                continue
            pn = norm(p["name"])
            if pn == tn:
                return p
            pk = {w for w in re.split(r"[^a-z0-9]+", pn) if len(w) >= 2}
            s = len(tk & pk)
            lh = any(len(w) >= 5 and w in pk for w in tk)
            if (s >= 2 or (s >= 1 and lh)) and s > sc:
                best, sc = p, s
        return best


def _devig2(a, b):
    ia, ib = 1 / a, 1 / b
    return ia / (ia + ib)


def _tip(k, txt, p_model, p_final, odds, ev, push=0.0, why=None, extra=None):
    d = {"k": k, "txt": txt, "ev": round(ev, 4), "p": round(p_final, 4), "pm": round(p_model, 4), "odds": odds, "kelly": kelly(p_final, odds),
         "why": why or []}
    if ev > CFG["SUSPECT_EV"]:
        d["warn"] = True
    if extra:
        d.update(extra)
    return d


def match_tips(ev, mp, M, hn, an, cfg):
    tips = []
    W = cfg["W_MKT_MATCH"]
    sm, st = M.sd_m, M.sd_t
    b0 = (ev.get("b") or [{}])[0]
    why = [f"Model {hn[0]} {mp['pts_h']:.1f} : {mp['pts_a']:.1f} {hn[1]} (tempo {mp['P']:.1f}, razlika {mp['margin']:+.1f})"]
    if abs(mp["rest_pts"]) >= 0.5:
        why.append(f"Odmor (iz podataka) {mp['rest_pts']:+.1f} p.")
    h2h = b0.get("h2h")
    if h2h and h2h[0] and h2h[1]:
        pm = phi(mp["margin"] / sm)
        pk = _devig2(h2h[0], h2h[1])
        for t, p_m, p_k, od in ((hn[0], pm, pk, h2h[0]), (hn[1], 1 - pm, 1 - pk, h2h[1])):
            p = (1 - W) * p_m + W * p_k
            e = ev_of(p, 0, od)
            if e >= cfg["MIN_EV"]:
                tips.append(_tip("h2h", f"{t} pobeda (1/2)", p_m, p, od, e, why=why, extra={"team": "h" if t == hn[0] else "a"}))
    best = {}
    for b in ev.get("b") or []:
        sp = b.get("sp")
        if sp and sp[0] is not None and sp[1] and sp[2]:
            L, oh, oa = sp
            pm = 1 - phi((-L - mp["margin"]) / sm)
            pk = _devig2(oh, oa)
            for side, t, ln, p_m, p_k, od in (("h", hn[0], L, pm, pk, oh), ("a", hn[1], -L, 1 - pm, 1 - pk, oa)):
                p = (1 - W) * p_m + W * p_k
                e = ev_of(p, 0, od)
                if od <= cfg["MAX_ODDS_ALT"] and e >= cfg["MIN_EV"] and (("sp", side) not in best or e > best[("sp", side)]["ev"]):
                    best[("sp", side)] = _tip("sp", f"{t} {ln:+g} (Hendikep)", p_m, p, od, e, why=why, extra={"line": ln, "team": side})
        tt = b.get("tt")
        if tt and tt[0] is not None and tt[1] and tt[2]:
            L, oo, ou = tt
            pm = 1 - phi((L - mp["total"]) / st)
            pk = _devig2(oo, ou)
            for side, nm_, p_m, p_k, od in (("o", "OVER", pm, pk, oo), ("u", "UNDER", 1 - pm, 1 - pk, ou)):
                p = (1 - W) * p_m + W * p_k
                e = ev_of(p, 0, od)
                if od <= cfg["MAX_ODDS_ALT"] and e >= cfg["MIN_EV"] and (("tt", side) not in best or e > best[("tt", side)]["ev"]):
                    best[("tt", side)] = _tip("tt", f"{nm_} {L:g} poena (ukupno)", p_m, p, od, e,
                                              why=why + [f"Projektovani zbir {mp['total']:.1f} (sd {st:.1f})"], extra={"line": L, "side": nm_})
    tips += list(best.values())
    return tips


def annotate_events(events, lg, root=".", cfg=None, log=print, asof=None, league=None):
    cfg = cfg or CFG
    L = league or League(lg, root)
    dg = collections.Counter()
    if not L.games:
        dg["nema_site_json"] += 1
        return dg
    asof = asof or time.time()
    M = L.fit(asof)
    if not M.ok:
        dg["premalo_utakmica"] += 1
        return dg
    PR = Props(L, M, asof)
    dg["utakmica_u_modelu"] = M.n
    dg["sut_podaci"] = int(PR.has_shots)
    for ev in events:
        if ev.get("lg") != lg:
            continue
        h, a = L.team_code(ev.get("h")), L.team_code(ev.get("a"))
        if not h or not a or h == a:
            dg["tim_nije_prepoznat"] += 1
            continue
        an = ev.get("an") or {}
        rh = an.get("rest_h") or [None, None]
        rd = (rh[0] - rh[1]) / 24.0 if rh[0] is not None and rh[1] is not None else 0.0
        mp = M.predict(h, a, rd)
        if not mp:
            continue
        ti = M.ti
        ffe, ffd = M.ff_edge(h, a)
        ev["q"] = {"h": h, "a": a, "P": round(mp["P"], 1), "pts": [round(mp["pts_h"], 1), round(mp["pts_a"], 1)], "margin": round(mp["margin"], 1),
                   "total": round(mp["total"], 1), "sd_m": round(M.sd_m, 1), "sd_t": round(M.sd_t, 1),
                   "ortg": [ti[h]["ortg"], ti[a]["ortg"]], "drtg": [ti[h]["drtg"], ti[a]["drtg"]], "net": [ti[h]["net"], ti[a]["net"]],
                   "ortg_ha": [ti[h]["ortg_h"], ti[a]["ortg_h"]], "drtg_ha": [ti[h]["drtg_h"], ti[a]["drtg_a"]], "pace": [ti[h]["pace"], ti[a]["pace"]],
                   "ff": {"h": {k: ti[h][k] for k in ("efg", "tov", "orb", "ftr", "efg_d", "tov_d", "orb_d", "ftr_d")},
                          "a": {k: ti[a][k] for k in ("efg", "tov", "orb", "ftr", "efg_d", "tov_d", "orb_d", "ftr_d")}, "edge": round(ffe, 2)},
                   "rest_pts": round(mp["rest_pts"], 2), "hca": round(M.h * mp["P"] / 100, 2), "n": M.n}
        tips = match_tips(ev, mp, M, (ev["h"], ev["a"]), an, cfg)
        outs = {}
        for tm in (h, a):
            for pl in PR.prof.values():
                if pl["tm"] == tm and pl["mean_min"] >= 12:
                    lv = PR.inj_level(tm, pl["name"])
                    if lv >= 1.5:
                        outs.setdefault(tm, []).append((pl["id"], 1.0 if lv >= 2 else 0.5))
        for pp in ev.get("player_props") or []:
            pl = PR.match_player(pp["p"], h) or PR.match_player(pp["p"], a)
            if not pl:
                dg["igrac_nije_prepoznat"] += 1
                continue
            tm = pl["tm"]
            home = tm == h
            if any(o[0] == pl["id"] and o[1] >= 1 for o in outs.get(tm, [])):
                continue
            pr = PR.project(pl["id"], tm, a if home else h, home, mp, M.sd_m, [o for o in outs.get(tm, []) if o[0] != pl["id"]])
            st = pp.get("s")
            if not pr or st not in pr["stats"]:
                continue
            S = pr["stats"][st]
            kind = "norm" if st == "pts" else "count"
            po, pu, ps = over_under(pp["l"], S["mu"], S["sd"], kind)
            pk = _devig2(pp["o"], pp["u"])
            Wp = cfg["W_MKT_PROP"]
            fo, fu = (1 - Wp) * po + Wp * pk, (1 - Wp) * pu + Wp * (1 - pk)
            eo, eu = ev_of(fo, ps, pp["o"]), ev_of(fu, ps, pp["u"])
            side, e, od, p_m, p_f = ("OVER", eo, pp["o"], po, fo) if eo >= eu else ("UNDER", eu, pp["u"], pu, fu)
            pp["q"] = {"proj": S["mu"], "sd": S["sd"], "po": round(po, 3), "pu": round(pu, 3), "ev_o": round(eo, 4), "ev_u": round(eu, 4),
                       "min": pr["min"], "base_min": pr["base_min"], "usg": pr["usg"], "dvp": S["dvp"], "dvp_rel": S["dvp_rel"], "pace": pr["pace"],
                       "spat": S["spat"], "inj": pr["inj_mult"], "inj_notes": S["inj"], "n": pr["n"], "pos": pr["pos"], "side": side if e >= cfg["MIN_EV"] else None,
                       "team": "h" if home else "a"}
            if pp.get("main") and e >= cfg["MIN_EV"] or (cfg["USE_ALT_PROPS"] and e >= cfg["MIN_EV"] and od <= cfg["MAX_ODDS_ALT"]):
                why = [f"Proj {S['mu']:.1f} (sd {S['sd']:.1f}) vs linija {pp['l']}", f"Min {pr['min']:.0f} (sezona {pr['base_min']:.0f}), USG {pr['usg']:.0f}%"]
                if S["dvp_rel"] is not None and abs(S["dvp_rel"] - 1) >= 0.03:
                    why.append(f"Protivnik vs {pr['pos']}: {S['dvp_rel'] * 100 - 100:+.0f}%")
                if S.get("zone") and st == "pts" and abs(S["spat"] - 1) >= 0.02:
                    why.append(f"Zona {S['zone']['z']}: odbrana {S['zone']['m'] * 100 - 100:+.0f}% vs liga")
                why += S["inj"]
                tips.append(_tip("prop", f"{pp['p']} - {side} {pp['l']} {SLAB[st]}", p_m, p_f, od, e, why=why,
                                 extra={"proj": S["mu"], "line": pp["l"], "side": side, "stat": st, "pid": pl["id"], "pname": pp["p"]}))
        tips.sort(key=lambda t: -t["ev"])
        ev["tips"] = tips
        dg["mecevi_sa_modelom"] += 1
        dg["tipova"] += len(tips)
    return dg