#!/usr/bin/env python3
"""LIVE GAMES: Superbet in-play feed -> model -> data/live.json (spreman za prikaz, sajt ga samo crta).

  py fb_live.py --probe                 # nadje koji 'index' u feedu daje mecove uzivo (isprobava nekoliko imena)
  py fb_live.py --dump                  # sirov izgled jednog live meca (rezultat, minut, statistika, trzista), da namestimo parser
  py fb_live.py --loop 45 --serve       # svakih ~45 s osvezi; --serve otvara http://localhost:8765/live.json za live.js (bez git push-a)
  py fb_live.py --loop 45 --serve --push-every 300    # + na svakih 5 min gurne data/live.json u repo (za telefon)
Isti javni feed i ista pravila kao fetch_superbet.py: jedan zahtev po prolazu, staje na 403/429, bez zaobilazenja.

Sta radi po meču: (1) rezultat, minut, statistika ako je feed daje; (2) predmeč ocekivani golovi iz data/*/predictions.json (context.lam),
a za kosarku linije iz data/odds_superbet.json; (3) preracuna sta je ostalo da se igra (rezultat, minut, crveni kartoni, tempo iz statistike),
(4) poredi sa kvotama uzivo, bira tip sa najvecim EV i pise 'zasto'. Svi koeficijenti u PARAMS su pretpostavke, nekalibrisani."""
import argparse, collections, datetime, difflib, glob, http.server, json, math, os, random, re, sys, threading, time, unicodedata, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_superbet import get, events_in, num, line_of, event_id, URL, push, DETAIL
try: from fetch_superbet import tour_text
except ImportError: tour_text = lambda ev: ""

LIVE = os.path.join("data", "live.json")
HIST = os.path.join("data", "live_history.json")
FEED = URL.replace("sports=4", "sports={sp}").replace("index=active-prematch", "index={idx}")
PARAMS = dict(w=0.30, min_ev=0.04, o_min=1.25, o_max=8.0, lead_def=0.06, trail_att=0.10, red_own=0.78, red_opp=1.12, tempo=(0.75, 1.30))
STAT = [("shots_on", r"u okvir|on target|na gol"), ("shots", r"udarc|sut|shots"), ("poss", r"posed|possession"), ("corners", r"korner|corner"),
        ("red", r"crven|red card"), ("yellow", r"zut|yellow"), ("danger", r"opasn|dangerous"), ("attacks", r"napad|attack"), ("xg", r"\bxg\b|expected")]


def low(s): return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")
def nrm(s): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", low(s))).strip()
def jl(p, d):
    try: return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError): return d


# ---------------------------------------------------------------- izvlacenje stanja meca iz sirovog dogadjaja (tolerantno, nema garancije za imena polja)
def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("markets", "results", "superbets", "priceboosts"): continue
            yield path + "." + k, k, v
            yield from walk(v, path + "." + k)
    elif isinstance(o, list):
        for i, v in enumerate(o[:40]): yield from walk(v, f"{path}[{i}]")


def find_score(ev):
    st = ev.get("inplay_stats") or {}
    h, a = num(st.get("home_team_score")), num(st.get("away_team_score"))
    if h is not None and a is not None: return [int(h), int(a)]
    for _, k, v in walk(ev):
        if not re.search(r"score|result|rezultat", k, re.I): continue
        if isinstance(v, dict):
            h, a = (num(v.get(x)) for x in ("home", "h")) if "home" in v or "h" in v else (None, None)
            a = num(v.get("away", v.get("a"))) if h is not None else None
            if h is not None and a is not None: return [int(h), int(a)]
        if isinstance(v, str):
            m = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", v)
            if m: return [int(m[1]), int(m[2])]
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v): return [int(v[0]), int(v[1])]
    return None


def find_minute(ev):
    st, md = ev.get("inplay_stats") or {}, ev.get("inplay_stats_metadata") or {}
    m = re.match(r"^\s*(\d+)(?:\s*\+\s*(\d+))?", str(st.get("minutes", "")))
    if m: return int(m[1]) + int(m[2] or 0)
    lab = str(md.get("event_status_label", "")).upper()
    if lab == "HT": return 45
    for _, k, v in walk(ev):
        if re.search(r"minute|elapsed|match_?time|clock|timer|time_?played", k, re.I) and not isinstance(v, (dict, list)):
            m = re.match(r"^\D*(\d+)(?::(\d+))?", str(v))
            if m: return int(m[1]) + (1 if m[2] and int(m[2]) > 0 and ":" in str(v) and int(m[1]) < 90 else 0)
    return None


INP = {"yellow_cards": "yellow", "red_cards": "red", "corners": "corners", "shots_on_target": "shots_on", "shots_on_goal": "shots_on",
       "shots": "shots", "possession": "poss", "dangerous_attacks": "danger", "attacks": "attacks", "xg": "xg"}


