#!/usr/bin/env python3
"""Statshub PROBA: otvara nekoliko javnih stranica (liga, 2 tima, 2 meca) u pravom pregledacu i snima JSON odgovore koje sama stranica
ucitava (XHR/fetch) + __NEXT_DATA__. Ne zaobilazi nista: robots.txt dozvoljava sve, jedan pregledac, razmak 3-6 s, staje na 403/429.
Cilj: da vidimo kojim endpointima Statshub servira formu, H2H, xG, korner, kartone, postave i stil igre, pa da napisem pravi parser.

  pip install playwright && playwright install chromium
  python statshub_probe.py                 # Premier liga (id 17), 2 tima, 2 meca
  python statshub_probe.py --teams 3 --fixtures 3
Rezultat: data/statshub_probe/ (index.txt + *.json). Posalji mi index.txt i 3-4 najveca .json fajla."""
import argparse, json, os, random, re, sys, time
from urllib.parse import urlparse

BASE = "https://www.statshub.com"
TABS = ("H2H", "Team Stats", "Lineups", "Trends", "Opponent Stats", "Player Stats", "Tables", "Charts")
OUT = os.path.join("data", "statshub_probe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="/leagues/england/premier-league/17")
    ap.add_argument("--teams", type=int, default=2)
    ap.add_argument("--fixtures", type=int, default=2)
    ap.add_argument("--headed", action="store_true", help="pokazi pregledac")
    a = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("pip install playwright && playwright install chromium")
    os.makedirs(OUT, exist_ok=True)
    seen, index, n = set(), [], [0]

    stop = []

    def on_resp(r):
        try:
            host = urlparse(r.url).hostname or ""
            if not host.endswith("statshub.com"):          # sentry, analitika, reklame itd. nas ne zanimaju (i njihov 429 nije Statshubov)
                return
            ct = r.headers.get("content-type", "")
            if r.status in (403, 429):
                stop.append(f"HTTP {r.status} od {r.url}")
                return
            if "json" not in ct or r.request.resource_type not in ("xhr", "fetch") or r.url in seen:
                return
            body = r.body()
            seen.add(r.url)
            n[0] += 1
            fn = f"{n[0]:03d}_" + re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(r.url).path + "_" + urlparse(r.url).query)[:80] + ".json"
            open(os.path.join(OUT, fn), "wb").write(body)
            try:
                j = json.loads(body)
                keys = list(j)[:12] if isinstance(j, dict) else f"lista[{len(j)}]"
            except ValueError:
                keys = "?"
            index.append(f"{fn}\t{r.status}\t{len(body)}\t{r.url}\t{keys}")
        except Exception as e:
            index.append(f"greska\t{r.url}\t{e}")

    pause = lambda: time.sleep(random.uniform(3, 6))
    with sync_playwright() as p:
        br = p.chromium.launch(headless=not a.headed)
        pg = br.new_context(locale="en-GB").new_page()
        pg.on("response", on_resp)

        def visit(path, tabs=False):
            url = path if path.startswith("http") else BASE + path
            print("otvaram", url)
            pg.goto(url, wait_until="networkidle", timeout=60000)
            nd = pg.evaluate("() => { const e = document.getElementById('__NEXT_DATA__'); return e ? e.textContent : null }")
            if nd:
                n[0] += 1
                fn = f"{n[0]:03d}_NEXT_DATA_" + re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(url).path)[:60] + ".json"
                open(os.path.join(OUT, fn), "w", encoding="utf-8").write(nd)
                index.append(f"{fn}\t200\t{len(nd)}\t{url}\t__NEXT_DATA__")
            if tabs:
                for t in TABS:
                    if stop:
                        break
                    try:
                        pg.get_by_text(t, exact=True).first.click(timeout=3000)
                        pg.wait_for_load_state("networkidle", timeout=15000)
                        print("  tab", t)
                    except Exception:
                        pass
                    time.sleep(random.uniform(1.5, 3))
            pause()

        visit(a.league)
        hrefs = pg.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))")
        teams = [h for h in dict.fromkeys(hrefs) if re.match(r"^/(en/)?team/", h or "")][:a.teams]
        fixes = [h for h in dict.fromkeys(hrefs) if re.match(r"^/(en/)?fixture/", h or "")][:a.fixtures]
        print("linkovi na timove:", teams, "| na mecove:", fixes)
        for h in teams + fixes:
            if stop:
                break
            visit(h, tabs=True)
        br.close()
    if stop:
        print("STAJEM (Statshub odbija zahteve, ne zaobilazim):", stop[0])
    open(os.path.join(OUT, "index.txt"), "w", encoding="utf-8").write("fajl\tstatus\tbajtova\turl\tkljucevi\n" + "\n".join(index))
    print(f"\nGotovo: {n[0]} JSON odgovora u {OUT}/ (index.txt = pregled). Posalji index.txt i najveca 3-4 fajla.")


if __name__ == "__main__":
    main()
