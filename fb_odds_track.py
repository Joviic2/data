#!/usr/bin/env python3
"""Drop / Live Odds Tracker (fudbal).

Svako pokretanje fetch_superbet_football.py upisuje snimak kvota u data/<liga>/odds_history.json,
a odavde se racuna kretanje trzista (data/<liga>/odds_moves.json) koje frontend koristi za korekciju EV-a.

  python fb_odds_track.py --league epl        # samo preracuna odds_moves.json iz istorije (bez mreze)

Kretanje = promena VERovatnoce bez marze (no-vig) izmedju referentnog snimka i poslednjeg:
  - referentni snimak: najstariji unutar poslednjih --win sati (default 24), a ako ga nema, prvi koji postoji;
  - 'sharp' = |dp| >= --thr (default 0.025 = 2.5 p.p.) i raspon snimaka >= 1h.
Ako trzista nema istoriju (jedan snimak), nema signala; nista se ne izmislja.
"""
import os, sys, json, time, argparse, datetime

MAXS, KEEP_AFTER = 400, 3 * 86400


def key(e): return f"{e['h']}|{e['a']}|{e.get('start') or ''}"


def snap(e):
    ou = {}
    for l in e.get("ou") or []:
        if l[0] in (1.5, 2.5, 3.5) and len(l) >= 3 and l[1] and l[2]: ou[str(l[0])] = [l[1], l[2]]
    return [int(time.time()), e.get("x12"), ou, e.get("btts")]


def same(a, b): return a[1:] == b[1:]


def record(d, evs):
    hp = os.path.join(d, "odds_history.json")
    try: H = json.load(open(hp, encoding="utf-8"))
    except Exception: H = {}
    now = time.time()
    for e in evs:
        s = snap(e)
        if not (s[1] or s[2] or s[3]): continue
        h = H.setdefault(key(e), {"start": e.get("start"), "snaps": []})
        if not h["snaps"] or not same(h["snaps"][-1], s): h["snaps"].append(s)
        h["snaps"] = h["snaps"][-MAXS:]
    for k in list(H):                                   # stari mecevi ispadaju
        try: st = datetime.datetime.fromisoformat((H[k].get("start") or "").replace("Z", "+00:00")).timestamp()
        except Exception: st = None
        if st and st < now - KEEP_AFTER: del H[k]
    tmp = hp + ".tmp"
    json.dump(H, open(tmp, "w", encoding="utf-8"), separators=(",", ":")); os.replace(tmp, hp)
    return moves(d, H)


def nv(pair):                                            # [o_a,o_b] -> verovatnoca prve strane bez marze
    a, b = 1 / pair[0], 1 / pair[1]; return a / (a + b)


def nv3(t):
    q = [1 / x for x in t]; s = sum(q); return [x / s for x in q]


def moves(d, H=None, win=24, thr=0.025):
    if H is None: H = json.load(open(os.path.join(d, "odds_history.json"), encoding="utf-8"))
    now = time.time(); out = {}
    for k, h in H.items():
        S = h.get("snaps") or []
        if len(S) < 2: continue
        last = S[-1]
        old = [s for s in S[:-1] if s[0] >= last[0] - win * 3600]
        ref = old[0] if old else S[0]
        span = (last[0] - ref[0]) / 3600
        if span < 0.05: continue
        m = {"span_h": round(span, 1), "n": len(S), "ou": {}, "x12": None, "btts": None}
        for l, v in (last[2] or {}).items():
            r = (ref[2] or {}).get(l)
            if r: m["ou"][l] = {"dp": round(nv(v) - nv(r), 4), "o0": r[0], "o1": v[0], "u0": r[1], "u1": v[1]}   # dp = promena verovatnoce za PREKO
        if last[1] and ref[1]:
            a, b = nv3(ref[1]), nv3(last[1]); m["x12"] = {"dp": [round(y - x, 4) for x, y in zip(a, b)], "o0": ref[1], "o1": last[1]}
        if last[3] and ref[3]:
            m["btts"] = {"dp": round(nv(last[3]) - nv(ref[3]), 4), "o0": ref[3][0], "o1": last[3][0]}   # dp za GG
        try: st = datetime.datetime.fromisoformat((h.get("start") or "").replace("Z", "+00:00")).timestamp(); m["mins_to_ko"] = int((st - now) / 60)
        except Exception: m["mins_to_ko"] = None
        dps = [abs(v["dp"]) for v in m["ou"].values()] + ([abs(x) for x in m["x12"]["dp"]] if m["x12"] else []) + ([abs(m["btts"]["dp"])] if m["btts"] else [])
        m["sharp"] = bool(dps) and max(dps) >= thr and span >= 1
        out[k] = m
    p = os.path.join(d, "odds_moves.json")
    json.dump({"updated": int(now), "thr": thr, "win_h": win, "moves": out}, open(p + ".tmp", "w", encoding="utf-8"), separators=(",", ":")); os.replace(p + ".tmp", p)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--league", default="epl"); ap.add_argument("--win", type=float, default=24); ap.add_argument("--thr", type=float, default=0.025)
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from fb_leagues import LEAGUES; d = os.path.join("data", LEAGUES[a.league]["dir"])
    except Exception: d = os.path.join("data", a.league)
    M = moves(d, win=a.win, thr=a.thr)
    sh = [k for k, v in M.items() if v["sharp"]]
    print(f"{len(M)} meceva sa kretanjem, {len(sh)} izrazitih"); [print(" ", k, M[k]["ou"].get("2.5"), M[k]["x12"] and M[k]["x12"]["dp"]) for k in sh[:15]]
