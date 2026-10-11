#!/usr/bin/env python3
"""Incremental LNB Betclic Elite official calendar and match-center boxscores."""
from __future__ import annotations

import json, re, sys, time, unicodedata, zlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"data"/"lnb"/"site.json"
CALENDAR="https://lnb.fr/fr/calendar/betclicelite?did=1&abbrev=PROA&yid=2026&cid=317"
TZ=ZoneInfo("Europe/Paris")
PCOLS=["id","name","no","gs","min","pts","fg2m","fg2a","fg3m","fg3a","ftm","fta","or","dr","tr","ast","stl","tov","blk","pf","pir","pm"]
NAME_ALIASES={"bourg en bresse":"JB","chalon saone":"ESC","lyon villeurbanne":"LA"}
CODE_ALIASES={"B":"JB","C":"ESC","L":"LA"}

def crc(v): return zlib.crc32(str(v).encode())&0x7fffffff
def norm(v): return " ".join("".join(c for c in unicodedata.normalize("NFKD",str(v or "")).encode("ascii","ignore").decode().lower() if c.isalnum() or c.isspace()).split())
def number(v,default=0):
    try: return int(float(str(v or "0").replace(",",".")))
    except (TypeError,ValueError): return default
def mins(v):
    m=re.search(r"(\d+):(\d+)",str(v or ""))
    return round(int(m[1])+int(m[2])/60,2) if m else 0
def clean(v): return re.sub(r"\s+"," ",str(v or "")).strip()

def map_code(name,clubs):
    key=norm(name)
    if not key: return None
    if key in NAME_ALIASES: return NAME_ALIASES[key]
    for code,row in clubs.items():
        other=norm(row.get("name"))
        if key==other: return code
    candidates=[(code,row) for code,row in clubs.items() if key in norm(row.get("name")) or norm(row.get("name")) in key]
    if len(candidates)==1: return candidates[0][0]
    words=set(key.split()); ranked=sorted(((len(words & set(norm(row.get("name")).split())),code) for code,row in clubs.items()),reverse=True)
    if ranked and ranked[0][0]>=1 and (len(ranked)==1 or ranked[0][0]>ranked[1][0]): return ranked[0][1]
    return "".join(x[0] for x in key.split()[:3]).upper() or None

def canonicalize_site(site):
    """Merge calendar display-name codes into the existing official team codes."""
    clubs=site.get("clubs") or {}
    for old,new in CODE_ALIASES.items():
        if old not in clubs: continue
        source=clubs.pop(old)
        target=clubs.setdefault(new,{"name":source.get("name"),"short":new})
        target["crest"]=target.get("crest") or source.get("crest")
    for game in site.get("games",[]):
        for side in ("h","a"):
            if game.get(side) in CODE_ALIASES: game[side]=CODE_ALIASES[game[side]]
    for fixture in site.get("fixtures",[]):
        for side in ("h","a"):
            if fixture.get(side) in CODE_ALIASES: fixture[side]=CODE_ALIASES[fixture[side]]
    rosters=site.get("rosters") or {}
    for old,new in CODE_ALIASES.items():
        if old not in rosters: continue
        existing=rosters.setdefault(new,{"players":[],"assistants":[]})
        merged={str(p.get("id") or p.get("name")):p for p in existing.get("players",[])}
        merged.update({str(p.get("id") or p.get("name")):p for p in rosters.pop(old).get("players",[])})
        existing["players"]=list(merged.values())
    site["clubs"]=clubs;site["rosters"]=rosters
    return site

