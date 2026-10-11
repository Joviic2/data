#!/usr/bin/env python3
"""Incremental easyCredit BBL schedule, boxscore, player-stat and shot feed scraper."""
from __future__ import annotations

import json, re, sys, time, zlib, unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "bbl" / "site.json"
SCHEDULE = "https://easycredit-bbl.de/saison/spielplaene_liga-pokalspiele/hauptrunde"
BASE = "https://easycredit-bbl.de"
UA = "Mozilla/5.0 (compatible; ValueAnalyzer/1.0)"
PCOLS = ["id", "name", "no", "gs", "min", "pts", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "or", "dr", "tr", "ast", "stl", "tov", "blk", "pf", "pir", "pm"]
S = requests.Session(); S.headers.update({"User-Agent": UA})

def read_site():
    try: return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}

def write_site(site):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)

def crc(value): return zlib.crc32(str(value).encode("utf-8")) & 0x7fffffff
def num(value, default=0):
    try: return int(float(value or 0))
    except (TypeError, ValueError): return default
def pct(value):
    try: return float(value or 0)
    except (TypeError, ValueError): return 0.0
def minutes(seconds): return round(num(seconds) / 60.0, 2)

def get_page_data(game_id):
    url = f"{BASE}/spiele/{game_id}"
    response = S.get(url, timeout=35); response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if not script or not script.string and not script.get_text(strip=True):
        raise RuntimeError(f"BBL page {game_id}: embedded game data missing")
    data = json.loads(script.string or script.get_text())
    props = data.get("props", {}).get("pageProps", {})
    game = props.get("initialGameData") or {}
    stats = props.get("initialGameStats") or {}
    if str(game.get("id")) != str(game_id): raise RuntimeError(f"BBL game ID mismatch: {game_id}")
    time.sleep(.15)
    return game, stats

def team_from_game(team):
    if not team: return None
    return {"name": team.get("name"), "short": team.get("tlc"), "crest": team.get("logoUrl"), "source_id": str(team.get("teamId") or team.get("id") or "")}

def player_rows(team_stats):
    out=[]
    for p in team_stats.get("playerStats") or []:
        person=p.get("seasonPlayer") or {}
        name=" ".join(filter(None, (person.get("firstName"), person.get("lastName")))).strip()
        if not name: continue
        out.append([str(person.get("id") or p.get("playerId") or ""), name, str(p.get("shirtNumber") or person.get("shirtNumber") or ""), int(bool(p.get("isStartingFive"))), minutes(p.get("secondsPlayed")), num(p.get("points")), num(p.get("twoPointShotsMade")), num(p.get("twoPointShotsAttempted")), num(p.get("threePointShotsMade")), num(p.get("threePointShotsAttempted")), num(p.get("freeThrowsMade")), num(p.get("freeThrowsAttempted")), num(p.get("offensiveRebounds")), num(p.get("defensiveRebounds")), num(p.get("totalRebounds")), num(p.get("assists")), num(p.get("steals")), num(p.get("turnovers")), num(p.get("blocks")), num(p.get("foulsCommitted")), num(p.get("efficiency")), num(p.get("plusMinus"))])
    return out

def team_box(team_stats):
    g=team_stats.get("gameStat") or {}
    return {"p":player_rows(team_stats),"tot":{"pts":num(g.get("points")),"fg2m":num(g.get("twoPointShotsMade")),"fg2a":num(g.get("twoPointShotsAttempted")),"fg3m":num(g.get("threePointShotsMade")),"fg3a":num(g.get("threePointShotsAttempted")),"ftm":num(g.get("freeThrowsMade")),"fta":num(g.get("freeThrowsAttempted")),"or":num(g.get("offensiveRebounds")),"dr":num(g.get("defensiveRebounds")),"tr":num(g.get("totalRebounds")),"ast":num(g.get("assists")),"stl":num(g.get("steals")),"tov":num(g.get("turnovers")),"blk":num(g.get("blocks")),"pf":num(g.get("foulsCommitted")),"pir":num(g.get("efficiency"))}}

