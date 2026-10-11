#!/usr/bin/env python3
"""Incremental basketball data pipeline for EuroLeague, ABA, ACB, LNB and BBL.

Run from any directory:
    python sync_data.py --all
    python sync_data.py --lg acb

This orchestrates the league-specific scrapers already in the project. It does
not fabricate boxscores, shot coordinates, rosters, player positions or injury
reports when a source does not publish them. See data/sync_report.json after a
run for source coverage and failures.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEAGUES = ("euroleague", "aba", "acb", "lnb", "bbl")

def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)

def archive_league_history(lg):
    """Keep scored games before a league scraper replaces its current site file."""
    folder = ROOT / "data" if lg == "euroleague" else ROOT / "data" / lg
    site = read_json(folder / "site.json", {}) or {}
    old = read_json(folder / "history_games.json", []) or []
    if isinstance(old, dict): old = old.get("games") or []
    merged = {}
    for game in list(old) + list(site.get("games") or []):
        if game.get("hs") is None or game.get("as") is None:
            continue
        date = str(game.get("utc") or game.get("dt") or "")[:10]
        key = f"{date}|{game.get('h')}|{game.get('a')}" if date and game.get("h") and game.get("a") else str(game.get("source_id") or game.get("n"))
        merged[key] = game
    if merged:
        write_json(folder / "history_games.json", list(merged.values()))
    return len(merged)

def run_step(label, args, report):
    cmd = [sys.executable, *args]
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    report.append({"step": label, "command": args, "exit_code": result.returncode,
                   "ok": result.returncode == 0})
    return result.returncode == 0

def norm(value):
    import unicodedata
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode().lower()).strip()

def team_lookup(name, rows):
    key = norm(name)
    exact = [r for r in rows if key and key == norm(r.get("name"))]
    if len(exact) == 1: return exact[0]
    contained = [r for r in rows if key and (key in norm(r.get("name")) or norm(r.get("name")) in key)]
    if len(contained) == 1: return contained[0]
    words = set(key.split())
    ranked = sorted(((len(words & set(norm(r.get("name")).split())), r) for r in rows), key=lambda x:x[0], reverse=True)
    if ranked and ranked[0][0] and (len(ranked)==1 or ranked[0][0] > ranked[1][0]): return ranked[0][1]
    return None

def code_for(row):
    if row.get("code"): return str(row["code"])
    name=norm(row.get("name")); parts=[x for x in name.split() if x]
    return "".join(x[0] for x in parts[:3]).upper() or "UNK"

def make_missing_site(lg):
    """Merge live odds fixtures into the official LNB/BBL scraper output."""
    folder=ROOT/"data"/lg
    odds=read_json(folder/"odds_superbet.json",{}) or {}
    stats=read_json(folder/"team_stats.json",{}) or {}
    rows=stats.get("teams") or []
    path=folder/"site.json"; site=read_json(path,{}) or {}
    clubs=site.get("clubs") or {}
    codes={}
    for row in rows:
        code=code_for(row); codes[norm(row.get("name"))]=code
        existing=clubs.get(code) or {}
        clubs[code]={**existing,"name":existing.get("name") or row.get("name",code),
                     "short":existing.get("short") or code,"crest":row.get("logo") or existing.get("crest")}
    now=dt.datetime.now(dt.timezone.utc)
    fixtures=list(site.get("fixtures") or []); seen=set()
    for f in fixtures:
        seen.add((str(f.get("utc") or f.get("dt") or "")[:10],str(f.get("h")),str(f.get("a"))))
    added=0
    for e in odds.get("events") or []:
        start=e.get("start") or e.get("utc")
        try:
            when=dt.datetime.fromisoformat(str(start).replace("Z","+00:00"))
            if when.tzinfo is None: when=when.replace(tzinfo=dt.timezone.utc)
            if when < now-dt.timedelta(hours=3): continue
        except (TypeError,ValueError): continue
        h=team_lookup(e.get("h"),rows); a=team_lookup(e.get("a"),rows)
        if not h or not a: continue
        hc,ac=code_for(h),code_for(a)
        key=(when.date().isoformat(),hc,ac)
        if key in seen: continue
        raw=f"{lg}|{when.isoformat()}|{hc}|{ac}".encode()
        fixtures.append({"n":zlib.crc32(raw)&0x7fffffff,"round":None,"h":hc,"a":ac,
                         "utc":when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "source":"Superbet schedule feed"})
        seen.add(key);added+=1
    fixtures.sort(key=lambda x:(x.get("utc") or "",str(x.get("n"))))
    coverage=site.get("data_coverage") or {}
    if not rows and not clubs: return {"ok":False,"reason":"no official team data"}
    site.update({"season":site.get("season") or "2026/27","updated":dt.datetime.now(dt.timezone.utc).isoformat(),
                 "league":{"key":lg,"name":{"lnb":"LNB Betclic Élite","bbl":"easyCredit BBL"}[lg],
                           "season_label":"2026/27"},"clubs":clubs,
                 "rosters":site.get("rosters") or {},"games":site.get("games") or [],"fixtures":fixtures,
                 "data_coverage":{**coverage,"team_stats":coverage.get("team_stats") or stats.get("source")}})
    write_json(path,site)
    return {"ok":True,"fixtures_added":added,"clubs":len(clubs),"games_with_boxscore":sum(bool(g.get("box")) for g in site["games"])}

def league_steps(lg):
    if lg=="euroleague":
        return [("EuroLeague results/boxscores and source caches",["fetch_data.py"]),
                ("Build EuroLeague site.json",["build_site.py"]),
                ("EuroLeague shot coordinates",["fetch_shots.py"])]
    if lg=="aba": return [("ABA calendar and incremental boxscores",["fetch_aba.py"])]
    if lg=="acb":
        return [("ACB calendar, incremental boxscores and available shot charts",["fetch_acb.py"]),
                ("ACB team scoring profiles",["fetch_team_stats.py","--lg","acb"])]
    if lg=="lnb": return [("LNB official calendar, match centers and player boxscores",["fetch_lnb.py"])]
    if lg=="bbl": return [("BBL official fixtures, boxscores, play-by-play and shots",["fetch_bbl.py"])]
    return [(f"{lg.upper()} team scoring profiles",["fetch_team_stats.py","--lg",lg])]

def main():
    ap=argparse.ArgumentParser(description="Incremental refresh for supported league sources.")
    group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all",action="store_true",help="refresh all five leagues")
    group.add_argument("--lg",choices=LEAGUES,help="refresh one league")
    ap.add_argument("--no-odds",action="store_true",help="do not refresh Superbet fixtures/markets")
    args=ap.parse_args()
    selected=list(LEAGUES if args.all else [args.lg]); steps=[]; status={}
    for lg in selected:
        archived_games = archive_league_history(lg)
        ok=True
        for label,cmd in league_steps(lg): ok=run_step(label,cmd,steps) and ok
        status[lg]={"source_steps_ok":ok,"historical_games_archived":archived_games}
    if not args.no_odds:
        if args.all:
            ok=run_step("Refresh Superbet odds for all leagues",["fetch_superbet.py","--analytics","aba,acb,lnb,bbl"],steps)
            for lg in selected: status[lg]["odds_refresh_ok"]=ok
        else:
            lg=selected[0]
            ok=run_step(f"Refresh {lg} Superbet schedule and odds",["fetch_superbet.py","--only-lg",lg,"--analytics",lg],steps)
            status[lg]["odds_refresh_ok"]=ok
    for lg in ("lnb","bbl"):
        if lg in selected:
            status[lg]["site_merge"]=make_missing_site(lg)
            status[lg]["scope_note"]="Official LNB/BBL calendars and match reports are preferred; Superbet events fill odds fixtures that are absent from official calendar results. Missing player positions and shot coordinates are not fabricated."
    # Model tips (box score + player stats, or results-only fallback) written into the odds files; must run after site.json/odds are final.
    if not args.no_odds:
        ok=run_step("Model tips and explanations for Superbet odds",["annotate_odds.py",*selected],steps)
        for lg in selected: status[lg]["model_tips_ok"]=ok
    # Keep any existing official/manual injury data. There is no configured,
    # reliable public injury feed for all five leagues in this repository.
    report={"updated":dt.datetime.now(dt.timezone.utc).isoformat(),"selected_leagues":selected,
            "leagues":status,"steps":steps,
            "injuries":"Existing data/injuries.json is retained; automatic injury feed is not configured.",
            "positions":"Positions are retained when present in source rosters; missing positions are not guessed."}
    write_json(ROOT/"data"/"sync_report.json",report)
    print("\nPipeline report: data/sync_report.json")
    for lg,info in status.items(): print(f"{lg}: {info}")
    if any(not x.get("ok",True) for x in steps): return 1
    return 0

if __name__=="__main__": raise SystemExit(main())
