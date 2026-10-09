"""Belezi otvorenu i zatvarajucu Pinnacle liniju po utakmici u data/clv.json (osnova za CLV i ucenje tezina).
Zatvarajuca linija = poslednji snimak pre pocetka utakmice."""
import json, time
from datetime import datetime, timezone
def load(p, d):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return d
fair = lambda a, b: round((1 / a) / (1 / a + 1 / b), 4)
od, clv = load("data/odds.json", {}), load("data/clv.json", {})
now = datetime.now(timezone.utc)
for e in od.get("events", []):
    try: start = datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
    except Exception: continue
    pin = next((b for b in e["b"] if b["key"] == "pinnacle"), None)
    if not pin: continue
    snap = {"t": int(time.time())}
    if pin.get("h2h"): snap["h"] = fair(*pin["h2h"]); snap["ho"] = pin["h2h"]
    if pin.get("sp") and pin["sp"][0] is not None: snap["sp"] = [pin["sp"][0], fair(pin["sp"][1], pin["sp"][2])]
    if pin.get("tt") and pin["tt"][0] is not None: snap["tt"] = [pin["tt"][0], fair(pin["tt"][1], pin["tt"][2])]
    r = clv.setdefault(e.get("id", e["h"] + e["a"]), {"h": e["h"], "a": e["a"], "start": e["start"], "open": snap})
    if now < start: r["close"] = snap  # posle starta se zamrzava
json.dump(clv, open("data/clv.json", "w", encoding="utf-8"))
print(f"CLV: {len(clv)} utakmica")
