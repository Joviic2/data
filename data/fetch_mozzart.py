#!/usr/bin/env python3
"""Lokalni scraper kvota sa Mozzart-a (kosarka) preko Playwright browsera -> data/odds_mozzart.json

Kako radi: otvori Mozzart stranicu kao obican browser, pusti sajt da sam salje svoje zahteve
(/betting/matches), pokupi te odgovore dok skroluje (sajt sam ucitava sledece strane) i iz njih
izvuce pobednika, hendikep i ukupno. Ne koristi tvoje kolacice iz curl-a i ne zaobilazi zastitu:
ako sajt pokaze proveru ("Just a moment"), skripta stane (u --headed modu ti resis proveru rucno).

Pokretanje (iz korena repoa):
  pip install playwright && python -m playwright install chromium
  python fetch_mozzart.py --dump              # prvi put: snimi sirov odgovor i ispisi sta je prepoznato
  python fetch_mozzart.py                     # jedan prolaz, upis u data/odds_mozzart.json
  python fetch_mozzart.py --loop 600 --push   # u pozadini: svakih ~10 min, pa git commit+push samo tog fajla
NAPOMENA: parser radi na pretpostavci o strukturi odgovora i NIJE potvrden na pravom odgovoru.
Pre nego sto ukljucis --push, uporedi ispisane kvote sa onim sto vidis na sajtu.
Proveri uslove koriscenja Mozzart-a: automatski pristup moze da bude zabranjen. Drzi razmak >= 5 min."""
import argparse, collections, datetime, json, os, random, re, subprocess, sys, time

PAGE = "https://www.mozzartbet.com/en/kladjenje/sport/2?date={date}"
OUT = os.path.join("data", "odds_mozzart.json")
PROFILE = os.path.join(os.path.expanduser("~"), ".mozzart_profile")
DUMP_DIR = "mozzart_dump"
NAME_H = ("home", "homeTeam", "home_team", "team1", "participant1")
NAME_A = ("visitor", "away", "awayTeam", "away_team", "guest", "team2", "participant2")
TIME_K = ("startTime", "start", "time", "kickOffTime", "kickoff", "startDate", "date")
PRICE_K = ("value", "odd", "odds", "price", "coefficient", "koef")
SPEC_K = ("specialOddValue", "specialValue", "special", "hdp", "handicap", "line", "total", "param")
LIST_K = ("subgames", "subGames", "odds", "outcomes", "selections")
SKIP = ("tim", "team", "poluvreme", "half", "quarter", "cetvrt", "četvrt", "1h", "2h", "1st", "2nd", "pol ")


def nstr(v):
    if isinstance(v, dict):
        for k in ("name", "title", "label", "text"):
            if isinstance(v.get(k), str):
                return v[k]
        return ""
    return v if isinstance(v, str) else ("" if v is None else str(v))


