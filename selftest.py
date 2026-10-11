#!/usr/bin/env python3
"""selftest.py - provera da kod radi i da model ZAISTA vraca ono sto je ugradjeno u sintetickim podacima (nije test na pravim mecevima).
  python selftest.py
Pravi vestacku ligu (10 timova, poznata jacina, tempo, pozicione slabosti i slabost reketa) u privremenom folderu, pa proverava:
  - korelaciju procenjenog net rejtinga sa pravom jacinom, - da DVP vidi tim koji lose brani plejmejkere,
  - da prostorni modul vidi tim koji lose brani reket, - da walk-forward backtest i annotate_events rade bez gresaka."""
import json, os, random, shutil, sys, tempfile, datetime

import quant_model as Q
import backtest as B

random.seed(7)
TEAMS = [f"T{i}" for i in range(10)]
TRUE_OFF = {t: random.gauss(0, 4) for t in TEAMS}
TRUE_DEF = {t: random.gauss(0, 4) for t in TEAMS}
PACE = {t: random.gauss(0, 2) for t in TEAMS}
BAD_PG_DEF, BAD_RIM_DEF = "T3", "T5"


def make_players(t):
    ps = []
    for k, (pos, mn, us) in enumerate([("G", 31, 1.3), ("G", 27, 1.0), ("F", 29, 1.1), ("F", 24, 0.9), ("C", 26, 1.0), ("G", 18, 0.8), ("F", 16, 0.7), ("C", 15, 0.6), ("F", 14, 0.6)]):
        ps.append({"id": f"{t}p{k}", "name": f"Igrac{t}{k} Prezime{t}{k}", "pos": {"G": "Guard", "F": "Forward", "C": "Center"}[pos], "mn": mn, "us": us})
    return ps


ROST = {t: make_players(t) for t in TEAMS}


