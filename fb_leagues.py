"""Podesavanja liga za fudbalske skripte. Izbor lige: --league laliga (ili FB_LEAGUE=laliga), podrazumevano epl.
Svaka skripta cita ovaj fajl (epl, laliga, l1, sa), pa se sve (folder, Statshub, Superbet, CSV, alias imena klubova) menja jednim parametrom."""
import os, sys

LEAGUES = {
    "epl": {"name": "Premier League", "dir": "epl", "path": "/leagues/england/premier-league/17", "ut": 17,
            "tourn": 1, "season": 96668, "csv": "E0", "struct": "premier|premijer|engleska|england|fudbal|football", "alias": {}},
    # La Liga: Statshub uniqueTournament 8. tournament/season se sami pronadju sa stranice lige (i snime u data/laliga/statshub_ids.json);
    # ako ne uspe, zadaj ih rucno: --tournament X --season Y (nadji ih sa statshub_probe.py --league /leagues/spain/la-liga/8)
    "laliga": {"name": "La Liga", "dir": "laliga", "path": "/leagues/spain/la-liga/8", "ut": 8,
               "tourn": None, "season": None, "csv": "SP1", "struct": "la liga|laliga|primera|spanija|spain|španija",
               "alias": {
                   # Statshub / engleski nazivi -> kratki kanonski kljuc
                   "atletico madrid": "atletico", "club atletico de madrid": "atletico", "athletic club": "athletic", "athletic bilbao": "athletic",
                   "deportivo alaves": "alaves", "rc celta": "celta", "rc celta de vigo": "celta", "celta vigo": "celta", "celta de vigo": "celta",
                   "rcd espanyol": "espanyol", "rcd espanyol de barcelona": "espanyol", "espanyol barcelona": "espanyol", "real betis": "betis",
                   "real betis balompie": "betis", "rayo vallecano": "rayo", "rayo vallecano de madrid": "rayo", "real oviedo": "oviedo",
                   "levante ud": "levante", "rcd mallorca": "mallorca", "ca osasuna": "osasuna", "real madrid cf": "real madrid",
                   "real sociedad de futbol": "real sociedad", "ud las palmas": "las palmas", "real valladolid": "valladolid",
                   "cd leganes": "leganes", "ud almeria": "almeria", "sd eibar": "eibar", "sd huesca": "huesca", "real zaragoza": "zaragoza",
                   "racing santander": "racing", "real racing club": "racing", "deportivo la coruna": "deportivo", "rc deportivo": "deportivo",
                   "sporting gijon": "gijon", "real sporting": "gijon", "ud logrones": "logrones", "malaga": "malaga", "cd tenerife": "tenerife",
                   # srpski prepisi (Superbet)
                   "barselona": "barcelona", "atletiko madrid": "atletico", "atletiko": "atletico", "atletik bilbao": "athletic",
                   "atletik klub": "athletic", "atletik": "athletic", "real sosijedad": "real sociedad", "real sosiedad": "real sociedad",
                   "valensija": "valencia", "viljareal": "villarreal", "sevilja": "sevilla", "selta vigo": "celta", "selta": "celta",
                   "rajo valjekano": "rayo", "rajo vaekano": "rayo", "hetafe": "getafe", "espanjol": "espanyol", "majorka": "mallorca",
                   "mayorka": "mallorca", "dzirona": "girona", "djirona": "girona", "elce": "elche", "real betis": "betis", "betis": "betis"}},
    # Ligue 1: Statshub uniqueTournament 34. Kljuc "l1" = folder data/l1/ (isto kao u index.html). tournament/season se sami pronadju.
    "l1": {"name": "Ligue 1", "dir": "l1", "path": "/leagues/france/ligue-1/34", "ut": 34,
           "tourn": None, "season": None, "csv": "F1", "struct": "ligue 1|ligue1|liga 1|francuska|france|francuske",
           "alias": {
               # nazivi se porede POSLE uklanjanja "fc/afc/cf", dijakritika i interpunkcije
               "paris saint germain": "psg", "paris sg": "psg", "paris saint germain fc": "psg", "pari sen zermen": "psg", "pari sen zhermen": "psg", "pari": "psg",
               "paris": "paris fc", "pari fc": "paris fc",
               "olympique marseille": "marseille", "olympique de marseille": "marseille", "marselj": "marseille", "marsej": "marseille", "marsilja": "marseille",
               "olympique lyonnais": "lyon", "olympique lyon": "lyon", "lyonnais": "lyon", "lion": "lyon", "olimpik lion": "lyon",
               "as monaco": "monaco", "monako": "monaco", "losc lille": "lille", "lille osc": "lille", "lil": "lille",
               "ogc nice": "nice", "nica": "nice", "nis": "nice", "rc lens": "lens", "lans": "lens",
               "stade rennais": "rennes", "stade rennes": "rennes", "rennes": "rennes", "ren": "rennes",
               "stade brestois 29": "brest", "stade brestois": "brest", "brestois": "brest",
               "rc strasbourg alsace": "strasbourg", "rc strasbourg": "strasbourg", "strazbur": "strasbourg",
               "nantes": "nantes", "nant": "nantes", "toulouse": "toulouse", "tuluz": "toulouse",
               "montpellier hsc": "montpellier", "monpelje": "montpellier", "stade de reims": "reims", "stade reims": "reims", "rems": "reims",
               "aj auxerre": "auxerre", "okser": "auxerre", "angers sco": "angers", "anze": "angers", "anzer": "angers",
               "le havre ac": "le havre", "le havre": "le havre", "havr": "le havre", "as saint etienne": "st etienne", "saint etienne": "st etienne",
               "sent etjen": "st etienne", "metz": "metz", "mec": "metz", "lorient": "lorient", "lorijan": "lorient",
               "girondins bordeaux": "bordeaux", "girondins de bordeaux": "bordeaux", "bordo": "bordeaux", "clermont foot": "clermont", "clermont foot 63": "clermont",
               "es troyes ac": "troyes", "troyes": "troyes", "ac ajaccio": "ajaccio", "gfc ajaccio": "ajaccio", "dijon fco": "dijon"}},
    # Serie A: Statshub uniqueTournament 23, football-data I1. Kljuc "sa" = folder data/sa/. tournament/season se sami pronadju.
    "sa": {"name": "Serie A", "dir": "sa", "path": "/leagues/italy/serie-a/23", "ut": 23,
           "tourn": 23, "season": 95836,    # iz probe: /api/tournament/23/95836/standing (sezona 26/27; za novu sezonu: --season ili obrisi pa se trazi ponovo)
           "csv": "I1", "struct": "serie a|seria a|serija a|italija|italy|italijanska",
           "alias": {
               "internazionale": "inter", "fc internazionale": "inter", "inter milan": "inter", "internacionale": "inter", "inter milano": "inter",
               "ac milan": "milan", "ac milano": "milan", "juventus fc": "juventus", "juve": "juventus", "juvents": "juventus",
               "ssc napoli": "napoli", "napolji": "napoli", "as roma": "roma", "ss lazio": "lazio", "lacio": "lazio",
               "atalanta bc": "atalanta", "acf fiorentina": "fiorentina", "florentina": "fiorentina",
               "bologna fc 1909": "bologna", "bolonja": "bologna", "torino fc": "torino", "udinese calcio": "udinese", "udineze": "udinese",
               "genoa cfc": "genoa", "dzenova": "genoa", "djenova": "genoa", "cagliari calcio": "cagliari", "kaljari": "cagliari",
               "hellas verona": "verona", "hellas verona fc": "verona", "us lecce": "lecce", "leche": "lecce",
               "parma calcio 1913": "parma", "como 1907": "como", "komo": "como", "empoli fc": "empoli",
               "us sassuolo": "sassuolo", "sassuolo calcio": "sassuolo", "sasuolo": "sassuolo", "us cremonese": "cremonese", "kremonese": "cremonese",
               "pisa sc": "pisa", "ac pisa 1909": "pisa", "venezia fc": "venezia", "venecija": "venezia", "ac monza": "monza",
               "us salernitana 1919": "salernitana", "frosinone calcio": "frosinone"}},
    # Bundesliga: Statshub uniqueTournament 35, football-data D1. Kljuc "bl" = folder data/bl/. tournament/season se sami pronadju (ili --tournament X --season Y).
    "bl": {"name": "Bundesliga", "dir": "bl", "path": "/leagues/germany/bundesliga/35", "ut": 35,
           "tourn": None, "season": None, "csv": "D1", "struct": "bundesliga|nemacka|germany|nemacke|njemacka",
           "alias": {
               # kanonski kljuc = kratko ime; levo: Statshub (engleski/nemacki) i srpski prepisi (Superbet), posle nrm (bez dijakritika, bez fc/afc/cf)
               "bayern munich": "bayern", "bayern munchen": "bayern", "fc bayern munchen": "bayern", "bajern": "bayern", "bajern minhen": "bayern", "bajern minchen": "bayern",
               "borussia dortmund": "dortmund", "bv borussia 09 dortmund": "dortmund", "borusija dortmund": "dortmund", "borusija d": "dortmund", "dortmund": "dortmund",
               "bayer 04 leverkusen": "leverkusen", "bayer leverkusen": "leverkusen", "bajer leverkuzen": "leverkusen", "bajer 04 leverkuzen": "leverkusen", "leverkuzen": "leverkusen",
               "rb leipzig": "leipzig", "rasenballsport leipzig": "leipzig", "rb lajpcig": "leipzig", "lajpcig": "leipzig", "lajpzig": "leipzig", "rb lajpzig": "leipzig",
               "vfb stuttgart": "stuttgart", "stutgart": "stuttgart", "vfb stutgart": "stuttgart",
               "eintracht frankfurt": "frankfurt", "ajntraht frankfurt": "frankfurt", "ajntrah frankfurt": "frankfurt", "ajntraht": "frankfurt", "ajntrakt frankfurt": "frankfurt",
               "tsg hoffenheim": "hoffenheim", "tsg 1899 hoffenheim": "hoffenheim", "hofenhajm": "hoffenheim", "hofenhajm tsg": "hoffenheim",
               "sc freiburg": "freiburg", "frajburg": "freiburg", "werder bremen": "werder", "sv werder bremen": "werder", "verder bremen": "werder", "verder": "werder",
               "vfl wolfsburg": "wolfsburg", "volfsburg": "wolfsburg", "1 fsv mainz 05": "mainz", "fsv mainz 05": "mainz", "mainz 05": "mainz", "majnc": "mainz", "majnc 05": "mainz",
               "fc augsburg": "augsburg", "augzburg": "augsburg", "1 fc union berlin": "union berlin", "fc union berlin": "union berlin", "unjon berlin": "union berlin", "union": "union berlin",
               "borussia monchengladbach": "gladbach", "borussia m gladbach": "gladbach", "monchengladbach": "gladbach", "m gladbach": "gladbach", "mgladbach": "gladbach",
               "borusija menhengladbah": "gladbach", "borusija monhengladbah": "gladbach", "menhengladbah": "gladbach", "borusija m": "gladbach",
               "1 fc heidenheim 1846": "heidenheim", "1 heidenheim 1846": "heidenheim", "fc heidenheim": "heidenheim", "heidenheim 1846": "heidenheim", "hajdenhajm": "heidenheim",
               "fc st pauli": "st pauli", "sankt pauli": "st pauli", "san pauli": "st pauli", "st pauli": "st pauli",
               "1 fc koln": "koln", "fc koln": "koln", "cologne": "koln", "keln": "koln", "kolonj": "koln", "1 koln": "koln",
               "hamburger sv": "hamburg", "hamburg sv": "hamburg", "hsv": "hamburg", "hamburger": "hamburg",
               "fc schalke 04": "schalke", "schalke 04": "schalke", "salke": "schalke", "salke 04": "schalke",
               "sc paderborn 07": "paderborn", "paderborn 07": "paderborn", "paderborn": "paderborn", "hertha bsc": "hertha", "hertha berlin": "hertha", "herta": "hertha",
               "fortuna dusseldorf": "dusseldorf", "1 fc nurnberg": "nurnberg", "nurnberg": "nurnberg", "hannover 96": "hannover", "sv elversberg": "elversberg",
               "holstein kiel": "kiel", "vfl bochum": "bochum", "bochum 1848": "bochum", "darmstadt 98": "darmstadt", "sv darmstadt 98": "darmstadt", "1 fc kaiserslautern": "kaiserslautern"}},
    # ===== EVROPSKA TAKMICENJA (od sutra). Nemaju svoj Statshub fajl: snaga ekipa se uzima iz domacih liga ("feeders"), a razlika u kvalitetu
    # liga je u "strength" (VAZNA PRETPOSTAVKA, menjaj po osecaju: >1 = jaca liga). Ekipe iz liga koje nemamo (Portugal, Holandija, Turska...) se preskacu bez tipa.
    "cl": {"name": "Liga šampiona", "dir": "cl", "path": "/leagues/europe/uefa-champions-league/7", "ut": 7, "tourn": None, "season": None, "csv": "",
           "struct": "liga sampiona|champions league", "cup": True, "feeders": ["epl", "laliga", "sa", "bl", "l1"],
           "strength": {"epl": 1.00, "laliga": 0.97, "sa": 0.95, "bl": 0.95, "l1": 0.90}, "alias": {}},
    "el": {"name": "Liga Evrope", "dir": "el", "path": "/leagues/europe/uefa-europa-league/679", "ut": 679, "tourn": None, "season": None, "csv": "",
           "struct": "liga evrope|europa league", "cup": True, "feeders": ["epl", "laliga", "sa", "bl", "l1"],
           "strength": {"epl": 1.00, "laliga": 0.97, "sa": 0.95, "bl": 0.95, "l1": 0.90}, "alias": {}},
<<<<<<< HEAD
    "conf": {"name": "Liga konferencije", "dir": "conf", "path": "/leagues/europe/uefa-conference-league/17015", "ut": 17015, "tourn": None, "season": None, "csv": "",
             "struct": "liga konferencije|conference league|uefa lk", "cup": True, "feeders": ["epl", "laliga", "sa", "bl", "l1"],
             "strength": {"epl": 1.00, "laliga": 0.97, "sa": 0.95, "bl": 0.95, "l1": 0.90}, "alias": {}},
}
CUP_TXT = {"cl": r"liga sampiona|champions league", "el": r"liga evrope|europa league", "conf": r"liga konferencij|conference league"}
CUP_NOT = r"kvalifik|qualif|zene|women|youth|omladin|\bu ?\d\d\b|super ?cup|superkup|play ?off"



