#!/usr/bin/env python3
"""Statshub -> data/epl/statshub_data.json  (utakmice, xG, forma, stil igre, povrede, odmor, H2H) za Premier ligu.

Koristi iste javne endpointe koje sama Statshub stranica zove (nadjeni probom): jedan pregledac (Playwright), pauza 1.5-3 s, kes u data/epl/statshub/
(podrazumevano 6 h), staje na 403/429. Bez prijave, bez zaobilazenja.
  pip install playwright && playwright install chromium
  python statshub_football.py                 # skida sve timove PL (oko 20 poziva x ~400 KB), pise data/epl/statshub_data.json
  python statshub_football.py --offline       # samo iz kesa (bez mreze)
Glavni endpoint: /api/team/<id>/players/performance?tournamentId=&limit=60&location=both  ->
  data[igrac].stats[eventId] (49 polja: expectedGoals, shots, touches, totalPass, totalLongBalls, totalCross, tackles... po igracu i meci),
  events[] (rezultati svih takmicenja), unavailability[] (povrede/suspenzije)."""
import argparse, collections, json, math, os, random, re, sys, time, unicodedata
from datetime import datetime, timezone

BASE = "https://www.statshub.com"
from fb_leagues import CUR
D = os.path.join("data", CUR["dir"])
CACHE = os.path.join(D, "statshub")
OUT = os.path.join(D, "statshub_data.json")
TOURN, SEASON = CUR["tourn"], CUR["season"]                      # PL 26/27 (iz probe: /api/tournament/1/96668/events); --season za sledecu sezonu
PL_UT = CUR["ut"]                                    # uniqueTournamentId Premier lige
ALIAS = {"manchester city": "man city", "manchester united": "man united", "tottenham hotspur": "tottenham", "newcastle united": "newcastle",
         "wolverhampton wanderers": "wolves", "wolverhampton": "wolves", "west ham united": "west ham", "brighton hove albion": "brighton",
         "brighton and hove albion": "brighton", "leeds united": "leeds", "nottingham forest": "nottm forest", "leicester city": "leicester",
         "afc bournemouth": "bournemouth", "west bromwich albion": "west brom", "ipswich town": "ipswich", "sheffield united": "sheffield utd",
         "norwich city": "norwich", "luton town": "luton", "coventry city": "coventry", "hull city": "hull", "stoke city": "stoke",
         "swansea city": "swansea", "cardiff city": "cardiff", "birmingham city": "birmingham", "blackburn rovers": "blackburn"}
ALIAS.update(CUR["alias"])
try:                                                       # UEFA takmicenja (cl/el/conf): ucesnici iz mnogo liga -> zajednicki aliasi + mrezni nacin rada (fb_eu.py)
    import fb_eu
    CUP = fb_eu.is_cup(CUR)
    if CUP: ALIAS.update(fb_eu.eu_alias())
except ImportError:
    fb_eu, CUP = None, False
STAT = ("expectedGoals", "expectedAssists", "shots", "onTargetScoringAttempt", "shotOffTarget", "blockedScoringAttempt", "touches", "totalPass",
        "accuratePass", "totalLongBalls", "totalCross", "totalTackle", "interceptionWon", "totalClearance", "aerialWon", "duelWon", "duelLost",
        "bigChanceCreated", "keyPass", "fouls", "wasFouled", "saves", "goals", "minutesPlayed")
JS = "async u => { const r = await fetch(u, {headers: {accept: 'application/json'}}); let j = null; try { j = await r.json() } catch (e) {} return [r.status, j] }"


class Blocked(Exception):
    pass


