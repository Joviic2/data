"""Pomocni modul za UEFA takmicenja (Liga sampiona, Liga Evrope, Liga konferencije): cl, el, conf.

Za razliku od nacionalnih liga, ucesnici dolaze iz desetak razlicitih liga, pa se podaci NE traze po 'ligi' nego po timovima:
statshub_football.py za svako takmicenje uzme 36 ucesnika (iz Statshub tabele) i za svakog skine SVE njegove utakmice (domace lige + Evropa).
Protivnici iz domacih liga postaju samo cvorovi u modelu (da se jacina tima poveze preko zajednickih protivnika), bez posebnog foldera i posebnih skripti.
Ovde su: prepoznavanje takmicenja, zajednicki aliasi imena klubova (Statshub / Superbet / srpski prepisi) i tezine meceva."""
import re

UEFA_UT = {7: "Liga sampiona", 679: "Liga Evrope", 17015: "Liga konferencije"}      # Statshub/Sofascore uniqueTournamentId
UEFA_CODES = ("cl", "el", "conf")


def is_cup(cur):
    """True ako je izabrano UEFA takmicenje (po kodu ili po uniqueTournamentId)."""
    return cur.get("code") in UEFA_CODES or cur.get("ut") in UEFA_UT


# klubovi koji nisu u cetiri-pet nacionalnih liga iz fb_leagues.py; leva strana = ime posle nrm (bez dijakritika, bez fc/afc/cf, bez interpunkcije)
EU_EXTRA = {
    "sporting cp": "sporting", "sporting lisbon": "sporting", "sporting lisabon": "sporting", "sporting clube de portugal": "sporting", "sporting lisbon cp": "sporting",
    "sl benfica": "benfica", "benfica lisbon": "benfica", "benfika": "benfica", "fc porto": "porto", "porto": "porto", "sc braga": "braga", "sporting braga": "braga", "braga": "braga",
    "afc ajax": "ajax", "ajax amsterdam": "ajax", "ajaks": "ajax", "psv eindhoven": "psv", "psv": "psv", "feyenoord rotterdam": "feyenoord", "fejenord": "feyenoord", "az alkmaar": "az", "az": "az",
    "club brugge kv": "club brugge", "club brugge": "club brugge", "klub briz": "club brugge", "royal union saint gilloise": "union sg", "union saint gilloise": "union sg", "union sg": "union sg",
    "rsc anderlecht": "anderlecht", "krc genk": "genk", "kaa gent": "gent", "galatasaray sk": "galatasaray", "galatasaraj": "galatasaray", "galatasaray": "galatasaray",
    "fenerbahce sk": "fenerbahce", "fenerbahce": "fenerbahce", "fenerbace": "fenerbahce", "besiktas jk": "besiktas", "besiktas": "besiktas", "besiktas istanbul": "besiktas", "trabzonspor": "trabzonspor",
    "celtic": "celtic", "keltik": "celtic", "rangers": "rangers", "rendzers": "rangers", "rangers fc": "rangers", "heart of midlothian": "hearts", "aberdeen": "aberdeen",
    "slavia prague": "slavia praha", "sk slavia praha": "slavia praha", "slavija prag": "slavia praha", "slavia praha": "slavia praha", "sparta prague": "sparta praha", "sparta praha": "sparta praha",
    "viktoria plzen": "plzen", "fc viktoria plzen": "plzen", "plzen": "plzen", "fc salzburg": "salzburg", "red bull salzburg": "salzburg", "rb salzburg": "salzburg", "salcburg": "salzburg",
    "sturm graz": "sturm graz", "sk sturm graz": "sturm graz", "lask": "lask", "lask linz": "lask", "rapid wien": "rapid wien", "rapid vienna": "rapid wien",
    "shakhtar donetsk": "shakhtar", "sahtar": "shakhtar", "shakhtar": "shakhtar", "dynamo kyiv": "dynamo kyiv", "dinamo kijev": "dynamo kyiv",
    "gnk dinamo zagreb": "dinamo zagreb", "dinamo zagreb": "dinamo zagreb", "hnk rijeka": "rijeka", "hajduk split": "hajduk",
    "olympiacos": "olympiacos", "olympiacos piraeus": "olympiacos", "olimpijakos": "olympiacos", "panathinaikos": "panathinaikos", "paok": "paok", "paok thessaloniki": "paok", "aek athens": "aek athens", "aek atina": "aek athens",
    "fc copenhagen": "copenhagen", "fc kobenhavn": "copenhagen", "kopenhagen": "copenhagen", "fc midtjylland": "midtjylland", "midtjylland": "midtjylland", "brondby": "brondby",
    "bodo glimt": "bodo glimt", "fk bodo glimt": "bodo glimt", "bodo": "bodo glimt", "viking": "viking", "viking fk": "viking", "brann": "brann", "sk brann": "brann", "molde": "molde",
    "malmo ff": "malmo", "malmo": "malmo", "malme": "malmo", "bk hacken": "hacken", "aik": "aik", "djurgarden": "djurgarden", "hjk helsinki": "hjk", "hjk": "hjk",
    "bsc young boys": "young boys", "young boys": "young boys", "jang bojs": "young boys", "fc basel 1893": "basel", "basel": "basel", "bazel": "basel", "fc zurich": "zurich", "fc lugano": "lugano", "servette": "servette",
    "qarabag": "qarabag", "qarabag fk": "qarabag", "karabag": "qarabag", "sabah": "sabah", "sabah fk": "sabah", "sabah baku": "sabah", "neftci": "neftchi",
    "crvena zvezda": "crvena zvezda", "fk crvena zvezda": "crvena zvezda", "red star belgrade": "crvena zvezda", "zvezda": "crvena zvezda", "fk partizan": "partizan", "partizan": "partizan",
    "ferencvaros": "ferencvaros", "ferencvarosi tc": "ferencvaros", "fehervar": "fehervar", "legia warszawa": "legia", "legia warsaw": "legia", "legia": "legia", "lech poznan": "lech poznan", "jagiellonia bialystok": "jagiellonia",
    "cfr cluj": "cfr cluj", "fcsb": "fcsb", "steaua bucuresti": "fcsb", "universitatea craiova": "craiova", "ludogorets": "ludogorets", "ludogorec": "ludogorets", "ludogorets razgrad": "ludogorets",
    "maccabi tel aviv": "maccabi tel aviv", "maccabi haifa": "maccabi haifa", "hapoel beer sheva": "hapoel beer sheva", "apoel": "apoel", "apoel nicosia": "apoel", "omonia": "omonia", "omonia nicosia": "omonia",
    "dinamo minsk": "dinamo minsk", "rakow czestochowa": "rakow", "zrinjski": "zrinjski", "hnk hajduk": "hajduk", "kairat": "kairat", "kairat almaty": "kairat", "shamrock rovers": "shamrock rovers",
    "ac omonia": "omonia", "sheriff tiraspol": "sheriff", "sheriff": "sheriff", "pyunik": "pyunik", "lincoln red imps": "lincoln red imps",
}


def eu_alias():
    """Zajednicki recnik imena: aliasi svih nacionalnih liga iz fb_leagues.py + EU_EXTRA (vrednosti su kanonski kljucevi)."""
    out = dict(EU_EXTRA)
    try:
        from fb_leagues import LEAGUES
        for code, cfg in LEAGUES.items():
            if code in UEFA_CODES:
                continue
            out.update(cfg.get("alias") or {})
    except Exception:
        pass
    return out


# Domace utakmice protivnika iz slabijih liga ne vrede koliko evropske (tezina u modelu; vidi football_poisson.py --dom-w)
def match_weight(ut, dom_w):
    return 1.0 if ut in UEFA_UT else dom_w