def page_cards(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('[id^="2026-"],[id^="2027-"]')).flatMap(day => {
      const d=day.id;
      return Array.from(day.querySelectorAll('.perspective')).map((card,index) => {
        const names=Array.from(card.querySelectorAll('span[class*=wrap-break-word]')).map(x=>x.innerText.trim()).filter(Boolean);
        const nums=Array.from(card.querySelectorAll('span')).map(x=>x.innerText.trim()).filter(x=>/^\\d{1,3}$/.test(x));
        const text=card.innerText||'';
        const images=Array.from(card.querySelectorAll('img')).map(x=>({alt:x.alt,src:x.src}));
        const time=(text.match(/\\b([01]?\\d|2[0-3]):[0-5]\\d\\b/)||[])[0]||'';
        const round=(card.querySelector('h2')?.innerText||'').trim();
        return {date:d,index,names:names.slice(0,2),scores:nums.slice(0,2),time,round,
          done:/TERMINÉ|COMPLETE|COMPLETED|FINISHED/i.test(text),images,
          label:text.slice(0,500)};
      }).filter(x=>x.names.length===2);
    })""")

def select_round(page, round_number):
    """Select one official matchday from the calendar's round dropdown."""
    print(f"LNB calendar: opening round J{round_number}/30",flush=True)
    selector=page.get_by_role("button",name=re.compile(r"^(?:J\d+|JOURNÉE)$",re.I)).first
    selector.click(timeout=10000)
    option=page.get_by_role("button",name=f"J{round_number}",exact=True)
    if option.count():
        option.last.click(timeout=10000)
    else:
        option=page.get_by_role("option",name=f"J{round_number}",exact=True)
        if option.count():
            option.last.click(timeout=10000)
        else:
            option=page.get_by_text(f"J{round_number}",exact=True)
            if not option.count(): raise RuntimeError(f"LNB calendar has no J{round_number} option")
            option.last.click(timeout=10000)
    page.wait_for_function("n => Array.from(document.querySelectorAll('.perspective h2')).some(x => x.innerText.trim() === 'J' + n)",arg=round_number,timeout=20000)
    page.wait_for_timeout(350)

def extract_box(page):
    return page.evaluate("""() => {
      const tables=Array.from(document.querySelectorAll('table')).filter(t=>{
        const s=getComputedStyle(t); return t.getClientRects().length>0 && s.visibility!=='hidden' && s.display!=='none';
      }).map(t=>({
        heads:Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()),
        rows:Array.from(t.querySelectorAll('tbody tr')).map(r=>Array.from(r.querySelectorAll('th,td')).map(c=>c.innerText.trim())),
        rowImages:Array.from(t.querySelectorAll('tbody tr')).map(r=>Array.from(r.querySelectorAll('img')).map(i=>i.src))
      }));
      const text=document.body.innerText||'';
      const teams=Array.from(document.querySelectorAll('img[alt]')).filter(i=>/logo/i.test(i.alt)).map(i=>({alt:i.alt,src:i.src}));
      return {tables,text:text.slice(text.indexOf('Match Center'),text.length),teams};
    }""")

def normalize_table(table):
    headers=[clean(x).upper() for x in table.get("heads",[])]
    rows=[]
    for ri,row in enumerate(table.get("rows",[])):
        if len(row)<8: continue
        vals={headers[i]:row[i] for i in range(min(len(headers),len(row)))}
        nm=clean(vals.get("NOM")); is_total=nm.upper().startswith(("TOTAL","EQUIPE"))
        if not nm or is_total: continue
        starter=1 if "*" in nm else 0
        nm=clean(nm.replace("*",""))
        no=clean(vals.get("#"))
        image=(table.get("rowImages",[])[ri] if ri<len(table.get("rowImages",[])) else [])
        pid=""
        for src in image:
            m=re.search(r"/(?:Player|Person)/(\d+)",src,re.I)
            if m: pid=m[1]; break
        rows.append([pid,nm,no,starter,mins(vals.get("MIN")),number(vals.get("PTS")),number(vals.get("2TR")),number(vals.get("2TT")),number(vals.get("3R")),number(vals.get("3T")),number(vals.get("LFR")),number(vals.get("LFT")),number(vals.get("RO")),number(vals.get("RD")),number(vals.get("REB")),number(vals.get("PD")),number(vals.get("INT")),number(vals.get("BP")),number(vals.get("CT")),number(vals.get("FTE")),number(vals.get("EVAL")),number(vals.get("+/-"))])
    return rows