def game_record(game_id, game, stats):
    home=game.get("homeTeam") or {}; away=game.get("guestTeam") or {}; result=game.get("result") or {}
    code_h=home.get("tlc"); code_a=away.get("tlc")
    if not code_h or not code_a: return None
    hs=num(result.get("homeTeamFinalScore"),-1); ass=num(result.get("guestTeamFinalScore"),-1)
    if hs < 0 or ass < 0: return None
    qh=[num(result.get(f"homeTeamQ{i}Score")) for i in range(1,5)]
    qa=[num(result.get(f"guestTeamQ{i}Score")) for i in range(1,5)]
    for i in range(1,10):
        oh=num(result.get(f"homeTeamOT{i}Score")); oa=num(result.get(f"guestTeamOT{i}Score"))
        if oh or oa: qh.append(oh); qa.append(oa)
    box_data=stats.get("boxScore") or stats
    hstats=box_data.get("homeTeam") or {}; astats=box_data.get("guestTeam") or {}
    # Older/current BBL page shape may expose teams directly in initialGameStats.
    if not hstats and stats.get("homeTeam"): hstats=stats["homeTeam"]
    if not astats and stats.get("guestTeam"): astats=stats["guestTeam"]
    hb=team_box(hstats); ab=team_box(astats)
    if not hb["p"] and not ab["p"]: return None
    dt=game.get("scheduledTime")
    return {"n":crc(game_id),"source_id":str(game_id),"round":game.get("matchDay"),"h":code_h,"a":code_a,"hs":hs,"as":ass,"utc":dt,"q":{"h":qh,"a":qa},"box":{"h":hb,"a":ab},"source":"easyCredit BBL official game report"}

def shot_rows(stats, game):
    """Preserve official play-by-play shot events and court coordinates."""
    actions=stats.get("actions") or []
    teams={}
    for side in ("homeTeam","guestTeam"):
        team=game.get(side) or {}
        code=team.get("tlc")
        for key in ("teamId","id","seasonTeamId"):
            if team.get(key) is not None: teams[str(team[key])]=code
    out=[]
    for a in actions:
        typ=str(a.get("type") or "")
        if typ not in ("TWO_POINT_THROW","THREE_POINT_THROW","FREE_THROW"): continue
        c=a.get("coordinates") or {}; x,y=c.get("x"),c.get("y")
        if x is None or y is None or (num(x)==0 and num(y)==0): continue
        made=bool(a.get("isSuccessful"))
        action=("3FG" if typ=="THREE_POINT_THROW" else "FT" if typ=="FREE_THROW" else "2FG")+ ("M" if made else "A")
        out.append({"TEAM":teams.get(str(a.get("seasonTeamId") or ""),str(a.get("seasonTeamId") or "")),"ID_PLAYER":str(a.get("seasonPlayerId") or ""),"ID_ACTION":action,"COORD_X":x,"COORD_Y":y,"POINTS":3 if typ=="THREE_POINT_THROW" and made else 2 if typ=="TWO_POINT_THROW" and made else 1 if typ=="FREE_THROW" and made else 0,"QUARTER":a.get("period"),"ZONE":None})
    return out