# UEFA (cl/el/conf) se vode po TIMOVIMA: statshub_football.py za svaku ekipu ucesnicu skida sve njene utakmice (domace lige + Evropa), bez novih liga/foldera.
# "feeders"/"strength" iznad ostaju samo kao rezervni nacin (kad data/<cup>/statshub_data.json ne postoji).
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

=======
    "ecl": {"name": "Liga konferencije", "dir": "ecl", "path": "/leagues/europe/uefa-conference-league/17015", "ut": 17015, "tourn": None, "season": None, "csv": "",
            "struct": "liga konferencije|conference league", "cup": True, "feeders": ["epl", "laliga", "sa", "bl", "l1"],
            "strength": {"epl": 1.00, "laliga": 0.97, "sa": 0.95, "bl": 0.95, "l1": 0.90}, "alias": {}},
}
CUP_TXT = {"cl": r"liga sampiona|champions league", "el": r"liga evrope|europa league", "ecl": r"liga konferencij|conference league"}
CUP_NOT = r"kvalifik|qualif|zene|women|youth|omladin|\bu ?\d\d\b|super ?cup|superkup|play ?off"


>>>>>>> c0636432980d531347fbd7fa74496488ec3a9d81
def _code():
    a = sys.argv
    for i, x in enumerate(a):
        if x == "--league" and i + 1 < len(a):
            return a[i + 1]
        if x.startswith("--league="):
            return x.split("=", 1)[1]
    return os.environ.get("FB_LEAGUE", "epl")


CODE = _code()
if CODE not in LEAGUES:
    sys.exit(f"nepoznata liga '{CODE}', dozvoljeno: {', '.join(LEAGUES)}")
CUR = dict(LEAGUES[CODE], code=CODE)