def find_stats(ev):
    out = {}
    st = ev.get("inplay_stats") or {}                       # npr. home_team_corners / away_team_corners
    for k, v in st.items():
        m = re.match(r"^home_team_(\w+)$", k)
        if m and m[1] in INP:
            h, a = num(v), num(st.get("away_team_" + m[1]))
            if h is not None and a is not None: out[INP[m[1]]] = [h, a]
    def put(name, h, a):
        for key, rx in STAT:
            if re.search(rx, low(name)) and key not in out:
                hv, av = num(str(h).replace("%", "")), num(str(a).replace("%", ""))
                if hv is not None and av is not None: out[key] = [hv, av]
                return
    for _, k, v in walk(ev):
        if not re.search(r"inplaystats|in_play_stats|statistic", k, re.I) or re.search(r"metadata", k, re.I): continue
        items = v.items() if isinstance(v, dict) else enumerate(v if isinstance(v, list) else [])
        for key, it in items:
            if isinstance(it, dict):
                nm = next((it[x] for x in ("name", "stat", "type", "key", "label") if isinstance(it.get(x), str)), key if isinstance(key, str) else "")
                vals = it.get("values") if isinstance(it.get("values"), list) and len(it["values"]) == 2 else [it.get("home", it.get("value1")), it.get("away", it.get("value2"))]
                put(nm, *vals)
    return out


# ---------------------------------------------------------------- kvote uzivo
def odds_of(ev):
    x12, ou, bt = {}, collections.defaultdict(dict), {}
    h2, tt = {}, collections.defaultdict(dict)
    for mk in ev.get("markets") or []:
        nm = low(mk.get("name", "")).strip()
        kind = "btts" if nm == "oba tima daju gol" else "x12" if nm in ("konacan ishod", "pobednik") else "ou" if nm in ("ukupno golova", "ukupno poena") else None
        if not kind: continue
        groups = collections.defaultdict(dict)
        for o in mk.get("odds") or []:
            p = num(o.get("price")); md = o.get("metadata") or {}
            if p is None or p <= 1.01 or o.get("status", 1) != 1 or o.get("display") is False: continue
            code, name = str(md.get("code", "")).strip().lower(), low(md.get("name", "")).strip()
            if kind == "x12":
                M12 = {"1": "1", "x": "x", "0": "x", "2": "2", "home": "1", "away": "2", "draw": "x", "remi": "x", "nereseno": "x"}
                s = M12.get(code) or M12.get(name) or ("x" if re.search(r"remi|neresen|draw", low(md.get("info", ""))) else None)
                if s: x12[s] = p
            elif kind == "btts":
                s = "y" if code in ("gg", "da", "yes") or name in ("da", "gg") else "n" if code in ("ng", "ne", "no") or name in ("ne", "ng") else None
                if s: bt[s] = p
            else:
                ln = line_of(o, mk.get("name", ""))
                s = "o" if code in ("+", "o", "over") or name.startswith(("vise", "preko")) else "u" if code in ("-", "u", "under") or name.startswith(("manje", "ispod")) else None
                if s and ln is not None: groups[md.get("market_line_uuid") or ln][s] = (p, ln)
        for g in groups.values():
            if "o" in g and "u" in g and (g["o"][1] * 2) % 1 == 0: ou[g["o"][1]] = [g["o"][0], g["u"][0]]
    X = [x12["1"], x12["x"], x12["2"]] if all(k in x12 for k in "1x2") else None
    H = [x12["1"], x12["2"]] if not X and "1" in x12 and "2" in x12 else None
    return {"x12": X, "h2h": H, "ou": sorted([[l, v[0], v[1]] for l, v in ou.items()]), "btts": [bt["y"], bt["n"]] if "y" in bt and "n" in bt else None}


# ---------------------------------------------------------------- fudbalski model: sta je ostalo da se igra
def pmf(l, i): return (math.exp(-l + i * math.log(l) - math.lgamma(i + 1)) if l > 0 else float(i == 0))


def final_dist(gh, ga, rh, ra, N=12):
    P = [[pmf(rh, i) * pmf(ra, j) for j in range(N)] for i in range(N)]
    s = sum(map(sum, P)); x, tot, bt = [0.0] * 3, collections.Counter(), 0.0
    for i in range(N):
        for j in range(N):
            v = P[i][j] / s; fh, fa = gh + i, ga + j
            x[0 if fh > fa else 1 if fh == fa else 2] += v; tot[fh + fa] += v
            if fh and fa: bt += v
    return {"x12": x, "over": lambda L: sum(v for t, v in tot.items() if t > L), "btts": bt}


def fb_rates(lh, la, minute, gh, ga, reds, stats, P=PARAMS):
    rem = max(0.0, 90 - minute)
    if minute >= 88: rem = max(rem, 3.0)                    # nadoknada
    f, ah, aa, notes = rem / 90, 1.0, 1.0, []
    d, k = gh - ga, min(1.0, minute / 60)
    if minute >= 15 and d:                                   # onaj koji gubi napada vise, onaj koji vodi se povlaci
        lead = min(abs(d), 2)
        if d > 0: ah *= 1 - P["lead_def"] * lead * k; aa *= 1 + P["trail_att"] * lead * k
        else: aa *= 1 - P["lead_def"] * lead * k; ah *= 1 + P["trail_att"] * lead * k
    for i, (own, opp) in enumerate(((0, 1), (1, 0))):
        n = reds[i]
        if n:
            if i == 0: ah *= P["red_own"] ** n; aa *= P["red_opp"] ** n
            else: aa *= P["red_own"] ** n; ah *= P["red_opp"] ** n
    xg = stats.get("xg")
    pr = None
    if xg: pr = xg
    elif stats.get("shots_on") or stats.get("shots"):        # grub xG proxy: 0.30 po udarcu u okvir, 0.07 po ostalom
        so, sh = stats.get("shots_on", [0, 0]), stats.get("shots", [0, 0])
        pr = [0.30 * so[i] + 0.07 * max(0, sh[i] - so[i]) for i in range(2)]
    if pr and minute >= 10:
        w = min(0.5, minute / 150)
        for i, lam in enumerate((lh, la)):
            exp_now = lam * minute / 90 + 0.2
            t = max(P["tempo"][0], min(P["tempo"][1], (pr[i] + 0.2) / exp_now))
            if i == 0: ah *= 1 + w * (t - 1)
            else: aa *= 1 + w * (t - 1)
    return lh * f * ah, la * f * aa, rem, notes


