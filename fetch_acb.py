#!/usr/bin/env python3
"""Official ACB 2026/27 calendar and boxscore scraper.

Reads the public ACB calendar and ACB Live statistics pages and writes
 data/acb/site.json in the same shape consumed by index.html.
"""
import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

CALENDAR = "https://acb.com/es/liga/calendario"
LIVE = "https://live.acb.com"
OUT = Path("data/acb/site.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ValueAnalyzer/1.0)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
PCOLS = ["id", "name", "no", "gs", "min", "pts", "fg2m", "fg2a", "fg3m", "fg3a",
         "ftm", "fta", "or", "dr", "tr", "ast", "stl", "tov", "blk", "pf", "pir", "pm"]
CODES = {
 "Surne Bilbao":"SBB","Kids&Us Manresa":"MAN","MoraBanc Andorra":"AND","Monbus Obradoiro":"OBR",
 "Barça":"BAR","Leyma Coruña":"COR","Río Breogán":"BRE","Asisa Joventut":"JOV",
 "Recoletas Salud San Pablo Burgos":"BUR","Kosner Baskonia":"BAS","La Laguna Tenerife":"TEN",
 "Casademont Zaragoza":"ZAR","FIATC Girona":"GIR","UCAM Murcia":"UCM","Valencia Basket":"VAL",
 "iLERNA Lleida":"LLE","Real Madrid":"RMA","Unicaja":"UNI"
}
MONTHS={"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
        "julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12}

def get(url):
    r=SESSION.get(url,timeout=35); r.raise_for_status()
    time.sleep(.35)
    return r.text

def clean(s): return re.sub(r"\s+"," ",s or "").strip()

def team_code(name):
    name=clean(name)
    return CODES.get(name, re.sub(r"[^A-Z0-9]","",name.upper())[:3].ljust(3,"X"))

def parse_date(text):
    m=re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})",clean(text).lower())
    if not m: return None
    month=MONTHS.get(m[2].replace("é","e"))
    if not month: return None
    return datetime(int(m[3]),month,int(m[1]),tzinfo=timezone.utc)

def team_anchors(node):
    out=[]
    for a in node.select('a[href*="/es/liga/equipos/"]'):
        im=a.find("img")
        raw=clean((im.get("alt") if im else None) or a.get("aria-label") or a.get("title") or a.get_text(" ",strip=True))
        name=next((known for known in CODES if raw.lower().startswith(known.lower())),raw)
        crest=urljoin("https://acb.com",im.get("src","")) if im and im.get("src") else None
        if name and name not in [x[0] for x in out]:
            out.append((name,urljoin("https://acb.com",a.get("href","")),crest))
    return out

def parse_calendar(html):
    soup=BeautifulSoup(html,"html.parser")
    rounds=soup.select('[id^="calendar-round-"]')
    if not rounds: raise RuntimeError("ACB calendar containers not found; page layout may have changed")
    games=[]; clubs={}
    for rndnode in rounds:
        rm=re.search(r"JORNADA\s+(\d+)",clean(rndnode.get_text(" ",strip=True)),re.I)
        round_no=int(rm[1]) if rm else None
        for a in rndnode.select('a[href*="/partidos/"]'):
            # Walk to the closest match card containing exactly two distinct team anchors.
            card=a; found=None
            for _ in range(7):
                teams=team_anchors(card)
                if len(teams)==2 and card is not rndnode:
                    found=(card,teams); break
                if card.parent is None or card.parent is rndnode: break
                card=card.parent
            if not found: continue
            card,teams=found
            href=urljoin(LIVE,a.get("href"))
            href=re.sub(r"(https?://live\.acb\.com)/partidos/",r"\1/es/partidos/",href)
            mid=re.search(r"-(\d+)/(?:estadisticas|previa)",href)
            if not mid: continue
            href=re.sub(r"/(?:estadisticas|previa|resumen|cronica|jugadas|estadisticas-avanzadas)(?:\?.*)?$","/estadisticas",href)
            text=clean(card.get_text(" ",strip=True))
            score=re.search(r"(?<!\d)(\d{1,3})\s*[-–]\s*(\d{1,3})(?!\d)",text)
            date_heading=[]
            for heading in a.find_all_previous(["h2","h3","h4"]):
                if heading in rndnode.find_all(["h2","h3","h4"]):
                    date_heading=[heading]; break
            dt=parse_date(date_heading[-1].get_text(" ",strip=True)) if date_heading else None
            mt=re.search(r"(\d{1,2}):(\d{2})\s*h?",text)
            if dt and mt: dt=dt.replace(hour=int(mt[1]),minute=int(mt[2]))
            hname,aname=teams[0][0],teams[1][0]
            hc,ac=team_code(hname),team_code(aname)
            clubs.setdefault(hc,{"name":hname,"short":hc,"crest":teams[0][2]})
            clubs.setdefault(ac,{"name":aname,"short":ac,"crest":teams[1][2]})
            if teams[0][2]: clubs[hc]["crest"]=teams[0][2]
            if teams[1][2]: clubs[ac]["crest"]=teams[1][2]
            item={"n":int(mid[1]),"round":round_no,"h":hc,"a":ac,"utc":dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None,"url":href}
            if score: item.update(hs=int(score[1]),as_=int(score[2]))
            games.append(item)
    uniq={g["n"]:g for g in games}
    return sorted(uniq.values(),key=lambda g:(g.get("utc") or "",g["n"])),clubs

