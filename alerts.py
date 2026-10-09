"""Telegram dojave: trazi kladionice cije su kvote bolje od Pinnaclea bez marže.
Nema modela, samo "oštra" referenca. Linije se poredjene pomeranjem (spread sd 11.5, total sd 13).
Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Opciono: ALERT_EDGE (default 0.04)."""
import os, json, requests
from statistics import NormalDist
ND = NormalDist()
TOK, CHAT = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
EDGE, MAXE = float(os.environ.get("ALERT_EDGE", "0.04")), 0.15
SKIP = {"pinnacle", "betfair_ex_eu", "matchbook"}  # berze i referenca se ne alarmiraju
fair = lambda a, b: (1 / a) / (1 / a + 1 / b)

def load(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d

od, sent = load("data/odds.json", {}), load("data/alerts_sent.json", [])
seen, out = set(sent), []
for e in od.get("events", []):
    pin = next((b for b in e["b"] if b["key"] == "pinnacle"), None)
    if not pin:
        continue
    mt = f'{e["h"]} - {e["a"]}'
    for b in e["b"]:
        if b["key"] in SKIP:
            continue
        cand = []  # (market, pick, odds, fair_prob)
        if pin.get("h2h") and b.get("h2h"):
            p = fair(*pin["h2h"])
            cand += [("Pobednik", e["h"], b["h2h"][0], p), ("Pobednik", e["a"], b["h2h"][1], 1 - p)]
        if pin.get("sp") and b.get("sp") and pin["sp"][0] is not None and b["sp"][0] is not None:
            lp, l = pin["sp"][0], b["sp"][0]
            z = ND.inv_cdf(min(.999, max(.001, fair(pin["sp"][1], pin["sp"][2]))))
            p = ND.cdf(z + (l - lp) / 11.5)
            cand += [(f"Hendikep {l:+g}", e["h"], b["sp"][1], p), (f"Hendikep {-l:+g}", e["a"], b["sp"][2], 1 - p)]
        if pin.get("tt") and b.get("tt") and pin["tt"][0] is not None and b["tt"][0] is not None:
            lp, l = pin["tt"][0], b["tt"][0]
            z = ND.inv_cdf(min(.999, max(.001, fair(pin["tt"][1], pin["tt"][2]))))
            p = ND.cdf(z + (lp - l) / 13)
            cand += [(f"Ukupno {l:g}", "Vise", b["tt"][1], p), (f"Ukupno {l:g}", "Manje", b["tt"][2], 1 - p)]
        for mk, pick, o, p in cand:
            ev = p * o - 1
            k = f'{e.get("id", mt)}|{mk}|{pick}|{b["key"]}|{o}'
            if EDGE <= ev <= MAXE and k not in seen:
                out.append((ev, k, f'<b>{mt}</b>\n{mk}: <b>{pick}</b> @ {o} ({b["title"]})\nEdge vs Pinnacle: +{ev*100:.1f}%'))
out.sort(reverse=True)
print(f"{len(out)} novih dojava")
if out and TOK and CHAT:
    for ev, k, txt in out[:8]:
        r = requests.post(f"https://api.telegram.org/bot{TOK}/sendMessage", json={"chat_id": CHAT, "text": txt, "parse_mode": "HTML"}, timeout=20)
        if r.ok:
            sent.append(k)
    json.dump(sent[-2000:], open("data/alerts_sent.json", "w"))