def selections(o, D):
    S = []
    if o.get("x12"):
        f = [1 / x for x in o["x12"]]; s = sum(f)
        S += [(k, nm, "1X2", o["x12"][i], D["x12"][i], f[i] / s) for i, (k, nm) in enumerate((("1", "Pobeda domaćina"), ("X", "Nerešeno"), ("2", "Pobeda gosta")))]
    for L, ov, un in o.get("ou") or []:
        po = D["over"](L); a, b = 1 / ov, 1 / un
        S += [(f"O{L:g}", f"Preko {L:g}", f"Golovi {L:g}", ov, po, a / (a + b)), (f"U{L:g}", f"Ispod {L:g}", f"Golovi {L:g}", un, 1 - po, b / (a + b))]
    if o.get("btts"):
        a, b = 1 / o["btts"][0], 1 / o["btts"][1]
        S += [("GG", "GG (oba daju gol)", "GG/NG", o["btts"][0], D["btts"], a / (a + b)), ("NG", "NG (bar jedan ne daje gol)", "GG/NG", o["btts"][1], 1 - D["btts"], b / (a + b))]
    return S


def pick_tip(S, minute, P=PARAMS):
    rows = []
    for k, label, mk, o, pm, pf in S:
        if not (P["o_min"] <= o <= P["o_max"]) or pm < 0.05: continue
        p = P["w"] * pm + (1 - P["w"]) * pf
        rows.append({"key": k, "label": label, "market": mk, "odds": round(o, 2), "p_model": round(pm, 3), "p_market": round(pf, 3), "p": round(p, 3), "ev": round(p * o - 1, 3)})
    rows.sort(key=lambda r: -r["ev"])
    good = [r for r in rows if r["ev"] >= P["min_ev"] and 5 <= minute <= 86]
    return (good[0] if good else None), good[1:3], rows


def history_note(hist, key):
    h = hist or []
    if len(h) < 2: return None
    now, old = h[-1], next((x for x in h if x["t"] >= h[-1]["t"] - 660), h[0])
    a, b = old["o"].get(key), now["o"].get(key)
    if a and b and abs(a - b) / a >= 0.04 and now["t"] - old["t"] >= 120:
        return f"kvota za taj izbor se u poslednjih {round((now['t'] - old['t']) / 60)} min kretala {a:.2f} → {b:.2f}" + (" (tržište sve više veruje u to)" if b < a else " (tržište gubi poverenje)")
    return None


def fb_why(m, h, a, lam, rem_l, st, minute, score, reds, D, tip, ctx, hist):
    why, risk = [], []
    gh, ga = score
    why.append(f"Stanje: {minute}'  {h} {gh}:{ga} {a}. Do kraja se očekuje još {rem_l[0] + rem_l[1]:.2f} gola ({h} {rem_l[0]:.2f}, {a} {rem_l[1]:.2f}), predmeč očekivanje je bilo {lam[0]:.2f} : {lam[1]:.2f}" + (" (opšti prosek, jer za ovaj meč nemamo predmeč model)." if m["prior"] == "generic" else "."))
    sh, so, ps = st.get("shots"), st.get("shots_on"), st.get("poss")
    if sh and so: why.append(f"Udarci (u okvir): {h} {sh[0]:g} ({so[0]:g}), {a} {sh[1]:g} ({so[1]:g})." + (f" Posed {ps[0]:g}%:{ps[1]:g}%." if ps else ""))
    elif ps: why.append(f"Posed {h} {ps[0]:g}%, {a} {ps[1]:g}%.")
    elif not st: why.append("Feed ne daje statistiku uživo za ovaj meč, pa se model oslanja na rezultat, minut i kvote (bez udaraca i poseda).")
    for i, t in enumerate((h, a)):
        if reds[i]: why.append(f"{t} igra sa {reds[i]} igrač{'a' if reds[i] > 1 else ''} manje: njegov napad pada, protivnik dobija prostora.")
    d = gh - ga
    if minute >= 55 and d != 0:
        lead_i = 0 if d > 0 else 1
        lp = ps[lead_i] if ps else None; ls = (sh[lead_i] / max(1, sh[0] + sh[1])) if sh else None
        if (lp is not None and lp <= 42) or (ls is not None and ls <= 0.35):
            why.append(f"{[h, a][lead_i]} čuva prednost i povukao se ('park the bus'): manje poseda/udaraca nego protivnik. To ide u prilog Under-u i većem pritisku na kraju.")
        if minute >= 60:
            why.append(f"Faktor očaja: {[a, h][lead_i]} gubi u {minute}. minutu pa mora da otvara igru, što znači veći pritisak ali i prostor za kontru ({[h, a][lead_i]}). To je pretpostavka modela, ne dokazan efekat.")
            risk.append("Tim koji juri rezultat povećava varijansu: golovi sa obe strane su verovatniji nego što govori ravnomerna raspodela.")
    elif minute >= 60 and d == 0 and sh and sh[0] + sh[1] <= 8: why.append("Nerešeno uz malo udaraca: obe ekipe igraju oprezno, što podržava Under.")
    for who, nm_ in (("h", h), ("a", a)):                                  # predmeč 'crvena zona' iz fb_factors, ako postoji
        ds = (((ctx or {}).get("factors") or {}).get("desperation") or {}).get(who) or {}
        if ds.get("red"): why.append(f"Predmeč: {nm_} je u crvenoj zoni očaja ({ds.get('score', 0):.2f}), dakle nestabilan ishod.")
    if tip:
        why.insert(0, f"Preporuka {tip['label']} @ {tip['odds']}: model {tip['p_model'] * 100:.0f}%, tržište bez marže {tip['p_market'] * 100:.0f}%, mešano {tip['p'] * 100:.0f}%, EV {tip['ev'] * 100:+.1f}%.")
        hn = history_note(hist, tip["key"])
        if hn: why.append("Kretanje kvote: " + hn + ".")
    risk += ["Kvote uzivo se menjaju za sekunde: proveri da je kvota još uvek ista kod kladionice.", "Model je nekalibrisan (koeficijenti su pretpostavke), ne tretiraj EV kao garanciju."]
    return why, risk


