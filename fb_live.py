#!/usr/bin/env python3
"""LIVE GAMES: Superbet in-play feed -> model -> data/live.json (spreman za prikaz, sajt ga samo crta).

  py fb_live.py --probe                 # nadje koji 'index' u feedu daje mecove uzivo (isprobava nekoliko imena)
  py fb_live.py --dump                  # sirov izgled jednog live meca (rezultat, minut, statistika, trzista), da namestimo parser
  py fb_live.py --loop 45 --serve       # svakih ~45 s osvezi; --serve otvara http://localhost:8765/live.json za live.js (bez git push-a)
  py fb_live.py --loop 45 --serve --push-every 300    # + na svakih 5 min gurne data/live.json u repo (za telefon)
Isti javni feed i ista pravila kao fetch_superbet.py: jedan zahtev po prolazu, staje na 403/429, bez zaobilazenja.

Sta radi po meču: (1) rezultat, minut, statistika ako je feed daje; (2) predmeč ocekivani golovi iz data/*/predictions.json (context.lam),
a za kosarku linije iz data/odds_superbet.json; (3) preracuna sta je ostalo da se igra (rezultat, minut, crveni kartoni, tempo iz statistike),
(4) poredi sa kvotama uzivo, bira tip sa najvecim EV i pise 'zasto'. Svi koeficijenti u PARAMS su pretpostavke, nekalibrisani."""
import argparse, collections, datetime, difflib, glob, http.server, json, math, os, random, re, sys, threading, time, unicodedata, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_superbet import get, events_in, num, line_of, event_id, URL, push, DETAIL
try: from fetch_superbet import tour_text
except ImportError: tour_text = lambda ev: ""

LIVE = os.path.join("data", "live.json")
HIST = os.path.join("data", "live_history.json")
FEED = URL.replace("sports=4", "sports={sp}").replace("index=active-prematch", "index={idx}")
PARAMS = dict(w=0.30, min_ev=0.04, o_min=1.25, o_max=8.0, lead_def=0.06, trail_att=0.10, red_own=0.78, red_opp=1.12, tempo=(0.75, 1.30),
              shot_on_xg=0.30, shot_off_xg=0.07, danger_xg_per_90=0.08, possession_max=0.10, stats_weight=0.55)
STAT = [("shots_on", r"u okvir|on target|na gol"), ("shots", r"udarc|sut|shots"), ("poss", r"posed|possession"), ("corners", r"korner|corner"),
        ("red", r"crven|red card"), ("yellow", r"zut|yellow"), ("danger", r"opasn|dangerous"), ("attacks", r"napad|attack"), ("xg", r"\bxg\b|expected")]


def low(s): return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")
def nrm(s): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", low(s))).strip()
def jl(p, d):
    try: return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError): return d


# ---------------------------------------------------------------- izvlacenje stanja meca iz sirovog dogadjaja (tolerantno, nema garancije za imena polja)
def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("markets", "results", "superbets", "priceboosts"): continue
            yield path + "." + k, k, v
            yield from walk(v, path + "." + k)
    elif isinstance(o, list):
        for i, v in enumerate(o[:40]): yield from walk(v, f"{path}[{i}]")


def find_score(ev):
    st = ev.get("inplay_stats") or {}
    h, a = num(st.get("home_team_score")), num(st.get("away_team_score"))
    if h is not None and a is not None: return [int(h), int(a)]
    for _, k, v in walk(ev):
        if not re.search(r"score|result|rezultat", k, re.I): continue
        if isinstance(v, dict):
            h, a = (num(v.get(x)) for x in ("home", "h")) if "home" in v or "h" in v else (None, None)
            a = num(v.get("away", v.get("a"))) if h is not None else None
            if h is not None and a is not None: return [int(h), int(a)]
        if isinstance(v, str):
            m = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", v)
            if m: return [int(m[1]), int(m[2])]
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v): return [int(v[0]), int(v[1])]
    return None


def find_minute(ev):
    st, md = ev.get("inplay_stats") or {}, ev.get("inplay_stats_metadata") or {}
    m = re.match(r"^\s*(\d+)(?:\s*\+\s*(\d+))?", str(st.get("minutes", "")))
    if m: return int(m[1]) + int(m[2] or 0)
    lab = str(md.get("event_status_label", "")).upper()
    if lab == "HT": return 45
    for _, k, v in walk(ev):
        if re.search(r"minute|elapsed|match_?time|clock|timer|time_?played", k, re.I) and not isinstance(v, (dict, list)):
            m = re.match(r"^\D*(\d+)(?::(\d+))?", str(v))
            if m: return int(m[1]) + (1 if m[2] and int(m[2]) > 0 and ":" in str(v) and int(m[1]) < 90 else 0)
    return None


INP = {"yellow_cards": "yellow", "red_cards": "red", "corners": "corners", "shots_on_target": "shots_on", "shots_on_goal": "shots_on", "shots_on": "shots_on",
       "shots": "shots", "total_shots": "shots", "shots_total": "shots", "possession": "poss", "possession_percentage": "poss", "dangerous_attacks": "danger", "attacks": "attacks", "xg": "xg"}


def find_stats(ev):
    out = {}
    normalized = ev.get("_normalized_live_stats") or {}
    if isinstance(normalized, dict): out.update(normalized)
    st = ev.get("inplay_stats") or ev.get("inPlayStats") or ev.get("in_play_stats") or {}  # feed varira izmedju snake/camel case
    if not isinstance(st, dict): st = {}
    for k, v in st.items():
        m = re.match(r"^home_team_(\w+)$", k)
        if m and m[1] in INP:
            h, a = num(v), num(st.get("away_team_" + m[1]))
            if h is not None and a is not None: out[INP[m[1]]] = [h, a]
    def put(name, h, a):
        for key, rx in STAT:
            if re.search(rx, low(name)) and key not in out:
                hv, av = num(str(h).replace("%", "")), num(str(a).replace("%", ""))
                if hv is not None and av is not None: out[key] = [hv, av]
                return
    for _, k, v in walk(ev):
        if not re.search(r"inplaystats|in_play_stats|statistic|statistics", k, re.I) or re.search(r"metadata", k, re.I): continue
        items = v.items() if isinstance(v, dict) else enumerate(v if isinstance(v, list) else [])
        for key, it in items:
            if isinstance(it, dict):
                nm = next((it[x] for x in ("name", "stat", "type", "key", "label") if isinstance(it.get(x), str)), key if isinstance(key, str) else "")
                vals = it.get("values") if isinstance(it.get("values"), list) and len(it["values"]) == 2 else [it.get("home", it.get("value1")), it.get("away", it.get("value2"))]
                put(nm, *vals)
            elif isinstance(it, (list, tuple)) and len(it) >= 3:
                put(str(key), it[-2], it[-1])
    return out


# ESPN's public scoreboard supplies live football boxscore stats for several
# top leagues. Superbet remains the source of scores/odds; this is stats-only.
ESPN_LEAGUES = {"epl": "eng.1", "laliga": "esp.1", "l1": "fra.1", "bl": "ger.1", "sa": "ita.1", "cl": "uefa.champions", "el": "uefa.europa", "ecl": "uefa.europa.conf"}


def espn_scoreboard(league, day):
    slug = ESPN_LEAGUES.get(league)
    if not slug: return []
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day.replace('-', '')}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; live-match-stats/1.0)"})
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.load(response).get("events", [])


def _team_match(a, b):
    aa, bb = nrm(a), nrm(b)
    if not aa or not bb: return 0.0
    if aa == bb or aa in bb or bb in aa: return 1.0
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def supplement_espn_stats(ev, league, cache):
    """Fill missing live football stats from ESPN, matched by both team names."""
    fixture = ev.get("fixture") or {}
    date = str(fixture.get("utc_date") or "")[:10]
    parts = re.split(r"\s*[·•]\s*", fixture.get("event_name", ""), maxsplit=1)
    if len(parts) != 2 or not date or league not in ESPN_LEAGUES: return ev
    key = (league, date)
    if key not in cache:
        try: cache[key] = espn_scoreboard(league, date)
        except Exception as exc:
            cache[key] = []
            print(f"ESPN live stats {league} {date}: unavailable ({type(exc).__name__})")
    best, best_score = None, 0.0
    for event in cache[key]:
        competitions = event.get("competitions") or []
        if not competitions: continue
        teams = competitions[0].get("competitors") or []
        home = next((x for x in teams if x.get("homeAway") == "home"), None)
        away = next((x for x in teams if x.get("homeAway") == "away"), None)
        if not home or not away: continue
        hm = _team_match(parts[0], (home.get("team") or {}).get("displayName", ""))
        am = _team_match(parts[1], (away.get("team") or {}).get("displayName", ""))
        score = (hm + am) / 2
        if min(hm, am) >= 0.84 and score > best_score: best, best_score = (home, away), score
    if not best or best_score < 0.90: return ev
    mapped = {"shotsOnTarget": "shots_on", "totalShots": "shots", "possessionPct": "poss",
              "dangerousAttacks": "danger", "attacks": "attacks", "wonCorners": "corners",
              "redCards": "red", "yellowCards": "yellow", "expectedGoals": "xg"}
    data = {}
    for idx, side in enumerate(best):
        for stat in side.get("statistics") or []:
            name = mapped.get(stat.get("name"))
            val = num(str(stat.get("displayValue", "")).replace("%", ""))
            if name and val is not None:
                data.setdefault(name, [None, None])[idx] = val
    if not any(k in data for k in ("shots_on", "shots", "poss")): return ev
    existing = dict(ev.get("_normalized_live_stats") or {})
    for name, values in data.items():
        if name not in existing or existing[name] is None:
            existing[name] = values
    old_source = str(existing.get("_source") or "")
    existing["_source"] = (old_source + " + ESPN live scoreboard") if old_source and "ESPN live scoreboard" not in old_source else (old_source or "ESPN live scoreboard")
    new = dict(ev); new["_normalized_live_stats"] = existing
    return new