def official_fixtures(clubs):
    """Parse the BBL's official full-season iCalendar feed (all dates, not odds coverage)."""
    r=S.get("https://api.basketball-bundesliga.de/calendar/ical/all-games",timeout=35);r.raise_for_status()
    raw=r.text.replace("\r\n ","").replace("\n ","")
    events=re.findall(r"BEGIN:VEVENT\s*(.*?)\s*END:VEVENT",raw,re.S)
    tz=ZoneInfo("Europe/Berlin");out=[]
    def unescape(v): return v.replace(r"\n"," ").replace(r"\,",",").replace(r"\;",";").replace("\\\\","\\").strip()
    def norm(v): return " ".join(re.sub(r"[^a-z0-9 ]"," ",unicodedata.normalize("NFKD",str(v or "")).encode("ascii","ignore").decode().lower()).split())
    def code(name):
        key=norm(name)
        exact=[c for c,row in clubs.items() if key and key==norm(row.get("name"))]
        if len(exact)==1: return exact[0]
        matches=[(c,row) for c,row in clubs.items() if key and (key in norm(row.get("name")) or norm(row.get("name")) in key)]
        if len(matches)==1: return matches[0][0]
        ranked=sorted(((len(set(key.split()) & set(norm(row.get("name")).split())),c) for c,row in clubs.items()),reverse=True)
        if ranked and ranked[0][0]>0 and (len(ranked)==1 or ranked[0][0]>ranked[1][0]): return ranked[0][1]
        return "".join(x[0] for x in key.split()[:3]).upper()
    for event in events:
        props={}
        for line in event.splitlines():
            if ":" in line:
                k,v=line.split(":",1);props[k.split(";",1)[0]]=v
        summary=unescape(props.get("SUMMARY",""))
        m=re.search(r"(?:easyCredit BBL|BBL(?: Hauptrunde)?) Spiel\s+(.+?)\s+vs\s+(.+)$",summary,re.I)
        if not m: continue
        start=props.get("DTSTART","")
        try: when=datetime.strptime(start[:15],"%Y%m%dT%H%M%S").replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError: continue
        h,a=unescape(m[1]),unescape(m[2]); uid=props.get("UID") or f"{h}|{a}|{when}"
        out.append({"n":crc(uid),"source_id":uid,"round":None,"h":code(h),"a":code(a),"utc":when,"hs":None,"as":None,"source":"easyCredit BBL official full-season calendar"})
    if not out: raise RuntimeError("BBL official iCalendar feed returned no league fixtures")
    return out

def schedule_ids():
    """Use the BBL's own schedule page, clicking its official load-more control."""
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(user_agent=UA,locale="de-DE")
        page.goto(SCHEDULE,wait_until="domcontentloaded",timeout=60000)
        page.locator("a[href*='/spiele/']").first.wait_for(timeout=30000)
        for _ in range(80):
            more=page.get_by_role("button",name=re.compile("mehr laden",re.I))
            if more.count()==0 or not more.first.is_visible(): break
            old=page.locator("a[href*='/spiele/']").count()
            more.first.click(timeout=10000)
            try: page.wait_for_function("n => document.querySelectorAll('a[href*=\\\"/spiele/\\\"]').length > n",arg=old,timeout=10000)
            except Exception: break
        ids=page.eval_on_selector_all("a[href*='/spiele/']", "els => { const m=new Map(); for (const a of els) { const u=a.getAttribute('href')||''; const id=(u.match(/\\/spiele\\/(\\d+)/)||[])[1]; if(!id) continue; const old=m.get(id)||{id,complete:false}; if(/#boxscore/i.test(u)) old.complete=true; m.set(id,old); } return [...m.values()]; }")
        browser.close()
    if not ids: raise RuntimeError("BBL official schedule exposed no game links")
    return ids

