"""
Korak 5: kvote kladionica -> data/odds.json (The Odds API, sport basketball_euroleague).
Kljuc ide u GitHub: Settings > Secrets and variables > Actions > New repository secret, ime ODDS_API_KEY.
Da potrosnja kredita ostane mala, kvote se osvezavaju najvise jednom u MIN_AGE_H sati.
"""
import json, os, time
from pathlib import Path
import requests

OUT = Path("data/odds.json"); KEY = os.environ.get("ODDS_API_KEY"); MIN_AGE_H = 6
URL = "https://api.the-odds-api.com/v4/sports/basketball_euroleague/odds/"


def main():
    if not KEY:
        print("Nema ODDS_API_KEY, preskacem."); return
    if OUT.exists():
        try:
            if time.time() - json.loads(OUT.read_text(encoding="utf-8"))["fetched"] < MIN_AGE_H * 3600:
                print("Kvote su sveze."); return
        except Exception:
            pass
    for mk in ("h2h,spreads,totals", "h2h"):      # ako hendikep/total nisu dostupni, probaj samo pobednika
        r = requests.get(URL, params={"apiKey": KEY, "regions": "eu", "markets": mk, "oddsFormat": "decimal"}, timeout=30)
        if r.status_code == 200: break
        print("odds", mk, r.status_code, r.text[:200])
    else:
        return
    ev = []
    for e in r.json():
        h, a, books = e["home_team"], e["away_team"], []
        for b in e.get("bookmakers", []):
            row = {"key": b["key"], "title": b["title"]}
            for m in b.get("markets", []):
                o = {x["name"]: x for x in m["outcomes"]}
                if m["key"] == "h2h":
                    row["h2h"] = [o.get(h, {}).get("price"), o.get(a, {}).get("price")]
                elif m["key"] == "spreads" and h in o and a in o:
                    row["sp"] = [o[h].get("point"), o[h]["price"], o[a]["price"]]
                elif m["key"] == "totals" and "Over" in o and "Under" in o:
                    row["tt"] = [o["Over"].get("point"), o["Over"]["price"], o["Under"]["price"]]
            books.append(row)
        ev.append({"h": h, "a": a, "t": e["commence_time"], "b": books})
    OUT.write_text(json.dumps({"fetched": time.time(), "remaining": r.headers.get("x-requests-remaining"), "events": ev},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"odds.json: {len(ev)} utakmica, preostalo kredita: {r.headers.get('x-requests-remaining')}")


if __name__ == "__main__":
    main()