FLASH_PAGES = {
    "epl": "https://www.flashscore.com/football/england/premier-league/",
    "laliga": "https://www.flashscore.com/football/spain/laliga/",
    "l1": "https://www.flashscore.com/football/france/ligue-1/",
    "bl": "https://www.flashscore.com/football/germany/bundesliga/",
    "sa": "https://www.flashscore.com/football/italy/serie-a/",
    "cl": "https://www.flashscore.com/football/europe/champions-league/",
    "el": "https://www.flashscore.com/football/europe/europa-league/",
    "ecl": "https://www.flashscore.com/football/europe/conference-league/",
}
FLASH_CACHE_SECONDS = 45
FLASH_STATS_TTL = 25
FLASH_BROWSER = {"pw": None, "browser": None, "page": None}
FLASH_LINK_CACHE = {}
FLASH_STATS_CACHE = {}
FLASH_CURSOR = 0
FLASH_WARNED = False


def _flash_page():
    if FLASH_BROWSER["page"] is None:
        from playwright.sync_api import sync_playwright
        FLASH_BROWSER["pw"] = sync_playwright().start()
        try:
            FLASH_BROWSER["browser"] = FLASH_BROWSER["pw"].chromium.launch(headless=True)
        except Exception:
            # Use the machine's regular Chrome if Playwright's bundled browser is absent.
            chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if not os.path.exists(chrome): raise
            FLASH_BROWSER["browser"] = FLASH_BROWSER["pw"].chromium.launch(headless=True, executable_path=chrome)
        ctx = FLASH_BROWSER["browser"].new_context(locale="en-US", viewport={"width": 1366, "height": 900},
                                                   user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        FLASH_BROWSER["page"] = ctx.new_page()
    return FLASH_BROWSER["page"]


def _flash_cookies(page):
    """Zatvori baner sa kolacicima (inace moze da prekrije stranicu); tiho ignorisi ako ga nema."""
    for sel in ("#onetrust-accept-btn-handler", "button:has-text('I Accept')", "button:has-text('Accept all')", "button:has-text('Prihvati')"):
        try:
            b = page.locator(sel).first
            if b.count() and b.is_visible():
                b.click(timeout=1500); page.wait_for_timeout(300); return
        except Exception:
            pass


def _flash_parse_rendered_stats(text):
    """Read stats displayed on the public Flashscore match page; no hidden feed."""
    flat = re.sub(r"\s+", " ", text)
    number = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?%?(?![A-Za-z0-9])")
    aliases = {
        "poss": ("ball possession",), "shots": ("total shots",),
        "shots_on": ("shots on target",), "corners": ("corner kicks",),
        "danger": ("dangerous attacks", "attacks dangereux", "ataques peligrosos"),
        "attacks": ("attacks", "ataques"),
        "xg": ("expected goals (xg)",),
    }
    label_rx = re.compile("|".join(re.escape(label) for labels in aliases.values() for label in labels), re.I)
    label_matches = list(label_rx.finditer(flat))
    out = {}
    for key, labels in aliases.items():
        for label in labels:
            for match in re.finditer(re.escape(label), flat, re.I):
                # Flashscore's rendered text puts the two team values after the
                # stat label (sometimes one value on each side). Stop at the next
                # stat label so we never mistake the previous row's numbers for
                # dangerous attacks or another metric.
                next_label = next((m.start() for m in label_matches if m.start() >= match.end()), len(flat))
                after = list(number.finditer(flat[match.end():next_label]))
                if len(after) >= 2:
                    raw = [x.group() for x in after[:2]]
                elif len(after) == 1:
                    previous_label_end = max((m.end() for m in label_matches if m.end() <= match.start()), default=max(0, match.start()-80))
                    before = list(number.finditer(flat[previous_label_end:match.start()]))
                    if not before: continue
                    raw = [before[-1].group(), after[0].group()]
                else:
                    previous_label_end = max((m.end() for m in label_matches if m.end() <= match.start()), default=max(0, match.start()-80))
                    before = list(number.finditer(flat[previous_label_end:match.start()]))
                    if len(before) < 2: continue
                    raw = [before[-2].group(), before[-1].group()]
                h = num(raw[0].replace("%", ""))
                a = num(raw[1].replace("%", ""))
                if h is not None and a is not None:
                    out[key] = [h, a]
                    break
            if key in out: break
    return out


FLASH_LABELS = {
    "poss": ("ball possession", "possession"), "shots": ("total shots",), "shots_on": ("shots on target", "shots on goal"),
    "shots_off": ("shots off target", "shots off goal"), "corners": ("corner kicks", "corners"), "danger": ("dangerous attacks",),
    "attacks": ("attacks",), "xg": ("expected goals (xg)", "expected goals"), "yellow": ("yellow cards",), "red": ("red cards",),
    "fouls": ("fouls",), "offsides": ("offsides",), "saves": ("goalkeeper saves",), "big": ("big chances",), "blocked": ("blocked shots",),
}


def _flash_testid_rows(page):
    """Flashscore 'wcl' markup: svaki red statistike je [data-testid=wcl-statistics] sa 2 vrednosti i nazivom. Najpouzdaniji nacin ako postoji."""
    try:
        rows = page.locator("[data-testid='wcl-statistics']").evaluate_all(
            "els => els.map(e => ({c: (e.querySelector(\"[data-testid='wcl-statistics-category']\")||{}).innerText||'', "
            "v: [...e.querySelectorAll(\"[data-testid='wcl-statistics-value']\")].map(x => x.innerText)}))")
    except Exception:
        return {}
    out = {}
    for r in rows:
        c = re.sub(r"\s+", " ", (r.get("c") or "")).strip().lower()
        vals = [num(re.sub(r"[^\d.,-]", "", str(x).split("(")[0]).replace(",", ".")) for x in (r.get("v") or [])]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2: continue
        for k, labels in FLASH_LABELS.items():
            if c in labels and k not in out:
                out[k] = [vals[0], vals[1]]
    return out


def _flash_generic_rows(text):
    """Bilo koji red 'vrednost / naziv / vrednost' iz teksta stranice (naziv = bilo sta sa slovima). Vraca {naziv_malim_slovima: [h, a]}."""
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.split("\n") if x.strip()]
    isnum = lambda x: bool(re.match(r"^\d+(?:[.,]\d+)?\s*%?(?:\s*\(.*\))?$", x))
    val = lambda x: num(re.sub(r"[^\d.,]", "", x.split("(")[0]).replace(",", "."))
    out = {}
    for i in range(1, len(lines) - 1):
        lab = lines[i]
        if isnum(lab) or not re.search(r"[A-Za-z]", lab) or len(lab) > 40: continue
        if isnum(lines[i - 1]) and isnum(lines[i + 1]):
            h, a = val(lines[i - 1]), val(lines[i + 1])
            if h is not None and a is not None and lab.lower() not in out: out[lab.lower()] = [h, a]
    return out


def _flash_parse_stat_rows(page):
    """Read each statistic from its own rendered DOM row, avoiding nearby-row mixups."""
    got = _flash_testid_rows(page)
    try: gen = _flash_generic_rows(page.locator("body").inner_text(timeout=3000))
    except Exception: gen = {}
    for lab, v in gen.items():                      # poznati nazivi -> kljucevi modela, sve ostalo -> 'x:naziv' (prikazuje se u 'Zasto?')
        key = next((k for k, labels in FLASH_LABELS.items() if lab in labels), None)
        if key:
            got.setdefault(key, v)
        else:
            got["x:" + lab] = v
    if got and any(k in got for k in ("poss", "shots", "shots_on", "corners", "xg")): return got
    return got or page.locator("body").evaluate(r"""body => {
      const aliases = {
        poss: ["ball possession", "possession", "posesión", "possession du ballon", "possession palla", "possession de balón"],
        shots: ["total shots", "shots", "tiros totales", "tirs", "schüsse"],
        shots_on: ["shots on target", "shots on goal", "tiros a puerta", "tirs cadrés", "tiri in porta"],
        corners: ["corner kicks", "corners", "corner", "córners", "coup de pied de coin"],
        danger: ["dangerous attacks", "dangerous attack", "attacks dangereux", "ataques peligrosos", "attacchi pericolosi", "gefahrene angriffe", "opasni napadi"],
        xg: ["expected goals (xg)"]
      };
      const norm = s => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
      const number = /(?<![A-Za-z0-9])\d+(?:[.,]\d+)?%?(?![A-Za-z0-9])/g;
      const nodes = Array.from(body.querySelectorAll("span,div,p,strong,b"));
      const lines = (body.innerText || "").split(/\n+/).map(x => x.replace(/\s+/g, " ").trim()).filter(Boolean);
      const out = {};
      for (const [key, labels] of Object.entries(aliases)) {
        let best = null;
        for (const label of labels) {
          const ln = norm(label);
          const exact = nodes.filter(el => el.getClientRects().length && norm(el.innerText || el.textContent) === ln);
          for (const leaf of exact) {
            let cur = leaf;
            for (let depth = 0; cur && depth < 7; depth++, cur = cur.parentElement) {
              const text = (cur.innerText || cur.textContent || "").replace(/\s+/g, " ").trim();
              if (!text || text.length > 180 || norm(text).split(ln).length !== 2) continue;
              const values = text.match(number) || [];
              if (values.length !== 2) continue;
              const candidate = {values: values.map(v => Number(v.replace(",", ".").replace("%", ""))),
                                depth, length: text.length};
              if (!best || candidate.depth < best.depth ||
                  (candidate.depth === best.depth && candidate.length < best.length)) best = candidate;
            }
          }
        }
        // Some Flashscore layouts render the label and each side's value as
        // separate text lines, so no single DOM ancestor contains the row.
        // Pair only the two immediate numeric lines following the label.
        if (!best) {
          for (let i = 0; i < lines.length; i++) {
            if (!labels.some(label => norm(lines[i]) === norm(label))) continue;
            const values = [];
            for (let j = i + 1; j < Math.min(lines.length, i + 4) && values.length < 2; j++) {
              if (/^\d+(?:[.,]\d+)?%?$/.test(lines[j])) values.push(Number(lines[j].replace(",", ".").replace("%", "")));
              else if (Object.values(aliases).flat().some(label => norm(lines[j]) === norm(label))) break;
            }
            if (values.length === 2) { best = {values, depth: 99, length: 0}; break; }
          }
        }
        if (best && best.values.every(Number.isFinite)) out[key] = best.values;
      }
      if (out.poss && (out.poss.some(v => v < 0 || v > 100) || Math.abs(out.poss[0] + out.poss[1] - 100) > 3)) delete out.poss;
      if (out.shots && out.shots.some(v => v < 0 || v > 60)) delete out.shots;
      if (out.shots_on && (out.shots_on.some(v => v < 0 || v > 30) ||
          (out.shots && out.shots_on.some((v, i) => v > out.shots[i])))) delete out.shots_on;
      if (out.danger && out.danger.some(v => v < 0 || v > 250)) delete out.danger;
      if (out.xg && out.xg.some(v => v < 0 || v > 12)) delete out.xg;
      return out;
    }""")