def total_from(table,players):
    heads=[clean(x).upper() for x in table.get("heads",[])]
    for row in table.get("rows",[]):
        vals={heads[i]:row[i] for i in range(min(len(heads),len(row)))}
        if clean(vals.get("NOM")).upper().startswith("TOTAL"):
            return {"pts":number(vals.get("PTS")),"fg2m":number(vals.get("2TR")),"fg2a":number(vals.get("2TT")),"fg3m":number(vals.get("3R")),"fg3a":number(vals.get("3T")),"ftm":number(vals.get("LFR")),"fta":number(vals.get("LFT")),"or":number(vals.get("RO")),"dr":number(vals.get("RD")),"tr":number(vals.get("REB")),"ast":number(vals.get("PD")),"stl":number(vals.get("INT")),"tov":number(vals.get("BP")),"blk":number(vals.get("CT")),"pf":number(vals.get("FTE")),"pir":number(vals.get("EVAL"))}
    return {k:sum((p[PCOLS.index(k)] if k in PCOLS else 0) for p in players) for k in ("pts","fg2m","fg2a","fg3m","fg3a","ftm","fta","or","dr","tr","ast","stl","tov","blk","pf","pir")}

def valid_boxscore(box, hs, ass):
    """Reject match-center tables that do not reconcile to the official score."""
    if hs is None or ass is None:
        return False
    for side, score in (("h", hs), ("a", ass)):
        part=box.get(side) or {}; total=part.get("tot") or {}; players=part.get("p") or []
        if not players or not total:
            return False
        if abs(number(total.get("pts"))-number(score))>2:
            return False
        if abs(sum(number(p[5]) for p in players)-number(score))>2:
            return False
        if number(total.get("fg2m"))>number(total.get("fg2a")) or number(total.get("fg3m"))>number(total.get("fg3a")):
            return False
        made=2*number(total.get("fg2m"))+3*number(total.get("fg3m"))+number(total.get("ftm"))
        if abs(made-number(total.get("pts")))>2:
            return False
    return True