def num(s):
    if isinstance(s,(int,float)): return int(s)
    m=re.search(r"-?\d+",clean(s).replace("\u2212","-"))
    return int(m[0]) if m else 0

def made_attempt(s):
    m=re.search(r"(\d+)\s*/\s*(\d+)",clean(s))
    return (int(m[1]),int(m[2])) if m else (0,0)

def parse_minutes(s):
    m=re.search(r"(\d+):(\d+)",clean(s))
    return round(int(m[1])+int(m[2])/60,2) if m else 0

def parse_box(url,game,browser):
    page=browser.new_page()
    try:
        page.goto(url,wait_until="domcontentloaded",timeout=60000)
        page.locator("table").first.wait_for(state="visible",timeout=25000)
        page.wait_for_function("""document.querySelectorAll('table a[href*="/liga/jugadores/"]').length >= 16""",timeout=20000)
        tables=page.locator("table").evaluate_all("""ts => ts.map(t => Array.from(t.querySelectorAll('tr')).map(r => ({
          v:Array.from(r.cells).map(c => c.innerText.trim()),
          href:r.querySelector('a[href*="/liga/jugadores/"]')?.getAttribute('href')||null,
          photo:r.querySelector('img')?.getAttribute('src')||r.querySelector('img')?.getAttribute('data-src')||r.querySelector('img')?.getAttribute('data-lazy-src')||null
        })))""")
    finally:
        page.close()
    # ACB Live summary tables contain the official player headshots; the stats
    # table used above omits them. Join photos by the stable ACB player id.
    summary=url.replace("/estadisticas","/resumen")
    sp=browser.new_page()
    try:
        sp.goto(summary,wait_until="domcontentloaded",timeout=60000)
        sp.locator('table a[href*="/liga/jugadores/"]').first.wait_for(state="visible",timeout=25000)
        photos=sp.locator("table").evaluate_all("""ts => ts.flatMap(t =>
          Array.from(t.querySelectorAll('tr')).map(r => {
            const a=r.querySelector('a[href*="/liga/jugadores/"]');
            const im=r.querySelector('img');
            return a ? {href:a.getAttribute('href'),
              photo:im?.currentSrc||im?.getAttribute('src')||im?.getAttribute('data-src')||im?.getAttribute('data-lazy-src')||null} : null
          }).filter(Boolean))""")
    except Exception as e:
        print(f"boxscore {game['n']}: player photos unavailable: {e}")
        photos=[]
    finally:
        sp.close()
    photo_by_id={}
    for row in photos:
        m=re.search(r"-(\\d+)(?:/|$)",row.get("href") or "")
        if m and row.get("photo"): photo_by_id[m[1]]=urljoin(LIVE,row["photo"])

    tables=[t for t in tables if t and max((len(row["v"]) for row in t),default=0)>=22 and any(row.get("href") for row in t)]
    if len(tables)<2: return None
    box={}; meta={}
    for side,table in zip(("h","a"),tables[:2]):
        players=[]; meta[side]={}
        for tr in table[1:]:
            vals=tr["v"]
            if len(vals)<22 or not tr["href"]: continue
            label=clean(vals[0])
            if label.lower() in ("team","totals","equipo"): continue
            m=re.search(r"-(\d+)(?:/|$)",tr["href"])
            pid=m[1] if m else tr["href"].rstrip("/").split("/")[-1]
            f2m,f2a=made_attempt(vals[3]); f3m,f3a=made_attempt(vals[5]); ftm,fta=made_attempt(vals[7])
            number=re.match(r"\s*(\d+)",label)
            slug=re.search(r"/liga/jugadores/([^/?#]+)",tr["href"])
            profile_name=re.sub(r"-\d+$","",slug[1]).replace("-"," ").title() if slug else ""
            name=profile_name or clean(re.sub(r"^\d+\s*","",label))
            row=[pid,name,number[1] if number else "",int("*" in label),parse_minutes(vals[1]),num(vals[2]),
                 f2m,f2a,f3m,f3a,ftm,fta,num(vals[10]),num(vals[9]),num(vals[11]),num(vals[12]),
                 num(vals[14]),num(vals[13]),num(vals[15]),num(vals[18]),num(vals[21]),num(vals[20])]
            if row[4]>0:
                players.append(row)
                photo=photo_by_id.get(str(pid)) or (urljoin(LIVE,tr["photo"]) if tr.get("photo") else None)
                meta[side][str(pid)]={"photo":photo} if photo else {}
        if not players: return None
        tot={k:0 for k in ("pts","fg2m","fg2a","fg3m","fg3a","ftm","fta","or","dr","tr","ast","stl","tov","blk","pf","pir")}
        for pl in players:
            for k,i in {"pts":5,"fg2m":6,"fg2a":7,"fg3m":8,"fg3a":9,"ftm":10,"fta":11,"or":12,"dr":13,"tr":14,"ast":15,"stl":16,"tov":17,"blk":18,"pf":19,"pir":20}.items(): tot[k]+=num(pl[i])
        box[side]={"coach":"","p":players,"tot":tot}
    return {"q":None,"box":box,"meta":meta}

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    games,clubs=parse_calendar(get(CALENDAR))
    if not games: raise RuntimeError("ACB calendar parsed zero games; refusing to overwrite site.json")
    done=[]; fixtures=[]; player_meta={}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        for g in games:
            if "hs" not in g:
                fixtures.append({"n":g["n"],"round":g["round"],"h":g["h"],"a":g["a"],"utc":g["utc"]})
                continue
            parsed=None
            try: parsed=parse_box(g["url"],g,browser)
            except Exception as e: print(f"boxscore {g['n']} failed: {e}")
            if parsed: player_meta.update({side+"|"+str(pid):data for side,players in parsed["meta"].items() for pid,data in players.items()})
            else: print(f"boxscore {g['n']}: no player tables on official page {g['url']}")
            item={"n":g["n"],"round":g["round"],"h":g["h"],"a":g["a"],"hs":g["hs"],"as":g["as_"],"dt":g["utc"],"q":None,"box":parsed["box"] if parsed else None}
            done.append(item)
        browser.close()
    site={"season":"E2026","updated":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "cols":PCOLS,"league":{"key":"acb","name":"ACB","season_label":"2026/27","season":"E2026","player_columns":PCOLS},
          "clubs":clubs,"rosters":{},"games":done,"fixtures":fixtures}
    # Derive player lists for app screens from all available boxscores.
    for g in done:
        if not g["box"]: continue
        for side in ("h","a"):
            code=g[side]; roster=site["rosters"].setdefault(code,{"players":[],"assistants":[]})["players"]
            known={str(p["id"]) for p in roster}
            for row in g["box"][side]["p"]:
                if str(row[0]) not in known:
                    roster.append({"id":row[0],"name":row[1],"no":row[2],"pos":"","photo":player_meta.get(side+"|"+str(row[0]),{}).get("photo")}); known.add(str(row[0]))
    OUT.write_text(json.dumps(site,ensure_ascii=False,separators=(",",":")),"utf-8")
    print(f"ACB 2026/27: {len(clubs)} clubs, {len(done)} played games, {sum(bool(g['box']) for g in done)} boxscores, {len(fixtures)} fixtures")

if __name__=="__main__":
    try: main()
    except Exception as e:
        print(f"ACB scraper error: {e}",file=sys.stderr); sys.exit(1)
