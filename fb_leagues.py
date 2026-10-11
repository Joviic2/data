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
}


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
