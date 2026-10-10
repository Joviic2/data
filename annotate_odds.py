#!/usr/bin/env python3
"""annotate_odds.py - dodaje model (q, tips, obrazlozenje) u data/odds_superbet.json i data/<liga>/odds_superbet.json.

  python annotate_odds.py              # sve lige
  python annotate_odds.py aba acb      # samo navedene

Za svaku ligu: (1) pun model iz box score-a i statistike igraca (quant_model.annotate_events); ako liga nema box score-ove,
(2) rezervni model iz rezultata (quant_model.annotate_scores, koristi i history_games.json iz sync_data.py).
Ne menja kvote ni 'an'/'rot' polja, pa radi iza bilo koje verzije fetch_superbet.py. Moze se pokretati koliko god puta (idempotentno)."""
import json, os, sys
import quant_model as Q

LEAGUES = ("euroleague", "aba", "acb", "lnb", "bbl")


def path(lg):
    return os.path.join("data", "odds_superbet.json") if lg == "euroleague" else os.path.join("data", lg, "odds_superbet.json")


def main(argv):
    leagues = [a for a in argv if a in LEAGUES] or list(LEAGUES)
    report = {}
    for lg in leagues:
        fp = path(lg)
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except (OSError, ValueError):
            print(f"[{lg}] nema {fp}")
            continue
        ev = d.get("events") or []
        for e in ev:
            e.pop("q", None)
            e.pop("tips", None)
        for e in ev:
            e.setdefault("lg", lg)
        try:
            dg = Q.annotate_events(ev, lg)
            mode = "box score + igraci"
            if not dg.get("mecevi_sa_modelom"):
                dg = Q.annotate_scores(ev, lg)
                mode = "samo rezultati"
        except Exception as ex:
            print(f"[{lg}] greska modela: {ex}")
            continue
        n_q = sum(1 for e in ev if e.get("q"))
        n_t = sum(len(e.get("tips") or []) for e in ev)
        if not n_q:
            print(f"[{lg}] model nije mogao da se napravi: {dict(dg)} (potrebni rezultati ili box score u data/{'' if lg == 'euroleague' else lg + '/'}site.json)")
        else:
            print(f"[{lg}] {mode}: {n_q}/{len(ev)} meceva sa modelom, {n_t} tipova, {dict(dg)}")
        report[lg] = {"events": len(ev), "with_model": n_q, "tips": n_t, "mode": mode if n_q else None}
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, fp)
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
