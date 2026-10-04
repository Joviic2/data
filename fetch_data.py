"""
Korak 1: skuplja Euroleague podatke (rezultati, boxscore, sutevi, klubovi, igraci, raspored).
Pokrece ga GitHub Actions (vidi .github/workflows/update.yml), pa onda build_site.py.
Rezultat ide u folder data/.
"""
import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

SEASON = os.environ.get("SEASON", "E2026")  # E2026 = sezona 2026/27
DATA = Path("data")
RAW = DATA / "raw"
RAW.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (personal basketball analytics project)"}
MAX_SAVE = 5_000_000  # ranije je ovde bilo 400_000 pa su igraci i raspored bili odsečeni
report = {"season": SEASON, "run_at": datetime.now(timezone.utc).isoformat(), "checks": []}


def fetch(name, url, params=None, save_as=None):
    """Poziva endpoint, belezi status u izvestaj i (opciono) cuva odgovor."""
    entry = {"name": name, "url": url, "params": params}
    resp = None
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        entry.update(status=resp.status_code, bytes=len(resp.content),
                     content_type=resp.headers.get("content-type"), preview=resp.text[:200])
        if resp.ok and save_as:
            if len(resp.text) > MAX_SAVE:
                entry["warning"] = "odgovor veci od limita, nije sacuvan"
            else:
                (RAW / save_as).write_text(resp.text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)
    report["checks"].append(entry)
    time.sleep(0.3)
    return resp if resp is not None and resp.ok else None


def parse_results(xml_text):
    root = ET.fromstring(xml_text)
    games = []
    for node in root.iter():
        if node.tag.split("}")[-1].lower() == "game":
            games.append({c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in node})
    return games


def is_final(path):
    """True ako je sacuvan boxscore gotov (Live=false). Utakmice u toku se skidaju ponovo."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("Live") is False
    except Exception:  # noqa: BLE001
        return False


def main():
    # 1) Rezultati svih odigranih utakmica
    resp = fetch("results", "https://api-live.euroleague.net/v1/results",
                 {"seasonCode": SEASON}, "results.xml")
    games = parse_results(resp.text) if resp else []
    if games:  # ako API padne, ne brisemo prethodne rezultate
        (DATA / "results.json").write_text(json.dumps(games, ensure_ascii=False, indent=1), encoding="utf-8")
    report["games_found"] = len(games)

    # 2) Boxscore i sutevi za svaku utakmicu sa rezultatom
    played = [g for g in games if g.get("homescore") and g.get("awayscore")]
    report["games_played"] = len(played)
    for g in played:
        code = g.get("gamenumber") or g.get("gamecode")
        if not code:
            continue
        for kind, ep in (("boxscore", "Boxscore"), ("shots", "Points")):
            target = RAW / f"{kind}_{code}.json"
            if target.exists() and is_final(target):
                continue
            fetch(f"{kind}_{code}", f"https://live.euroleague.net/api/{ep}",
                  {"gamecode": code, "seasoncode": SEASON}, f"{kind}_{code}.json")

    # 3) Klubovi, igraci i treneri, raspored
    base = f"https://api-live.euroleague.net/v2/competitions/E/seasons/{SEASON}"
    for name, url, params in [
        ("v2_clubs", f"{base}/clubs", None),
        ("v2_people", f"{base}/people", None),
        ("v2_games", f"{base}/games", None),
        ("v1_standings", "https://api-live.euroleague.net/v1/standings", {"seasonCode": SEASON}),
    ]:
        fetch(name, url, params, f"recon_{name}.txt")

    ok = sum(1 for c in report["checks"] if c.get("status") == 200)
    report["summary"] = f"{ok} od {len(report['checks'])} poziva uspelo"
    (DATA / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(report["summary"], "| odigranih utakmica:", report["games_played"])


if __name__ == "__main__":
    main()