# ---------------------------------------------------------------- kosarka (normalna aproksimacija, prior iz predmeč linija)
def phi(z): return 0.5 * math.erfc(-z / math.sqrt(2))


def bb_state(ev, minute_hint=None):
    per = clk = None
    for _, k, v in walk(ev):
        if re.search(r"period|quarter|cetvrt", k, re.I) and not isinstance(v, (dict, list)) and per is None:
            m = re.search(r"\d+", str(v)); per = int(m[0]) if m else None
        if re.search(r"clock|time_?left|remaining", k, re.I) and not isinstance(v, (dict, list)) and clk is None:
            m = re.match(r"^\D*(\d+):(\d+)", str(v)); clk = int(m[1]) + int(m[2]) / 60 if m else None
    if per is None or clk is None: return None
    return per, clk


def bb_model(m, o, prior, per, clk):
    gh, ga = m["score"]; rem = ((4 - per) * 10 + clk) / 40 if per <= 4 else clk / 40
    rem = max(0.0, min(1.0, rem)); el = 1 - rem
    T0, m0 = prior; pace = 1.0
    if el > 0.15 and (gh + ga) > 0: pace = max(0.85, min(1.15, 1 + min(0.5, el) * ((gh + ga) / (T0 * el) - 1)))
    R, mr = T0 * rem * pace, m0 * rem
    sm, st = max(1.0, 12.5 * math.sqrt(rem)), max(1.0, 15.0 * math.sqrt(rem))   # sigma celog meca: razlika ~12.5, ukupno ~15 (procena)
    S = []
    if o.get("h2h"):
        ph = 1 - phi(-((gh - ga) + mr) / sm); a, b = 1 / o["h2h"][0], 1 / o["h2h"][1]
        S += [("1", "Pobeda domaćina", "Pobednik", o["h2h"][0], ph, a / (a + b)), ("2", "Pobeda gosta", "Pobednik", o["h2h"][1], 1 - ph, b / (a + b))]
    for L, ov, un in o.get("ou") or []:
        po = 1 - phi((L - (gh + ga + R)) / st); a, b = 1 / ov, 1 / un
        S += [(f"O{L:g}", f"Preko {L:g}", f"Ukupno {L:g}", ov, po, a / (a + b)), (f"U{L:g}", f"Ispod {L:g}", f"Ukupno {L:g}", un, 1 - po, b / (a + b))]
    return S, R, pace, rem


# ---------------------------------------------------------------- BELA LISTA LIGA (fudbal): samo ove 4 lige prolaze, sve ostalo (Indija, amateri, zene, U-timovi, kupovi) se odbacuje
LEAGUE_ORDER = ["epl", "laliga", "l1", "sa"]
LEAGUE_NAME = {"epl": "Premier League", "laliga": "La Liga", "l1": "Ligue 1", "sa": "Serie A"}
LEAGUE_DIR = {"epl": "epl", "laliga": "laliga", "l1": "l1", "sa": "sa"}
# naziv turnira/kategorije iz feeda: I naziv lige I zemlja moraju da se slazu (da "Indian Premier League" ili "Brazil Serie A" ne prodju)
LEAGUE_TXT = {"epl": (r"premier lig|premijer lig|premier league", r"engles|england"),
              "laliga": (r"la ?liga(?! ?2)|primera", r"spanij|spain|spanjol"),
              "l1": (r"ligue ?1\b|liga 1\b", r"francusk|france|french"),
              "sa": (r"serie ?a\b|serija a\b|seria a\b", r"italij|italy|italian")}
NOT_LEAGUE = re.compile(r"\((ž|z|r|w)\)|\bu ?\d\d\b|\bwomen\b|\bwomens\b|\bladies\b|\bzene\b|\bzenska|\bfemin|\bsegunda\b|\bserie b\b|\bligue 2\b|\bchampionship\b|"
                        r"\bkup\b|\bcup\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bsupercup|\bsuper cup|\bchampions\b|\bliga sampiona|\beuropa\b|\bconference\b|\bprijateljsk|\bfriendl|\bklub(ov)?ski\b|\bamater|\bindij|\bindia", re.I)
_wl = {"t": 0, "known": None, "ids": None, "alias": None}


