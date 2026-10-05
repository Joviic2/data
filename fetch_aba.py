#!/usr/bin/env python3
"""ABA liga (aba-liga.com) -> data/aba/site.json

Čita zvanični KALENDAR (rezultati + raspored). Pristojno ponašanje:
  - poštuje robots.txt (ako zabranjuje, skripta staje),
  - jedan zahtev po pokretanju (kalendar), pauza između zahteva,
  - ne osvežava ligu češće od MIN_AGE_MIN minuta,
  - jasan User-Agent.
Samo standardna biblioteka. Test lokalno:  python fetch_aba.py --file calendar.html --out /tmp/site.json
"""
import json, os, re, sys, time, urllib.request, urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

BASE = "https://www.aba-liga.com"
UA = "EuroleagueValueAnalyzer/1.0 (licna upotreba; https://github.com/Joviic2/data)"
TZ = ZoneInfo("Europe/Belgrade")
MIN_AGE = int(os.environ.get("MIN_AGE_MIN", "60"))
DEFAULT_TIME = (18, 0)        # za utakmice kojima je poznat samo datum (placeholder)

SHORT = {"BOR": "Borac", "BOS": "Bosna", "BUD": "Budućnost", "CIB": "Cibona", "CLU": "U-BT Cluj", "COL": "Cedevita Olimpija",
         "CZV": "Crvena zvezda", "DUB": "Dubai", "FMP": "FMP", "IGO": "Igokea", "ILI": "Ilirija", "KRK": "Krka",
         "MEG": "Mega", "PAR": "Partizan", "SBR": "Slovan", "SCD": "SC Derby", "SIR": "Široki", "SPA": "Spartak",
         "VIE": "Vienna", "ZAD": "Zadar"}

def season_id(today=None):
    d = today or datetime.now(TZ)
    return int(os.environ.get("ABA_SEASON") or (d.year - 2000 if d.month >= 7 else d.year - 2001))   # 2026/27 -> 26