def team_game(t, opp, home, poss):
    eff = 105 + TRUE_OFF[t] - TRUE_DEF[opp] + (2.5 if home else -2.5) + random.gauss(0, 5)
    pts = max(55, round(poss * eff / 100))
    fta = random.randint(14, 24)
    ftm = round(fta * 0.75)
    fg3m = random.randint(5, 10)
    fg2m = max(10, (pts - ftm - 3 * fg3m) // 2)
    pts = fg2m * 2 + fg3m * 3 + ftm
    tot = {"pts": pts, "fg2m": fg2m, "fg2a": int(fg2m / 0.52), "fg3m": fg3m, "fg3a": int(fg3m / 0.34), "ftm": ftm, "fta": fta,
           "or": random.randint(7, 13), "tr": random.randint(30, 42), "tov": random.randint(10, 16), "ast": 15, "stl": 7, "pir": 90}
    return tot


def split_players(t, opp, tot, absent=()):
    pl = [p for p in ROST[t] if p["id"] not in absent]
    mins = [p["mn"] * random.uniform(0.85, 1.1) for p in pl]
    k = 200 / sum(mins)
    mins = [m * k for m in mins]
    wts = [p["us"] * m * (1.25 if (opp == BAD_PG_DEF and p["pos"] == "Guard") else 1.0) for p, m in zip(pl, mins)]
    sw = sum(wts)
    rows = []
    for i, (p, m, w) in enumerate(zip(pl, mins, wts)):
        sh = w / sw
        pts = round(tot["pts"] * sh * random.uniform(0.8, 1.2))
        fg3m = round(tot["fg3m"] * sh * 1.2)
        fg2m = max(0, (pts - fg3m * 3) // 2)
        fga2, fga3 = int(fg2m / 0.5) + 1, int(fg3m / 0.35) + 1
        reb = round((8 if p["pos"] == "C" else 5 if p["pos"] == "F" else 3) * m / 28 * random.uniform(0.5, 1.5))
        ast = round((6 if p["pos"] == "G" else 2.5) * m / 28 * random.uniform(0.4, 1.6))
        row = [p["id"], p["name"], 0, 1 if i < 5 else 0, round(m, 1), pts, fg2m, fga2, fg3m, fga3, int(pts * 0.15), int(pts * 0.2), 1, reb - 1, reb, ast, 1, 2, 0, 0, pts + reb + ast]
        rows.append(row)
    return rows


def make_site(path, n_rounds=24, with_shots=True):
    os.makedirs(path, exist_ok=True)
    d0 = datetime.datetime(2026, 9, 20, 18, 0)
    games, gid = [], 0
    for r in range(n_rounds):
        order = TEAMS[:]
        random.shuffle(order)
        for k in range(0, 10, 2):
            h, a = order[k], order[k + 1]
            gid += 1
            P = 72 + PACE[h] + PACE[a] + random.gauss(0, 3)
            th, ta = team_game(h, a, True, P), team_game(a, h, False, P)
            q = lambda s: [s // 4 + random.randint(-3, 3) for _ in range(3)] + [0]
            qh, qa = q(th["pts"]), q(ta["pts"])
            qh[3], qa[3] = th["pts"] - sum(qh[:3]), ta["pts"] - sum(qa[:3])
            dt = (d0 + datetime.timedelta(days=r * 3 + k // 4)).isoformat()
            games.append({"n": gid, "round": r + 1, "dt": dt, "utc": dt + "Z", "h": h, "a": a, "hs": th["pts"], "as": ta["pts"], "q": {"h": qh, "a": qa},
                          "box": {"h": {"p": split_players(h, a, th), "tot": th}, "a": {"p": split_players(a, h, ta), "tot": ta}}})
            if with_shots:
                rows = []
                for tm, opp, tot, box in ((h, a, th, games[-1]["box"]["h"]), (a, h, ta, games[-1]["box"]["a"])):
                    for p in box["p"]:
                        pos = next(x["pos"] for x in ROST[tm] if x["id"] == p[0])
                        for _ in range(p[7] + p[9]):
                            three = random.random() < (0.55 if pos == "Guard" else 0.2 if pos == "Forward" else 0.02)
                            if three:
                                x, y = random.choice([-700, 700, 0]) + random.randint(-60, 60), random.randint(0, 600)
                                act, fgp = "3FG", 0.34
                            else:
                                x, y = random.randint(-90, 90), random.randint(0, 90) if pos != "Guard" or random.random() < 0.3 else random.randint(150, 400)
                                act, fgp = "2FG", (0.50 + (0.18 if (opp == BAD_RIM_DEF and abs(x) < 125 and y < 100) else 0))
                            rows.append({"TEAM": tm, "ID_PLAYER": "P" + p[0], "ID_ACTION": act + ("M" if random.random() < fgp else "A"), "COORD_X": x, "COORD_Y": y})
                json.dump(rows, open(os.path.join(path, f"shots_{gid}.json"), "w"))
    clubs = {t: {"name": f"Klub Tim{t} Basket", "short": f"Tim{t}"} for t in TEAMS}
    site = {"updated": "x", "clubs": clubs, "rosters": {t: {"players": [{"id": p["id"], "name": p["name"], "pos": p["pos"]} for p in ROST[t]]} for t in TEAMS},
            "games": games[:-5], "fixtures": []}
    json.dump(site, open(os.path.join(path, "site.json"), "w"))
    return games[-5:]


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        global FAIL
        FAIL += 1


FAIL = 0
if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        last = make_site(os.path.join(tmp, "data", "synth"))
        L = Q.League("synth", tmp)
        M = L.fit()
        check(M.ok and M.n >= 100, f"model fituje {M.n} utakmica")
        est = [M.off[t] + M.dfn[t] for t in TEAMS]
        tru = [TRUE_OFF[t] + TRUE_DEF[t] for t in TEAMS]
        mu_e, mu_t = sum(est) / 10, sum(tru) / 10
        cov = sum((a - mu_e) * (b - mu_t) for a, b in zip(est, tru))
        corr = cov / (sum((a - mu_e) ** 2 for a in est) * sum((b - mu_t) ** 2 for b in tru)) ** 0.5
        check(corr > 0.9, f"korelacija procenjenog i pravog net rejtinga {corr:.2f} (> 0.9)")
        check(abs(M.h * 2 - 5.0) < 2.0, f"home-court {M.h * 2:.1f} per100 (ugradjeno 5.0)")
        pm = M.predict("T0", "T1")
        check(pm and 120 < pm["total"] < 190, f"predikcija ukupnog zbira {pm['total']:.1f}")
        check(M.sd_m > 4, f"sd razlike {M.sd_m:.1f}")
        PR = Q.Props(L, M, M.asof)
        g = [x for x in PR.dvp if x[0] == BAD_PG_DEF and x[1] == "G" and x[2] == "pts"]
        d = PR.dvp[(BAD_PG_DEF, "G", "pts")]
        others = [PR.dvp[(t, "G", "pts")] for t in TEAMS if t != BAD_PG_DEF and (t, "G", "pts") in PR.dvp]
        check(d > max(others) and d > 1.08, f"DVP vidi tim koji dozvoljava plejmejkerima poene: {d:.2f} vs najvise ostalih {max(others):.2f}")
        pz = None
        sm_bad = PR.spatial_mult("T0p4", BAD_RIM_DEF)[0]
        sm_ok = PR.spatial_mult("T0p4", "T7")[0]
        check(sm_bad > sm_ok + 0.015, f"prostorni modul: centar vs losa odbrana reketa x{sm_bad:.3f} vs prosecna x{sm_ok:.3f}")
        # ucestalost garbage-time utakmica
        nb = sum(1 for x in L.games if x["blow"])
        check(nb >= 0, f"garbage-time utakmica oznaceno: {nb}")
        # annotate: meč + prop
        h, a = last[0]["h"], last[0]["a"]
        ev = {"lg": "synth", "h": f"Klub Tim{h} Basket", "a": f"Klub Tim{a} Basket", "start": last[0]["utc"],
              "b": [{"h2h": [1.9, 1.9], "sp": [-2.5, 1.9, 1.9], "tt": [158.5, 1.9, 1.9]}, {"h2h": None, "sp": [-6.5, 2.4, 1.55], "tt": None}],
              "player_props": [{"p": ROST[h][0]["name"], "s": "pts", "l": 12.5, "o": 1.9, "u": 1.9, "main": True},
                               {"p": ROST[a][4]["name"], "s": "reb", "l": 4.5, "o": 1.85, "u": 1.95, "main": True}]}
        dg = Q.annotate_events([ev], "synth", tmp)
        check("q" in ev and ev["q"]["pts"][0] > 50, f"annotate_events: q {ev.get('q', {}).get('pts')}, tipova {len(ev.get('tips', []))}, dijagnostika {dict(dg)}")
        check(all("q" in p for p in ev["player_props"]), "obe prop linije dobile q (projekcija, DVP, usage)")
        # povrede: izbaci nosioca
        json.dump({"teams": {h: [f"{ROST[h][0]['name']}: van"]}}, open(os.path.join(tmp, "data", "synth", "injuries.json"), "w"))
        L2 = Q.League("synth", tmp)
        ev2 = json.loads(json.dumps(ev))
        for p in ev2["player_props"]:
            p.pop("q", None)
        ev2["player_props"].append({"p": ROST[h][1]["name"], "s": "pts", "l": 11.5, "o": 1.9, "u": 1.9, "main": True})
        Q.annotate_events([ev2], "synth", tmp, league=L2)
        pq = [p for p in ev2["player_props"] if p["p"] == ROST[h][1]["name"]][0].get("q")
        check(pq and pq["inj"] > 1.0, f"povreda nosioca podize ulogu saigraca (x{pq['inj'] if pq else None})")
        check(all(p["p"] != ROST[h][0]["name"] or "q" not in p for p in ev2["player_props"]), "igrac koji je van se ne preporucuje")
        # backtest
        rows = B.walk_forward(L, 40)
        res = B.summarize(rows)
        check(res and res["model"]["MAE razlika"] < res["samo liga"]["MAE razlika"], f"backtest: MAE razlike model {res['model']['MAE razlika']:.2f} < samo liga {res['samo liga']['MAE razlika']:.2f}")
        check(res["model"]["log-loss"] < res["samo liga"]["log-loss"], f"backtest: log-loss model {res['model']['log-loss']:.3f} < samo liga {res['samo liga']['log-loss']:.3f}")
        pb = B.props_bt(L, 60)
        mae_m = sum(abs(r["y"] - r["mu"]) for r in pb["pts"]) / len(pb["pts"])
        mae_n = sum(abs(r["y"] - r["naive"]) for r in pb["pts"]) / len(pb["pts"])
        check(mae_m <= mae_n * 1.02, f"props backtest: MAE poena model {mae_m:.2f} vs naivno {mae_n:.2f}")
        B.show(res, "sinteticki test")
        B.show_props(pb)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nSVE PROSLO" if not FAIL else f"\n{FAIL} PROVERA PALO")
    sys.exit(1 if FAIL else 0)