def _flash_match_links(page, league):
    cached = FLASH_LINK_CACHE.get("home")
    if cached and time.time() - cached["at"] < FLASH_CACHE_SECONDS: return cached["links"]
    # The public homepage's live scoreboard lists active match-centre links
    # across competitions; use its normal rendered links, not private feeds.
    JS = "els => els.map(a => {let e=a, context=''; for(let i=0;i<5&&e;i++,e=e.parentElement) {let t=(e.innerText||'').trim(); if(!context&&t&&t.length<300) context=t;} return {href:a.href,context}})"
    links = []
    for url in ["https://www.flashscore.com/"] + [u.rstrip("/") + "/fixtures/" for u in FLASH_PAGES.values()]:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            _flash_cookies(page)
            page.locator('a[href*="/match/football/"]').first.wait_for(state="attached", timeout=12000)
            page.wait_for_timeout(2200)  # let the public scoreboard hydrate before collecting links
            links += page.locator('a[href*="/match/football/"]').evaluate_all(JS)
        except Exception as exc:
            print(f"  Flashscore: {url} ne daje linkove mečeva ({type(exc).__name__})")
        if url == "https://www.flashscore.com/" and len(links) >= 20: break   # pocetna je dovoljna; lige samo kao rezerva
    records, seen = [], set()
    for item in links:
        href = item.get("href", "").split("#", 1)[0]
        match = re.search(r"/match/football/([^/]+)/([^/?#]+)", href)
        if not match: continue
        home_slug = re.sub(r"-[A-Za-z0-9]{8}$", "", match.group(1))
        away_slug = re.sub(r"-[A-Za-z0-9]{8}$", "", match.group(2))
        if href in seen: continue
        seen.add(href)
        records.append({"href": href, "home": nrm(home_slug.replace("-", " ")),
                        "away": nrm(away_slug.replace("-", " ")), "context": nrm(item.get("context", ""))})
    FLASH_LINK_CACHE["home"] = {"at": time.time(), "links": records}
    return records


def flashscore_refresh(candidates, max_details=3):
    """Read available live stats from ordinary rendered Flashscore match pages."""
    global FLASH_CURSOR, FLASH_WARNED
    if not candidates or max_details <= 0: return {}
    wanted = []
    for ev, league in candidates:
        if league not in FLASH_PAGES: continue
        key = str(event_id(ev) or ename(ev))
        cached = FLASH_STATS_CACHE.get(key)
        if cached and time.time() - cached["at"] <= FLASH_STATS_TTL: continue
        parts = re.split(r"\s*[·•]\s*", ename(ev), maxsplit=1)
        if len(parts) == 2: wanted.append((key, ev, league, nrm(parts[0]), nrm(parts[1])))
    if not wanted: return {k: v["stats"] for k, v in FLASH_STATS_CACHE.items() if time.time()-v["at"] <= FLASH_STATS_TTL}
    start = FLASH_CURSOR % len(wanted)
    selected = [wanted[(start+i) % len(wanted)] for i in range(min(max_details, len(wanted)))]
    FLASH_CURSOR = (start + len(selected)) % max(1, len(wanted))
    try:
        page = _flash_page()
        for key, ev, league, home, away in selected:
            try:
                links = _flash_match_links(page, league)
                match_link, best = None, 0.0
                for item in links:
                    hs = max(_team_match(home, item["home"]), _team_match(home, item["context"]))
                    aws = max(_team_match(away, item["away"]), _team_match(away, item["context"]))
                    score = (hs + aws) / 2
                    if min(hs, aws) >= 0.72 and score > best:
                        match_link, best = item, score
                if not match_link:
                    print(f"  Flashscore: nema linka za {home} - {away} medju {len(links)} linkova sa stranice (ime se ne poklapa ili meč nije na listi)")
                    continue
                base = match_link["href"].split("#", 1)[0]
                page.goto(base + "#/match-summary/match-statistics/0", wait_until="domcontentloaded", timeout=25000)
                _flash_cookies(page)
                try: page.locator("[data-testid='wcl-statistics'], [class*='stat__']").first.wait_for(state="attached", timeout=7000)
                except Exception: pass
                # Statistics are often behind a match-centre tab, and the site
                # may localize labels. Treat these controls as optional: a
                # missing English label must never abort the live scan.
                try:
                    stats_tab = page.get_by_text(re.compile(r"^(Statistics|Stats|Statistika|Estadísticas|Statistiques|Statistiken|Statistiche)$", re.I)).first
                    if stats_tab.count(): stats_tab.click(timeout=1800)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                # Expand additional public rows where the rendered page offers it.
                for _ in range(3):
                    more = page.get_by_text(re.compile(r"^(Show more|Prikaži više|Afficher plus|Mostrar más|Mostra altro|Mehr anzeigen)$", re.I)).first
                    if not more.count(): break
                    try: more.click(timeout=1200)
                    except Exception: break
                stats = _flash_parse_stat_rows(page)
                if any(k in stats for k in ("shots", "shots_on", "poss", "danger", "corners", "xg")):
                    stats["_source"] = "Flashscore public match page"
                    print(f"  Flashscore OK: {ename(ev)} -> {sorted(k for k in stats if k != '_source')}")
                    FLASH_STATS_CACHE[key] = {"at": time.time(), "stats": stats}
                else:
                    print(f"  Flashscore: otvorena stranica {match_link['href']} ali statistika nije pročitana ({ename(ev)}); pokreni: py fb_live.py --flash-probe \"{home.split()[0]}\"")
            except Exception as exc:
                print(f"  Flashscore stranica nedostupna ({type(exc).__name__}: {str(exc)[:160]}); ESPN/Superbet rezerva ostaje")
    except Exception as exc:
        print(f"  Flashscore browser nije pokrenut ({type(exc).__name__}: {str(exc)[:160]}). Treba: py -m pip install playwright && py -m playwright install chromium")
    return {k: v["stats"] for k, v in FLASH_STATS_CACHE.items() if time.time()-v["at"] <= FLASH_STATS_TTL}


def merge_supplemental_stats(ev, supplemental):
    if not supplemental: return ev
    merged = dict(ev.get("_normalized_live_stats") or {})
    old_source = merged.get("_source")
    prefer_provider = str(supplemental.get("_source") or "").startswith("Flashscore")
    for key, value in supplemental.items():
        if key == "_source": continue
        if prefer_provider and isinstance(value, list) and len(value) == 2 and all(x is not None for x in value):
            merged[key] = value
        elif key not in merged or merged[key] is None: merged[key] = value
    new_source = supplemental.get("_source")
    merged["_source"] = f"{old_source} + {new_source}" if old_source and new_source and old_source != new_source else (new_source or old_source)
    new = dict(ev); new["_normalized_live_stats"] = merged
    return new


LIVE_STAT_FIELDS = ("shots_on", "shots", "poss")
OPTIONAL_LIVE_STAT_FIELDS = ("danger",)


SR = {"poss": "Posed (%)", "shots": "Ukupno udaraca", "shots_on": "Udarci u okvir", "shots_off": "Udarci van okvira", "blocked": "Blokirani udarci", "xg": "xG (očekivani golovi)",
      "big": "Velike šanse", "corners": "Korneri", "danger": "Opasni napadi", "attacks": "Napadi", "fouls": "Prekršaji", "offsides": "Ofsajdi", "saves": "Odbrane golmana",
      "yellow": "Žuti kartoni", "red": "Crveni kartoni"}