def _canon_known(n, known, alias):
    n = alias.get(n, n)
    if n in known: return n
    pre = sorted((k for k in known if len(k) >= 4 and (n.startswith(k + " ") or k.startswith(n + " "))), key=len, reverse=True)
    if pre and (len(pre) == 1 or len(pre[0]) > len(pre[1])): return pre[0]
    m = difflib.get_close_matches(n, list(known), 1, 0.85)
    return m[0] if m else None


def wl_load(cli_comp=None):
    """Klubovi svake dozvoljene lige (data/<liga>/statshub_data.json) + aliasi (fb_leagues.py) + rucno mapiranje turnira (data/superbet_comps_fb.json, --comp)."""
    if _wl["known"] is not None and time.time() - _wl["t"] < 300: return
    alias, known = {}, {}
    try:
        from fb_leagues import LEAGUES
    except Exception: LEAGUES = {}
    try:
        from football_poisson import ALIAS as EPL_ALIAS
        base = dict(EPL_ALIAS)
    except Exception: base = {}
    for code in LEAGUE_ORDER:
        al = dict(base) if code == "epl" else {}
        al.update((LEAGUES.get(code) or {}).get("alias") or {})
        al = {nrm(k): nrm(v) for k, v in al.items()}
        alias[code] = al
        kn = set(jl(os.path.join("data", LEAGUE_DIR[code], "statshub_data.json"), {}).get("teams") or {})
        known[code] = {nrm(k) for k in kn} or (set(al.values()))     # nema Statshub fajla: koristi bar kanonska imena iz aliasa
    ids = {}
    for lg, v in jl(os.path.join("data", "superbet_comps_fb.json"), {}).items():
        if lg in LEAGUE_NAME:
            for i in (v if isinstance(v, list) else [v]): ids[str(i).strip().lstrip("t")] = lg
    for x in cli_comp or []:
        lg, _, v = x.partition("=")
        if lg in LEAGUE_NAME:
            for i in v.split(","): ids[i.strip().lstrip("t")] = lg
    _wl.update(t=time.time(), known=known, ids=ids, alias=alias)


def league_of(ev, h, a):
    """Kod lige ('epl','laliga','l1','sa') ako je meč iz bele liste, inace None."""
    if NOT_LEAGUE.search(low(h)) or NOT_LEAGUE.search(low(a)): return None
    f = ev.get("fixture") or {}
    tid = str(f.get("tournament_id", "")).strip()
    if tid in _wl["ids"]: return _wl["ids"][tid]                       # rucno potvrdjen id turnira ima prednost
    txt = ""
    try: txt = tour_text(ev)
    except Exception: pass
    if txt and NOT_LEAGUE.search(txt): return None                      # kup, evropska takmicenja, zene, druga liga...
    for code in LEAGUE_ORDER:                                            # naziv lige + zemlja iz feeda
        lg_rx, co_rx = LEAGUE_TXT[code]
        if txt and re.search(lg_rx, txt) and re.search(co_rx, txt): return code
    hn, an = nrm(h), nrm(a)                                              # klubovi: oba moraju biti iz iste lige
    for code in LEAGUE_ORDER:
        kn, al = _wl["known"][code], _wl["alias"][code]
        if kn and _canon_known(hn, kn, al) and _canon_known(an, kn, al): return code
    return None


# ---------------------------------------------------------------- glavni deo
_priors = {"t": 0, "d": {}}


def priors():
    if time.time() - _priors["t"] < 60: return _priors["d"]
    d = {}
    for p in glob.glob(os.path.join("data", "*", "predictions.json")):
        for c in jl(p, {}).get("context") or []:
            if c.get("lam") and c.get("h") and c.get("a"): d[(nrm(c["h"]), nrm(c["a"]))] = c
    _priors.update(t=time.time(), d=d)
    return d


def bb_priors():
    d = {}
    for e in jl(os.path.join("data", "odds_superbet.json"), {}).get("events", []):
        b = (e.get("b") or [{}])[0]
        if b.get("tt") and b.get("sp"): d[(nrm(e["h"]), nrm(e["a"]))] = (b["tt"][0], -b["sp"][0])
    return d


def est_minute(start):
    try: el = (time.time() - datetime.datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()) / 60
    except Exception: return None
    return int(max(1, min(90, el - 15 if el > 47 else el))) if el > 0 else None


ALLOW_GENERIC = False   # bez predmec modela za tim se NE daje tip (osim sa --allow-generic)