def main():
    try: site=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError,ValueError): site={}
    site=canonicalize_site(site)
    clubs=site.get("clubs") or {}; old_games={str(g.get("source_id") or g.get("n")):g for g in site.get("games",[]) if g.get("box") and valid_boxscore(g["box"],g.get("hs"),g.get("as"))}; old_fixtures=site.get("fixtures",[])
    failures=[]; new=0; cached=0
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(user_agent="Mozilla/5.0 (compatible; ValueAnalyzer/1.0)",locale="fr-FR",timezone_id="Europe/Paris")
        page.goto(CALENDAR,wait_until="domcontentloaded",timeout=60000)
        page.locator('[id^="2026-"],[id^="2027-"]').first.wait_for(timeout=60000)
        page.wait_for_timeout(1200)
        fixtures=[]
        played=[]
        for round_number in range(1,31):
            select_round(page,round_number)
            cards=page_cards(page)
            print(f"LNB calendar: J{round_number} loaded ({len(cards)} games)",flush=True)
            for c in cards:
                hname,aname=c["names"]
                logos=c.get("images") or []
                hlogo=next((x.get("src") for x in logos if "home" in (x.get("alt") or "").lower()),None)
                alogo=next((x.get("src") for x in logos if "away" in (x.get("alt") or "").lower()),None)
                hc,ac=map_code(hname,clubs),map_code(aname,clubs)
                for code,name,logo in ((hc,hname,hlogo),(ac,aname,alogo)):
                    if code: clubs[code]={**(clubs.get(code) or {}),"name":(clubs.get(code) or {}).get("name") or name,"short":code,"crest":logo or (clubs.get(code) or {}).get("crest")}
                dt=datetime.strptime(c["date"],"%Y-%m-%d").replace(tzinfo=TZ)
                if c.get("time"):
                    hh,mm=map(int,c["time"].split(":")); dt=dt.replace(hour=hh,minute=mm)
                else: dt=dt.replace(hour=20,minute=0)
                f={"n":None,"source_id":None,"round":round_number,"h":hc,"a":ac,"utc":dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"hs":number(c["scores"][0],None) if len(c["scores"])>0 else None,"as":number(c["scores"][1],None) if len(c["scores"])>1 else None,"source":"LNB official calendar","_date":c["date"],"_index":c["index"],"_played":c["done"]}
                fixtures.append(f)
                if c["done"]: played.append((f,round_number))
        if not fixtures:
            browser.close(); raise RuntimeError("LNB official calendar rendered without match cards in rounds J1-J30")
        unique={}
        for f in fixtures:
            unique[(f.get("round"),f.get("h"),f.get("a"),f.get("utc"))]=f
        fixtures=list(unique.values())
        played=[(f,f.get("round")) for f in fixtures if f.get("_played")]
        old_by_fixture={(g.get("h"),g.get("a"),(g.get("utc") or "")[:10]):g for g in old_games.values()}
        for f in fixtures:
            cached_game=old_by_fixture.get((f.get("h"),f.get("a"),(f.get("utc") or "")[:10]))
            if cached_game:
                f.update({"n":cached_game.get("n"),"source_id":cached_game.get("source_id"),"hs":cached_game.get("hs"),"as":cached_game.get("as")})
        reload_failed=False
        for f,round_number in played:
            if f.get("source_id") and f["source_id"] in old_games:
                cached+=1
                print(f"LNB boxscore: cached {f.get('h')} - {f.get('a')} ({cached}/{len(played)})",flush=True)
                f.pop("_date",None);f.pop("_index",None);f.pop("_played",None)
                continue
            selector=f'xpath=//*[@id="{f.pop("_date")}"]//div[contains(@class,"perspective")]'
            index=f.pop("_index")
            f.pop("_played",None)
            key=f"{f.get('h')}|{f.get('a')}|{f.get('utc')[:10]}"
            # Click the visible finished game card to obtain its official match-center UUID.
            try:
                print(f"LNB boxscore: loading {f.get('h')} - {f.get('a')} ({new+cached+1}/{len(played)})",flush=True)
                select_round(page,round_number)
                page.locator(selector).nth(index).click(timeout=12000)
                page.wait_for_url(re.compile(r".*/fr/match-center/[^/?#]+"),timeout=20000)
                source_id=page.url.rstrip("/").split("/")[-1]
                f["source_id"]=source_id; f["n"]=crc(source_id)
                if source_id in old_games: cached+=1; page.goto(CALENDAR,wait_until="domcontentloaded",timeout=60000);page.locator('[id^="2026-"],[id^="2027-"]').first.wait_for(timeout=45000);continue
                page.locator("table:visible").first.wait_for(timeout=20000)
                detail=extract_box(page); tables=detail.get("tables") or []
                stat_tables=[t for t in tables if any(norm(h).upper()=="NOM" for h in t.get("heads",[]))]
                if len(stat_tables)<2: raise RuntimeError("match center has no two player boxscore tables")
                hplayers=normalize_table(stat_tables[0]); aplayers=normalize_table(stat_tables[1])
                if not hplayers and not aplayers: raise RuntimeError("empty player boxscore")
                qh=[];qa=[]
                if tables and len(tables[0].get("rows",[]))>=2:
                    qtable=tables[0]["rows"]
                    for row in qtable[:2]:
                        nums=[number(x) for x in row if re.fullmatch(r"\d+",str(x).strip())]
                        if len(nums)>=4:
                            (qh if not qh else qa).extend(nums[:4])
                hs=f.get("hs"); ass=f.get("as")
                if hs is None and qh: hs=sum(qh)
                if ass is None and qa: ass=sum(qa)
                f["hs"]=hs;f["as"]=ass
                # Team identity and order are the calendar's home/away order; the official report uses the same order.
                game={"n":f["n"],"source_id":source_id,"round":f.get("round"),"h":f["h"],"a":f["a"],"hs":hs,"as":ass,"utc":f["utc"],"q":{"h":qh,"a":qa},"box":{"h":{"p":hplayers,"tot":total_from(stat_tables[0],hplayers)},"a":{"p":aplayers,"tot":total_from(stat_tables[1],aplayers)}},"source":"LNB official match center"}
                if not valid_boxscore(game["box"],hs,ass):
                    raise RuntimeError(f"boxscore/player totals do not reconcile with official score {hs}-{ass}; refusing to save")
                old_games[source_id]=game;new+=1
                print(f"LNB boxscore: saved {f.get('h')} - {f.get('a')} ({new+cached}/{len(played)})",flush=True)
                for code,players in ((f["h"],hplayers),(f["a"],aplayers)):
                    roster=site.setdefault("rosters",{}).setdefault(code,{"players":[],"assistants":[]})
                    byid={str(x.get("id")):x for x in roster.get("players",[]) if x.get("id")}
                    for p_row in players:
                        if not p_row[1]: continue
                        pid=str(p_row[0] or f"{code}:{p_row[1]}")
                        byid[pid]={"id":p_row[0],"name":p_row[1],"no":p_row[2],"pos":None,"photo":None}
                    roster["players"]=list(byid.values())
            except Exception as e:
                failures.append(f"{key}: {e}")
                print(f"LNB boxscore warning: {key}: {e}",file=sys.stderr,flush=True)
                # If navigation happened despite an error, return to the calendar to continue safely.
            finally:
                if "/match-center/" in page.url:
                    try:
                        page.goto(CALENDAR,wait_until="domcontentloaded",timeout=60000)
                        page.locator('[id^="2026-"],[id^="2027-"]').first.wait_for(timeout=45000)
                    except Exception as e:
                        failures.append(f"calendar reload: {e}")
                        reload_failed=True
            if reload_failed:
                break
        browser.close()
    for f in fixtures:
        f.pop("_date",None);f.pop("_index",None);f.pop("_played",None)
        if not f.get("n"):
            f["n"]=crc(f"{f.get('h')}|{f.get('a')}|{f.get('utc')}")
    # Keep non-LNB fixture providers (Superbet odds metadata) only where official schedule has no equivalent.
    for f in old_fixtures:
        if "Superbet" in str(f.get("source") or "") and not any(x.get("h")==f.get("h") and x.get("a")==f.get("a") and (x.get("utc") or "")[:10]==(f.get("utc") or "")[:10] for x in fixtures): fixtures.append(f)
    fixtures.sort(key=lambda x:x.get("utc") or "")
    games=sorted(old_games.values(),key=lambda x:x.get("utc") or "")
    site.update({"season":"2026/27","updated":datetime.now(timezone.utc).isoformat(),"league":{"key":"lnb","name":"LNB Betclic Élite","season_label":"2026/27"},"clubs":clubs,"rosters":site.get("rosters") or {},"games":games,"fixtures":fixtures,"data_coverage":{"team_stats":"official LNB match-center boxscores","boxscores":f"{len(games)} official player boxscores","player_rosters":"official match participants; positions and portraits retained only when published","shot_charts":"LNB Match Center displays shot positions; import not yet enabled"}})
    OUT.parent.mkdir(parents=True,exist_ok=True);tmp=OUT.with_suffix(".json.tmp");tmp.write_text(json.dumps(site,ensure_ascii=False,separators=(",",":")),encoding="utf-8");tmp.replace(OUT)
    print(f"LNB: {len(clubs)} clubs, {len(fixtures)} fixtures, {len(games)} player boxscores (+{new} new; {cached} cached), {len(failures)} issues")
    for e in failures[:20]: print("LNB warning:",e,file=sys.stderr)
    return 0 if games or not any(f.get("hs") is not None for f in fixtures) else (1 if not games and failures else 0)

if __name__=="__main__": raise SystemExit(main())
