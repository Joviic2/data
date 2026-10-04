"""Povlaci Euroleague kvote sa the-odds-api.com i snima data/odds.json
u formatu koji index.html vec ocekuje:
{fetched, remaining, events:[{h, a, start, b:[{key,title,h2h:[1,2],sp:[linija,dom,gost],tt:[linija,vise,manje]}]}]}
Kljuc se cita iz okruzenja (GitHub Secret ODDS_API_KEY), nikad iz fajla."""
import os, sys, json, time, requests

API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
if not API_KEY:
    sys.exit("Nedostaje ODDS_API_KEY (GitHub Secret). Ostavljam stari data/odds.json.")

SPORT = os.environ.get("ODDS_SPORT", "basketball_euroleague")
REGIONS = os.environ.get("ODDS_REGIONS", "eu")  # 1 region x 3 trzista = 3 kredita po pozivu

r = requests.get(
    f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds",
    params={
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
    },
    timeout=30,
)
if r.status_code != 200:
    sys.exit(f"Odds API greska {r.status_code}: {r.text[:200]}. Ostavljam stari data/odds.json.")

remaining = r.headers.get("x-requests-remaining")
events = []
for ev in r.json():
    home, away = ev["home_team"], ev["away_team"]
    books = []
    for bk in ev.get("bookmakers", []):
        row = {"key": bk["key"], "title": bk["title"]}
        for mk in bk.get("markets", []):
            o = {x["name"]: x for x in mk["outcomes"]}
            if mk["key"] == "h2h" and home in o and away in o:
                row["h2h"] = [o[home]["price"], o[away]["price"]]
            elif mk["key"] == "spreads" and home in o and away in o:
                row["sp"] = [o[home].get("point"), o[home]["price"], o[away]["price"]]
            elif mk["key"] == "totals" and "Over" in o and "Under" in o:
                row["tt"] = [o["Over"].get("point"), o["Over"]["price"], o["Under"]["price"]]
        if "h2h" in row or "sp" in row or "tt" in row:
            books.append(row)
    events.append({"id": ev["id"], "h": home, "a": away, "start": ev["commence_time"], "b": books})

out = {"fetched": int(time.time()), "remaining": remaining, "events": events}
os.makedirs("data", exist_ok=True)
with open("data/odds.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print(f"OK: {len(events)} utakmica, preostalo kredita: {remaining}")
