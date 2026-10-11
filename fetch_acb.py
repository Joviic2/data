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
API = "https://api2.acb.com/api/matchdata"
API_KEY = "0dd94928-6f57-4c08-a3bd-b1b2f092976e"  # public key used by ACB Live
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

def api_json(path):
    """Fetch ACB Live's public match-data endpoint (same API as its website)."""
    r=SESSION.get(f"{API}/{path}",headers={"X-APIKEY":API_KEY},timeout=35)
    if r.status_code==204: return None
    r.raise_for_status()
    time.sleep(.2)
    return r.json()

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

def shot_zone(x,y,is_three):
    if is_three:
        return ("c3l" if x < 0 else "c3r") if abs(x)>=600 and y<=150 else "ab3"
    if (x*x+y*y)**0.5<=125: return "rim"
    return "paint" if abs(x)<=245 and y<=580 else "mid"

def scrape_shots(url,game,browser=None):
    """Download official ACB shot coordinates directly, without browser scraping."""
    data=api_json(f"MatchShots/match-shots?matchId={game['n']}")
    if not data: return None
    # ACB coordinates are millimetres from the basket; convert to centimetres
    # used by the site's zone classifier. API event types distinguish 2/3/FT.
    actions={92:("FTM",1),96:("FTA",0),93:("2FGM",2),97:("2FGA",0),
             100:("2FGM",2),94:("3FGM",3),98:("3FGA",0)}
    events=[]
    for shot in data.get("shotPoints") or []:
        spec=actions.get(shot.get("playType"))
        # ACB reports blocked field-goal attempts as event 533 (no x/y),
        # while the boxscore still includes them in FGA. Keep them by zone.
        if shot.get("playType")==533:
            spec=("3FGA" if int(shot.get("points") or 2)==3 else "2FGA",0)
        if not spec: continue
        action,points=spec
        is_ft=action.startswith("FT")
        player_id=str(shot.get("playerLicenseId") or "")
        events.append({"TEAM":game["h"] if shot.get("local") else game["a"],
                       "ID_PLAYER":player_id,
                       "ID_ACTION":action,"COORD_X":0 if is_ft else round(float(shot.get("posY") or 0)/10,1),
                       "COORD_Y":0 if is_ft else round(float(shot.get("posX") or 0)/10,1),
                       "POINTS":points if action.endswith("M") else 0,
                       "QUARTER":shot.get("quarter"),"ZONE":shot.get("zone")})
    if game.get("_box") and not shots_match_box(events,game,game["_box"]):
        raise RuntimeError(f"ACB API shot totals do not match boxscore for game {game['n']}")
    return events

def shots_match_box(shots,game,box):
    try:
        for side in ("h","a"):
            team=game[side]
            team_rows=[r for r in shots if str(r.get("TEAM"))==team]
            team_fg=[r for r in team_rows if str(r.get("ID_ACTION"," ")).startswith(("2FG","3FG"))]
            team_ft=[r for r in team_rows if str(r.get("ID_ACTION"," ")).startswith("FT")]
            players=box[side]["p"]
            expected_fg=(sum(p[6]+p[8] for p in players),sum(p[7]+p[9] for p in players))
            expected_ft=(sum(p[10] for p in players),sum(p[11] for p in players))
            if (sum(str(r.get("ID_ACTION","")).endswith("M") for r in team_fg),len(team_fg))!=expected_fg: return False
            if (sum(str(r.get("ID_ACTION","")).endswith("M") for r in team_ft),len(team_ft))!=expected_ft: return False
            for p in players:
                rows=[r for r in shots if str(r.get("TEAM"))==team and str(r.get("ID_PLAYER"))==str(p[0])]
                fg=[r for r in rows if str(r.get("ID_ACTION","")).startswith(("2FG","3FG"))]
                ft=[r for r in rows if str(r.get("ID_ACTION","")).startswith("FT")]
                made=sum(str(r.get("ID_ACTION","")).endswith("M") for r in fg)
                if (made,len(fg))!=(p[6]+p[8],p[7]+p[9]): return False
                if len(ft)!=p[11] or sum(str(r.get("ID_ACTION","")).endswith("M") for r in ft)!=p[10]: return False
        return True
    except Exception: return False