def first(d, keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def num(x):
    try:
        return float(str(x).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def to_iso(t):
    n = num(t)
    if n and n > 1e9:
        n = n / 1000 if n > 1e12 else n
        return datetime.datetime.fromtimestamp(n, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    return t if isinstance(t, str) else None


def flatten(item):
    """Daje (naziv_trzista, ishod, specijalna_vrednost, kvota) za ravnu i ugnjezdenu strukturu."""
    for lk in ("odds", "markets", "games", "oddsList"):
        for e in item.get(lk) or []:
            if not isinstance(e, dict):
                continue
            nested = next((e[k] for k in LIST_K if isinstance(e.get(k), list)), None)
            if nested is not None:
                mk = nstr(first(e, ("name", "gameName", "game", "marketName")))
                for o in nested:
                    if isinstance(o, dict):
                        yield mk, nstr(first(o, ("subGameName", "name", "subgame", "outcome"))), first(o, SPEC_K), first(o, PRICE_K)
            else:
                mk = nstr(first(e, ("gameName", "game", "marketName", "name")))
                yield mk, nstr(first(e, ("subGameName", "subgame", "outcome", "selection"))), first(e, SPEC_K), first(e, PRICE_K)


def classify(mk):
    m = mk.lower()
    if any(s in m for s in SKIP):
        return None
    if any(s in m for s in ("hendikep", "handicap")):
        return "sp"
    if any(s in m for s in ("ukupno", "total", "over", "under", "više", "vise", "manje")):
        return "tt"
    if any(s in m for s in ("konačan ishod", "konacan ishod", "pobednik", "winner", "moneyline", "ishod")):
        return "h2h"
    return None


def side(name, kind):
    n = name.lower().strip()
    if kind == "tt":
        if any(s in n for s in ("over", "više", "vise", "gore", "ugore")):
            return "o"
        if any(s in n for s in ("under", "manje", "dole", "udole")):
            return "u"
        return None
    if n.startswith("1") or n in ("home", "domaćin", "domacin"):
        return "h"
    if n.startswith("2") or n in ("away", "gost", "visitor"):
        return "a"
    return None


def parse_item(item, diag):
    hn = nstr(first(item, NAME_H)) if first(item, NAME_H) is not None else ""
    an = nstr(first(item, NAME_A)) if first(item, NAME_A) is not None else ""
    if not hn or not an:
        t = nstr(first(item, ("name", "title", "matchName")))
        p = re.split(r"\s+(?:-|vs\.?|v)\s+", t, maxsplit=1)
        if len(p) == 2:
            hn, an = p
    if not hn or not an:
        diag["bez_imena"] += 1
        return None
    h2h, sp, tt = {}, collections.defaultdict(dict), collections.defaultdict(dict)
    for mk, out, spec, price in flatten(item):
        kind = classify(mk)
        p = num(price)
        if kind is None:
            diag["nepoznato:" + mk[:40]] += 1
            continue
        if p is None or not 1.01 < p <= 50:
            continue
        s = side(out, kind)
        if s is None:
            continue
        line = num(spec)
        if line is None:
            m = re.search(r"[-+]?\d+(?:[.,]\d+)?", mk + " " + out)
            line = num(m.group(0)) if m else None
        if kind == "h2h":
            h2h[s] = p
        elif kind == "sp" and line is not None and abs(line) <= 40 and (line * 2).is_integer():
            sp[line if s == "h" else -line][s] = p
        elif kind == "tt" and line is not None and 100 <= line <= 260 and (line * 2).is_integer():
            tt[line][s] = p
    pairs = lambda d, a, b: sorted(((l, v[a], v[b]) for l, v in d.items() if a in v and b in v), key=lambda x: abs(1 / x[1] - 1 / x[2]))
    SP, TT = pairs(sp, "h", "a"), pairs(tt, "o", "u")
    H2H = [h2h["h"], h2h["a"]] if "h" in h2h and "a" in h2h else None
    if not (H2H or SP or TT):
        diag["bez_kvota"] += 1
        return None
    b = [{"h2h": H2H, "sp": list(SP[0]) if SP else None, "tt": list(TT[0]) if TT else None}]
    b += [{"h2h": None, "sp": list(x), "tt": None} for x in SP[1:6]] + [{"h2h": None, "sp": None, "tt": list(x)} for x in TT[1:6]]
    return {"h": hn.strip(), "a": an.strip(), "start": to_iso(first(item, TIME_K)), "comp": nstr(first(item, ("competition", "league", "competitionName"))), "b": b}


def find_items(j):
    if isinstance(j, list):
        return [x for x in j if isinstance(x, dict)]
    if isinstance(j, dict):
        for k in ("items", "matches", "data", "content", "events", "results"):
            if isinstance(j.get(k), list):
                return [x for x in j[k] if isinstance(x, dict)]
            if isinstance(j.get(k), dict):
                r = find_items(j[k])
                if r:
                    return r
    return []


def parse(responses):
    diag, seen, out = collections.Counter(), set(), []
    for j in responses:
        for it in find_items(j):
            diag["stavki"] += 1
            e = parse_item(it, diag)
            if e and (e["h"], e["a"], e["start"]) not in seen:
                seen.add((e["h"], e["a"], e["start"]))
                out.append(e)
    return out, diag


def capture(a):
    from playwright.sync_api import sync_playwright
    got, urls = [], []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=not a.headed, locale="en-US", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "/betting/matches" in r.url and r.request.method == "POST" and r.status == 200:
                    got.append(r.json()); urls.append(r.url)
            except Exception:
                pass
        page.on("response", on_resp)
        page.goto(PAGE.format(date=a.date), wait_until="domcontentloaded", timeout=60000)
        t0 = time.time()
        while time.time() - t0 < (150 if a.headed else 25):
            if got:
                break
            title = (page.title() or "").lower()
            if "just a moment" in title or "attention required" in title:
                if not a.headed:
                    ctx.close()
                    sys.exit("Sajt trazi proveru pregledaca (Cloudflare). Pokreni sa --headed i reši proveru u prozoru; ne pokusavam da je zaobidjem.")
            page.wait_for_timeout(1000)
        for _ in range(a.scrolls):
            n = len(got)
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(1800 + random.randint(0, 800))
            if len(got) == n:
                break
        ctx.close()
    return got


def write(events):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched": int(time.time()), "source": "mozzartbet.com", "events": events}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)


def push():
    run = lambda *c: subprocess.run(c, capture_output=True, text=True)
    run("git", "add", OUT)
    if run("git", "diff", "--cached", "--quiet", "--", OUT).returncode == 0:
        return
    run("git", "commit", "-m", "Mozzart odds", "--", OUT)
    for i in range(3):
        run("git", "pull", "--rebase", "--autostash", "origin", "main")
        if run("git", "push", "origin", "HEAD:main").returncode == 0:
            return
        time.sleep(5 * (i + 1))
    print("push nije uspeo")


def once(a):
    got = capture(a)
    if a.dump:
        os.makedirs(DUMP_DIR, exist_ok=True)
        with open(os.path.join(DUMP_DIR, "raw.json"), "w", encoding="utf-8") as f:
            json.dump(got, f, ensure_ascii=False, indent=1)
        print("Sirov odgovor snimljen u", DUMP_DIR + "/raw.json", "(posalji mi prvih 60 linija)")
    ev, diag = parse(got)
    print(f"{datetime.datetime.now():%H:%M:%S} odgovora {len(got)}, stavki {diag['stavki']}, prepoznato mečeva {len(ev)}")
    for e in ev[:40]:
        b = e["b"][0]
        print(f"  {e['h']} - {e['a']} | 1/2 {b['h2h']} | hend {b['sp']} | ukupno {b['tt']}")
    bad = [(k, v) for k, v in diag.most_common(12) if k.startswith("nepoznato") or k in ("bez_imena", "bez_kvota")]
    if bad:
        print("Neprepoznato:", bad)
    if not ev:
        print("Nista nije prepoznato: ne pisem fajl. Pokreni sa --dump i posalji mi raw.json.")
        return
    write(ev)
    if a.push:
        push()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=os.environ.get("MOZZART_DATE", "all_days"), help="vrednost date= iz adrese sajta (proveri u browseru)")
    ap.add_argument("--scrolls", type=int, default=12)
    ap.add_argument("--headed", action="store_true", help="vidljiv prozor (potrebno za rucnu proveru)")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--loop", type=int, default=0, help="sekundi izmedju prolaza (min 300)")
    ap.add_argument("--push", action="store_true", help="git commit+push samo data/odds_mozzart.json")
    a = ap.parse_args()
    if not a.loop:
        once(a)
    else:
        while True:
            try:
                once(a)
            except SystemExit as e:
                print(e); break
            except Exception as e:
                print("greska:", e)
            time.sleep(max(300, a.loop) * random.uniform(.9, 1.2))
