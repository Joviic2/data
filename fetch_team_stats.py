#!/usr/bin/env python3
"""Refresh score-only team profiles used when official boxscores are unavailable.

BBL data comes from the league's server-rendered official statistics page.
LNB fallback comes from Basketball-Reference's current-season standings table.
ACB profiles are aggregated only from scores already present in its official feed.
No synthetic player boxscores are created.
"""
import argparse, json, re, sys, time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

for _stream in (sys.stdout, sys.stderr):
    try: _stream.reconfigure(encoding="utf-8")
    except Exception: pass

ROOT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; ValueAnalyzer/1.0)"

def get(url):
    req = Request(url, headers={"User-Agent": UA})
    return urlopen(req, timeout=35).read().decode("utf-8", "replace")

def save(lg, teams, source):
    path = ROOT / "data" / lg / "team_stats.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"league": lg, "season": "2026/27", "updated": datetime.now(timezone.utc).isoformat(),
               "source": source, "teams": teams}
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{lg}: sačuvani profili za {len(teams)} timova ({source})")

def bbl():
    html = get("https://www.easycredit-bbl.de/statistiken/teams")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m: raise RuntimeError("BBL official page has no __NEXT_DATA__")
    data = json.loads(m.group(1))["props"]["pageProps"].get("preloadedWidgetData", {})
    widget = next((v for k, v in data.items() if "season-teams-statistics-widget" in k), None)
    if not widget: raise RuntimeError("BBL team statistics widget not found")
    rows=[]
    for r in widget.get("data", []):
        team=r.get("seasonTeam") or {}; gp=float(r.get("gamesPlayed") or 0)
        if gp <= 0: continue
        ppg=float(r.get("pointsPerGame") or 0)
        pm=float(r.get("plusMinusPerGame") or 0)
        rows.append({"code":team.get("tlc"),"name":team.get("name"),"logo":team.get("logoUrl"),
                     "gp":gp,"ppg":ppg,"papg":ppg-pm,
                     "stats":{"reb":r.get("totalReboundsPerGame"),"ast":r.get("assistsPerGame"),
                              "tov":r.get("turnoversPerGame"),"3p_pct":r.get("threePointShotSuccessPercent")}})
    if len(rows)<10: raise RuntimeError(f"BBL stats incomplete: {len(rows)} teams")
    save("bbl",rows,"easyCredit BBL official team statistics")

class Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.table=False; self.row=False; self.cell=False; self.current=[]; self.rows=[]; self.stat=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=="table" and a.get("id")=="fra_standings": self.table=True
        elif self.table and tag=="tr": self.row=True; self.current=[]
        elif self.row and tag in ("td","th"):
            self.cell=True; self.stat=a.get("data-stat")
        elif self.cell and tag=="br": self.current.append(" ")
    def handle_data(self,data):
        if self.cell: self.current.append(data)
    def handle_endtag(self,tag):
        if tag in ("td","th") and self.cell: self.cell=False
        elif tag=="tr" and self.row:
            self.row=False
            if self.current: self.rows.append(self.current)
        elif tag=="table" and self.table: self.table=False

def lnb():
    html=get("https://www.basketball-reference.com/international/france-lnb-pro-a/2027.html")
    # Parse the stable data-stat attributes, selecting only the current-season standings table.
    start=html.find('id="fra_standings"')
    if start<0: raise RuntimeError("LNB current-season standings table missing")
    start=html.rfind("<table",0,start); end=html.find("</table>",start)
    if end<0: raise RuntimeError("LNB standings table is incomplete")
    table=html[start:end+8]
    teams=[]
    row_re=re.compile(r'<tr[^>]*>(.*?)</tr>',re.S)
    cell_re=re.compile(r'<(td|th)\b([^>]*)>(.*?)</\1>',re.S)
    for row in row_re.findall(table):
        vals={}
        for tag,attrs,body in cell_re.findall(row):
            sm=re.search(r'data-stat="([^"]+)',attrs)
            if sm:
                text=re.sub(r"<[^>]+>"," ",body); text=re.sub(r"\s+"," ",text).strip()
                vals[sm.group(1)]=text
        name=vals.get("team")
        try:
            gp=float(vals.get("wins|regular-season",0) or 0)+float(vals.get("losses|regular-season",0) or 0)
            ppg=float(vals.get("pts_per_g|regular-season",0) or 0)
            papg=float(vals.get("opp_pts_per_g|regular-season",0) or 0)
        except (TypeError,ValueError): continue
        if not name or name.lower()=="team": continue
        if gp and ppg and papg: teams.append({"name":name,"gp":gp,"ppg":ppg,"papg":papg})
    if len(teams)<10: raise RuntimeError(f"LNB standings incomplete: {len(teams)} teams")
    save("lnb",teams,"Basketball-Reference current-season LNB standings")

def acb():
    path=ROOT/"data/acb/site.json"
    if not path.exists(): return
    site=json.loads(path.read_text(encoding="utf-8")); agg={}
    for g in site.get("games",[]):
        if g.get("hs") is None or g.get("as") is None: continue
        h,a=g.get("h"),g.get("a")
        for code,pf,pa in ((h,float(g["hs"]),float(g["as"])),(a,float(g["as"]),float(g["hs"]))):
            if not code: continue
            x=agg.setdefault(code,{"code":code,"name":(site.get("clubs",{}).get(code) or {}).get("name",code),
                                  "logo":(site.get("clubs",{}).get(code) or {}).get("crest"),"gp":0,"pf":0,"pa":0})
            x["gp"]+=1;x["pf"]+=pf;x["pa"]+=pa
    teams=[dict(x,ppg=x.pop("pf")/x["gp"],papg=x.pop("pa")/x["gp"]) for x in agg.values() if x["gp"]]
    if teams: save("acb",teams,"ACB official calendar results; score-only, no player boxscore")

if __name__=="__main__":
    ap=argparse.ArgumentParser(description="Refresh public team scoring profiles for ACB, LNB and BBL.")
    ap.add_argument("--lg",choices=("acb","lnb","bbl"),action="append",help="refresh only selected league (repeatable); default: all")
    args=ap.parse_args()
    funcs={"acb":acb,"lnb":lnb,"bbl":bbl}
    failed=[]
    for lg in args.lg or ("acb","lnb","bbl"):
        fn=funcs[lg]
        try: fn()
        except Exception as e: print(f"{fn.__name__} scraper error: {e}",file=sys.stderr); failed.append(fn.__name__)
    if failed: sys.exit(1)