def parse_box(url,game,browser=None):
    """Read official ACB boxscore JSON; HTML is only a JS skeleton now."""
    data=api_json(f"Result/boxscores?matchId={game['n']}")
    if not data or not data.get("teamBoxscores"): return None
    role_map={"base":"PG","escolta":"SG","alero":"SF","ala-pivot":"PF","pivot":"C"}
    box={}; meta={}; quarter_scores={"h":[],"a":[]}
    totals_keys={"pts":"points","fg2m":"twoPointersMade","fg2a":"twoPointersAttempted",
        "fg3m":"threePointersMade","fg3a":"threePointersAttempted","ftm":"freeThrowsMade",
        "fta":"freeThrowsAttempted","or":"offRebounds","dr":"defRebounds","tr":"totalRebounds",
        "ast":"assists","stl":"steals","tov":"turnovers","blk":"blocks","pf":"personalFouls","pir":"rating"}
    teams=data["teamBoxscores"]
    if len(teams)!=2: return None
    for side,team_data in zip(("h","a"),teams):
        periods={int(p.get("quarter",0)):p.get("stats") or {} for p in team_data.get("statsByPeriods") or []}
        aggregate=periods.get(0) or {}
        players=[]; meta[side]={}
        for entry in aggregate.get("players") or []:
            p=entry.get("player") or {}
            pid=str(p.get("id") or "")
            if not pid: continue
            mins=parse_minutes(entry.get("playTime") or "")
            role=clean(p.get("gameRole") or "").lower().replace("á","a").replace("í","i")
            row=[pid,p.get("firstInitialAndLastName") or p.get("nickname") or "",
                 p.get("shirtNumber") or "",int(bool(entry.get("isStarted"))),mins,
                 int(entry.get("points") or 0),int(entry.get("twoPointersMade") or 0),
                 int(entry.get("twoPointersAttempted") or 0),int(entry.get("threePointersMade") or 0),
                 int(entry.get("threePointersAttempted") or 0),int(entry.get("freeThrowsMade") or 0),
                 int(entry.get("freeThrowsAttempted") or 0),int(entry.get("offRebounds") or 0),
                 int(entry.get("defRebounds") or 0),int(entry.get("totalRebounds") or 0),
                 int(entry.get("assists") or 0),int(entry.get("steals") or 0),
                 int(entry.get("turnovers") or 0),int(entry.get("blocks") or 0),
                 int(entry.get("personalFouls") or 0),int(entry.get("rating") or 0),
                 int(entry.get("plusMinus") or 0)]
            players.append(row)
            meta[side][pid]={"photo":p.get("headshotImageUrl"),"pos":role_map.get(role,"")}
        if not players: return None
        totals={k:sum(int((entry.get(k_api) or 0)) for entry in aggregate.get("players") or [])
                for k,k_api in totals_keys.items()}
        box[side]={"coach":team_data.get("headCoach") or "","p":players,"tot":totals}
        quarter_scores[side]=[int((periods.get(q,{}).get("total") or {}).get("points") or 0)
                              for q in sorted(k for k in periods if k>0)]
    return {"q":quarter_scores,"box":box,"meta":meta}