def main():
    site={}
    try: site=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError,ValueError): pass
    known={str(g.get("source_id") or g.get("n")) for g in site.get("games",[]) if g.get("box")}
    ids=schedule_ids(); clubs=site.get("clubs") or {}; games={str(g.get("source_id") or g.get("n")):g for g in site.get("games",[]) if g.get("box")}; fixtures=official_fixtures(clubs); shots={}
    # Carry scores and round metadata forward for boxscores already cached locally.
    for old in games.values():
        match=next((f for f in fixtures if f.get("h")==old.get("h") and f.get("a")==old.get("a") and (f.get("utc") or "")[:10]==(old.get("utc") or "")[:10]),None)
        if match: match.update({"source_id":old.get("source_id"),"round":old.get("round"),"hs":old.get("hs"),"as":old.get("as")})
    fetched=0; skipped=0; failures=[]
    for item in ids:
        gid=item["id"]
        try:
            if not item.get("complete"):
                # Upcoming games are already included in the official calendar/Superbet merge;
                # avoid downloading a full match page before its boxscore exists.
                continue
            if str(gid) in known:
                skipped+=1
                continue
            game,stats=get_page_data(gid); home=team_from_game(game.get("homeTeam")); away=team_from_game(game.get("guestTeam"))
            if home and home.get("short"): clubs[home["short"]]={"name":home["name"],"short":home["short"],"crest":home.get("crest"),"source_id":home.get("source_id")}
            if away and away.get("short"): clubs[away["short"]]={"name":away["name"],"short":away["short"],"crest":away.get("crest"),"source_id":away.get("source_id")}
            dt=game.get("scheduledTime"); status=str(game.get("status") or "").upper(); done=status in {"OFFICIAL","COMPLETE","COMPLETED","FINISHED"}
            game_h=(game.get("homeTeam") or {}).get("tlc"); game_a=(game.get("guestTeam") or {}).get("tlc")
            official=next((f for f in fixtures if f.get("h")==game_h and f.get("a")==game_a and (f.get("utc") or "")[:10]==str(dt or "")[:10]),None)
            if official:
                official.update({"source_id":str(gid),"round":game.get("matchDay"),"hs":(game.get("result") or {}).get("homeTeamFinalScore"),"as":(game.get("result") or {}).get("guestTeamFinalScore")})
            else:
                fixtures.append({"n":crc(gid),"source_id":str(gid),"round":game.get("matchDay"),"h":game_h,"a":game_a,"utc":dt,"hs":(game.get("result") or {}).get("homeTeamFinalScore"),"as":(game.get("result") or {}).get("guestTeamFinalScore"),"source":"easyCredit BBL official schedule"})
            if done and str(gid) not in known:
                rec=game_record(gid,game,stats)
                if rec:
                    games[str(gid)]=rec; fetched+=1
                    points=shot_rows(stats,game)
                    if points: shots[str(rec["n"])]=points
                else: failures.append(f"{gid}: official boxscore not published or player rows missing")
            elif done: skipped+=1
        except Exception as e: failures.append(f"{gid}: {e}")
    allgames=sorted(games.values(),key=lambda g:g.get("utc") or "")
    rosters={}
    for g in allgames:
        for side in ("h","a"):
            code=g.get(side)
            if not code: continue
            for p in (g.get("box",{}).get(side,{}) or {}).get("p",[]):
                if not p[0]: continue
                row=rosters.setdefault(code,{}).setdefault(str(p[0]),{"id":str(p[0]),"name":p[1],"no":p[2],"position":None,"photo":None})
    # Official schedule is authoritative; preserve other provider fixtures that do not map to BBL's game IDs.
    for f in site.get("fixtures",[]):
        if f.get("source") and "Superbet" in f.get("source","") and not any(x.get("h")==f.get("h") and x.get("a")==f.get("a") and (x.get("utc") or "")[:10]==(f.get("utc") or "")[:10] for x in fixtures): fixtures.append(f)
    fixtures.sort(key=lambda f:f.get("utc") or "")
    site.update({"season":"2026/27","updated":datetime.now(timezone.utc).isoformat(),"league":{"key":"bbl","name":"easyCredit BBL","season_label":"2026/27"},"clubs":clubs,"rosters":{k:list(v.values()) for k,v in rosters.items()},"games":allgames,"fixtures":fixtures,"data_coverage":{"team_stats":"easyCredit BBL official game boxscores and team statistics","boxscores":f"{len(allgames)} official player boxscores","player_rosters":"official boxscore participants; positions not assumed","shot_charts":"official play-by-play shot coordinates when present"}})
    write_site(site)
    shot_dir=OUT.parent
    for n,rows in shots.items():
        (shot_dir/f"shots_{n}.json").write_text(json.dumps(rows,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    print(f"BBL: {len(clubs)} clubs, {len(fixtures)} fixtures, {len(allgames)} boxscores (+{fetched} new; {skipped} cached), {sum(map(len,shots.values()))} shot coordinates; {len(failures)} issues")
    for e in failures[:20]: print("BBL warning:",e,file=sys.stderr)
    if not allgames: return 1
    return 0

if __name__=="__main__": raise SystemExit(main())
