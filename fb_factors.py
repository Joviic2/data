#!/usr/bin/env python3
"""fb_factors.py - dodatni faktori za football_poisson.py. Svaki faktor vraca multiplikatore ocekivanih golova (domacin, gost) + tekstualne napomene.

 1. Momentum (EMA reziduala): koliko je tim u poslednjih N mecova postizao/primao VISE od onoga sto model ocekuje, sa eksponencijalnim ponderima
    (najnoviji meč najteži). Daje napadacki i odbrambeni momentum.
 2. Raspad odbrane: primljeni golovi iznad ocekivanja (odbrambeni momentum) ili 3 meca zaredom sa 2+ primljena, uz nedostupne defanzivce/golmana.
 3. "Ne diraj hot tim": pobede u zadnja 4 meca + napadacki momentum => Under/NG tipovi na tom mecu se blokiraju (ili im se spusta EV).
 4. Stilski mismatch: pritisak vs slaba izgradnja, posed vs nizak blok, otvorena igra (oba presuju/suteraju) vs zatvorena. Iz Statshub stila (z-skor u ligi).
 5. Sudija: profil iz football-data CSV (Referee, HF/AF fauls, HY/AY/HR/AR kartoni); sudija meca se zadaje u data/<liga>/referees.json ili --ref.
 6. Motivacija: tabela iz rezultata (Statshub) -> opstanak / titula / Evropa / rasterecen; vazi tek kad je odigran veci deo sezone.
 7. Vreme: Open-Meteo prognoza za stadion (kisa, vetar, ucinak prethodnih 24h na teren).
 8. Faktor ocaja (desperation): serija bez pobede + malo bodova u 5 + losa xG/gol razlika, pojacano kad tim mora da osvaja bodove (motivacija).
    Iznad praga (desp_thr) lagano spusta napad tima i podize ono sto primaju; u "crvenoj zoni" dodaje opomenu za cash-out. Losi rezultati uz
    dobru xG razliku se tretiraju kao pech (bez korekcije). OVO JE HIPOTEZA: nema dokaza u repou, testiraj sa fb_desp_test.py pre nego sto poveruješ.

VAZNO: koeficijenti u PARAMS su pocetne, NEKALIBRISANE vrednosti. Samo momentum se moze testirati walk-forward-om (football_poisson.py --backtest K --bt-grid);
stil, sudija, motivacija i vreme nemaju istorijske podatke u repou, pa su to male, ogranicene korekcije (max nekoliko procenata) i ne treba im verovati kao dokazanim."""
import csv, glob, json, math, os, re, time, unicodedata, urllib.request
from datetime import datetime, timezone

