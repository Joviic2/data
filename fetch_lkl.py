#!/usr/bin/env python3
"""Official LKL 2026/27 schedule, results and player protocol importer.

The public LKL site serves game protocols in its interactive match page. This
uses Chromium only for those official protocol tables; schedules are read from
the public HTML calendar. Writes data/lkl/site.json in the app's shared schema.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = "https://lkl.lt"
OUT = Path("data/lkl/site.json")
CACHE = Path("data/cache/lkl_boxes.json")
MAX_BOX = max(1, int(os.environ.get("LKL_MAX_BOX", "8")))
PCOLS = ["id", "name", "no", "gs", "min", "pts", "fg2m", "fg2a", "fg3m", "fg3a",
         "ftm", "fta", "or", "dr", "tr", "ast", "stl", "tov", "blk", "pf", "pir", "pm"]
MONTHS = {"sausio":1,"vasario":2,"kovo":3,"balandžio":4,"gegužės":5,"birželio":6,
          "liepos":7,"rugpjūčio":8,"rugsėjo":9,"spalio":10,"lapkričio":11,"gruodžio":12}
CODES = {"ZAL":"ZAL","ŽAL":"ZAL","JUV":"JUV","LIE":"LIE","NEP":"NEP","NEV":"NEV",
         "RYT":"RYT","ŠIA":"SIA","SIA":"SIA","TAU":"TAU","GAR":"GAR","JON":"JON"}
TZ = ZoneInfo("Europe/Vilnius")

def clean(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()

def integer(x):
    m = re.search(r"-?\d+", clean(x).replace("−", "-"))
    return int(m.group()) if m else 0

def mins(x):
    m = re.search(r"(\d+):(\d+)", clean(x))
    return round(int(m[1]) + int(m[2]) / 60, 3) if m else 0

def ma(x):
    m = re.search(r"(\d+)\s*/\s*(\d+)", clean(x))
    return (int(m[1]), int(m[2])) if m else (0, 0)

def date_from(text):
    m = re.search(r"(20\d{2})\s*m\.\s*([\wąčęėįšųūž]+)\s+(\d{1,2})\s*d\.", clean(text).lower())
    if not m:
        return None
    month = MONTHS.get(m[2])
    if not month:
        return None
    return datetime(int(m[1]), month, int(m[3]), tzinfo=TZ)

def code_from(a):
    short = ""
    st = a.find("strong")
    if st:
        short = clean(st.get_text()).upper()
    if short not in CODES:
        short = clean(a.get_text(" ", strip=True)).upper()
        short = re.sub(r"[^A-ZŽŠČ]", "", short)[:3]
    return CODES.get(short, short[:3] or "UNK")

def team_name(a):
    im = a.find("img", alt=True)
    if im:
        return clean(im.get("alt"))
    text = clean(a.get_text(" ", strip=True))
    return re.sub(r"\b[A-ZŽŠČ]{2,4}\b", "", text).strip() or text

def extract_pages(path, browser, max_pages=40):
    found = {}
    for page in range(1, max_pages + 1):
        url = f"{BASE}/index.php/{path}" + (f"?page={page}" if page > 1 else "")
        page = browser.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"LKL returned HTTP {response.status} for {url}")
            html = page.content()
        finally:
            page.close()
        soup = BeautifulSoup(html, "html.parser")
        selector = ".schedule-holder[data-championship='lkl']" if path == "tvarkarastis" else ".results-holder[data-championship='lkl']"
        holder = soup.select_one(selector)
        if holder is None:
            if page == 1:
                raise RuntimeError(f"LKL {path}: ne nalazim zvanični raspored")
            break
        season = holder.get("data-season")
        current_day = None
        page_ids = set()
        for child in holder.find_all(recursive=False):
            if "result-item" not in (child.get("class") or []):
                d = date_from(child.get_text(" ", strip=True))
                if d:
                    current_day = d
                continue
            link = child.select_one('a[href*="/rungtynes/"]')
            battle = child.select_one(".battle-row")
            if link is None or battle is None or current_day is None:
                continue
            mid = re.search(r"/rungtynes/(\d+)", link.get("href", ""))
            if not mid:
                continue
            teams = battle.select('a[href*="/komandos/"]')
            if len(teams) < 2:
                continue
            hc, ac = code_from(teams[0]), code_from(teams[1])
            if hc == "UNK" or ac == "UNK":
                continue
            names = (team_name(teams[0]), team_name(teams[1]))
            for c, nm in zip((hc, ac), names):
                clubs.setdefault(c, {"name":nm, "short":c, "crest":None})
            for side, c, a in (("h", hc, teams[0]), ("a", ac, teams[1])):
                image = a.find("img", src=True)
                if image:
                    clubs[c]["crest"] = urljoin(BASE, image["src"])
            score = re.search(r"(\d{1,3})\s*-\s*(\d{1,3})", clean(link.get_text(" ", strip=True)))
            tm = child.select_one(".location .text-lg")
            hour, minute = (0, 0)
            if tm:
                hm = re.search(r"(\d{1,2}):(\d{2})", tm.get_text())
                if hm:
                    hour, minute = int(hm[1]), int(hm[2])
            dt = current_day.replace(hour=hour, minute=minute)
            game = {"n":int(mid[1]),"h":hc,"a":ac,"dt":dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "url":urljoin(BASE, link["href"]), "season_id":season}
            if score:
                game["hs"], game["as"] = int(score[1]), int(score[2])
            found[game["n"]] = game
            page_ids.add(game["n"])
        if page > 1 and not page_ids:
            break
        time.sleep(0.12)
    return found

# Populated during calendar extraction.
clubs = {}

def read_cache():
    try:
        return json.loads(CACHE.read_text("utf-8"))
    except Exception:
        return {}

def write_cache(data):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")

def parse_table_rows(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.get_by_text("Protokolas", exact=True).click(timeout=15000)
    page.locator("table").first.wait_for(state="visible", timeout=20000)
    return page.locator("table").evaluate_all("""tables => tables.map(t => ({
      headers: Array.from(t.querySelectorAll('thead th')).map(x => x.innerText.trim()),
      rows: Array.from(t.querySelectorAll('tbody tr')).map(tr => ({
        cells: Array.from(tr.cells).map(td => td.innerText.trim()),
        player: tr.querySelector('a[href*="/zaidejai/"]')?.getAttribute('href') || null
      }))
    }))""")

def side_box(table):
    players = []
    official = None
    for rr in table.get("rows", []):
        c = rr.get("cells") or []
        if not c:
            continue
        if clean(c[0]).lower() == "total":
            official = c
            continue
        if len(c) < 20:
            continue
        href = rr.get("player") or ""
        slugm = re.search(r"/zaidejai/([^/?#]+)", href)
        if not slugm:
            continue
        pid = slugm[1]
        visible_name = clean(c[1]).replace("*", "").replace("(C)", "").strip()
        name = visible_name or pid.replace("-", " ").title()
        f2m, f2a = ma(c[5])
        f3m, f3a = ma(c[6])
        ftm, fta = ma(c[7])
        number = clean(c[0])
        starter = 1 if "*" in c[1] else 0
        row = [pid, name, number, starter, mins(c[2]), integer(c[3]),
               f2m, f2a, f3m, f3a, ftm, fta,
               integer(c[9]), integer(c[10]), integer(c[8]), integer(c[11]),
               integer(c[12]), integer(c[13]), integer(c[14]), integer(c[15]),
               integer(c[19]), integer(c[18])]
        if row[4] > 0:
            players.append(row)
    if not players:
        return None
    total = {k:0 for k in ("pts","fg2m","fg2a","fg3m","fg3a","ftm","fta","or","dr","tr","ast","stl","tov","blk","pf","pir")}
    for p in players:
        for k, i in {"pts":5,"fg2m":6,"fg2a":7,"fg3m":8,"fg3a":9,"ftm":10,"fta":11,
                     "or":12,"dr":13,"tr":14,"ast":15,"stl":16,"tov":17,"blk":18,"pf":19,"pir":20}.items():
            total[k] += p[i]
    if official and len(official) >= 18:
        fgm, fga = ma(official[3])
        f2m, f2a = ma(official[4])
        f3m, f3a = ma(official[5])
        ftm, fta = ma(official[6])
        total.update({"pts":integer(official[2]),"fg2m":f2m,"fg2a":f2a,"fg3m":f3m,"fg3a":f3a,
                      "ftm":ftm,"fta":fta,"tr":integer(official[7]),"or":integer(official[8]),
                      "dr":integer(official[9]),"ast":integer(official[10]),"stl":integer(official[11]),
                      "tov":integer(official[12]),"blk":integer(official[13]),"pf":integer(official[14]),
                      "pir":integer(official[17])})
    return {"coach":"","p":players,"tot":total}

def fetch_box(browser, url):
    page = browser.new_page()
    try:
        tables = parse_table_rows(page, url)
        if len(tables) < 2:
            return None
        h, a = side_box(tables[0]), side_box(tables[1])
        if not h or not a:
            return None
        return {"h":h,"a":a}
    finally:
        page.close()

def main():
    global clubs
    clubs = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            games = extract_pages("tvarkarastis", browser)
            results = extract_pages("rezultatai", browser)
        finally:
            browser.close()
    for gid, g in results.items():
        if gid in games:
            games[gid].update({k:v for k,v in g.items() if k in ("hs","as","url","season_id")})
        else:
            games[gid] = g
    if not games:
        raise RuntimeError("LKL schedule/results parsed zero games; refusing to overwrite data")
    ordered = sorted(games.values(), key=lambda g:g["dt"])
    team_round = {}
    for g in ordered:
        r = max(team_round.get(g["h"], 0), team_round.get(g["a"], 0)) + 1
        g["round"] = r
        team_round[g["h"]] = team_round[g["a"]] = r

    cache = read_cache()
    played = [g for g in ordered if "hs" in g]
    todo = [g for g in played if str(g["n"]) not in cache]
    todo.sort(key=lambda g:g["dt"])
    if todo:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for g in todo[:MAX_BOX]:
                    try:
                        box = fetch_box(browser, g["url"])
                        if box:
                            cache[str(g["n"])] = box
                            print(f"LKL protocol {g['n']}: imported")
                        else:
                            print(f"LKL protocol {g['n']}: table not available")
                    except Exception as e:
                        print(f"LKL protocol {g['n']} failed: {e}")
            finally:
                browser.close()
        write_cache(cache)

    finished, fixtures = [], []
    for g in ordered:
        base = {"n":g["n"],"round":g["round"],"h":g["h"],"a":g["a"]}
        if "hs" in g:
            item = {**base,"hs":g["hs"],"as":g["as"],"dt":g["dt"],
                    "box":cache.get(str(g["n"]))}
            finished.append(item)
        else:
            fixtures.append({**base,"utc":g["dt"]})

    rosters = {}
    for g in finished:
        box = g.get("box")
        if not box:
            continue
        for sd in ("h","a"):
            roster = rosters.setdefault(g[sd], {"players":[],"assistants":[]})["players"]
            ids = {str(p["id"]) for p in roster}
            for row in box[sd]["p"]:
                if str(row[0]) not in ids:
                    roster.append({"id":row[0],"name":row[1],"no":row[2],"pos":""})
                    ids.add(str(row[0]))

    site = {"season":"LKL-2026","updated":datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cols":PCOLS,"league":{"key":"lkl","name":"LKL","season_label":"2026/27",
            "season":"LKL-2026","player_columns":PCOLS},"clubs":clubs,"rosters":rosters,
            "games":finished,"fixtures":fixtures}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"LKL 2026/27: {len(clubs)} teams, {len(finished)} results, "
          f"{sum(bool(g.get('box')) for g in finished)} boxscores, {len(fixtures)} fixtures")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"LKL scraper error: {exc}", file=sys.stderr)
        sys.exit(1)