def build(ev, sp, hist_all, pri, bpri, diag):
    f = ev.get("fixture") or {}
    parts = re.split(r"\s*[·•]\s*", f.get("event_name", ""), maxsplit=1)
    if len(parts) != 2: return None
    h, a = parts[0].strip(), parts[1].strip(); eid = str(event_id(ev) or f"{h}|{a}")
    score = find_score(ev); o = odds_of(ev)
    if score is None: diag["bez_rezultata"] += 1
    m = {"id": eid, "sport": "fudbal" if sp == "5" else "kosarka", "h": h, "a": a, "start": f.get("utc_date"), "comp": f"t{f.get('tournament_id', '')}",
         "score": score, "minute": None, "odds": o, "stats": {}, "tip": None, "alts": [], "why": [], "risk": [], "prior": None}
    if score is None: m["why"] = ["Feed za ovaj meč nema prepoznat rezultat uživo (pokreni --dump)."]; return m
    hist = hist_all.setdefault(eid, [])
    snap = {"t": int(time.time()), "o": {}}
    if o["x12"]: snap["o"].update({"1": o["x12"][0], "X": o["x12"][1], "2": o["x12"][2]})
    for L, ov, un in o["ou"]: snap["o"].update({f"O{L:g}": ov, f"U{L:g}": un})
    if o["btts"]: snap["o"].update({"GG": o["btts"][0], "NG": o["btts"][1]})
    hist.append(snap); del hist[:-80]
    if sp == "5":
        st = find_stats(ev); m["stats"] = st
        minute = find_minute(ev); est = minute is None
        minute = minute if minute is not None else est_minute(m["start"] or "")
        if minute is None: m["why"] = ["Nema minuta meča u feedu."]; return m
        m["minute"] = minute
        key = (nrm(h), nrm(a)); ctx = pri.get(key)
        if not ctx:
            near = difflib.get_close_matches(" | ".join(key), [" | ".join(k) for k in pri], 1, 0.85); ctx = pri.get(tuple(near[0].split(" | "))) if near else None
        lam = tuple(ctx["lam"]) if ctx else (1.45, 1.15); m["prior"] = "model" if ctx else "generic"
        if not ctx and not ALLOW_GENERIC:
            diag["bez_modela"] += 1
            m["why"] = ["Nema predmeč modela za ovaj meč (liga ili tim nisu u tvojim modelima), pa se ne daje tip: bez znanja o timovima 'prosečan' model samo izmišlja razliku u odnosu na kvote."]
            return m
        reds = [int(st.get("red", [0, 0])[0]), int(st.get("red", [0, 0])[1])]
        rh, ra, rem, _ = fb_rates(lam[0], lam[1], minute, score[0], score[1], reds, st)
        D = final_dist(score[0], score[1], rh, ra)
        S = selections(o, D); tip, alts, _all = pick_tip(S, minute)
        m["model"] = {"lam_pre": [round(lam[0], 2), round(lam[1], 2)], "lam_rem": [round(rh, 2), round(ra, 2)], "x12": [round(x, 3) for x in D["x12"]], "btts": round(D["btts"], 3)}
        m["tip"], m["alts"] = tip, alts
        m["why"], m["risk"] = fb_why(m, h, a, lam, (rh, ra), st, minute, score, reds, D, tip, ctx, hist)
        if est: m["risk"].insert(0, "Minut je procenjen iz početka meča (feed ga ne daje), pa je preciznost manja.")
    else:
        bs = bb_state(ev); pr = bpri.get((nrm(h), nrm(a)))
        if not bs or not pr: m["why"] = ["Za košarku treba period/sat iz feeda i predmeč linije (data/odds_superbet.json); jedno od toga fali."]; return m
        S, R, pace, rem = bb_model(m, o, pr, *bs); tip, alts, _ = pick_tip(S, 5)
        m["minute"] = f"{bs[0]}. period {int(bs[1])}:{int(bs[1] % 1 * 60):02d}"; m["prior"] = "market"; m["tip"], m["alts"] = tip, alts
        m["why"] = [f"Stanje: {score[0]}:{score[1]}, {m['minute']}. Predmeč linija: ukupno {pr[0]:g}, razlika {pr[1]:+g}. Do kraja se očekuje još {R:.0f} poena (tempo x{pace:.2f})."]
        if tip: m["why"].insert(0, f"Preporuka {tip['label']} @ {tip['odds']}: model {tip['p_model'] * 100:.0f}%, tržište {tip['p_market'] * 100:.0f}%, EV {tip['ev'] * 100:+.1f}%.")
        m["risk"] = ["Model je normalna aproksimacija sa predmeč linijama kao osnovom, nekalibrisan.", "Kvote uzivo se menjaju za sekunde: proveri kvotu pre uplate."]
    return m


def ename(ev): return (ev.get("fixture") or {}).get("event_name", "")


def enrich(ev):
    """Detalj meca (events?events=<id>) ima 'results' i 'inPlayStats' koje lista nema. Vraca bogatiji dogadjaj (ili isti ako detalj nije dostupan)."""
    eid = event_id(ev)
    if eid in (None, ""): return ev
    time.sleep(random.uniform(1.5, 3))
    try: d = events_in(get(DETAIL.format(id=eid)))
    except urllib.error.HTTPError as ex:
        if ex.code in (403, 429): raise
        return ev
    except Exception: return ev
    if not d: return ev
    d = d[0]
    new = dict(ev)
    for k, v in d.items():
        if k == "markets":
            if len(v or []) > len(ev.get("markets") or []): new[k] = v
        elif k != "fixture": new[k] = v
    return new