SR_X = {"big chances missed": "Promašene velike šanse", "big chances scored": "Iskorišćene velike šanse", "clearances": "Izbijanja", "crosses": "Centarsuti", "defense": "Odbrana",
        "duels won": "Dobijeni dueli", "errors leading to goal": "Greške pred gol", "errors leading to shot": "Greške pred udarac", "expected assists (xa)": "xA (očekivane asistencije)",
        "final third passes": "Pasovi u završnoj trećini", "free kicks": "Slobodni udarci", "goal kicks": "Aut sa gola", "goalkeeping": "Golmanska igra", "goals conceded": "Primljeni golovi",
        "goals prevented": "Sprečeni golovi", "headed goals": "Golovi glavom", "hit the woodwork": "Okvir gola (stativa/prečka)", "interceptions": "Presecanja", "key passes": "Ključni pasovi",
        "long passes": "Dugi pasovi", "passes": "Pasovi", "shots inside the box": "Udarci iz kaznenog prostora", "shots outside the box": "Udarci van kaznenog prostora", "tackles": "Startovi",
        "throw-ins": "Auti", "touches in opposition box": "Dodiri u kaznenom prostoru protivnika", "xg on target (xgot)": "xGOT (xG u okvir)", "xgot faced": "xGOT protiv golmana", "attack": "Napad"}


def stats_full(stats):
    """Lista {k,l,h,a} svega sto je izvor dao, na srpskom, redom: osnovno pa ostalo. UI samo crta ovu listu."""
    out, seen = [], set()
    for k in ("poss", "shots", "shots_on", "shots_off", "blocked", "xg", "big", "corners", "danger", "attacks", "fouls", "offsides", "saves", "yellow", "red"):
        v = stats.get(k)
        if isinstance(v, list) and len(v) == 2 and all(x is not None for x in v): out.append({"k": k, "l": SR[k], "h": v[0], "a": v[1]}); seen.add(k)
    for k, v in stats.items():
        if k.startswith("x:") and isinstance(v, list) and len(v) == 2 and len(k) > 4 and k[2:] not in ("shots", "x"):
            out.append({"k": k, "l": SR_X.get(k[2:], k[2:].capitalize()), "h": v[0], "a": v[1]})
    return out


def stats_status(stats):
    present = [k for k in LIVE_STAT_FIELDS if isinstance(stats.get(k), list) and len(stats[k]) == 2 and all(v is not None for v in stats[k])]
    missing = [k for k in LIVE_STAT_FIELDS if k not in present]
    optional_missing = [k for k in OPTIONAL_LIVE_STAT_FIELDS if not (isinstance(stats.get(k), list) and len(stats[k]) == 2 and all(v is not None for v in stats[k]))]
    return {"present": present, "missing": missing, "optional_missing": optional_missing,
            "complete": not missing, "source": stats.get("_source") or ("Superbet in-play feed" if present else None)}


# ---------------------------------------------------------------- kvote uzivo
def odds_of(ev):
    x12, ou, bt = {}, collections.defaultdict(dict), {}
    h2, tt = {}, collections.defaultdict(dict)
    for mk in ev.get("markets") or []:
        nm = low(mk.get("name", "")).strip()
        kind = "btts" if nm == "oba tima daju gol" else "x12" if nm in ("konacan ishod", "pobednik") else "ou" if nm in ("ukupno golova", "ukupno poena") else None
        if not kind: continue
        groups = collections.defaultdict(dict)
        for o in mk.get("odds") or []:
            p = num(o.get("price")); md = o.get("metadata") or {}
            if p is None or p <= 1.01 or o.get("status", 1) != 1 or o.get("display") is False: continue
            code, name = str(md.get("code", "")).strip().lower(), low(md.get("name", "")).strip()
            if kind == "x12":
                M12 = {"1": "1", "x": "x", "0": "x", "2": "2", "home": "1", "away": "2", "draw": "x", "remi": "x", "nereseno": "x"}
                s = M12.get(code) or M12.get(name) or ("x" if re.search(r"remi|neresen|draw", low(md.get("info", ""))) else None)
                if s: x12[s] = p
            elif kind == "btts":
                s = "y" if code in ("gg", "da", "yes") or name in ("da", "gg") else "n" if code in ("ng", "ne", "no") or name in ("ne", "ng") else None
                if s: bt[s] = p
            else:
                ln = line_of(o, mk.get("name", ""))
                s = "o" if code in ("+", "o", "over") or name.startswith(("vise", "preko")) else "u" if code in ("-", "u", "under") or name.startswith(("manje", "ispod")) else None
                if s and ln is not None: groups[md.get("market_line_uuid") or ln][s] = (p, ln)
        for g in groups.values():
            if "o" in g and "u" in g and (g["o"][1] * 2) % 1 == 0: ou[g["o"][1]] = [g["o"][0], g["u"][0]]
    X = [x12["1"], x12["x"], x12["2"]] if all(k in x12 for k in "1x2") else None
    H = [x12["1"], x12["2"]] if not X and "1" in x12 and "2" in x12 else None
    return {"x12": X, "h2h": H, "ou": sorted([[l, v[0], v[1]] for l, v in ou.items()]), "btts": [bt["y"], bt["n"]] if "y" in bt and "n" in bt else None}


# ---------------------------------------------------------------- fudbalski model: sta je ostalo da se igra
def pmf(l, i): return (math.exp(-l + i * math.log(l) - math.lgamma(i + 1)) if l > 0 else float(i == 0))


def final_dist(gh, ga, rh, ra, N=12):
    P = [[pmf(rh, i) * pmf(ra, j) for j in range(N)] for i in range(N)]
    s = sum(map(sum, P)); x, tot, bt = [0.0] * 3, collections.Counter(), 0.0
    for i in range(N):
        for j in range(N):
            v = P[i][j] / s; fh, fa = gh + i, ga + j
            x[0 if fh > fa else 1 if fh == fa else 2] += v; tot[fh + fa] += v
            if fh and fa: bt += v
    return {"x12": x, "over": lambda L: sum(v for t, v in tot.items() if t > L), "btts": bt}


def fb_rates(lh, la, minute, gh, ga, reds, stats, P=PARAMS):
    rem = max(0.0, 90 - minute)
    if minute >= 88: rem = max(rem, 3.0)                    # nadoknada
    f, ah, aa, notes = rem / 90, 1.0, 1.0, []
    d, k = gh - ga, min(1.0, minute / 60)
    if minute >= 15 and d:                                   # onaj koji gubi napada vise, onaj koji vodi se povlaci
        lead = min(abs(d), 2)
        if d > 0: ah *= 1 - P["lead_def"] * lead * k; aa *= 1 + P["trail_att"] * lead * k
        else: aa *= 1 - P["lead_def"] * lead * k; ah *= 1 + P["trail_att"] * lead * k
    for i, (own, opp) in enumerate(((0, 1), (1, 0))):
        n = reds[i]
        if n:
            if i == 0: ah *= P["red_own"] ** n; aa *= P["red_opp"] ** n
            else: aa *= P["red_own"] ** n; ah *= P["red_opp"] ** n
    # Shot quality has priority; dangerous attacks provide a lower-weight pressure
    # signal, while possession is deliberately capped because possession alone is
    # not a reliable proxy for goals. Never fill missing provider fields with fakes.
    xg, so, sh = stats.get("xg"), stats.get("shots_on"), stats.get("shots")
    danger, poss = stats.get("danger"), stats.get("poss")
    if minute >= 10 and any(v is not None for v in (xg, so, sh, danger, poss)):
        evidence = sum(v is not None for v in (xg, so, sh, danger, poss))
        evidence_q = min(1.0, evidence / 3.0)
        blend = min(P["stats_weight"], minute / 150) * evidence_q
        for i, lam in enumerate((lh, la)):
            if xg is not None:
                observed = max(0.0, xg[i])
            else:
                observed = P["shot_on_xg"] * (so[i] if so else 0) + P["shot_off_xg"] * max(0, (sh[i] if sh else 0) - (so[i] if so else 0))
                if danger is not None:
                    observed += P["danger_xg_per_90"] * danger[i] * min(1.0, 90 / max(15, minute))
            # Possession only nudges the shot/pressure estimate, at most +/-10%.
            if poss is not None:
                observed *= max(0.90, min(1.10, 1 + (poss[i] - 50) / 500))
            expected = max(0.15, lam * minute / 90)
            ratio = max(P["tempo"][0], min(P["tempo"][1], (observed + 0.15) / expected))
            mult = 1 + blend * (ratio - 1)
            if i == 0: ah *= mult
            else: aa *= mult
        notes.append({"stats_quality": round(evidence_q, 2), "stats_blend": round(blend, 3), "pressure_mult": [round(ah, 3), round(aa, 3)]})
    return lh * f * ah, la * f * aa, rem, notes


def selections(o, D):
    S = []
    if o.get("x12"):
        f = [1 / x for x in o["x12"]]; s = sum(f)
        S += [(k, nm, "1X2", o["x12"][i], D["x12"][i], f[i] / s) for i, (k, nm) in enumerate((("1", "Pobeda domaćina"), ("X", "Nerešeno"), ("2", "Pobeda gosta")))]
    for L, ov, un in o.get("ou") or []:
        po = D["over"](L); a, b = 1 / ov, 1 / un
        S += [(f"O{L:g}", f"Preko {L:g}", f"Golovi {L:g}", ov, po, a / (a + b)), (f"U{L:g}", f"Ispod {L:g}", f"Golovi {L:g}", un, 1 - po, b / (a + b))]
    if o.get("btts"):
        a, b = 1 / o["btts"][0], 1 / o["btts"][1]
        S += [("GG", "GG (oba daju gol)", "GG/NG", o["btts"][0], D["btts"], a / (a + b)), ("NG", "NG (bar jedan ne daje gol)", "GG/NG", o["btts"][1], 1 - D["btts"], b / (a + b))]
    return S