def key(name):
    s = "".join(c for c in unicodedata.normalize("NFD", str(name).lower()) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\b(fc|afc|cf)\b", " ", re.sub(r"[^a-z0-9 ]", " ", s.replace("'", "").replace("&", " and ")))
    s = re.sub(r"\s+", " ", s).strip()
    return ALIAS.get(s, s)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class Client:
    def __init__(self, offline=False, ttl_h=6.0):
        self.offline, self.ttl, self.pw, self.page = offline, ttl_h * 3600, None, None
        os.makedirs(CACHE, exist_ok=True)

    def _open(self):
        if self.page:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            sys.exit("pip install playwright && playwright install chromium   (ili --offline sa kesom)")
        self.pw = sync_playwright().start()
        self.br = self.pw.chromium.launch(headless=True)
        self.page = self.br.new_context(locale="en-GB").new_page()
        self.found, self.apis, self.bodies, self.status = [], [], [], None

        def _on(r):
            if "/api/" in r.url:
                self.apis.append(r.url)
                if "/api/tournament/" in r.url:
                    self.found.append(r.url)
                try:                                  # telo JSON-a: trazimo tournamentId/seasonId i u odgovorima (ne samo u adresama)
                    if "json" in r.headers.get("content-type", ""):
                        self.bodies.append((r.url, r.text()[:400000]))
                except Exception:
                    pass
        self.page.on("response", _on)
        resp = self.page.goto(BASE + CUR["path"], wait_until="domcontentloaded", timeout=60000)
        self.status = resp.status if resp else None
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(2)

    def get(self, path):
        fn = os.path.join(CACHE, re.sub(r"[^a-zA-Z0-9]+", "_", path)[:170] + ".json")
        if os.path.exists(fn) and (self.offline or time.time() - os.path.getmtime(fn) < self.ttl):
            return json.load(open(fn, encoding="utf-8"))
        if self.offline:
            return None
        self._open()
        time.sleep(random.uniform(1.5, 3.0))
        st, j = self.page.evaluate(JS, BASE + path)
        if st in (403, 429):
            raise Blocked(f"HTTP {st} za {path}")
        if st != 200 or j is None:
            print(f"  ! {st} {path}")
            return None
        json.dump(j, open(fn, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        return j

    def close(self):
        if self.pw:
            self.br.close(); self.pw.stop()


def events_in(o):
    """Svi dogadjaji (dict koji ima homeTeam i awayTeam), bez obzira na ugnjezdjenje."""
    if isinstance(o, dict):
        if isinstance(o.get("homeTeam"), dict) and isinstance(o.get("awayTeam"), dict):
            yield o
        else:
            for v in o.values():
                yield from events_in(v)
    elif isinstance(o, list):
        for v in o:
            yield from events_in(v)


def norm_event(it):
    e = it["events"] if isinstance(it.get("events"), dict) else it
    ht, at = it["homeTeam"], it["awayTeam"]
    pick = lambda *ks: next((e[k] for k in ks if e.get(k) is not None), None)
    tn = it.get("tournaments") if isinstance(it.get("tournaments"), dict) else {}
    return {"id": e.get("id"), "ts": pick("timeStartTimestamp", "startTimestamp"), "h": ht.get("id"), "a": at.get("id"),
            "hn": ht.get("name"), "an": at.get("name"), "hs": pick("homeScoreNormaltime", "homeScoreCurrent"), "as": pick("awayScoreNormaltime", "awayScoreCurrent"),
            "fin": e.get("status") == "finished", "ut": e.get("uniqueTournamentId") or tn.get("uniqueTournamentId"), "tn": tn.get("name"),
            "round": e.get("roundInfo"), "ref": e.get("refereeId")}


def team_pack(j, tid):
    """(events, per_event_totals_za_tim, igraci, nedostupni) iz players/performance odgovora."""
    evs = {}
    for it in j.get("events") or []:
        try:
            n = norm_event(it)
            if n["id"] is not None:
                evs[n["id"]] = n
        except (KeyError, TypeError):
            continue
    per, pl = {}, {}
    for p in j.get("data") or []:
        rec = pl.setdefault(p.get("id"), {"id": p.get("id"), "n": p.get("name"), "pos": p.get("position"), "min": 0.0, "xg": 0.0, "xa": 0.0, "g": 0, "ev": {}})
        for eid, st in (p.get("stats") or {}).items():
            if st.get("teamId") not in (None, tid):
                continue
            mins = fnum(st.get("minutesPlayed"))
            if mins <= 0:
                continue
            a = per.setdefault(int(eid), dict.fromkeys(STAT, 0.0))
            for k in STAT:
                a[k] += fnum(st.get(k))
            rec["min"] += mins; rec["xg"] += fnum(st.get("expectedGoals")); rec["xa"] += fnum(st.get("expectedAssists")); rec["g"] += fnum(st.get("goals"))
            rec["ev"][int(eid)] = mins
    for a in per.values():
        a["sh"] = a["shots"] or (a["onTargetScoringAttempt"] + a["shotOffTarget"] + a["blockedScoringAttempt"])
    un = [{"n": u.get("name"), "pos": u.get("position"), "reason": u.get("reason"), "status": u.get("status"), "kind": u.get("kind"),
           "ret": u.get("expectedReturn"), "id": u.get("playerId")} for u in (j.get("unavailability") or [])]
    return evs, per, pl, un


def teams_in(o, out, parent=""):
    """Timovi iz tabele (standing): dict {"team": {...}} ili bilo koji dict sa id+name+slug/shortName/nameCode."""
    if isinstance(o, dict):
        if o.get("id") is not None and isinstance(o.get("name"), str) and (parent == "team" or any(k in o for k in ("slug", "shortName", "nameCode"))):
            if isinstance(o["id"], int):
                out[o["id"]] = o["name"]
        for k, v in o.items():
            teams_in(v, out, k)
    elif isinstance(o, list):
        for v in o:
            teams_in(v, out, parent)


def discover(cl, tourn, season):
    """{statshub_id: ime} svih timova lige. Redom: /unique-tournament/<ut>/<s>/standing (sva ekipa), /events (tekuce kolo), stari /tournament/... putevi, pa kola."""
    teams = {}
    ut = CUR["ut"]
    def add(j):
        for it in events_in(j or {}):
            for side in ("homeTeam", "awayTeam"):
                t = it[side]
                if t.get("id") is not None and t.get("name"):
                    teams[t["id"]] = t["name"]
    for base in (f"/api/unique-tournament/{ut}/{season}", f"/api/tournament/{tourn}/{season}"):
        if len(teams) >= 16:
            break
        teams_in(cl.get(base + "/standing") or {}, teams)
        add(cl.get(base + "/events"))
    if len(teams) < 16:      # poslednje: kola
        rj = cl.get(f"/api/tournament/{tourn}/{season}/rounds") or {}
        nums = sorted({int(x) for x in re.findall(r'"round":\s*(\d+)', json.dumps(rj))}) or list(range(1, 9))
        for r in nums[-10:]:
            add(cl.get(f"/api/tournament/{tourn}/{season}/{r}/events"))
            if len(teams) >= 18:
                break
    return teams


def official_table(j, tk, teams):
    """Sluzbena tabela sa Statshuba ({key: {pl,w,d,l,gf,ga,pts}}) ili {} ako se ne da procitati. Ona je izvor istine za odigrano/bodove."""
    out = {}
    def rows(o):
        if isinstance(o, dict):
            if isinstance(o.get("rows"), list):
                yield from o["rows"]
            for v in o.values():
                yield from rows(v)
        elif isinstance(o, list):
            for v in o:
                yield from rows(v)
    byname = {n: t for t, n in teams.items()}
    for r in rows(j):
        t = r.get("team") or {}
        tid = t.get("id") if t.get("id") in tk else byname.get(t.get("name"))
        g = lambda *ks: next((r[k] for k in ks if isinstance(r.get(k), (int, float))), None)
        pl, w, d, l, gf, ga, pts = g("matches", "played", "gamesPlayed"), g("wins", "win"), g("draws", "draw"), g("losses", "loss"), g("scoresFor", "goalsFor", "gf"), g("scoresAgainst", "goalsAgainst", "ga"), g("points", "pts")
        if tid in tk and None not in (pl, w, d, l, gf, ga, pts) and pl == w + d + l:
            out[tk[tid]] = {"pl": int(pl), "w": int(w), "d": int(d), "l": int(l), "gf": int(gf), "ga": int(ga), "pts": int(pts)}
    if len(out) < 10:
        print(f"  sluzbena tabela nije procitana ({len(out)} timova): tabela se racuna iz mecova")
        return {}
    print(f"  sluzbena tabela: {len(out)} timova, odigrano {min(v['pl'] for v in out.values())}-{max(v['pl'] for v in out.values())}")
    return out


def labels(z):
    out = []
    if z["poss"] > .8: out.append("posed/kontrola lopte")
    if z["poss"] < -.8: out.append("kontra/nizak blok")
    if z["long"] > .8: out.append("direktna igra (duge lopte)")
    if z["cross"] > .8: out.append("napad preko bokova (centarsuti)")
    if z["press"] > .8: out.append("agresivan pritisak (tackle+presecanja)")
    if z["clear"] > .8: out.append("puno odbrane (clearance)")
    if z["shots"] > .8: out.append("visok volumen suteva")
    return out


def build(a):
    cl = Client(a.offline, a.ttl)
    try:
        teams = discover(cl, a.tournament, a.season)
        if len(teams) < 10:
            sys.exit(f"Nasao samo {len(teams)} timova u /api/tournament/{a.tournament}/{a.season}/events; proveri --tournament/--season ili posalji mi taj JSON.")
        print(f"{len(teams)} timova: " + ", ".join(sorted(teams.values())))
        packs = {}

        def load(tid):
            j = cl.get(f"/api/team/{tid}/players/performance?tournamentId=&limit={a.limit}&location=both")
            if j and not (j.get("data") or j.get("events")):
                j = cl.get(f"/api/team/{tid}/players/performance?tournamentId=&limit=20&location=both")
            if j:
                packs[tid] = team_pack(j, tid)
                print(f"  {teams[tid]:<22} {len(packs[tid][0])} mecova, {len(packs[tid][3])} nedostupnih")
        for tid in list(teams):
            load(tid)
        # discover() zna da nadje samo deo timova (npr. 17 od 20): protivnike iz ligaskih mecova koji nisu medju timovima dodajemo i ucitavamo,
        # inace se njihovi mecovi odbace i tabela/forma svih ostalih ispadnu pogresni (jedan tim ima 3 utakmice, drugi 6)
        now_ = datetime.now(timezone.utc)
        s0 = datetime(now_.year if now_.month >= 7 else now_.year - 1, 7, 1, tzinfo=timezone.utc).timestamp()
        for _ in range(0 if CUP else 2):                  # UEFA: protivnike iz kvalifikacija ne skidamo kao posebne timove
            miss = {}
            for tid_, (evs_, per_, pl_, un_) in list(packs.items()):
                for e_ in evs_.values():
                    if e_["ut"] == PL_UT and e_["ts"] and e_["ts"] >= s0:
                        for i_, n_ in ((e_["h"], e_["hn"]), (e_["a"], e_["an"])):
                            if i_ is not None and i_ not in teams and n_: miss[i_] = n_
            if not miss: break
            print("  dodajem timove koji nisu bili u listi lige: " + ", ".join(miss.values()))
            for i_, n_ in miss.items():
                teams[i_] = n_; load(i_)
        ups = {tid: cl.get(f"/api/team/{tid}/upcoming-events?page=1&limit=10") for tid in teams} if not a.no_upcoming else {}
        std_json = cl.get(f"/api/unique-tournament/{CUR['ut']}/{a.season}/standing") or {}
    except Blocked as e:
        sys.exit(f"STAJEM: {e}. Statshub odbija zahteve; ne zaobilazim. Pokusaj kasnije (kes ostaje).")
    finally:
        cl.close()
    tk = {tid: key(n) for tid, n in teams.items()}
    node = dict(tk)                                    # id -> kljuc; u UEFA modu i protivnici iz domacih liga (bez profila, samo cvorovi mreze za model)
    if CUP:
        for tid_, (evs_, per_, pl_, un_) in packs.items():
            for e_ in evs_.values():
                for i_, n_ in ((e_["h"], e_["hn"]), (e_["a"], e_["an"])):
                    if i_ is not None and n_ and i_ not in node: node[i_] = key(n_)
        print(f"  UEFA mod: {len(tk)} ucesnika, {len(node) - len(tk)} protivnika iz drugih takmicenja kao cvorovi modela")
    official = official_table(std_json, tk, teams)
    # utakmice izmedju timova lige (svi turniri), jedna po event id; xG = zbir xG igraca svakog tima
    M = {}
    for tid, (evs, per, pl, un) in packs.items():
        for eid, e in evs.items():
            if not e["fin"] or e["hs"] is None or e["as"] is None or e["h"] not in node or e["a"] not in node or not e["ts"]:
                continue
            m = M.setdefault(eid, {"id": eid, "ts": e["ts"], "d": datetime.fromtimestamp(e["ts"], timezone.utc).strftime("%Y-%m-%d"), "h": node[e["h"]], "a": node[e["a"]], "tid": (e["h"], e["a"]),
                                   "hg": int(e["hs"]), "ag": int(e["as"]), "ut": e["ut"], "round": e["round"], "x": {}})
            if eid in per:
                m["x"][tid] = per[eid]
    matches = []
    for m in sorted(M.values(), key=lambda m: m["ts"]):
        ht, at = m["tid"]
        xh, xa = m["x"].get(ht), m["x"].get(at)         # xG imamo samo kad su OBA tima ucesnici (za protivnike iz drugih liga nema xG: tamo se koriste golovi)
        ok = bool(xh and xa and xh["expectedGoals"] + xa["expectedGoals"] > 0.2)
        matches.append({"d": m["d"], "ts": m["ts"], "h": m["h"], "a": m["a"], "hg": m["hg"], "ag": m["ag"], "ut": m["ut"], "round": m["round"],
                        "eu": (1 if (fb_eu and m["ut"] in fb_eu.UEFA_UT) else 0),
                        "xh": round(xh["expectedGoals"], 3) if ok else None, "xa": round(xa["expectedGoals"], 3) if ok else None})
    # profili timova
    prof, raw_style = {}, {}
    for tid, (evs, per, pl, un) in packs.items():
        fin = sorted((e for e in evs.values() if e["fin"] and e["ts"]), key=lambda e: e["ts"])
        last5 = fin[-5:]
        form = {"str": "", "pts": 0, "gf": 0, "ga": 0, "xgf": 0.0, "xga": 0.0, "nx": 0, "xnga": 0}
        for e in last5:
            home = e["h"] == tid
            gf, ga = (e["hs"], e["as"]) if home else (e["as"], e["hs"])
            if gf is None or ga is None:
                continue
            form["str"] += "W" if gf > ga else "D" if gf == ga else "L"
            form["pts"] += 3 if gf > ga else 1 if gf == ga else 0
            form["gf"] += gf; form["ga"] += ga
            if e["id"] in per and per[e["id"]]["expectedGoals"] > 0:
                form["xgf"] += per[e["id"]]["expectedGoals"]; form["nx"] += 1
        recent = [e for e in fin[-10:] if e["id"] in per and per[e["id"]]["touches"] > 0]
        s = collections.Counter()
        for e in recent:
            for k, v in per[e["id"]].items():
                s[k] += v
        n = max(1, len(recent))
        # posed: udeo dodira u meci ako je protivnik iz lige i imamo njegove podatke, inace None
        sh = []
        for e in recent:
            opp = e["a"] if e["h"] == tid else e["h"]
            if opp in packs and e["id"] in packs[opp][1] and packs[opp][1][e["id"]]["touches"] > 0:
                t1, t2 = per[e["id"]]["touches"], packs[opp][1][e["id"]]["touches"]
                sh.append(t1 / (t1 + t2))
        raw_style[tid] = {"poss": sum(sh) / len(sh) if sh else None, "long": s["totalLongBalls"] / max(1, s["totalPass"]), "cross": s["totalCross"] / n,
                          "press": (s["totalTackle"] + s["interceptionWon"]) / n, "clear": s["totalClearance"] / n, "shots": s["sh"] / n,
                          "touches": s["touches"] / n, "pass_acc": s["accuratePass"] / max(1, s["totalPass"]), "sot": s["onTargetScoringAttempt"] / n,
                          "aerial": s["aerialWon"] / n, "fouls": s["fouls"] / n, "xg": s["expectedGoals"] / n, "n": len(recent)}
        # ucinak igraca: udeo u xG+xA tima i udeo minuta (odbrana/golman) u poslednjih 10 mecova
        ids = {e["id"] for e in fin[-10:]}
        tot = sum(pr["xg"] + pr["xa"] for pr in pl.values()) or 1.0
        pls = []
        for pr in pl.values():
            mins10 = sum(m_ for eid, m_ in pr["ev"].items() if eid in ids)
            xg10 = pr["xg"] + pr["xa"]
            if pr["min"] > 0:
                pls.append({"id": pr["id"], "n": pr["n"], "pos": pr["pos"], "min10": round(mins10), "att": round(xg10 / tot, 4), "min": round(pr["min"])})
        last = fin[-1] if fin else None
        nxt = None
        for it in events_in(ups.get(tid) or {}):
            e = norm_event(it)
            if e["ts"] and not e["fin"] and (nxt is None or e["ts"] < nxt["ts"]):
                nxt = e
        prof[tid] = {"id": tid, "name": teams[tid], "key": tk[tid], "form": form, "players": pls, "unav": un, "games": len(fin),
                     "last": {"ts": last["ts"], "ut": last["ut"], "away": last["a"] == tid} if last else None,
                     "next": {"ts": nxt["ts"], "ut": nxt["ut"], "away": nxt["a"] == tid} if nxt else None,
                     "recent": [{"ts": e["ts"], "ut": e["ut"], "tn": e["tn"], "home": e["h"] == tid, "opp": (e["an"] if e["h"] == tid else e["hn"]),
                                 "gf": e["hs"] if e["h"] == tid else e["as"], "ga": e["as"] if e["h"] == tid else e["hs"]} for e in fin[-8:]]}
    # stil igre kao z-skor u odnosu na ligu
    keys = ("poss", "long", "cross", "press", "clear", "shots")
    for k in keys:
        v = [r[k] for r in raw_style.values() if r.get(k) is not None]
        mu = sum(v) / len(v) if v else 0
        sd = math.sqrt(sum((x - mu) ** 2 for x in v) / len(v)) if len(v) > 1 else 0
        for tid, r in raw_style.items():
            r.setdefault("z", {})[k] = ((r[k] - mu) / sd) if (r.get(k) is not None and sd > 1e-9) else 0.0
    for tid, r in raw_style.items():
        prof[tid]["style"] = {**{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "z"}, "z": {k: round(v, 2) for k, v in r["z"].items()}, "labels": labels(r["z"])}
    out = {"updated": datetime.now().isoformat(timespec="seconds"), "source": "statshub.com", "tournament": a.tournament, "season": a.season,
           "teams": {tk[t]: prof[t] for t in prof}, "matches": matches, "standing": official, "cup": CUP}
    os.makedirs(D, exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    nx = sum(1 for m in matches if m["xh"] is not None)
    print(f"\nupisano {OUT}: {len(matches)} utakmica medju timovima lige ({nx} sa xG), {len(prof)} profila")
    return out


IDS = os.path.join(D, "statshub_ids.json")


def resolve_ids(offline=False):
    """(tournament, season): iz podesavanja lige, iz kesa data/<liga>/statshub_ids.json, ili sa stranice lige (mrezni pozivi /api/tournament/<t>/<s>/...)."""
    if TOURN and SEASON:
        return TOURN, SEASON
    try:
        j = json.load(open(IDS, encoding="utf-8"))
        if j.get("tournament") and j.get("season"):
            return j["tournament"], j["season"]
    except (OSError, ValueError):
        pass
    if offline:
        sys.exit("Nepoznati tournament/season za ovu ligu. Pokreni bez --offline ili zadaj --tournament i --season.")
    cl = Client()
    cl._open()
    pat = re.compile(r"/api/tournament/(\d+)/(\d+)/")
    pairs_of = lambda: collections.Counter(m.groups() for u in cl.found for m in [pat.search(u)] if m)
    pairs, note = pairs_of(), ""
    if not pairs:   # adresa (slug) mozda ne postoji: nadji link lige na /leagues pa otvori njega
        try:
            cl.page.goto(BASE + "/leagues", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            hrefs = cl.page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))")
            cand = [h for h in dict.fromkeys(hrefs) if re.search(r"/leagues/.*/%d/?$" % CUR["ut"], h or "")]
            note = f"link lige na /leagues: {cand[:3]}"
            if cand:
                cl.page.goto(cand[0] if cand[0].startswith("http") else BASE + cand[0], wait_until="domcontentloaded", timeout=60000)
                try:
                    cl.page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(2)
            pairs = pairs_of()
        except Exception as e:
            note += f" | greska: {e}"
    if not pairs:   # klik po tabovima lige (Fixtures/Table/Matches...) pa opet pogledaj mrezne pozive
        for t in ("Fixtures", "Matches", "Table", "Standings", "Teams", "Overview", "Results"):
            try:
                cl.page.get_by_text(t, exact=True).first.click(timeout=2500)
                cl.page.wait_for_load_state("networkidle", timeout=10000)
                time.sleep(1.5)
            except Exception:
                pass
            pairs = pairs_of()
            if pairs:
                break
    if not pairs:   # telo API odgovora: {"tournamentId":..,"seasonId":..} ili uniqueTournament.id==ut + season.id
        cnt = collections.Counter()
        for u, b in cl.bodies:
            for m in re.finditer(r'"(?:tournamentId|tournament_id)":\s*(\d+)[^{}]{0,300}?"(?:seasonId|season_id)":\s*(\d+)', b):
                cnt[m.groups()] += 1
            for m in re.finditer(r'"(?:seasonId|season_id)":\s*(\d+)[^{}]{0,300}?"(?:tournamentId|tournament_id)":\s*(\d+)', b):
                cnt[(m.group(2), m.group(1))] += 1
        for u in cl.apis:
            m = re.search(r"/(?:tournament|tournaments|league)/(\d+)/(?:season/)?(\d+)", u)
            if m:
                cnt[m.groups()] += 1
        if cnt:
            pairs, note = cnt, note + " | ID-jevi iz tela/adresa API poziva"
    if not pairs:   # poslednja nada: brojevi iz HTML-a stranice (ne snima se, jer je manje pouzdano)
        try:
            html = cl.page.content()
            tt = collections.Counter(re.findall(r'"tournamentId":(\d+)', html)).most_common(1)
            ss = collections.Counter(re.findall(r'"seasonId":(\d+)', html)).most_common(1)
            if tt and ss:
                pairs = collections.Counter({(tt[0][0], ss[0][0]): 1})
                print(f"UPOZORENJE: brojevi uzeti iz HTML-a stranice (tournament={tt[0][0]}, season={ss[0][0]}), ne iz API poziva. Proveri rezultat.")
                IDS_SAVE = False
            else:
                IDS_SAVE = True
        except Exception:
            IDS_SAVE = True
    else:
        IDS_SAVE = True
    if not pairs:
        os.makedirs(D, exist_ok=True)
        dg, cur_url = os.path.join(D, "statshub_discovery.txt"), cl.page.url
        open(dg, "w", encoding="utf-8").write(f"HTTP status stranice lige: {cl.status}\nadresa posle otvaranja: {cur_url}\n{note}\n\nAPI pozivi koje je stranica napravila ({len(cl.apis)}):\n" + "\n".join(dict.fromkeys(cl.apis)) + "\n\n--- kljucevi/uzorak tela ---\n" + "\n".join(f"{u}\n   {b[:300]}" for u, b in cl.bodies[:40]))
        try:
            cl.br.close(); cl.pw.stop()
        except Exception:
            pass
        sys.exit(f"Nisam nasao tournament/season (HTTP {cl.status}, {cur_url}). Dijagnostika je u {dg}, posalji mi taj fajl. "
                 f"Ili zadaj rucno: --tournament X --season Y (statshub_probe.py --league {CUR['path']}, pa index.txt).")
    try:
        cl.br.close(); cl.pw.stop()
    except Exception:
        pass
    t, s = (int(x) for x in pairs.most_common(1)[0][0])
    os.makedirs(D, exist_ok=True)
    if IDS_SAVE:
        json.dump({"tournament": t, "season": s}, open(IDS, "w"))
    print(f"pronadjeno: tournament={t}, season={s} (sacuvano u {IDS})")
    return t, s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="samo iz kesa u data/epl/statshub/")
    ap.add_argument("--ttl", type=float, default=6.0, help="kes vazi N sati")
    ap.add_argument("--limit", type=int, default=60, help="koliko poslednjih mecova po timu (sajt koristi 20 i 60)")
    ap.add_argument("--tournament", type=int, default=None); ap.add_argument("--season", type=int, default=None); ap.add_argument("--league", default="epl", help="epl | laliga")
    ap.add_argument("--no-upcoming", action="store_true")
    a = ap.parse_args()
    if a.tournament is None or a.season is None:
        t, s = resolve_ids(a.offline)
        a.tournament, a.season = a.tournament or t, a.season or s
    build(a)


if __name__ == "__main__":
    main()