PARAMS = dict(
    mom_k=0.25, mom_cap=0.15, def_cap=0.20, mom_rho=0.65, mom_n=5,      # momentum: jacina, granice, pad tezine po meču unazad, broj mečeva
    hot_wins=3, hot_mom=1.02, collapse_mom=1.10, collapse_k=0.06,        # hot tim: >=3 pobede u 4 + napad >= +2%; raspad: odbrambeni momentum >= +10%
    style_k=0.03, mot_k=0.04, ref_k=0.015, wx_k=0.02, late_share=0.27,   # stil, motivacija, sudija, vreme; udeo golova u poslednjih ~20 min
    desp_n=8, desp_thr=0.45, desp_red=0.60, desp_att_k=0.06, desp_conc_k=0.04,   # ocaj: prozor mecova, prag korekcije, prag crvene zone, max -6% napad / +4% primljeno (0 = iskljuceno)
)
ZONES = {"epl": {"cl": 4, "eu": 7, "rel": 3}, "laliga": {"cl": 4, "eu": 7, "rel": 3}, "l1": {"cl": 3, "eu": 6, "rel": 3}, "sa": {"cl": 4, "eu": 7, "rel": 3}}
# priblizne koordinate stadiona (dovoljno za prognozu); drugi klubovi: data/<liga>/stadiums.json  {"kljuc": [lat, lon]}
STADIUMS = {
    "arsenal": (51.555, -0.109), "aston villa": (52.509, -1.885), "bournemouth": (50.735, -1.838), "brentford": (51.491, -0.289), "brighton": (50.862, -0.083),
    "burnley": (53.789, -2.230), "chelsea": (51.482, -0.191), "crystal palace": (51.398, -0.086), "everton": (53.426, -3.004), "fulham": (51.475, -0.222),
    "leeds": (53.778, -1.572), "liverpool": (53.431, -2.961), "man city": (53.483, -2.200), "man united": (53.463, -2.291), "newcastle": (54.976, -1.622),
    "nottm forest": (52.940, -1.133), "sunderland": (54.914, -1.388), "tottenham": (51.604, -0.066), "west ham": (51.539, -0.017), "wolves": (52.590, -2.130),
    "hull": (53.746, -0.368), "coventry": (52.448, -1.496), "ipswich": (52.055, 1.145), "leicester": (52.620, -1.142), "southampton": (50.906, -1.391),
    "sheffield utd": (53.370, -1.471), "middlesbrough": (54.578, -1.217), "norwich": (52.622, 1.309), "west brom": (52.509, -1.964), "stoke": (52.988, -2.175),
    "watford": (51.650, -0.402), "luton": (51.884, -0.431), "cardiff": (51.473, -3.203), "swansea": (51.643, -3.935), "derby": (52.915, -1.447),
    "blackburn": (53.728, -2.489), "preston": (53.772, -2.688), "qpr": (51.509, -0.232), "millwall": (51.486, -0.051), "birmingham": (52.475, -1.868),
    "bristol city": (51.440, -2.620), "charlton": (51.486, 0.036), "oxford": (51.716, -1.208), "portsmouth": (50.796, -1.064), "plymouth": (50.388, -4.151),
    "sheffield weds": (53.411, -1.501), "wrexham": (53.051, -3.004),
    "real madrid": (40.453, -3.688), "barcelona": (41.381, 2.123), "atletico": (40.436, -3.599), "sevilla": (37.384, -5.971), "betis": (37.357, -5.982),
    "valencia": (39.475, -0.358), "villarreal": (39.944, -0.104), "athletic": (43.264, -2.949), "real sociedad": (43.301, -1.974), "osasuna": (42.797, -1.637),
    "inter": (45.478, 9.124), "milan": (45.478, 9.124), "juventus": (45.110, 7.641), "napoli": (40.828, 14.193), "roma": (41.934, 12.455), "lazio": (41.934, 12.455),
    "atalanta": (45.709, 9.681), "fiorentina": (43.780, 11.283), "bologna": (44.492, 11.310), "torino": (45.042, 7.650),
    "psg": (48.841, 2.253), "marseille": (43.270, 5.396), "lyon": (45.765, 4.982), "monaco": (43.728, 7.416), "lille": (50.612, 3.130), "nice": (43.705, 7.193),
}


def clamp(x, a, b):
    return max(a, min(b, x))