# ----------------------------------------------------------------- parser kalendara
class Cal(HTMLParser):
    """Vraća listu redova: {round_label, id, codes[2], names[2], result, when, group}."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.label = [], ""
        self.in_title = self.in_tr = False
        self.cells, self.cur = [], None
        self.p = None          # "xs" | "full" | None
        self.xs, self.full, self.href = [], [], None
        self.in_span = False

    def handle_starttag(self, tag, a):
        a = dict(a); cls = a.get("class", "") or ""
        if tag == "h4" and "panel-title" in cls: self.in_title, self.label = True, ""
        elif tag == "tr": self.in_tr, self.cells = True, []; self.xs, self.full, self.href = [], [], None
        elif tag == "td" and self.in_tr: self.cur = {"cls": cls, "t": []}; self.cells.append(self.cur)
        elif tag == "p" and self.cur is not None and len(self.cells) == 1:
            self.p = "xs" if "visible-xs" in cls else "full" if "hidden-xs" in cls else None
        elif tag == "span" and self.p:
            self.in_span = True
            (self.xs if self.p == "xs" else self.full).append("\x00")      # separator domaćin/gost (ne ':' jer ime ima "m:tel")
        elif tag == "a" and self.cur is not None and len(self.cells) == 1 and self.href is None:
            self.href = a.get("href")

    def handle_endtag(self, tag):
        if tag == "h4": self.in_title = False
        elif tag == "span": self.in_span = False
        elif tag == "p": self.p = None
        elif tag == "td": self.cur = None
        elif tag == "tr" and self.in_tr:
            self.in_tr = False
            if len(self.cells) >= 3: self._row()

    def handle_data(self, d):
        if self.in_title: self.label += d
        if self.cur is None or self.in_span: return
        if len(self.cells) == 1 and self.p: (self.xs if self.p == "xs" else self.full).append(d)
        self.cur["t"].append(d)

    def _row(self):
        norm = lambda l: re.sub(r"\s+", " ", "".join(l)).strip()
        split = lambda l: [x.strip() for x in re.sub(r"[ \t\r\n]+", " ", "".join(l)).split("\x00")]
        m = re.search(r"/match/(\d+)/", self.href or "")
        codes, names = split(self.xs), split(self.full)
        self.rows.append({"label": norm([self.label]), "id": int(m.group(1)) if m else None,
                          "codes": codes, "names": names,
                          "result": norm(self.cells[1]["t"]), "when": norm(self.cells[2]["t"]),
                          "group": norm(self.cells[4]["t"]) if len(self.cells) > 4 else ""})

# ----------------------------------------------------------------- pretvaranje u format aplikacije
def parse_when(s):
    """'Friday, 02.10.2026 18:30 CET' -> (datetime lokalno, tačno_vreme) ; 'TBA' -> None"""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if not m or " - " in s: return None
    d, mo, y = map(int, m.groups())
    t = re.search(r"(\d{2}):(\d{2})", s[m.end():])
    hh, mm = (int(t.group(1)), int(t.group(2))) if t else DEFAULT_TIME
    return datetime(y, mo, d, hh, mm, tzinfo=TZ), bool(t)

def build(rows, old):
    clubs = dict(old.get("clubs", {}))
    games, fixtures = [], []
    for r in rows:
        mround = re.fullmatch(r"ROUND (\d+)", r["label"].strip(), re.I)
        if not mround or r["id"] is None: continue            # samo regularni deo; plej-of/plej-aut dodajemo kad krene
        if len(r["codes"]) != 2 or not all(r["codes"]) or len(r["names"]) != 2: continue
        h, a = r["codes"]; rnd = int(mround.group(1))
        for c, n in zip((h, a), r["names"]):
            clubs.setdefault(c, {"name": n, "short": SHORT.get(c, n), "crest": ""})
        w = parse_when(r["when"])
        sc = re.search(r"(\d+)\s*:\s*(\d+)", r["result"])
        if sc:
            dt = w[0].strftime("%Y-%m-%dT%H:%M") if w else ""
            games.append({"n": r["id"], "round": rnd, "dt": dt, "h": h, "a": a, "hs": int(sc.group(1)), "as": int(sc.group(2))})
        elif w:                                                # zakazana, još neodigrana
            fixtures.append({"n": r["id"], "round": rnd, "h": h, "a": a,
                             "utc": w[0].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")})
    # sačuvaj boxscore ako je već povučen (kad ga dodamo)
    box = {g["n"]: g["box"] for g in old.get("games", []) if g.get("box")}
    for g in games:
        if g["n"] in box: g["box"] = box[g["n"]]
    site = {k: v for k, v in old.items()}
    site.update({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "clubs": clubs,
                 "games": sorted(games, key=lambda g: (g["dt"], g["n"])),
                 "fixtures": sorted(fixtures, key=lambda f: (f["utc"], f["n"]))})
    site.setdefault("rosters", {})
    return site

# ----------------------------------------------------------------- mreža
def get(url):
    rp = urllib.robotparser.RobotFileParser(BASE + "/robots.txt")
    try: rp.read()
    except Exception: pass                                      # nema robots.txt = dozvoljeno
    if rp.default_entry is not None or rp.entries:
        if not rp.can_fetch(UA, url): sys.exit(f"robots.txt ne dozvoljava {url} - prekidam.")
    time.sleep(2)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r: return r.read().decode("utf-8", "replace")

def fresh(path):
    try:
        with open(path, encoding="utf8") as f: u = json.load(f)["updated"]
        return (datetime.now(timezone.utc) - datetime.strptime(u, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 60 < MIN_AGE
    except Exception: return False

def main():
    a = sys.argv[1:]
    out = a[a.index("--out") + 1] if "--out" in a else "data/aba/site.json"
    if "--file" in a: html = open(a[a.index("--file") + 1], encoding="utf8").read()
    else:
        if fresh(out): print(f"aba: osveženo pre manje od {MIN_AGE} min, preskačem"); return
        html = get(f"{BASE}/calendar/{season_id()}/1/")
    p = Cal(); p.feed(html)
    old = {}
    try:
        with open(out, encoding="utf8") as f: old = json.load(f)
    except Exception: pass
    site = build(p.rows, old)
    if not site["games"] and not site["fixtures"]: sys.exit("aba: nijedna utakmica nije pročitana (promenjen HTML?).")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf8") as f: json.dump(site, f, ensure_ascii=False, separators=(",", ":"))
    print(f"aba: {len(site['games'])} odigranih, {len(site['fixtures'])} predstojećih, {len(site['clubs'])} klubova -> {out}")

if __name__ == "__main__": main()