def pick_tip(S, minute, P=PARAMS, model_weight=None):
    rows = []
    for k, label, mk, o, pm, pf in S:
        if not (P["o_min"] <= o <= P["o_max"]) or pm < 0.05: continue
        w = P["w"] if model_weight is None else max(0.05, min(P["w"], model_weight))
        p = w * pm + (1 - w) * pf
        rows.append({"key": k, "label": label, "market": mk, "odds": round(o, 2), "p_model": round(pm, 3), "p_market": round(pf, 3), "model_weight": round(w, 3), "p": round(p, 3), "ev": round(p * o - 1, 3)})
    rows.sort(key=lambda r: -r["ev"])
    good = [r for r in rows if r["ev"] >= P["min_ev"] and 5 <= minute <= 86]
    return (good[0] if good else None), good[1:3], rows


def history_note(hist, key):
    h = hist or []
    if len(h) < 2: return None
    now, old = h[-1], next((x for x in h if x["t"] >= h[-1]["t"] - 660), h[0])
    a, b = old["o"].get(key), now["o"].get(key)
    if a and b and abs(a - b) / a >= 0.04 and now["t"] - old["t"] >= 120:
        return f"kvota za taj izbor se u poslednjih {round((now['t'] - old['t']) / 60)} min kretala {a:.2f} → {b:.2f}" + (" (tržište sve više veruje u to)" if b < a else " (tržište gubi poverenje)")
    return None


def fb_why(m, h, a, lam, rem_l, st, minute, score, reds, D, tip, ctx, hist):
    why, risk = [], []
    gh, ga = score
    why.append(f"Stanje: {minute}'  {h} {gh}:{ga} {a}. Do kraja se očekuje još {rem_l[0] + rem_l[1]:.2f} gola ({h} {rem_l[0]:.2f}, {a} {rem_l[1]:.2f}), predmeč očekivanje je bilo {lam[0]:.2f} : {lam[1]:.2f}" + (" (opšti prosek, jer za ovaj meč nemamo predmeč model)." if m["prior"] == "generic" else "."))
    sh, so, ps = st.get("shots"), st.get("shots_on"), st.get("poss")
    if sh and so: why.append(f"Udarci (u okvir): {h} {sh[0]:g} ({so[0]:g}), {a} {sh[1]:g} ({so[1]:g})." + (f" Posed {ps[0]:g}%:{ps[1]:g}%." if ps else ""))
    elif ps: why.append(f"Posed {h} {ps[0]:g}%, {a} {ps[1]:g}%.")
    if st.get("danger"): why.append(f"Opasni napadi: {h} {st['danger'][0]:g}, {a} {st['danger'][1]:g}.")
    more = [f"{lab} {st[k][0]:g}:{st[k][1]:g}" for k, lab in (("xg", "xG"), ("corners", "korneri"), ("yellow", "žuti"), ("red", "crveni"), ("fouls", "prekršaji"), ("offsides", "ofsajdi"), ("saves", "odbrane golmana"), ("big", "velike šanse"))
            if isinstance(st.get(k), list) and len(st[k]) == 2 and all(v is not None for v in st[k]) and (k != "red" or any(st[k]))]
    more += [f"{r['l'].lower()} {r['h']:g}:{r['a']:g}" for r in stats_full(st) if r["k"].startswith("x:")]
    if more: why.append("Ostalo sa utakmice (" + h + " : " + a + "): " + ", ".join(more) + ".")
    for i, t in enumerate((h, a)):
        if reds[i]: why.append(f"{t} igra sa {reds[i]} igrač{'a' if reds[i] > 1 else ''} manje: njegov napad pada, protivnik dobija prostora.")
    d = gh - ga
    if minute >= 55 and d != 0:
        lead_i = 0 if d > 0 else 1
        lp = ps[lead_i] if ps else None; ls = (sh[lead_i] / max(1, sh[0] + sh[1])) if sh else None
        if (lp is not None and lp <= 42) or (ls is not None and ls <= 0.35):
            why.append(f"{[h, a][lead_i]} čuva prednost i povukao se ('park the bus'): manje poseda/udaraca nego protivnik. To ide u prilog Under-u i većem pritisku na kraju.")
        if minute >= 60:
            why.append(f"Faktor očaja: {[a, h][lead_i]} gubi u {minute}. minutu pa mora da otvara igru, što znači veći pritisak ali i prostor za kontru ({[h, a][lead_i]}). To je pretpostavka modela, ne dokazan efekat.")
            risk.append("Tim koji juri rezultat povećava varijansu: golovi sa obe strane su verovatniji nego što govori ravnomerna raspodela.")
    elif minute >= 60 and d == 0 and sh and sh[0] + sh[1] <= 8: why.append("Nerešeno uz malo udaraca: obe ekipe igraju oprezno, što podržava Under.")
    for who, nm_ in (("h", h), ("a", a)):                                  # predmeč 'crvena zona' iz fb_factors, ako postoji
        ds = (((ctx or {}).get("factors") or {}).get("desperation") or {}).get(who) or {}
        if ds.get("red"): why.append(f"Predmeč: {nm_} je u crvenoj zoni očaja ({ds.get('score', 0):.2f}), dakle nestabilan ishod.")
    if tip:
        why.insert(0, f"Preporuka {tip['label']} @ {tip['odds']}: model {tip['p_model'] * 100:.0f}%, tržište bez marže {tip['p_market'] * 100:.0f}%, mešano {tip['p'] * 100:.0f}% (težina modela {tip.get('model_weight', PARAMS['w'])*100:.0f}%), EV {tip['ev'] * 100:+.1f}%.")
        hn = history_note(hist, tip["key"])
        if hn: why.append("Kretanje kvote: " + hn + ".")
    status = stats_status(st)
    if status["complete"]:
        why.append(f"Live statistika ({status.get('source') or 'provider'}) dostupna je za udarce u okvir, ukupne udarce i posed; model je koristi uz ograničen uticaj.")
        if status.get("optional_missing"):
            why.append("Opasni napadi nisu objavljeni za ovaj meč ni na Flashscore-u ni na ESPN-u; ta metrika nije procenjena niti izmišljena.")
    else:
        labels = {"shots_on":"udarci u okvir", "shots":"ukupni udarci", "poss":"posed", "danger":"opasni napadi"}
        risk.append("Nedostaje live statistika: " + ", ".join(labels[x] for x in status["missing"]) + ". Model je umesto toga više oslonjen na tržišne kvote.")
    risk += ["Live EV je tržišno ponderisana heuristika, nije empirijski kalibrisana na istorijskim live ishodima; kvote proveri pre uplate."]
    return why, risk


# ---------------------------------------------------------------- kosarka (normalna aproksimacija, prior iz predmeč linija)
def phi(z): return 0.5 * math.erfc(-z / math.sqrt(2))


def bb_state(ev, minute_hint=None):
    per = clk = None
    for _, k, v in walk(ev):
        if re.search(r"period|quarter|cetvrt", k, re.I) and not isinstance(v, (dict, list)) and per is None:
            m = re.search(r"\d+", str(v)); per = int(m[0]) if m else None
        if re.search(r"clock|time_?left|remaining", k, re.I) and not isinstance(v, (dict, list)) and clk is None:
            m = re.match(r"^\D*(\d+):(\d+)", str(v)); clk = int(m[1]) + int(m[2]) / 60 if m else None
    if per is None or clk is None: return None
    return per, clk


def bb_model(m, o, prior, per, clk):
    gh, ga = m["score"]; rem = ((4 - per) * 10 + clk) / 40 if per <= 4 else clk / 40
    rem = max(0.0, min(1.0, rem)); el = 1 - rem
    T0, m0 = prior; pace = 1.0
    if el > 0.15 and (gh + ga) > 0: pace = max(0.85, min(1.15, 1 + min(0.5, el) * ((gh + ga) / (T0 * el) - 1)))
    R, mr = T0 * rem * pace, m0 * rem
    sm, st = max(1.0, 12.5 * math.sqrt(rem)), max(1.0, 15.0 * math.sqrt(rem))   # sigma celog meca: razlika ~12.5, ukupno ~15 (procena)
    S = []
    if o.get("h2h"):
        ph = 1 - phi(-((gh - ga) + mr) / sm); a, b = 1 / o["h2h"][0], 1 / o["h2h"][1]
        S += [("1", "Pobeda domaćina", "Pobednik", o["h2h"][0], ph, a / (a + b)), ("2", "Pobeda gosta", "Pobednik", o["h2h"][1], 1 - ph, b / (a + b))]
    for L, ov, un in o.get("ou") or []:
        po = 1 - phi((L - (gh + ga + R)) / st); a, b = 1 / ov, 1 / un
        S += [(f"O{L:g}", f"Preko {L:g}", f"Ukupno {L:g}", ov, po, a / (a + b)), (f"U{L:g}", f"Ispod {L:g}", f"Ukupno {L:g}", un, 1 - po, b / (a + b))]
    return S, R, pace, rem