def norm(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode().lower()


def _zstat(T, key, field):
    """z-skor polja iz Statshub stila za tim `key` u odnosu na celu ligu (0.0 ako nema podataka)."""
    vals = {k: (v.get("style") or {}).get(field) for k, v in T.items()}
    vv = [x for x in vals.values() if isinstance(x, (int, float))]
    if len(vv) < 4 or not isinstance(vals.get(key), (int, float)):
        return 0.0
    mu = sum(vv) / len(vv)
    sd = math.sqrt(sum((x - mu) ** 2 for x in vv) / len(vv))
    return 0.0 if sd < 1e-9 else (vals[key] - mu) / sd


# ------------------------------------------------------------------ 1-3: momentum, raspad odbrane, hot tim
def momentum(Mraw, beta, lam_fn, key, P, nout_def=0):
    """EMA reziduala (stvarni - ocekivani golovi, xG se meša sa golovima sa udelom beta) u poslednjih P['mom_n'] mečeva tima.
    Vraca multiplikatore: att (napad tima) i conc (primljeni golovi tima; mnozi napad protivnika), plus hot / collapse zastavice."""
    ms = sorted((m for m in Mraw if key in (m["h"], m["a"])), key=lambda m: m["ts"])[-P["mom_n"]:]
    out = {"att": 1.0, "conc": 1.0, "m_att": 0.0, "m_def": 0.0, "n": len(ms), "wins4": 0, "hot": False, "collapse": False, "notes": []}
    if len(ms) < 3:
        return out
    wsum = da = dd = 0.0
    mean_lam = []
    for k, m in enumerate(reversed(ms)):                     # k = 0 je najnoviji
        home = m["h"] == key
        gf, ga = (m["hg"], m["ag"]) if home else (m["ag"], m["hg"])
        if beta and m.get("xh") is not None:
            xf, xa = (m["xh"], m["xa"]) if home else (m["xa"], m["xh"])
            gf, ga = beta * xf + (1 - beta) * gf, beta * xa + (1 - beta) * ga
        lh, la = lam_fn(m["h"], m["a"])
        ef, ea = (lh, la) if home else (la, lh)
        w = P["mom_rho"] ** k
        wsum += w; da += w * (gf - ef); dd += w * (ga - ea); mean_lam.append(ef)
    n = len(ms)
    s = n / (n + 2.0)                                        # skupljanje ka nuli: mali uzorak = mali momentum
    ref = max(0.8, sum(mean_lam) / n)
    out["m_att"], out["m_def"] = s * da / wsum, s * dd / wsum
    out["att"] = clamp(1 + P["mom_k"] * out["m_att"] / ref, 1 - P["mom_cap"], 1 + P["mom_cap"])
    out["conc"] = clamp(1 + P["mom_k"] * out["m_def"] / ref, 1 - P["mom_cap"], 1 + P["def_cap"])
    last4 = ms[-4:]
    out["wins4"] = sum(1 for m in last4 if ((m["hg"] > m["ag"]) if m["h"] == key else (m["ag"] > m["hg"])))
    conc2 = all(((m["ag"] if m["h"] == key else m["hg"]) >= 2) for m in ms[-3:])
    out["collapse"] = out["conc"] >= P["collapse_mom"] or (conc2 and nout_def >= 1)
    out["hot"] = out["wins4"] >= P["hot_wins"] and out["att"] >= P["hot_mom"]
    if out["hot"]:
        out["notes"].append(f"u naletu: {out['wins4']} pobede u poslednja 4 meča, napad {(out['att'] - 1) * 100:+.0f}% iznad očekivanja modela")
    elif abs(out["att"] - 1) >= 0.03:
        out["notes"].append(f"napadački momentum {(out['att'] - 1) * 100:+.0f}% (EMA poslednjih {n} mečeva)")
    if out["collapse"]:
        out["notes"].append(f"raspad odbrane: prima {out['m_def']:+.2f} gola/meč više od očekivanog" + (f", nedostupno {nout_def} odbrambenih igrača" if nout_def else ""))
    return out


# ------------------------------------------------------------------ 8: faktor ocaja
def desperation(Mraw, key, P, mom=None, mo=None):
    """Vraca {score, eff, att, conc, red, unlucky, streak, pts5, xgd, cashout, notes}. score u [0,1]: pritisak forme (serija bez pobede, bodovi u 5, xG/gol razlika)
    pomnozen potrebom (0.5 bez imperativa .. 1.0 borba za opstanak/titulu). eff = deo iznad praga; samo on pomera napad (att<1) i primljene golove (conc>1).
    mom = rezultat momentum() (koristi m_def: prima vise nego sto model ocekuje); mo = rezultat motivation(). Bez dovoljno podataka (<4 meca) vraca neutralno."""
    ms = sorted((m for m in (Mraw or []) if key in (m["h"], m["a"])), key=lambda m: m["ts"])
    last = ms[-P["desp_n"]:]
    out = {"score": 0.0, "eff": 0.0, "att": 1.0, "conc": 1.0, "red": False, "unlucky": False, "streak": 0, "pts5": None, "xgd": None, "cashout": False, "n": len(last), "notes": []}
    if len(last) < 4:
        return out
    res = lambda m: (m["hg"], m["ag"]) if m["h"] == key else (m["ag"], m["hg"])
    streak = 0
    for m in reversed(ms):
        gf, ga = res(m)
        if gf > ga or streak >= 12:
            break
        streak += 1
    l5 = last[-5:]
    pts5 = sum(3 if res(m)[0] > res(m)[1] else 1 if res(m)[0] == res(m)[1] else 0 for m in l5)
    q, has_xg = [], 0
    for m in last:
        gf, ga = res(m)
        xf, xa = (m.get("xh"), m.get("xa")) if m["h"] == key else (m.get("xa"), m.get("xh"))
        if xf is not None and xa is not None:
            q.append(xf - xa); has_xg += 1
        else:
            q.append(gf - ga)
    qd = sum(q) / len(q)
    streak_t, pts_t, bad_t = clamp((streak - 2) / 4, 0, 1), clamp((1.5 - pts5 / len(l5)) / 1.5, 0, 1), clamp(-qd / 0.8, 0, 1)
    pressure = 0.4 * streak_t + 0.3 * pts_t + 0.3 * bad_t
    need = 0.5 + 0.5 * max(0.0, (mo or {}).get("score", 0.0))
    score = pressure * need
    thr = P["desp_thr"]
    eff = clamp((score - thr) / (1 - thr), 0, 1)
    if eff < 0.02:
        eff = 0.0                                                        # zanemarljivo iznad praga: bez korekcije i bez napomene
    unlucky = streak_t >= 0.5 and bad_t < 0.15 and has_xg >= 4          # losi rezultati, ali igra je dobra: pech, ne ocaj
    if unlucky:
        eff = 0.0
    red = eff > 0 and score >= P["desp_red"] and bad_t >= 0.4
    m_def = (mom or {}).get("m_def", 0.0) or 0.0
    out.update(score=round(score, 3), eff=round(eff, 3), streak=streak, pts5=pts5, xgd=round(qd, 2), red=red, unlucky=unlucky, cashout=red,
               att=round(1 - P["desp_att_k"] * eff, 4), conc=round(1 + P["desp_conc_k"] * eff * (1.0 if m_def > 0 else 0.5), 4))
    if eff > 0:
        lbl = (mo or {}).get("label")
        out["notes"].append(f"faktor očaja {score:.2f}: {streak} mečeva bez pobede, {pts5} bod. u poslednjih {len(l5)}, {'xG' if has_xg >= 4 else 'gol'} razlika {qd:+.2f} po meču"
                            + (f", {lbl}" if lbl and lbl != 'bez izraženog imperativa' else "")
                            + f" → napad {(out['att'] - 1) * 100:+.0f}%, primljeni golovi {(out['conc'] - 1) * 100:+.0f}% (nekalibrisana hipoteza)")
        if red:
            out["notes"].append("crvena zona: igra loše a mora da osvaja bodove, rezultat je nestabilan; ako tipuješ na ovaj tim i povedeš, razmisli o delimičnom cash-outu")
    elif unlucky:
        out["notes"].append(f"{streak} mečeva bez pobede, ali xG razlika je {qd:+.2f} po meču: pre pech nego očaj, bez korekcije")
    return out


# ------------------------------------------------------------------ 4: stilski mismatch
def style_mismatch(SD, kh, ka, P):
    T = (SD or {}).get("teams") or {}
    if kh not in T or ka not in T or not T[kh].get("style") or not T[ka].get("style"):
        return None
    z = {t: {f: _zstat(T, t, f) for f in ("poss", "press", "shots", "clear", "long", "cross", "pass_acc", "fouls")} for t in (kh, ka)}
    k, m, notes = P["style_k"], {kh: 1.0, ka: 1.0}, []
    for a, b in ((kh, ka), (ka, kh)):
        if z[a]["press"] > 0.8 and z[b]["pass_acc"] < -0.5:
            m[a] *= 1 + k
            notes.append(f"{a}: visok pritisak vs slaba izgradnja protivnika (gubici lopte visoko)")
        if z[a]["poss"] > 0.8 and z[b]["poss"] < -0.8:
            m[a] *= 1 - 0.5 * k; m[b] *= 1 + 0.5 * k
            notes.append(f"{a} (posed) vs {b} (nizak blok): teže prolaze, ali kontre su opasne")
    if all(z[t]["press"] > 0.5 for t in (kh, ka)) or all(z[t]["shots"] > 0.5 and z[t]["clear"] < 0.3 for t in (kh, ka)):
        m[kh] *= 1 + k; m[ka] *= 1 + k
        notes.append("otvorena igra: oba tima presuju/šutiraju mnogo, očekuje se više šansi")
    elif all(z[t]["shots"] < -0.7 and z[t]["poss"] < 0 for t in (kh, ka)):
        m[kh] *= 1 - k; m[ka] *= 1 - k
        notes.append("zatvorena igra: oba tima malo šutiraju i ne drže loptu")
    lim = lambda v: clamp(v, 1 - 2 * k, 1 + 2 * k)
    return {"mult": [round(lim(m[kh]), 4), round(lim(m[ka]), 4)], "notes": notes, "z": {t: {f: round(v, 2) for f, v in z[t].items()} for t in z}}


# ------------------------------------------------------------------ 5: sudija
def _rk(name):
    t = [w for w in re.split(r"[^a-z]+", norm(name)) if w]
    return (t[-1], t[0][0]) if len(t) >= 2 else ((t[0], "") if t else ("", ""))


def referee_profiles(d, csv_code):
    """{(prezime, inicijal): {n, fouls, cards}} + prosek lige iz football-data CSV (Referee, HF/AF, HY/AY/HR/AR)."""
    prof, tot = {}, [0, 0.0, 0.0]
    for fp in glob.glob(os.path.join(d, f"{csv_code}_*.csv")):
        for r in csv.DictReader(open(fp, encoding="utf-8-sig")):
            try:
                f = float(r["HF"]) + float(r["AF"]); c = float(r["HY"]) + float(r["AY"]) + 2 * (float(r["HR"]) + float(r["AR"]))
            except (KeyError, ValueError, TypeError):
                continue
            k = _rk(r.get("Referee", ""))
            if not k[0]:
                continue
            p = prof.setdefault(k, {"n": 0, "fouls": 0.0, "cards": 0.0})
            p["n"] += 1; p["fouls"] += f; p["cards"] += c
            tot[0] += 1; tot[1] += f; tot[2] += c
    avg = {"fouls": tot[1] / tot[0], "cards": tot[2] / tot[0], "n": tot[0]} if tot[0] else None
    return prof, avg


def referee_effect(d, csv_code, kh, ka, P, override=None):
    """Sudija se cita iz data/<liga>/referees.json ({"domacin|gost": "Ime Prezime"}) ili iz override recnika (--ref)."""
    name = None
    for src in (override or {}, _jload(os.path.join(d, "referees.json")) or {}):
        name = src.get(f"{kh}|{ka}") or src.get(f"{kh},{ka}") or name
    if not name:
        return None
    prof, avg = referee_profiles(d, csv_code)
    p = prof.get(_rk(name))
    if not avg or not p or p["n"] < 10:
        return {"name": name, "total": 1.0, "notes": [f"sudija {name}: nema dovoljno mečeva u CSV-u (potrebno 10+) za profil"]}
    fr, cr = (p["fouls"] / p["n"]) / avg["fouls"], (p["cards"] / p["n"]) / avg["cards"]
    dev = clamp((fr + cr) / 2 - 1, -0.3, 0.3)
    tot = 1 - 10 * P["ref_k"] * dev                            # strog sudija: vise prekida = nesto manje golova
    return {"name": name, "n": p["n"], "fouls": round(p["fouls"] / p["n"], 1), "cards": round(p["cards"] / p["n"], 2), "fouls_lg": round(avg["fouls"], 1),
            "cards_lg": round(avg["cards"], 2), "total": round(clamp(tot, 0.96, 1.04), 4),
            "notes": [f"sudija {name}: {p['fouls'] / p['n']:.1f} fauls i {p['cards'] / p['n']:.1f} kartona-poena po meču (liga {avg['fouls']:.1f} / {avg['cards']:.1f}, {p['n']} mečeva)"]}


def _jload(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ 6: motivacija i kontekst tabele
def standings(SD, ut, today):
    T = (SD or {}).get("teams") or {}
    start = f"{today.year if today.month >= 7 else today.year - 1}-07-01"
    tab = {k: {"pts": 0, "pl": 0, "gd": 0, "gf": 0} for k in T}
    for m in (SD or {}).get("matches") or []:
        if m.get("ut") != ut or m["d"] < start or m["h"] not in tab or m["a"] not in tab:
            continue
        for t, gf, ga in ((m["h"], m["hg"], m["ag"]), (m["a"], m["ag"], m["hg"])):
            r = tab[t]; r["pl"] += 1; r["gf"] += gf; r["gd"] += gf - ga; r["pts"] += 3 if gf > ga else 1 if gf == ga else 0
    order = sorted(tab, key=lambda t: (-tab[t]["pts"], -tab[t]["gd"], -tab[t]["gf"]))
    for i, t in enumerate(order):
        tab[t]["rank"] = i + 1
    return tab, order


def motivation(tab, order, key, zone, P):
    """score u [-1, 1]: + = mora da osvaja bodove (opstanak/titula/Evropa), - = rasterecen. Ne vazi rano u sezoni (ramp 40% -> 80% sezone)."""
    N = len(order)
    if N < 10 or key not in tab:
        return None
    r = tab[key]; total = 2 * (N - 1); R = max(0, total - r["pl"])
    scale = clamp((r["pl"] / total - 0.4) / 0.4, 0, 1)
    pts = lambda pos: tab[order[clamp(pos, 1, N) - 1]]["pts"]
    drop_first = N - zone["rel"] + 1                       # prvo mesto u zoni ispadanja
    lead = r["pts"] - pts(drop_first) if r["rank"] < drop_first else r["pts"] - pts(drop_first - 1)
    label, score = "bez izraženog imperativa", 0.0
    if r["rank"] >= drop_first - 2 and abs(lead) <= 3 * R * 0.5 + 3:
        label, score = "borba za opstanak", 1.0
    elif r["rank"] <= 2 and pts(1) - r["pts"] <= 1.5 * R:
        label, score = "borba za titulu", 0.8
    elif abs(pts(zone["eu"]) - r["pts"]) <= 1.5 * R and r["rank"] <= zone["eu"] + 4 and r["rank"] > 2:
        label, score = "borba za Evropu", 0.6
    elif r["rank"] < drop_first - 2 and lead > 3 * R and pts(zone["eu"]) - r["pts"] > 3 * R:
        label, score = "rasterećen (bez imperativa)", -0.6
    return {"label": label, "score": round(score * scale, 3), "rank": r["rank"], "pts": r["pts"], "pl": r["pl"], "scale": round(scale, 2), "R": R}


# ------------------------------------------------------------------ 7: vreme
def weather(key, start_iso, d, cache_path, offline_flag, timeout=8):
    stad = dict(STADIUMS); stad.update({k: tuple(v) for k, v in (_jload(os.path.join(d, "stadiums.json")) or {}).items()})
    xy = stad.get(key)
    try:
        ts = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    if not xy or offline_flag.get("off") or not (-1 < (ts - datetime.now(timezone.utc)).total_seconds() / 86400 < 7):
        return None
    cache = _jload(cache_path) or {}
    ck = f"{key}|{ts.strftime('%Y-%m-%dT%H')}"
    c = cache.get(ck)
    if not c or time.time() - c.get("at", 0) > 6 * 3600:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={xy[0]}&longitude={xy[1]}&hourly=precipitation,wind_speed_10m,temperature_2m"
               f"&past_days=1&forecast_days=7&timezone=UTC")
        try:
            j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout).read())
            H = j["hourly"]; T = H["time"]
        except Exception:
            offline_flag["off"] = True                    # mreza nedostupna: ne cekaj ponovo za svaki meč
            return None
        idx = next((i for i, t in enumerate(T) if t.startswith(ts.strftime("%Y-%m-%dT%H"))), None)
        if idx is None:
            return None
        sl = slice(idx, idx + 3)
        avg = lambda a: sum(x or 0 for x in a[sl]) / max(1, len(a[sl]))
        c = {"at": time.time(), "rain": round(avg(H["precipitation"]), 2), "wind": round(avg(H["wind_speed_10m"]), 1), "temp": round(avg(H["temperature_2m"]), 1),
             "prior24": round(sum(x or 0 for x in H["precipitation"][max(0, idx - 24):idx]), 1)}
        cache[ck] = c
        try:
            json.dump(cache, open(cache_path, "w", encoding="utf-8"), separators=(",", ":"))
        except OSError:
            pass
    return c


