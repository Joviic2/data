#!/usr/bin/env python3
"""Dodaje UEFA Ligu sampiona (cl), Ligu Evrope (el) i Ligu konferencije (conf) u fudbalske skripte. Pokreni jednom iz korena repoa:
    py add_uefa.py            # izmeni fb_leagues.py, fb_table.py i osvezi.bat (pre izmene pravi .bak kopije; ponovno pokretanje nista ne menja)
    py add_uefa.py --dry      # samo pokaze sta bi izmenio
Sta dodaje:
  fb_leagues.py : lige cl / el / conf (Statshub uniqueTournament 7 / 679 / 17015, folder data/cl|el|conf); aliasi klubova su zbir aliasa svih drugih liga
  fb_table.py   : zone tabele ligaske faze (1-8 osmina finala, 9-24 baraz, 25-36 eliminisani) i ogranicenje na utakmice ligaske faze
                  (144 za LS i LE, 108 za LK), da eliminacione utakmice ne pokvare tabelu
  osvezi.bat    : call :liga cl / el / conf posle poslednje lige
Napomena: 'path' ka Statshub stranici je pretpostavka; ako statshub_football.py ne nadje takmicenje, zadaj --tournament X --season Y (nadji ih sa statshub_probe.py --league <path>)."""
import os, re, shutil, sys

DRY = "--dry" in sys.argv
UEFA = '''
# --- UEFA takmicenja (dodato iz add_uefa.py) ---
UEFA = {
    "cl": {"name": "UEFA Liga šampiona", "dir": "cl", "path": "/leagues/europe/uefa-champions-league/7", "ut": 7, "tourn": None, "season": None, "csv": None,
           "struct": "liga sampiona|liga šampiona|champions league|uefa lš", "alias": {}},
    "el": {"name": "UEFA Liga Evrope", "dir": "el", "path": "/leagues/europe/uefa-europa-league/679", "ut": 679, "tourn": None, "season": None, "csv": None,
           "struct": "liga evrope|europa league|uefa le", "alias": {}},
    "conf": {"name": "UEFA Liga konferencije", "dir": "conf", "path": "/leagues/europe/uefa-conference-league/17015", "ut": 17015, "tourn": None, "season": None, "csv": None,
             "struct": "liga konferencije|conference league|uefa lk", "alias": {}},
}
_ALL_ALIAS = {a: b for _k, _v in LEAGUES.items() for a, b in (_v.get("alias") or {}).items()}
for _k, _v in UEFA.items():
    if _k not in LEAGUES:
        LEAGUES[_k] = dict(_v, alias=dict(_ALL_ALIAS))
'''
TABLE = '''
# --- UEFA takmicenja: ligaska faza (dodato iz add_uefa.py) ---
_LP = [(1, 8, "cl", "Osmina finala (direktno)"), (9, 24, "po", "Baraž za osminu finala"), (25, 36, "rel", "Eliminisani")]
for _c in ("cl", "el", "conf"):
    BANDS.setdefault(_c, _LP); SIZE.setdefault(_c, 36)
LP_CAP = {7: 144, 679: 144, 17015: 108}   # utakmica u ligaskoj fazi: 36 klubova x 8 (LS, LE) ili 6 (LK) kola / 2

'''


def patch(path, fn):
    if not os.path.exists(path):
        print(f"- {path}: nema fajla, preskacem"); return
    raw = open(path, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    txt = raw.replace("\r\n", "\n")
    new, msg = fn(txt)
    if new is None:
        print(f"- {path}: {msg}"); return
    print(f"+ {path}: {msg}")
    if not DRY:
        shutil.copy(path, path + ".bak")
        open(path, "w", encoding="utf-8", newline="").write(new.replace("\n", nl))


def leagues(t):
    if "UEFA = {" in t: return None, "vec ima UEFA takmicenja"
    i = t.find("\ndef _code():")
    if i < 0: return None, "ne nalazim 'def _code():' (fajl je drugacije napisan), dodaj rucno blok iz add_uefa.py"
    return t[:i] + "\n" + UEFA + t[i:], "dodate lige cl, el, conf"


def table(t):
    if "LP_CAP" in t: return None, "vec ima ogranicenje ligaske faze"
    i = t.find("\ndef jl(")
    if i < 0 or "SIZE = " not in t or "BANDS = " not in t: return None, "ne nalazim BANDS/SIZE/def jl (fajl je drugacije napisan), dodaj rucno blok iz add_uefa.py"
    t = t[:i] + "\n" + TABLE + t[i:]
    a = "        seen.add(sig)\n"
    if t.count(a) != 1: return None, "ne nalazim 'seen.add(sig)' u computed(); dodaj rucno: posle seen.add(sig) -> if LP_CAP.get(ut) and len(seen) > LP_CAP[ut]: break"
    return t.replace(a, a + "        if LP_CAP.get(ut) and len(seen) > LP_CAP[ut]:\n            break\n"), "dodate zone ligaske faze i ogranicenje broja utakmica"


def bat(t):
    if re.search(r"call :liga cl\b", t): return None, "vec ima cl/el/conf"
    ms = list(re.finditer(r"^call :liga .*$", t, re.M))
    if not ms: return None, "ne nalazim 'call :liga' linije"
    e = ms[-1].end()
    return t[:e] + '\ncall :liga cl "Liga sampiona"\ncall :liga el "Liga Evrope"\ncall :liga conf "Liga konferencije"' + t[e:], "dodati pozivi za cl, el, conf"


if __name__ == "__main__":
    patch("fb_leagues.py", leagues); patch("fb_table.py", table); patch("osvezi.bat", bat)
    print("\nGotovo." + (" (dry-run, nista nije upisano)" if DRY else " Pokreni:  py statshub_football.py --league cl  pa  py fb_table.py --league cl"))