def once(a):
    global ALLOW_GENERIC
    ALLOW_GENERIC = a.allow_generic
    now = datetime.datetime.now(datetime.timezone.utc)
    s, e = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z"), (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    hist, pri, bpri, diag, out, dropped = jl(HIST, {}), priors(), bb_priors(), collections.Counter(), [], []
    wl_load(a.comp)
    for sp in a.sports.split(","):
        try: evs = events_in(get(FEED.format(s=s, e=e, sp=sp, idx=a.index)))
        except urllib.error.HTTPError as ex: sys.exit(feed_err(ex, a.index))
        if a.match: evs = [x for x in evs if a.match.lower() in ename(x).lower()]
        diag["u_feedu"] += len(evs); nd = 0
        for ev in evs:
            if a.detail and nd < a.detail:
                try: ev = enrich(ev); nd += 1
                except urllib.error.HTTPError as ex: sys.exit(feed_err(ex, a.index))
            lg = "kosarka"
            if sp == "5":
                parts = re.split(r"\s*[·•]\s*", ename(ev), maxsplit=1)
                lg = league_of(ev, *parts) if len(parts) == 2 and not a.all_leagues else ("sve" if len(parts) == 2 else None)
                if lg is None:
                    diag["odbaceno_van_liste"] += 1
                    if len(dropped) < 400: dropped.append((ename(ev), (ev.get("fixture") or {}).get("tournament_id", ""), tour_text(ev)[:60]))
                    continue
            r = build(ev, sp, hist, pri, bpri, diag)
            if r: r["league"] = lg; out.append(r)
    keep = {m["id"] for m in out}
    for k in [k for k in hist if k not in keep]: del hist[k]
    os.makedirs("data", exist_ok=True)
    for p, o in ((LIVE, {"updated": int(time.time()), "source": "superbet.rs", "index": a.index, "matches": out}), (HIST, hist)):
        json.dump(o, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":")); os.replace(p + ".tmp", p)
    print(f"{datetime.datetime.now():%H:%M:%S} uzivo: {len(out)} meceva, sa tipom {sum(1 for m in out if m['tip'])}" + (f" | {dict(diag)}" if diag else ""))
    print_grouped(out, dropped, a)


def sort_key(m):
    mi = m["minute"] if isinstance(m["minute"], int) else -1
    return (-mi, str(m.get("start") or ""), m["h"])


def print_grouped(out, dropped, a):
    """Mečevi grupisani po ligama (PL, La Liga, Ligue 1, Serie A, pa ostalo/košarka); unutar lige po minutu, najkasniji prvi."""
    groups = collections.OrderedDict((c, []) for c in LEAGUE_ORDER)
    other = []
    for m in out:
        (groups[m["league"]] if m.get("league") in groups else other).append(m)
    for code, ms in list(groups.items()) + [("sve", [m for m in other if m.get("league") == "sve"]), ("kosarka", [m for m in other if m.get("league") == "kosarka"])]:
        if not ms: continue
        title = LEAGUE_NAME.get(code, "Fudbal (sve lige)" if code == "sve" else "Košarka")
        print(f"\n=== {title} ({len(ms)}) ===")
        for m in sorted(ms, key=sort_key):
            mn = f"{m['minute']}'" if isinstance(m["minute"], int) else (m["minute"] or "?")
            tip = f"{m['tip']['label']} @ {m['tip']['odds']} EV {m['tip']['ev']*100:+.1f}%" if m["tip"] else "bez tipa"
            sc = f"{m['score'][0]}:{m['score'][1]}" if m["score"] else "-:-"
            print(f"  {m['h']} - {m['a']} | {mn} | {sc} | {tip}")
    if dropped:
        print(f"\nodbaceno van bele liste: {len(dropped)}" + ("" if a.show_dropped else "  (--show-dropped prikazuje nazive i tournament_id)"))
        if a.show_dropped:
            for n_, t_, tx in dropped[:60]: print(f"    {n_} | tournament_id={t_} | {tx}")
            print("    ako je neki od ovih ipak PL/La Liga/Ligue 1/Serie A: dodaj id u data/superbet_comps_fb.json, npr. {\"epl\": [123]}  ili  --comp epl=123")


def http_body(ex):
    try: return ex.read().decode("utf-8", "replace").strip().replace("\n", " ")[:200]
    except Exception: return ""


def feed_err(ex, idx):
    """403/429 = feed nas blokira (STANI). 400/404 = taj 'index' nije validan, to NIJE blokada."""
    if ex.code in (403, 429): return f"HTTP {ex.code}: feed odbija zahtev. Ne zaobilazim; pokusaj kasnije."
    return (f"HTTP {ex.code} za index='{idx}': to NIJE blokada, nego ime indexa koje feed ne poznaje (--index nije ime tima ni lige). "
            f"Pokreni 'py fb_live.py --probe' da nadjemo ispravan index. Odgovor feeda: {http_body(ex)}")


def probe(a):
    now = datetime.datetime.now(datetime.timezone.utc)
    s, e = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z"), (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    sp = a.sports.split(",")[0]
    found = []
    for idx in ("active-inplay", "active-live", "inplay", "live", "in-play", "active", "all", "active-all", "active-prematch", "(bez index parametra)"):
        url = re.sub(r"&index=[^&]*", "", FEED.format(s=s, e=e, sp=sp, idx="x")) if idx.startswith("(") else FEED.format(s=s, e=e, sp=sp, idx=idx)
        try: evs = events_in(get(url))
        except urllib.error.HTTPError as ex:
            if ex.code in (403, 429): print(f"index={idx}: " + feed_err(ex, idx) + " STAJEM."); return
            print(f"index={idx:<22} HTTP {ex.code} (nevazece ime, nije blokada) {http_body(ex)[:150]}"); time.sleep(random.uniform(2, 3)); continue
        except Exception as ex: print(f"index={idx}: {ex}"); continue
        try: live = sum(1 for x in evs if find_score(x))
        except Exception: live = "?"
        print(f"index={idx:<22} dogadjaja {len(evs):<5} sa rezultatom (uzivo) {live:<4} primeri {[(x.get('fixture') or {}).get('event_name') for x in evs[:3]]}")
        if evs: found.append((idx, len(evs), live))
        time.sleep(random.uniform(2, 3))
    names = [x[0] for x in found]
    if "active-live" in names or "live" in names:
        n_ = dict((x[0], x[1]) for x in found).get("active-live") or dict((x[0], x[1]) for x in found).get("live")
        print(f"\nINDEX ZA UZIVO: 'active-live' (isto i 'live'): {n_} mečeva u toku. Rezultat i minut NISU u listi nego u detalju meča: "
              f"pokreni  py fb_live.py --index active-live --dump  (dodaj --match Arouca da izabereš meč).")
        return
    liv = [x for x in found if isinstance(x[2], int) and x[2] > 0]
    if liv:
        best = max(liv, key=lambda x: (x[2], x[1]))
        print(f"\nNajbolji kandidat za uzivo: --index {best[0]}  (pokreni: py fb_live.py --index {best[0]} --dump)")
    elif found:
        print("\nNijedan index ne vraca mecove sa rezultatom (uzivo). Ako trenutno nema utakmica u toku, pokreni --probe dok traje neki meč (npr. vikendom popodne). Posalji mi ceo ispis.")
    else:
        print("\nNijedan index nije dao dogadjaje. Posalji mi ceo ovaj ispis.")