def weather_effect(w, P, poss_z_h=0.0, poss_z_a=0.0):
    if not w:
        return None
    bad, notes = 0.0, []
    if w["rain"] >= 2.5: bad += 1.0; notes.append(f"jaka kiša ({w['rain']} mm/h)")
    elif w["rain"] >= 1.0: bad += 0.5; notes.append(f"kiša ({w['rain']} mm/h)")
    if w["wind"] >= 45: bad += 1.0; notes.append(f"veoma jak vetar ({w['wind']:.0f} km/h)")
    elif w["wind"] >= 30: bad += 0.5; notes.append(f"jak vetar ({w['wind']:.0f} km/h)")
    if w["prior24"] >= 12: bad += 0.5; notes.append(f"težak teren ({w['prior24']} mm u prethodna 24h)")
    k = P["wx_k"]
    return {**w, "total": round(1 - k * bad, 4), "mult": [round(1 - 0.5 * k * bad * (poss_z_h > 0.8), 4), round(1 - 0.5 * k * bad * (poss_z_a > 0.8), 4)], "notes": notes}


# ------------------------------------------------------------------ sve zajedno
def all_factors(SD, Mraw, beta, lam_fn, e, ch, ca, cfg, P=None):
    """e: dogadjaj {h, a, start, H, A}; ch/ca: team_ctx rezultati; cfg: {dir, csv, league, ut, today, ref, wx_off}. Vraca recnik za context + mult."""
    P = P or PARAMS
    kh, ka = e["h"], e["a"]
    F = {"mult": [1.0, 1.0], "total": 1.0, "notes": [], "under_block": [], "cashout": []}
    nd = lambda c: sum(1 for u in (c.get("out") or []) if u.get("pos") in ("D", "G") and u.get("status") in (None, "out"))
    mh_, ma_ = (momentum(Mraw, beta, lam_fn, kh, P, nd(ch)), momentum(Mraw, beta, lam_fn, ka, P, nd(ca))) if Mraw else (None, None)
    mh, ma = 1.0, 1.0
    if mh_ and ma_:
        mh *= mh_["att"] * ma_["conc"]; ma *= ma_["att"] * mh_["conc"]
        if ma_["collapse"]: mh *= 1 + P["collapse_k"]
        if mh_["collapse"]: ma *= 1 + P["collapse_k"]
        F["momentum"] = {"h": {k: v for k, v in mh_.items() if k != "notes"}, "a": {k: v for k, v in ma_.items() if k != "notes"}}
        F["notes"] += [f"{kh}: {n}" for n in mh_["notes"]] + [f"{ka}: {n}" for n in ma_["notes"]]
        for t, mm in ((kh, mh_), (ka, ma_)):
            if mm["hot"]: F["under_block"].append(f"{t} je u naletu ({mm['wins4']} pobede u 4, napad {(mm['att'] - 1) * 100:+.0f}%)")
            if mm["collapse"]: F["under_block"].append(f"{t}: raspad odbrane ({mm['m_def']:+.2f} primljenih gola/meč iznad očekivanog)")
    sm = style_mismatch(SD, kh, ka, P)
    if sm:
        mh *= sm["mult"][0]; ma *= sm["mult"][1]; F["style"] = sm; F["notes"] += sm["notes"]
    tab, mo = None, None
    if cfg.get("ut") and SD:
        tab, order = standings(SD, cfg["ut"], cfg["today"])
        zone = ZONES.get(cfg.get("league"), ZONES["epl"])
        mo = {t: motivation(tab, order, t, zone, P) for t in (kh, ka)}
        if all(mo.values()):
            mh *= 1 + P["mot_k"] * mo[kh]["score"]; ma *= 1 + P["mot_k"] * mo[ka]["score"]
            F["motivation"] = {"h": mo[kh], "a": mo[ka], "late20": {"h": round(P["late_share"] * (1 + P["mot_k"] * 4 * mo[kh]["score"]), 3), "a": round(P["late_share"] * (1 + P["mot_k"] * 4 * mo[ka]["score"]), 3)}}
            for t, m_ in ((kh, mo[kh]), (ka, mo[ka])):
                if m_["score"]:
                    F["notes"].append(f"{t}: {m_['label']} ({m_['rank']}. mesto, {m_['pts']} bod., preostalo {m_['R']} kola); imperativ važi i za poslednjih 20 minuta")
            if not (mo[kh]["scale"] or mo[ka]["scale"]):
                F["motivation"]["note"] = "rana sezona: motivacija iz tabele se ne računa"
    if Mraw:                                                 # 8) faktor ocaja
        dh_, da_ = desperation(Mraw, kh, P, mh_, (mo or {}).get(kh)), desperation(Mraw, ka, P, ma_, (mo or {}).get(ka))
        if dh_["eff"] or da_["eff"]:
            mh *= dh_["att"] * da_["conc"]; ma *= da_["att"] * dh_["conc"]
        F["desperation"] = {"h": dh_, "a": da_}
        for t, dd in ((kh, dh_), (ka, da_)):
            F["notes"] += [f"{t}: {n}" for n in dd["notes"]]
            if dd["cashout"]: F["cashout"].append(f"{t} je u crvenoj zoni očaja")
    ref = referee_effect(cfg["dir"], cfg["csv"], kh, ka, P, cfg.get("ref"))
    if ref:
        F["referee"] = ref; F["total"] *= ref["total"]; F["notes"] += ref["notes"]
    wx = weather_effect(weather(kh, e.get("start"), cfg["dir"], os.path.join(cfg["dir"], "weather_cache.json"), cfg.setdefault("wx_flag", {"off": bool(cfg.get("wx_off"))})),
                        P, _zstat((SD or {}).get("teams") or {}, kh, "poss"), _zstat((SD or {}).get("teams") or {}, ka, "poss"))
    if wx:
        F["weather"] = wx; F["total"] *= wx["total"]; mh *= wx["mult"][0]; ma *= wx["mult"][1]
        if wx["notes"]: F["notes"].append("vreme: " + ", ".join(wx["notes"]))
    F["mult"] = [round(clamp(mh, 0.8, 1.25), 4), round(clamp(ma, 0.8, 1.25), 4)]
    F["total"] = round(F["total"], 4)
    return F
