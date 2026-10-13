#!/usr/bin/env python3
"""Download actual Pinnacle 1X2 odds through The Odds API for the selected football league.
Requires ODDS_API_KEY in the environment. Does not overwrite Superbet odds.
"""
import argparse, datetime as dt, json, os, re, sys, unicodedata, urllib.parse, urllib.request

SPORTS={"epl":"soccer_epl","laliga":"soccer_spain_la_liga","l1":"soccer_france_ligue_one","bl":"soccer_germany_bundesliga","sa":"soccer_italy_serie_a"}

def request_json(url, params):
    q=urllib.parse.urlencode(params)
    req=urllib.request.Request(url+"?"+q,headers={"User-Agent":"football-analysis-dashboard/1.0"})
    with urllib.request.urlopen(req,timeout=35) as r:
        data=r.read(); headers=dict(r.headers.items())
    return json.loads(data),headers

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--league",choices=SPORTS,default=os.environ.get("FB_LEAGUE","epl"));a=ap.parse_args()
    key=os.environ.get("ODDS_API_KEY","").strip()
    if not key:
        print("Pinnacle sharp odds preskočene: ODDS_API_KEY nije podešen (Superbet kvote ostaju netaknute).")
        return
    root=os.path.dirname(os.path.abspath(__file__));d=os.path.join(root,"data",a.league)
    try:
        os.environ["FB_LEAGUE"]=a.league
        sys.path.insert(0,root)
        from fb_leagues import LEAGUES
        from football_poisson import canon
        cfg=LEAGUES[a.league]
        sd=json.load(open(os.path.join(d,"statshub_data.json"),encoding="utf-8"))
        known={str(k) for k in (sd.get("teams") or {})}
        events,headers=request_json(f"https://api.the-odds-api.com/v4/sports/{SPORTS[a.league]}/odds",{
            "apiKey":key,"regions":"eu","bookmakers":"pinnacle","markets":"h2h","oddsFormat":"decimal"})
        out=[];hp=os.path.join(d,"sharp_history.json");cp=os.path.join(d,"sharp_closing.json")
        try:history=json.load(open(hp,encoding="utf-8"))
        except (OSError,ValueError):history={}
        try:closing=json.load(open(cp,encoding="utf-8"))
        except (OSError,ValueError):closing={}
        fetched=int(dt.datetime.now(dt.timezone.utc).timestamp())
        for ev in events:
            names={str(x.get("name")):x.get("price") for bk in ev.get("bookmakers",[]) if bk.get("key")=="pinnacle" for mk in bk.get("markets",[]) if mk.get("key")=="h2h" for x in mk.get("outcomes",[])}
            h,a_name=ev.get("home_team"),ev.get("away_team")
            hk,ak=canon(h,known),canon(a_name,known)
            draw=next((v for k,v in names.items() if k.lower() in ("draw","tie")),None)
            odds=[names.get(h),draw,names.get(a_name)]
            if not hk or not ak or not all(isinstance(x,(int,float)) and x>1 for x in odds):
                continue
            inv=[1/x for x in odds];s=sum(inv);fair=[x/s for x in inv]
            start=ev.get("commence_time") or "";key_=f"{hk}|{ak}|{start}"
            snapshots=history.setdefault(key_,{"h":h,"a":a_name,"hk":hk,"ak":ak,"start":start,"snaps":[]})["snaps"]
            if not snapshots or snapshots[-1][1]!=odds:snapshots.append([fetched,odds])
            history[key_]["snaps"]=snapshots[-400:]
            out.append({"id":ev.get("id"),"h":h,"a":a_name,"hk":hk,"ak":ak,"start":start,"x12":odds,"fair_x12":fair})
        now=fetched
        for k,hist in list(history.items()):
            try:ko=dt.datetime.fromisoformat((hist.get("start") or "").replace("Z","+00:00")).timestamp()
            except ValueError:ko=None
            if ko and ko < now-3*86400:
                before=[s for s in hist.get("snaps",[]) if s[0]<=ko]
                if before:closing[k]={"h":hist.get("h"),"a":hist.get("a"),"hk":hist.get("hk"),"ak":hist.get("ak"),"start":hist.get("start"),"close":before[-1]}
                del history[k]
        if len(closing)>8000:closing=dict(sorted(closing.items(),key=lambda kv:kv[1].get("close",[0])[0],reverse=True)[:8000])
        payload={"updated":dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),"source":"The Odds API / Pinnacle","sport":SPORTS[a.league],"remaining":headers.get("x-requests-remaining"),"events":out}
        os.makedirs(d,exist_ok=True);p=os.path.join(d,"sharp_odds.json");tmp=p+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:json.dump(payload,f,ensure_ascii=False,separators=(",",":"))
        os.replace(tmp,p)
        for name,data in (("sharp_history.json",history),("sharp_closing.json",closing)):
            dest=os.path.join(d,name)
            with open(dest+".tmp","w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False,separators=(",",":"))
            os.replace(dest+".tmp",dest)
        print(f"Pinnacle {a.league}: {len(out)} uparenih mečeva; API krediti preostali: {payload['remaining'] or '?'} -> {p}")
    except Exception as e:
        print(f"Pinnacle {a.league}: feed nije osvežen ({type(e).__name__}: {e}); prethodni fajl ostaje sačuvan.")
        return
if __name__=="__main__":main()
