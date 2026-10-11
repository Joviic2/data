#!/usr/bin/env python3
"""Dodaje tekstualno objasnjenje svakom izboru u data/<liga>/predictions.json (polja: label, meaning, why[], risk[]).

Pokretanje (iz korena repoa, POSLE football_poisson.py):
  python fb_explain.py                      # svi data/*/predictions.json i data/predictions.json
  python fb_explain.py data/epl/predictions.json
index.html cita ova polja ako postoje; ako ih nema, isti tekst sam pravi iz istih podataka (rows + context), pa sajt radi i bez ovog koraka.
Objasnjenje koristi samo podatke koji vec stoje u fajlu: p, p_model, p_market, odds, ev, context (lam, xg, form, out, rest, style, mult, h2h)."""
import glob, json, math, os, re, sys


def pc(v):
    return f"{(v - 1) * 100:+.0f}%"


def ctx_for(doc, h, a, start):
    L = doc.get("context") or []
    for c in L:
        if c.get("h") == h and c.get("a") == a and (c.get("start") or "") == (start or ""):
            return c
    for c in L:
        if c.get("h") == h and c.get("a") == a:
            return c
    return None


def has(styles, *keys):
    s = " ".join(styles or []).lower()
    return any(k in s for k in keys)


def explain_row(r, h, a, C):
    mk, pk = str(r.get("market", "")), str(r.get("pick", ""))
    p, pm, pmk, o, ev = r.get("p"), r.get("p_model"), r.get("p_market"), r.get("odds"), r.get("ev")
    side = h if pk == "1" else a if pk == "2" else None
    kind, n = "other", None
    if mk.startswith("1X2"):
        kind = "1x2"
        label = {"1": f"1X2 · Pobeda domaćina ({h})", "X": "1X2 · Nerešeno", "2": f"1X2 · Pobeda gosta ({a})"}.get(pk, "1X2 · " + pk)
        meaning = {"1": f"{h} pobeđuje u regularnom delu meča (90 minuta, bez produžetaka).", "X": "Meč se posle 90 minuta završava nerešeno.",
                   "2": f"{a} pobeđuje u regularnom delu meča (90 minuta, bez produžetaka)."}.get(pk, "")
    elif mk.startswith("Golovi"):
        kind = "ou"
        m = re.search(r"\d+(?:\.\d+)?", mk)
        n = float(m[0]) if m else 2.5
        lo = int(n)
        if pk == "Vise":
            label, meaning = f"Preko {n:g} golova", f"Na meču padne {lo + 1} ili više golova (oba tima zajedno)."
        else:
            label, meaning = f"Ispod {n:g} golova", f"Na meču padne {lo} ili manje golova (oba tima zajedno)."
    elif mk.startswith("GG"):
        kind = "gg"
        if pk == "GG":
            label, meaning = "GG · Oba tima daju gol", f"I {h} i {a} postižu bar jedan gol."
        else:
            label, meaning = "NG · Bar jedan tim ne daje gol", f"Bar jedan od timova ({h} ili {a}) ostaje bez gola."
    elif mk.startswith(("AH", "EH")):
        kind = "hc"
        line = mk[2:].strip()
        label = f"{'Azijski' if mk.startswith('AH') else 'Evropski'} hendikep {line} · {side or 'Nerešeno'}"
        meaning = f"Tip na {side or 'nerešeno'} uz hendikep {line}: golovi tima se računaju sa unapred dodeljenom prednošću ili manjkom."
    else:
        label, meaning = f"{mk} · {pk}", ""
    why = []
    if p is not None and pm is not None and pmk is not None and o:
        why.append(f"Model daje {pm * 100:.0f}% a tržište (bez marže) {pmk * 100:.0f}%, što je zajedno {p * 100:.0f}%. "
                   f"Kvota {o:.2f} traži samo {100 / o:.0f}% (fer kvota je {1 / p:.2f}), pa je očekivana vrednost {ev * 100:+.1f}%.")
    risk = []
    if C and C.get("home") and C.get("away"):
        H, A = C["home"], C["away"]
        lam = C.get("lam")
        if lam:
            lh, la = lam
            if kind in ("1x2", "hc") and side:
                mine, opp = (lh, la) if pk == "1" else (la, lh)
                d = mine - opp
                base = f"Očekivani golovi: {h} {lh:.2f} : {la:.2f} {a}. "
                if d >= .25:
                    why.append(base + f"{side} ima prednost od {d:.2f} gola u očekivanim golovima.")
                elif d <= -.25:
                    why.append(base + f"{side} je po očekivanim golovima {abs(d):.2f} iza, ali kvota je viša nego što taj odnos snaga opravdava.")
                else:
                    why.append(base + f"Odnos je gotovo izjednačen, pa je kvota za {side} viša nego što ravnoteža opravdava.")
            elif kind == "1x2":
                why.append(f"Očekivani golovi {lh:.2f} : {la:.2f} (razlika {abs(lh - la):.2f}) znače tesan meč u kome je nerešeno realnije nego što kvota sugeriše.")
            elif kind == "ou":
                tot = lh + la
                rel = "iznad" if tot > n else "ispod"
                why.append(f"Zbir očekivanih golova je {tot:.2f} ({h} {lh:.2f} + {a} {la:.2f}), {rel} linije {n:g}"
                           + (", što ide u prilog tipu." if (pk == "Vise") == (tot > n) else ", pa je prednost mala i zavisi od varijanse."))
            elif kind == "gg":
                ph, pa = 1 - math.exp(-lh), 1 - math.exp(-la)
                why.append(f"Šansa da {h} postigne bar jedan gol je {ph * 100:.0f}%, a da {a} postigne {pa * 100:.0f}% (iz očekivanih golova {lh:.2f} : {la:.2f}). "
                           + ("Oba tima su realno u igri za gol." if pk == "GG" else "Bar jednom timu je realno teško da postigne gol."))
        if H.get("xg") is not None and A.get("xg") is not None:
            why.append(f"xG po meču (poslednjih 10): {h} {H['xg']:.2f}, {a} {A['xg']:.2f}.")
        fh, fa = H.get("form") or {}, A.get("form") or {}
        if fh.get("str") and fa.get("str"):
            s = f"Forma (5): {h} {fh['str']} ({fh['pts']} bod., {fh['gf']}:{fh['ga']}), {a} {fa['str']} ({fa['pts']} bod., {fa['gf']}:{fa['ga']}). "
            nm = max(1, len(fh["str"]))
            if kind in ("1x2", "hc") and side:
                dp = (fh["pts"] - fa["pts"]) * (1 if pk == "1" else -1)
                s += (f"{side} je u boljoj formi ({dp} bodova više)." if dp >= 3 else f"{side} je u slabijoj formi ({abs(dp)} bodova manje), ali to je već u kvoti." if dp <= -3 else "Forma je približno izjednačena.")
            elif kind == "ou":
                g = (fh["gf"] + fh["ga"] + fa["gf"] + fa["ga"]) / (2 * nm)
                s += f"U tim mečevima timovi u proseku igraju {g:.1f} golova po meču."
            elif kind == "gg":
                s += f"Golovi po meču u formi: {h} daje {fh['gf'] / nm:.1f}, {a} daje {fa['gf'] / nm:.1f}."
            why.append(s.strip())
        mu = C.get("mult") or [1, 1]
        parts = []
        for t, X in ((h, H), (a, A)):
            out = X.get("out") or []
            if out:
                parts.append(f"{t} bez: " + ", ".join(f"{u.get('n', '?')}" + ((" (" + ", ".join(x for x in (u.get("pos"), "upitan" if u.get("status") not in (None, "out") else "") if x) + ")") if (u.get("pos") or u.get("status") not in (None, "out")) else "") for u in out[:3]) + (f" i još {len(out) - 3}" if len(out) > 3 else ""))
            if X.get("rest") is not None and X["rest"] < 3.5:
                parts.append(f"{t} igra posle kratkog odmora ({X['rest']:.1f} d)")
        if parts or any(abs(v - 1) >= .02 for v in mu):
            why.append("; ".join(parts) + ("; " if parts else "") + f"korekcija očekivanih golova za povrede i odmor: {h} {pc(mu[0])}, {a} {pc(mu[1])}.")
        sh, sa = H.get("style") or [], A.get("style") or []
        if sh or sa:
            s = f"Stil igre: {h} – {', '.join(sh) or 'bez izraženog stila'}; {a} – {', '.join(sa) or 'bez izraženog stila'}. "
            if kind == "ou" and pk == "Manje" or kind == "gg" and pk == "NG":
                s += "Kontre i oprezna igra smanjuju broj šansi." if has(sh + sa, "kontr", "defan", "nizak", "blok") else ""
            elif kind == "ou" or kind == "gg":
                s += "Visok pritisak i napadački stil donose više šansi sa obe strane." if has(sh + sa, "pres", "visok", "posed", "napad") else ""
            elif side:
                ss = sh if pk == "1" else sa
                s += (f"{side} kontroliše posed i stvara više šansi." if has(ss, "posed", "dominac") else f"{side} igra na kontre, što odgovara ulozi autsajdera." if has(ss, "kontr") else "")
            why.append(s.strip())
        hh = C.get("h2h")
        if hh and hh.get("n", 0) >= 3:
            why.append(f"H2H (poslednjih {hh['n']}): {h} {hh['h_win']} pob., {hh['draw']} nere., {a} {hh['n'] - hh['h_win'] - hh['draw']} pob.; prosek {hh['avg_goals']:.2f} gola po meču.")
    else:
        why.append("Kontekst (xG, forma, povrede) nije dostupan za ovaj meč, pa se tip oslanja samo na model i kvotu.")
    if pm is not None and pmk is not None and abs(pm - pmk) > .10:
        risk.append(f"Model i tržište se razilaze za {abs(pm - pmk) * 100:.0f} p.p. Proveri da li je kvota pravilno uparena sa ovim tržištem.")
    if (r.get("ev_raw") or 0) > .25:
        risk.append("Neobično visok EV, proveri liniju kod kladionice.")
    risk.append("Konačne postave i vesti iz poslednjeg sata nisu uključeni u model.")
    return {"label": label, "meaning": meaning, "why": why, "risk": risk}


def annotate(path):
    doc = json.load(open(path, encoding="utf-8"))
    rows = doc.get("rows") or []
    for r in rows:
        C = ctx_for(doc, r.get("h"), r.get("a"), r.get("start"))
        r.update(explain_row(r, r.get("h"), r.get("a"), C))
    tmp = path + ".tmp"
    json.dump(doc, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return len(rows)


if __name__ == "__main__":
    paths = sys.argv[1:] or sorted(set(glob.glob("data/*/predictions.json") + glob.glob("data/predictions.json")))
    if not paths:
        sys.exit("nema predictions.json fajlova (pokreni iz korena repoa)")
    for p in paths:
        print(f"{p}: objasnjenje dodato za {annotate(p)} izbora")