def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    oldsite={}
    try: oldsite=json.loads(OUT.read_text("utf-8"))
    except (OSError,ValueError): pass
    oldgames={str(g.get("n")):g for g in oldsite.get("games",[]) if isinstance(g,dict)}
    oldrosters=oldsite.get("rosters") or {}
    games,clubs=parse_calendar(get(CALENDAR))
    if not games: raise RuntimeError("ACB calendar parsed zero games; refusing to overwrite site.json")
    done=[]; fixtures=[]; player_meta={}; shot_data={}
    # Only open the browser if a new/corrected boxscore or missing shot map needs work.
    pending=[]
    for g in games:
        if "hs" not in g: continue
        old=oldgames.get(str(g["n"]))
        cached_box=bool(old and old.get("box") and old.get("hs")==g["hs"] and old.get("as")==g["as_"])
        shotfile=OUT.parent/f"shots_{g['n']}.json"
        valid_shots=False
        if cached_box and shotfile.exists():
            try: valid_shots=shots_match_box(json.loads(shotfile.read_text("utf-8")),g,old["box"])
            except (OSError,ValueError,KeyError,TypeError): pass
        if not cached_box or not valid_shots: pending.append(g)
    browser=None
    with sync_playwright() as pw:
        for g in games:
            if "hs" not in g:
                fixtures.append({"n":g["n"],"round":g["round"],"h":g["h"],"a":g["a"],"utc":g["utc"]})
                continue
            old=oldgames.get(str(g["n"]))
            if old and old.get("box") and old.get("hs")==g["hs"] and old.get("as")==g["as_"]:
                item=dict(old); item.update({"round":g["round"],"h":g["h"],"a":g["a"],"hs":g["hs"],"as":g["as_"],"dt":g["utc"]})
                done.append(item)
                shotfile=OUT.parent/f"shots_{g['n']}.json"
                if shotfile.exists():
                    try:
                        candidate=json.loads(shotfile.read_text("utf-8"))
                        if shots_match_box(candidate,g,item["box"]): shot_data[str(g["n"])]=candidate
                    except (OSError,ValueError): pass
                # Existing verified boxscores and shot maps are left untouched.
                if str(g["n"]) in shot_data: continue
                # Boxscore is cached but chart is missing; fetch only the chart.
                try:
                    sg=dict(g); sg["_box"]=item["box"]
                    shots=scrape_shots(g["url"],sg,browser)
                    if shots is None: print(f"ACB shot chart {g['n']}: official feed has no chart yet")
                    else:
                        shot_data[str(g["n"])]=shots
                        print(f"ACB shot chart {g['n']}: {len(shots)} attempts, verified against cached official boxscore")
                except Exception as e: print(f"ACB shot chart {g['n']} skipped: {e}")
                continue
            parsed=None
            try: parsed=parse_box(g["url"],g,browser)
            except Exception as e: print(f"boxscore {g['n']} failed: {e}")
            if parsed:
                player_meta.update({side+"|"+str(pid):data for side,players in parsed["meta"].items() for pid,data in players.items()})
                try:
                    cached=OUT.parent/f"shots_{g['n']}.json"
                    shots=None
                    if cached.exists():
                        try:
                            candidate=json.loads(cached.read_text("utf-8"))
                            if shots_match_box(candidate,g,parsed["box"]):
                                shots=candidate
                                print(f"ACB shot chart {g['n']}: reused verified cached map")
                        except Exception: pass
                    if shots is None:
                        sg=dict(g); sg["_box"]=parsed["box"]
                        shots=scrape_shots(g["url"],sg,browser)
                    if shots is None: print(f"ACB shot chart {g['n']}: official feed has no chart yet")
                    else:
                        shot_data[str(g["n"])]=shots
                        print(f"ACB shot chart {g['n']}: {len(shots)} attempts, verified against official boxscore")
                except Exception as e:
                    print(f"ACB shot chart {g['n']} skipped: {e}")
                    if "ACB_MODAL_DIAGNOSTIC" in str(e): raise
            else: print(f"boxscore {g['n']}: no official ACB API boxscore yet ({g['url']})")
            item={"n":g["n"],"round":g["round"],"h":g["h"],"a":g["a"],"hs":g["hs"],"as":g["as_"],"dt":g["utc"],"q":parsed["q"] if parsed else None,"box":parsed["box"] if parsed else None}
            done.append(item)
        if browser: browser.close()
    site={"season":"E2026","updated":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "cols":PCOLS,"league":{"key":"acb","name":"ACB","season_label":"2026/27","season":"E2026","player_columns":PCOLS},
          "clubs":clubs,"rosters":oldrosters,"games":done,"fixtures":fixtures}
    # Derive player lists for app screens from all available boxscores.
    for g in done:
        if not g["box"]: continue
        for side in ("h","a"):
            code=g[side]; roster=site["rosters"].setdefault(code,{"players":[],"assistants":[]})["players"]
            by_id={str(p["id"]):p for p in roster}
            for row in g["box"][side]["p"]:
                pdata=player_meta.get(side+"|"+str(row[0]),{})
                if str(row[0]) not in by_id:
                    player={"id":row[0],"name":row[1],"no":row[2],"pos":pdata.get("pos", ""),"photo":pdata.get("photo")}
                    roster.append(player); by_id[str(row[0])]=player
                else:
                    player=by_id[str(row[0])]
                    player["name"]=player.get("name") or row[1]
                    player["no"]=player.get("no") or row[2]
                    player["pos"]=player.get("pos") or pdata.get("pos", "")
                    player["photo"]=player.get("photo") or pdata.get("photo")
    OUT.write_text(json.dumps(site,ensure_ascii=False,separators=(",",":")),"utf-8")
    for gid,shots in shot_data.items():
        OUT.parent.joinpath(f"shots_{gid}.json").write_text(json.dumps(shots,ensure_ascii=False,separators=(",",":")),"utf-8")
    print(f"ACB 2026/27: {len(clubs)} clubs, {len(done)} played games, {sum(bool(g['box']) for g in done)} boxscores, {len(fixtures)} fixtures, {len(shot_data)} shot maps")

if __name__=="__main__":
    try: main()
    except Exception as e:
        print(f"ACB scraper error: {e}",file=sys.stderr); sys.exit(1)