# ---------------------------------------------------------------- BELA LISTA LIGA (fudbal): samo ove 4 lige prolaze, sve ostalo (Indija, amateri, zene, U-timovi, kupovi) se odbacuje
LEAGUE_ORDER = ["epl", "laliga", "l1", "bl", "sa", "cl", "el", "ecl"]
LEAGUE_NAME = {"epl": "Premier League", "laliga": "La Liga", "l1": "Ligue 1", "bl": "Bundesliga", "sa": "Serie A", "cl": "Liga šampiona", "el": "Liga Evrope", "ecl": "Liga konferencije"}
LEAGUE_DIR = {"epl": "epl", "laliga": "laliga", "l1": "l1", "bl": "bl", "sa": "sa", "cl": "cl", "el": "el", "ecl": "ecl"}
# naziv turnira/kategorije iz feeda: I naziv lige I zemlja moraju da se slazu (da "Indian Premier League" ili "Brazil Serie A" ne prodju)
LEAGUE_TXT = {"epl": (r"premier lig|premijer lig|premier league", r"engles|england"),
              "laliga": (r"la ?liga(?! ?2)|primera", r"spanij|spain|spanjol"),
              "l1": (r"ligue ?1\b|liga 1\b", r"francusk|france|french"),
              "bl": (r"bundesliga(?! ?2)", r"nemack|german|deutsch|njemack"),
              "sa": (r"serie ?a\b|serija a\b|seria a\b", r"italij|italy|italian"),
              "cl": (r"liga sampiona|champions league", r"."), "el": (r"liga evrope|europa league", r"."), "ecl": (r"liga konferencij|conference league", r".")}
CUP_LEAGUES = ("cl", "el", "ecl")
CUP_TXT_ = {"cl": r"liga sampiona|champions league", "el": r"liga evrope|europa league", "ecl": r"liga konferencij|conference league"}
CUP_NOT_ = r"kvalifik|qualif|\((z|w|r)\)|zene|women|youth|omladin|\bu ?\d\d\b|super ?cup|superkup|play ?off"
NOT_LEAGUE = re.compile(r"\((ž|z|r|w)\)|\bu ?\d\d\b|\bwomen\b|\bwomens\b|\bladies\b|\bzene\b|\bzenska|\bfemin|\bsegunda\b|\bserie b\b|\b2\.? ?bundesliga|bundesliga 2\b|\b3\.? liga\b|\bligue 2\b|\bchampionship\b|"
                        r"\bkup\b|\bcup\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bsupercup|\bsuper cup|\bchampions\b|\bliga sampiona|\beuropa\b|\bconference\b|\bprijateljsk|\bfriendl|\bklub(ov)?ski\b|\bamater|\bindij|\bindia", re.I)
_wl = {"t": 0, "known": None, "ids": None, "alias": None}


def _canon_known(n, known, alias):
    n = alias.get(n, n)
    if n in known: return n
    pre = sorted((k for k in known if len(k) >= 4 and (n.startswith(k + " ") or k.startswith(n + " "))), key=len, reverse=True)
    if pre and (len(pre) == 1 or len(pre[0]) > len(pre[1])): return pre[0]
    m = difflib.get_close_matches(n, list(known), 1, 0.85)
    return m[0] if m else None


def wl_load(cli_comp=None):
    """Klubovi svake dozvoljene lige (data/<liga>/statshub_data.json) + aliasi (fb_leagues.py) + rucno mapiranje turnira (data/superbet_comps_fb.json, --comp)."""
    if _wl["known"] is not None and time.time() - _wl["t"] < 300: return
    alias, known = {}, {}
    try:
        from fb_leagues import LEAGUES
    except Exception: LEAGUES = {}
    try:
        from football_poisson import ALIAS as EPL_ALIAS
        base = dict(EPL_ALIAS)
    except Exception: base = {}
    for code in LEAGUE_ORDER:
        al = dict(base) if code == "epl" else {}
        al.update((LEAGUES.get(code) or {}).get("alias") or {})
        al = {nrm(k): nrm(v) for k, v in al.items()}
        alias[code] = al
        kn = set(jl(os.path.join("data", LEAGUE_DIR[code], "statshub_data.json"), {}).get("teams") or {})
        known[code] = {nrm(k) for k in kn} or (set(al.values()))     # nema Statshub fajla: koristi bar kanonska imena iz aliasa
    ids = {}
    for lg, v in jl(os.path.join("data", "superbet_comps_fb.json"), {}).items():
        if lg in LEAGUE_NAME:
            for i in (v if isinstance(v, list) else [v]): ids[str(i).strip().lstrip("t")] = lg
    for x in cli_comp or []:
        lg, _, v = x.partition("=")
        if lg in LEAGUE_NAME:
            for i in v.split(","): ids[i.strip().lstrip("t")] = lg
    _wl.update(t=time.time(), known=known, ids=ids, alias=alias)


def league_of(ev, h, a):
    """Kod lige ('epl','laliga','l1','sa') ako je meč iz bele liste, inace None."""
    if NOT_LEAGUE.search(low(h)) or NOT_LEAGUE.search(low(a)): return None
    f = ev.get("fixture") or {}
    tid = str(f.get("tournament_id", "")).strip()
    if tid in _wl["ids"]: return _wl["ids"][tid]                       # rucno potvrdjen id turnira ima prednost
    txt = ""
    try: txt = tour_text(ev)
    except Exception: pass
    if txt:                                                              # Liga sampiona / Evrope / Konferencije (faza lige i nokaut, bez kvalifikacija)
        lt = low(txt)
        for c in CUP_LEAGUES:
            if re.search(CUP_TXT_[c], lt) and not re.search(CUP_NOT_, lt): return c
    if txt and NOT_LEAGUE.search(txt): return None                      # kup, evropska takmicenja, zene, druga liga...
    for code in LEAGUE_ORDER:                                            # naziv lige + zemlja iz feeda
        if code in CUP_LEAGUES: continue
        lg_rx, co_rx = LEAGUE_TXT[code]
        if txt and re.search(lg_rx, txt) and re.search(co_rx, txt): return code
    hn, an = nrm(h), nrm(a)                                              # klubovi: oba moraju biti iz iste lige
    for code in LEAGUE_ORDER:
        kn, al = _wl["known"][code], _wl["alias"][code]
        if kn and _canon_known(hn, kn, al) and _canon_known(an, kn, al): return code
    return None


# ---------------------------------------------------------------- glavni deo
_priors = {"t": 0, "d": {}}


def priors():
    if time.time() - _priors["t"] < 60: return _priors["d"]
    d = {}
    for p in glob.glob(os.path.join("data", "*", "predictions.json")):
        for c in jl(p, {}).get("context") or []:
            if c.get("lam") and c.get("h") and c.get("a"): d[(nrm(c["h"]), nrm(c["a"]))] = c
    _priors.update(t=time.time(), d=d)
    return d


def bb_priors():
    d = {}
    for e in jl(os.path.join("data", "odds_superbet.json"), {}).get("events", []):
        b = (e.get("b") or [{}])[0]
        if b.get("tt") and b.get("sp"): d[(nrm(e["h"]), nrm(e["a"]))] = (b["tt"][0], -b["sp"][0])
    return d


def est_minute(start):
    try: el = (time.time() - datetime.datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()) / 60
    except Exception: return None
    return int(max(1, min(90, el - 15 if el > 47 else el))) if el > 0 else None


ALLOW_GENERIC = False   # bez predmec modela za tim se NE daje tip (osim sa --allow-generic)
DETAIL_AFTER = None     # round-robin kursors za bounded event-detail pozive po ciklusu


def build(ev, sp, hist_all, pri, bpri, diag):
    f = ev.get("fixture") or {}
    parts = re.split(r"\s*[·•]\s*", f.get("event_name", ""), maxsplit=1)
    if len(parts) != 2: return None
    h, a = parts[0].strip(), parts[1].strip(); eid = str(event_id(ev) or f"{h}|{a}")
    score = find_score(ev); o = odds_of(ev)
    if score is None: diag["bez_rezultata"] += 1
    m = {"id": eid, "sport": "fudbal" if sp == "5" else "kosarka", "h": h, "a": a, "start": f.get("utc_date"), "comp": f"t{f.get('tournament_id', '')}",
        "score": score, "minute": None, "odds": o, "stats": {}, "stats_status": stats_status({}), "tip": None, "alts": [], "why": [], "risk": [], "prior": None}
    if score is None: m["why"] = ["Feed za ovaj meč nema prepoznat rezultat uživo (pokreni --dump)."]; return m
    hist = hist_all.setdefault(eid, [])
    snap = {"t": int(time.time()), "o": {}}
    if o["x12"]: snap["o"].update({"1": o["x12"][0], "X": o["x12"][1], "2": o["x12"][2]})
    for L, ov, un in o["ou"]: snap["o"].update({f"O{L:g}": ov, f"U{L:g}": un})
    if o["btts"]: snap["o"].update({"GG": o["btts"][0], "NG": o["btts"][1]})
    hist.append(snap); del hist[:-80]
    if sp == "5":
        st = find_stats(ev); m["stats"] = st; m["stats_status"] = stats_status(st); m["stats_full"] = stats_full(st)
        minute = find_minute(ev); est = minute is None
        minute = minute if minute is not None else est_minute(m["start"] or "")
        if minute is None: m["why"] = ["Nema minuta meča u feedu."]; return m
        m["minute"] = minute
        key = (nrm(h), nrm(a)); ctx = pri.get(key)
        if not ctx:
            near = difflib.get_close_matches(" | ".join(key), [" | ".join(k) for k in pri], 1, 0.85); ctx = pri.get(tuple(near[0].split(" | "))) if near else None
        lam = tuple(ctx["lam"]) if ctx else (1.45, 1.15); m["prior"] = "model" if ctx else "generic"
        if not ctx and not ALLOW_GENERIC:
            diag["bez_modela"] += 1
            m["why"] = ["Nema predmeč modela za ovaj meč (liga ili tim nisu u tvojim modelima), pa se ne daje tip: bez znanja o timovima 'prosečan' model samo izmišlja razliku u odnosu na kvote."]
            return m
        reds = [int(st.get("red", [0, 0])[0]), int(st.get("red", [0, 0])[1])]
        rh, ra, rem, live_diag = fb_rates(lam[0], lam[1], minute, score[0], score[1], reds, st)
        D = final_dist(score[0], score[1], rh, ra)
        S = selections(o, D)
        quality = len(m["stats_status"]["present"]) / len(LIVE_STAT_FIELDS)
        model_weight = PARAMS["w"] * (0.45 + 0.55 * quality)
        tip, alts, _all = pick_tip(S, minute, model_weight=model_weight)
        m["model"] = {"lam_pre": [round(lam[0], 2), round(lam[1], 2)], "lam_rem": [round(rh, 2), round(ra, 2)], "x12": [round(x, 3) for x in D["x12"]], "btts": round(D["btts"], 3),
                       "stats_quality": round(quality, 2), "model_weight": round(model_weight, 3), "calibration": "market_shrunk_heuristic", "live_adjustment": live_diag}
        m["tip"], m["alts"] = tip, alts
        m["why"], m["risk"] = fb_why(m, h, a, lam, (rh, ra), st, minute, score, reds, D, tip, ctx, hist)
        if est: m["risk"].insert(0, "Minut je procenjen iz početka meča (feed ga ne daje), pa je preciznost manja.")
    else:
        bs = bb_state(ev); pr = bpri.get((nrm(h), nrm(a)))
        if not bs or not pr: m["why"] = ["Za košarku treba period/sat iz feeda i predmeč linije (data/odds_superbet.json); jedno od toga fali."]; return m
        S, R, pace, rem = bb_model(m, o, pr, *bs); tip, alts, _ = pick_tip(S, 5)
        m["minute"] = f"{bs[0]}. period {int(bs[1])}:{int(bs[1] % 1 * 60):02d}"; m["prior"] = "market"; m["tip"], m["alts"] = tip, alts
        m["why"] = [f"Stanje: {score[0]}:{score[1]}, {m['minute']}. Predmeč linija: ukupno {pr[0]:g}, razlika {pr[1]:+g}. Do kraja se očekuje još {R:.0f} poena (tempo x{pace:.2f})."]
        if tip: m["why"].insert(0, f"Preporuka {tip['label']} @ {tip['odds']}: model {tip['p_model'] * 100:.0f}%, tržište {tip['p_market'] * 100:.0f}%, EV {tip['ev'] * 100:+.1f}%.")
        m["risk"] = ["Košarkaški live model je normalna aproksimacija, nije empirijski kalibrisan.", "Kvote uživo se menjaju za sekunde: proveri kvotu pre uplate."]
    return m


