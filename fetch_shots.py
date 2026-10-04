#!/usr/bin/env python3
"""Skida šuteve (sa koordinatama) za odigrane utakmice iz Euroleague API-ja
i snima ih kao data/shots_<broj utakmice>.json, pored data/site.json.
Pokretanje: python fetch_shots.py   (prvo mora da postoji data/site.json)
Podešavanje: EL_SEASON (podrazumevano E2026), DATA_DIR (podrazumevano data)."""
import json, os, time, urllib.request

SEASON = os.environ.get("EL_SEASON", "E2026")
DATA = os.environ.get("DATA_DIR", "data")
URL = "https://live.euroleague.net/api/Points?gamecode={g}&seasoncode={s}"


def get(g):
    req = urllib.request.Request(URL.format(g=g, s=SEASON), headers={"User-Agent": "Mozilla/5.0 (value-analyzer)"})
    err = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            err = e
            time.sleep(2 * (i + 1))
    raise err


def main():
    with open(os.path.join(DATA, "site.json"), encoding="utf-8") as f:
        site = json.load(f)
    ok = skip = bad = 0
    for g in site["games"]:
        n = g["n"]
        path = os.path.join(DATA, f"shots_{n}.json")
        if os.path.exists(path) and os.path.getsize(path) > 50:
            skip += 1
            continue
        try:
            j = get(n)
            rows = j.get("Rows", []) if isinstance(j, dict) else j
            if not rows:
                print("prazno:", n)
                bad += 1
                continue
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f, separators=(",", ":"), ensure_ascii=False)
            ok += 1
        except Exception as e:
            print("greška:", n, e)
            bad += 1
        time.sleep(0.5)
    print(f"novih {ok}, preskočeno {skip}, neuspešno {bad}")


if __name__ == "__main__":
    main()