def dump(a):
    now = datetime.datetime.now(datetime.timezone.utc)
    s, e = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z"), (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    try: evs = events_in(get(FEED.format(s=s, e=e, sp=a.sports.split(",")[0], idx=a.index)))
    except urllib.error.HTTPError as ex: sys.exit(feed_err(ex, a.index))
    if not evs: sys.exit("Nema dogadjaja u tom indexu.")
    print(f"index={a.index}: {len(evs)} dogadjaja. Prvih 10:", [ename(x) for x in evs[:10]])
    pick = [x for x in evs if a.match.lower() in ename(x).lower()] if a.match else evs
    if not pick: sys.exit(f"Nijedan dogadjaj ne sadrzi '{a.match}'.")
    ev = pick[0]
    print("\nIZABRAN:", ename(ev), "| kljucevi u listi:", list(ev))
    full = enrich(ev)
    print("Kljucevi posle detalja:", list(full))
    raw = {k: v for k, v in full.items() if k != "markets"}
    print("\nBez trzista:", json.dumps(raw, ensure_ascii=False)[:3500])
    for k in ("results", "inPlayStats", "inPlayStatsMetadata"):
        if k in full: print(f"\n[{k}]", json.dumps(full[k], ensure_ascii=False)[:1800])
    names = [m.get("name") for m in (full.get("markets") or [])]
    print(f"\nTrzista ({len(names)}):", names[:25])
    for mk in (full.get("markets") or []):
        if low(mk.get("name", "")).strip() in ("konacan ishod", "ukupno golova", "oba tima daju gol"):
            print(f"\n[{mk.get('name')}]", [(str((o.get('metadata') or {}).get('code')), (o.get('metadata') or {}).get('name'), (o.get('metadata') or {}).get('specifiers'), o.get('price'), o.get('status')) for o in (mk.get("odds") or [])[:14]])
    print("\nPrepoznato: rezultat", find_score(full), "| minut", find_minute(full), "| statistika", find_stats(full), "| kvote", odds_of(full))
    os.makedirs("data", exist_ok=True)
    json.dump(full, open(os.path.join("data", "live_dump.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print("Snimljeno u data/live_dump.json (posalji mi ovaj ispis).")


def serve(port):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try: b = open(LIVE, "rb").read()
            except OSError: b = b'{"matches":[]}'
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
        def log_message(self, *x): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"live.json se servira na http://localhost:{port}/live.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="active-live", help="ime 'index' parametra feeda (NIJE ime tima ni lige!). Ispravnu vrednost nadji sa --probe")
    ap.add_argument("--sports", default="5,4", help="5 = fudbal, 4 = kosarka")
    ap.add_argument("--loop", type=int, default=0, help="sekundi izmedju prolaza (min 20)"); ap.add_argument("--serve", action="store_true"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--push-every", type=int, default=0, help="na toliko sekundi gurni data/live.json u repo (0 = nikad)")
    ap.add_argument("--probe", action="store_true"); ap.add_argument("--dump", action="store_true")
    ap.add_argument("--allow-generic", action="store_true", help="daj tipove i za mecove bez predmec modela (NE preporucujem: tipovi su nepouzdani)")
    ap.add_argument("--all-leagues", action="store_true", help="iskljuci belu listu liga (prikazi sve fudbalske mecove)")
    ap.add_argument("--show-dropped", action="store_true", help="ispisi koje je mecove bela lista odbacila (naziv + tournament_id)")
    ap.add_argument("--comp", action="append", default=[], help="rucno mapiranje turnira: --comp epl=123 (ponovi za vise; liga: epl, laliga, l1, sa)")
    ap.add_argument("--match", default="", help="samo mecevi ciji naziv sadrzi ovaj tekst (npr. --match Arouca)")
    ap.add_argument("--detail", type=int, default=0, help="OBICNO NIJE POTREBNO (lista vec ima inplay_stats i trzista). Za toliko meceva po prolazu povuci i DETALJ; svaki je jedan zahtev + pauza")
    a = ap.parse_args()
    if a.probe: probe(a); sys.exit()
    if a.dump: dump(a); sys.exit()
    if a.serve: serve(a.port)
    last = 0
    while True:
        try:
            once(a)
            if a.push_every and time.time() - last >= a.push_every: push([LIVE]); last = time.time()
        except SystemExit as e: print(e); break
        except Exception as e: print("greska:", e)
        if not a.loop: break
        time.sleep(max(20, a.loop) * random.uniform(.9, 1.15))