def ename(ev): return (ev.get("fixture") or {}).get("event_name", "")


def enrich(ev):
    """Detalj meca (events?events=<id>) ima 'results' i 'inPlayStats' koje lista nema. Vraca bogatiji dogadjaj (ili isti ako detalj nije dostupan)."""
    eid = event_id(ev)
    if eid in (None, ""): return ev
    time.sleep(random.uniform(1.5, 3))
    try: d = events_in(get(DETAIL.format(id=eid)))
    except urllib.error.HTTPError as ex:
        if ex.code in (403, 429): raise
        return ev
    except Exception: return ev
    if not d: return ev
    d = d[0]
    new = dict(ev)
    for k, v in d.items():
        if k == "markets":
            if len(v or []) > len(ev.get("markets") or []): new[k] = v
        elif k != "fixture": new[k] = v
    return new


def once(a):
    global ALLOW_GENERIC, DETAIL_AFTER
    ALLOW_GENERIC = a.allow_generic
    now = datetime.datetime.now(datetime.timezone.utc)
    s, e = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z"), (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    hist, pri, bpri, diag, out, dropped = jl(HIST, {}), priors(), bb_priors(), collections.Counter(), [], []
    wl_load(a.comp)
    espn_cache = {}
    for sp in a.sports.split(","):
        try: evs = events_in(get(FEED.format(s=s, e=e, sp=sp, idx=a.index)))
        except urllib.error.HTTPError as ex: sys.exit(feed_err(ex, a.index))
        if a.match: evs = [x for x in evs if a.match.lower() in ename(x).lower()]
        diag["u_feedu"] += len(evs)
        detail_ids = []
        if sp == "5" and a.detail > 0:
            for candidate in evs:
                parts = re.split(r"\s*[·•]\s*", ename(candidate), maxsplit=1)
                allowed = league_of(candidate, *parts) if len(parts) == 2 and not a.all_leagues else ("sve" if len(parts) == 2 else None)
                if allowed is None or not stats_status(find_stats(candidate))["missing"]: continue
                detail_ids.append(str(event_id(candidate) or ename(candidate)))
        selected_detail = set()
        if detail_ids:
            start_at = (detail_ids.index(DETAIL_AFTER) + 1) % len(detail_ids) if DETAIL_AFTER in detail_ids else 0
            count = min(a.detail, len(detail_ids))
            selected_detail = {detail_ids[(start_at + i) % len(detail_ids)] for i in range(count)}
            DETAIL_AFTER = detail_ids[(start_at + count - 1) % len(detail_ids)]
        flash_candidates = []
        if sp == "5" and a.flash_details > 0:
            for candidate in evs:
                parts = re.split(r"\s*[·•]\s*", ename(candidate), maxsplit=1)
                if len(parts) != 2 or find_minute(candidate) is None: continue
                allowed = league_of(candidate, *parts) if not a.all_leagues else "sve"
                # Query Flashscore for every tracked live football match, not only
                # when ESPN is empty: ESPN does not publish dangerous attacks and
                # would otherwise prevent the richer Flashscore stats from loading.
                if allowed in FLASH_PAGES:
                    flash_candidates.append((candidate, allowed))
        flash_stats = flashscore_refresh(flash_candidates, a.flash_details)
        for ev in evs:
            lg = "kosarka"
            if sp == "5":
                parts = re.split(r"\s*[·•]\s*", ename(ev), maxsplit=1)
                lg = league_of(ev, *parts) if len(parts) == 2 and not a.all_leagues else ("sve" if len(parts) == 2 else None)
                if lg is None:
                    diag["odbaceno_van_liste"] += 1
                    if len(dropped) < 400: dropped.append((ename(ev), (ev.get("fixture") or {}).get("tournament_id", ""), tour_text(ev)[:60]))
                    continue
                # The list feed has score/clock for many fixtures but no in-play
                # statistics. Fill a small bounded number of missing-stat games
                # from the provider's event-detail endpoint on every loop.
                if str(event_id(ev) or ename(ev)) in selected_detail:
                    try:
                        enriched = enrich(ev)
                        if len(find_stats(enriched)) >= len(find_stats(ev)): ev = enriched
                    except urllib.error.HTTPError as ex: sys.exit(feed_err(ex, a.index))
                flash = flash_stats.get(str(event_id(ev) or ename(ev)))
                if flash:
                    ev = merge_supplemental_stats(ev, flash)
                # ESPN fills any remaining core fields only after Flashscore has
                # supplied its available values, including dangerous attacks.
                ev = supplement_espn_stats(ev, lg, espn_cache)
            r = build(ev, sp, hist, pri, bpri, diag)
            if r: r["league"] = lg; out.append(r)
    keep = {m["id"] for m in out}
    for k in [k for k in hist if k not in keep]: del hist[k]
    os.makedirs("data", exist_ok=True)
    for p, o in ((LIVE, {"updated": int(time.time()), "source": "superbet.rs", "index": a.index, "matches": out}), (HIST, hist)):
        json.dump(o, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":")); os.replace(p + ".tmp", p)
    print(f"{datetime.datetime.now():%H:%M:%S} uzivo: {len(out)} meceva, sa tipom {sum(1 for m in out if m['tip'])}" + (f" | {dict(diag)}" if diag else ""))
    print_grouped(out, dropped, a)


def sort_key(m):
    mi = m["minute"] if isinstance(m["minute"], int) else -1
    return (-mi, str(m.get("start") or ""), m["h"])


def print_grouped(out, dropped, a):
    """Mečevi grupisani po ligama (PL, La Liga, Ligue 1, Serie A, pa ostalo/košarka); unutar lige po minutu, najkasniji prvi."""
    groups = collections.OrderedDict((c, []) for c in LEAGUE_ORDER)
    other = []
    for m in out:
        (groups[m["league"]] if m.get("league") in groups else other).append(m)
    for code, ms in list(groups.items()) + [("sve", [m for m in other if m.get("league") == "sve"]), ("kosarka", [m for m in other if m.get("league") == "kosarka"])]:
        if not ms: continue
        title = LEAGUE_NAME.get(code, "Fudbal (sve lige)" if code == "sve" else "Košarka")
        print(f"\n=== {title} ({len(ms)}) ===")
        for m in sorted(ms, key=sort_key):
            mn = f"{m['minute']}'" if isinstance(m["minute"], int) else (m["minute"] or "?")
            tip = f"{m['tip']['label']} @ {m['tip']['odds']} EV {m['tip']['ev']*100:+.1f}%" if m["tip"] else "bez tipa"
            sc = f"{m['score'][0]}:{m['score'][1]}" if m["score"] else "-:-"
            print(f"  {m['h']} - {m['a']} | {mn} | {sc} | {tip}")
    if dropped:
        print(f"\nodbaceno van bele liste: {len(dropped)}" + ("" if a.show_dropped else "  (--show-dropped prikazuje nazive i tournament_id)"))
        if a.show_dropped:
            for n_, t_, tx in dropped[:60]: print(f"    {n_} | tournament_id={t_} | {tx}")
            print("    ako je neki od ovih ipak PL/La Liga/Ligue 1/Serie A: dodaj id u data/superbet_comps_fb.json, npr. {\"epl\": [123]}  ili  --comp epl=123")


def http_body(ex):
    try: return ex.read().decode("utf-8", "replace").strip().replace("\n", " ")[:200]
    except Exception: return ""


def feed_err(ex, idx):
    """403/429 = feed nas blokira (STANI). 400/404 = taj 'index' nije validan, to NIJE blokada."""
    if ex.code in (403, 429): return f"HTTP {ex.code}: feed odbija zahtev. Ne zaobilazim; pokusaj kasnije."
    return (f"HTTP {ex.code} za index='{idx}': to NIJE blokada, nego ime indexa koje feed ne poznaje (--index nije ime tima ni lige). "
            f"Pokreni 'py fb_live.py --probe' da nadjemo ispravan index. Odgovor feeda: {http_body(ex)}")


def probe(a):
    now = datetime.datetime.now(datetime.timezone.utc)
    s, e = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z"), (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    sp = a.sports.split(",")[0]
    found = []
    for idx in ("active-inplay", "active-live", "inplay", "live", "in-play", "active", "all", "active-all", "active-prematch", "(bez index parametra)"):
        url = re.sub(r"&index=[^&]*", "", FEED.format(s=s, e=e, sp=sp, idx="x")) if idx.startswith("(") else FEED.format(s=s, e=e, sp=sp, idx=idx)
        try: evs = events_in(get(url))
        except urllib.error.HTTPError as ex:
            if ex.code in (403, 429): print(f"index={idx}: " + feed_err(ex, idx) + " STAJEM."); return
            print(f"index={idx:<22} HTTP {ex.code} (nevazece ime, nije blokada) {http_body(ex)[:150]}"); time.sleep(random.uniform(2, 3)); continue
        except Exception as ex: print(f"index={idx}: {ex}"); continue
        try: live = sum(1 for x in evs if find_score(x))
        except Exception: live = "?"
        print(f"index={idx:<22} dogadjaja {len(evs):<5} sa rezultatom (uzivo) {live:<4} primeri {[(x.get('fixture') or {}).get('event_name') for x in evs[:3]]}")
        if evs: found.append((idx, len(evs), live))
        time.sleep(random.uniform(2, 3))
    names = [x[0] for x in found]
    if "active-live" in names or "live" in names:
        n_ = dict((x[0], x[1]) for x in found).get("active-live") or dict((x[0], x[1]) for x in found).get("live")
        print(f"\nINDEX ZA UZIVO: 'active-live' (isto i 'live'): {n_} mečeva u toku. Rezultat i minut NISU u listi nego u detalju meča: "
              f"pokreni  py fb_live.py --index active-live --dump  (dodaj --match Arouca da izabereš meč).")
        return
    liv = [x for x in found if isinstance(x[2], int) and x[2] > 0]
    if liv:
        best = max(liv, key=lambda x: (x[2], x[1]))
        print(f"\nNajbolji kandidat za uzivo: --index {best[0]}  (pokreni: py fb_live.py --index {best[0]} --dump)")
    elif found:
        print("\nNijedan index ne vraca mecove sa rezultatom (uzivo). Ako trenutno nema utakmica u toku, pokreni --probe dok traje neki meč (npr. vikendom popodne). Posalji mi ceo ispis.")
    else:
        print("\nNijedan index nije dao dogadjaje. Posalji mi ceo ovaj ispis.")


def dump(a):
    now = datetime.datetime.now(datetime.timezone.utc)
    s, e = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z"), (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")
    try: evs = events_in(get(FEED.format(s=s, e=e, sp=a.sports.split(",")[0], idx=a.index)))
    except urllib.error.HTTPError as ex: sys.exit(feed_err(ex, a.index))
    if not evs: sys.exit("Nema dogadjaja u tom indexu.")
    print(f"index={a.index}: {len(evs)} dogadjaja. Prvih 10:", [ename(x) for x in evs[:10]])
    pick = [x for x in evs if a.match.lower() in ename(x).lower()] if a.match else evs
    if not pick: sys.exit(f"Nijedan dogadjaj ne sadrzi '{a.match}'.")
    ev = pick[0]
    print("\nIZABRAN:", ename(ev), "| kljucevi u listi:", list(ev))
    full = enrich(ev)
    print("Kljucevi posle detalja:", list(full))
    raw = {k: v for k, v in full.items() if k != "markets"}
    print("\nBez trzista:", json.dumps(raw, ensure_ascii=False)[:3500])
    for k in ("results", "inPlayStats", "inPlayStatsMetadata"):
        if k in full: print(f"\n[{k}]", json.dumps(full[k], ensure_ascii=False)[:1800])
    names = [m.get("name") for m in (full.get("markets") or [])]
    print(f"\nTrzista ({len(names)}):", names[:25])
    for mk in (full.get("markets") or []):
        if low(mk.get("name", "")).strip() in ("konacan ishod", "ukupno golova", "oba tima daju gol"):
            print(f"\n[{mk.get('name')}]", [(str((o.get('metadata') or {}).get('code')), (o.get('metadata') or {}).get('name'), (o.get('metadata') or {}).get('specifiers'), o.get('price'), o.get('status')) for o in (mk.get("odds") or [])[:14]])
    print("\nPrepoznato: rezultat", find_score(full), "| minut", find_minute(full), "| statistika", find_stats(full), "| kvote", odds_of(full))
    os.makedirs("data", exist_ok=True)
    json.dump(full, open(os.path.join("data", "live_dump.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print("Snimljeno u data/live_dump.json (posalji mi ovaj ispis).")


def flash_probe(a):
    """py fb_live.py --flash-probe Atalanta : otvori Flashscore, nadji meč, ispisi sta je procitano; snimi data/flash_debug.png/.txt za dijagnozu."""
    want = nrm(a.flash_probe)
    page = _flash_page()
    FLASH_LINK_CACHE.clear()
    links = _flash_match_links(page, "sa")
    print(f"Linkova mečeva sa Flashscore: {len(links)}")
    hit = [x for x in links if want in x["home"] or want in x["away"] or want in x["context"]]
    print("Pogodaka za", repr(a.flash_probe), ":", [(x["home"], x["away"], x["href"]) for x in hit[:5]])
    if not hit:
        print("Primeri linkova:", [(x["home"], x["away"]) for x in links[:12]]); return
    base = hit[0]["href"].split("#", 1)[0]
    page.goto(base + "#/match-summary/match-statistics/0", wait_until="domcontentloaded", timeout=25000)
    _flash_cookies(page); page.wait_for_timeout(4000)
    os.makedirs("data", exist_ok=True)
    page.screenshot(path=os.path.join("data", "flash_debug.png"))
    txt = page.locator("body").inner_text()
    open(os.path.join("data", "flash_debug.txt"), "w", encoding="utf-8").write(txt)
    print("testid redovi:", _flash_testid_rows(page))
    print("DOM parser   :", _flash_parse_stat_rows(page))
    print("Snimljeno data/flash_debug.png i data/flash_debug.txt (posalji mi ih ako stats ostane prazan).")


def serve(port):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try: b = open(LIVE, "rb").read()
            except OSError: b = b'{"matches":[]}'
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
        def log_message(self, *x): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"live.json se servira na http://localhost:{port}/live.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="active-live", help="ime 'index' parametra feeda (NIJE ime tima ni lige!). Ispravnu vrednost nadji sa --probe")
    ap.add_argument("--sports", default="5,4", help="5 = fudbal, 4 = kosarka")
    ap.add_argument("--loop", type=int, default=0, help="sekundi izmedju prolaza (min 20)"); ap.add_argument("--serve", action="store_true"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--push-every", type=int, default=0, help="na toliko sekundi gurni data/live.json u repo (0 = nikad)")
    ap.add_argument("--flash-probe", default="", help="dijagnostika Flashscore-a za jedan meč, npr. --flash-probe Atalanta")
    ap.add_argument("--probe", action="store_true"); ap.add_argument("--dump", action="store_true")
    ap.add_argument("--allow-generic", action="store_true", help="daj tipove i za mecove bez predmec modela (NE preporucujem: tipovi su nepouzdani)")
    ap.add_argument("--all-leagues", action="store_true", help="iskljuci belu listu liga (prikazi sve fudbalske mecove)")
    ap.add_argument("--show-dropped", action="store_true", help="ispisi koje je mecove bela lista odbacila (naziv + tournament_id)")
    ap.add_argument("--comp", action="append", default=[], help="rucno mapiranje turnira: --comp epl=123 (ponovi za vise; liga: epl, laliga, l1, sa)")
    ap.add_argument("--match", default="", help="samo mecevi ciji naziv sadrzi ovaj tekst (npr. --match Arouca)")
    ap.add_argument("--detail", type=int, default=2, help="maksimalno detaljnih poziva po prolazu za fudbalske mečeve kojima nedostaje bar jedno od 4 live polja; 0 isključuje dopunu")
    ap.add_argument("--flash-details", type=int, default=3, help="maksimalno javnih Flashscore stranica utakmica koje se otvaraju po prolazu; 0 isključuje Flashscore")
    a = ap.parse_args()
    if a.flash_probe: flash_probe(a); sys.exit()
    if a.probe: probe(a); sys.exit()
    if a.dump: dump(a); sys.exit()
    if a.serve: serve(a.port)
    last = 0
    while True:
        try:
            once(a)
            if a.push_every and time.time() - last >= a.push_every: push([LIVE]); last = time.time()
        except SystemExit as e: print(e); break
        except Exception as e: print("greska:", e)
        if not a.loop: break
        time.sleep(max(20, a.loop) * random.uniform(.9, 1.15))
