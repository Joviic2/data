"""Euroleague Value Analyzer - pokretanje: streamlit run app.py"""
import math
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Euroleague Value Analyzer", page_icon="🏀", layout="wide")
st.markdown(
    "<style>.big{font-size:2rem;font-weight:700}.ok{color:#16a34a}.no{color:#dc2626}"
    "div[data-testid=stMetric]{background:rgba(120,120,120,.10);padding:12px 16px;border-radius:10px}</style>",
    unsafe_allow_html=True,
)

DATA = Path("data")
DATA.mkdir(exist_ok=True)
BETS = DATA / "bets.csv"
API = "https://api-live.euroleague.net/v1/results"
BET_COLS = ["datum", "utakmica", "trziste", "izbor", "kvota", "ulog", "model_p", "ishod"]


# ---------- PODACI ----------
def _pick(cols, *keys):
    for c in cols:
        if all(k in c for k in keys):
            return c
    return None


@st.cache_data(ttl=3600, show_spinner="Skidam rezultate sa Euroleague API-ja...")
def fetch_results(season_code: str) -> pd.DataFrame:
    r = requests.get(API, params={"seasonCode": season_code}, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = [
        {c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in g}
        for g in root.iter()
        if g.tag.split("}")[-1].lower() == "game"
    ]
    raw = pd.DataFrame(rows)
    cols = list(raw.columns)
    m = {
        "datum": _pick(cols, "date"),
        "home": _pick(cols, "home", "team"),
        "away": _pick(cols, "away", "team"),
        "hp": _pick(cols, "home", "score"),
        "ap": _pick(cols, "away", "score"),
    }
    if None in m.values():
        raise ValueError(f"Neocekivan format odgovora, kolone: {cols}")
    df = pd.DataFrame({k: raw[v] for k, v in m.items()})
    df["hp"] = pd.to_numeric(df["hp"], errors="coerce")
    df["ap"] = pd.to_numeric(df["ap"], errors="coerce")
    df = df.dropna(subset=["hp", "ap"])
    return df[(df.hp > 0) & (df.ap > 0)].assign(sezona=season_code)


# ---------- MODEL ----------
def fit_ratings(df: pd.DataFrame, lam: float):
    """Ridge regresija na razliku u poenima: margin = r_domaci - r_gost + prednost_domaceg."""
    teams = sorted(set(df.home) | set(df.away))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X = np.zeros((len(df), n + 1))
    for k, (h, a) in enumerate(zip(df.home, df.away)):
        X[k, idx[h]], X[k, idx[a]], X[k, n] = 1, -1, 1
    y = (df.hp - df.ap).to_numpy(float)
    reg = lam * np.eye(n + 1)
    reg[n, n] = 0  # prednost domaceg terena se ne regularizuje
    beta = np.linalg.solve(X.T @ X + reg, X.T @ y)
    resid = y - X @ beta
    sigma = max(float(resid.std(ddof=1)), 8.0) if len(df) > 10 else 11.0
    table = pd.DataFrame({"Tim": teams, "Rejting": beta[:n]})
    g = pd.concat([
        df.assign(tim=df.home, m=df.hp - df.ap),
        df.assign(tim=df.away, m=df.ap - df.hp),
    ]).groupby("tim").agg(Utakmica=("m", "size"), ProsecnaRazlika=("m", "mean"))
    table = table.merge(g, left_on="Tim", right_index=True).sort_values("Rejting", ascending=False)
    return table.reset_index(drop=True), float(beta[n]), sigma


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def kelly_stake(p, odds, bankroll, frac, max_pct):
    f = (p * odds - 1) / (odds - 1) if odds > 1 else 0
    return max(0.0, min(f * frac, max_pct / 100)) * bankroll


# ---------- SIDEBAR ----------
st.sidebar.title("🏀 Podesavanja")
seasons = st.sidebar.multiselect("Sezone (podaci)", ["E2024", "E2025", "E2026"], default=["E2025", "E2026"])
st.sidebar.caption("Rano u sezoni ima malo meceva, pa prosla sezona daje stabilniji pocetni rejting.")
bank0 = st.sidebar.number_input("Pocetni bankroll", 10.0, 1e7, 1000.0, 50.0)
kfrac = st.sidebar.select_slider("Kelly razlomak", [0.1, 0.25, 0.5], value=0.25)
maxpct = st.sidebar.slider("Max ulog (% bankrolla)", 0.5, 5.0, 2.0, 0.5)
lam = st.sidebar.slider("Regularizacija (veca = oprezniji rejtinzi)", 1.0, 30.0, 8.0, 1.0)
if st.sidebar.button("Osvezi podatke"):
    fetch_results.clear()

# ---------- UCITAVANJE ----------
frames, errors = [], []
for s in seasons:
    try:
        frames.append(fetch_results(s))
    except Exception as e:  # noqa: BLE001
        errors.append(f"{s}: {e}")
for e in errors:
    st.sidebar.error(e)

up = st.sidebar.file_uploader("Ili ucitaj CSV (date,home,away,hp,ap)", type="csv")
if up is not None:
    c = pd.read_csv(up)
    c.columns = ["datum", "home", "away", "hp", "ap"]
    frames.append(c.assign(sezona="CSV"))

st.title("Euroleague Value Analyzer")
if not frames:
    st.warning("Nema podataka. Proveri internet vezu ili ucitaj CSV u levom meniju.")
    st.stop()

games = pd.concat(frames, ignore_index=True)
ratings, hca, sigma = fit_ratings(games, lam)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Odigranih utakmica", len(games))
c2.metric("Timova", len(ratings))
c3.metric("Prednost domaceg (poena)", f"{hca:+.1f}")
c4.metric("Standardna devijacija (poena)", f"{sigma:.1f}")

tab1, tab2, tab3 = st.tabs(["🎯 Analiza utakmice", "📊 Rejting timova", "💰 Bankroll"])

# ---------- TAB 1 ----------
with tab1:
    teams = ratings.Tim.tolist()
    a, b = st.columns(2)
    home = a.selectbox("Domacin", teams, index=0)
    away = b.selectbox("Gost", teams, index=min(1, len(teams) - 1))
    if home == away:
        st.info("Izaberi dva razlicita tima.")
        st.stop()
    rt = ratings.set_index("Tim").Rejting
    margin = rt[home] - rt[away] + hca
    p_home = phi(margin / sigma)

    m1, m2, m3 = st.columns(3)
    m1.metric(f"Ocekivana razlika ({home})", f"{margin:+.1f}")
    m2.metric(f"Sansa: {home}", f"{p_home:.1%}")
    m3.metric(f"Sansa: {away}", f"{1 - p_home:.1%}")

    st.subheader("Pobednik (moneyline)")
    o1, o2 = st.columns(2)
    odd_h = o1.number_input(f"Kvota {home}", 1.01, 50.0, 1.90, 0.01)
    odd_a = o2.number_input(f"Kvota {away}", 1.01, 50.0, 1.90, 0.01)
    fair_h = (1 / odd_h) / (1 / odd_h + 1 / odd_a)

    st.subheader("Hendikep")
    line = st.number_input(f"Hendikep za {home} (npr. -4.5 znaci da {home} daje 4.5)", -40.0, 40.0, -4.5, 0.5)
    h1, h2 = st.columns(2)
    odd_sh = h1.number_input(f"Kvota hendikep {home}", 1.01, 10.0, 1.90, 0.01)
    odd_sa = h2.number_input(f"Kvota hendikep {away}", 1.01, 10.0, 1.90, 0.01)
    p_cover = phi((margin + line) / sigma)

    bank_now = bank0
    if BETS.exists():
        b_df = pd.read_csv(BETS)
        pl = np.where(b_df.ishod == "pobeda", b_df.ulog * (b_df.kvota - 1), np.where(b_df.ishod == "gubitak", -b_df.ulog, 0))
        bank_now = bank0 + float(pl.sum())

    options = [
        ("Pobednik", home, p_home, odd_h),
        ("Pobednik", away, 1 - p_home, odd_a),
        (f"Hendikep {line:+.1f}", home, p_cover, odd_sh),
        (f"Hendikep {-line:+.1f}", away, 1 - p_cover, odd_sa),
    ]
    rows = []
    for mk, ch, p, o in options:
        ev = p * o - 1
        rows.append({"Trziste": mk, "Izbor": ch, "Model %": p * 100, "Kvota": o,
                     "Implicitno %": 100 / o, "EV %": ev * 100,
                     "Preporuceni ulog": kelly_stake(p, o, bank_now, kfrac, maxpct)})
    res = pd.DataFrame(rows)
    st.subheader("Rezultat analize")
    st.dataframe(
        res.style.format({"Model %": "{:.1f}", "Implicitno %": "{:.1f}", "EV %": "{:+.1f}", "Preporuceni ulog": "{:.2f}"})
        .map(lambda v: "color:#16a34a;font-weight:700" if isinstance(v, float) and v > 0 else "", subset=["EV %"]),
        use_container_width=True, hide_index=True,
    )
    st.caption(f"Nepristrasna verovatnoca iz kvota bez marze kladionice: {home} {fair_h:.1%}. "
               "EV vece od 0 znaci da model vidi vrednost, ali je model samo procena. "
               "Veliki EV (preko 10%) cesto znaci da model nesto ne vidi (povreda, rotacija).")

    pick = st.selectbox("Upisi opkladu u evidenciju", [f"{r['Trziste']} | {r['Izbor']}" for r in rows])
    if st.button("Dodaj u evidenciju"):
        r = rows[[f"{x['Trziste']} | {x['Izbor']}" for x in rows].index(pick)]
        new = pd.DataFrame([{"datum": date.today().isoformat(), "utakmica": f"{home} - {away}",
                             "trziste": r["Trziste"], "izbor": r["Izbor"], "kvota": r["Kvota"],
                             "ulog": round(r["Preporuceni ulog"], 2), "model_p": round(r["Model %"] / 100, 4),
                             "ishod": "cekanje"}])
        old = pd.read_csv(BETS) if BETS.exists() else pd.DataFrame(columns=BET_COLS)
        pd.concat([old, new], ignore_index=True).to_csv(BETS, index=False)
        st.success("Dodato. Ulog mozes da izmenis u tabu Bankroll.")

# ---------- TAB 2 ----------
with tab2:
    st.subheader("Rejting timova")
    st.caption("Rejting = koliko poena tim vredi iznad proseka, uz ispravku za jacinu protivnika.")
    st.dataframe(ratings.style.format({"Rejting": "{:+.2f}", "ProsecnaRazlika": "{:+.1f}"})
                 .background_gradient(subset=["Rejting"], cmap="RdYlGn"),
                 use_container_width=True, hide_index=True)

# ---------- TAB 3 ----------
with tab3:
    st.subheader("Evidencija opklada")
    bets = pd.read_csv(BETS) if BETS.exists() else pd.DataFrame(columns=BET_COLS)
    edited = st.data_editor(
        bets, num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={"ishod": st.column_config.SelectboxColumn("ishod", options=["cekanje", "pobeda", "gubitak", "povrat"])},
    )
    if st.button("Sacuvaj izmene"):
        edited.to_csv(BETS, index=False)
        st.success("Sacuvano.")

    done = edited[edited.ishod.isin(["pobeda", "gubitak", "povrat"])].copy()
    if len(done):
        done["profit"] = np.where(done.ishod == "pobeda", done.ulog * (done.kvota - 1),
                                  np.where(done.ishod == "gubitak", -done.ulog, 0.0))
        staked = done.ulog.sum()
        wins = (done.ishod == "pobeda").sum()
        decided = (done.ishod != "povrat").sum()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Bankroll", f"{bank0 + done.profit.sum():.2f}", f"{done.profit.sum():+.2f}")
        k2.metric("ROI", f"{done.profit.sum() / staked:.1%}" if staked else "-")
        k3.metric("Hit rate", f"{wins / decided:.1%}" if decided else "-")
        k4.metric("Prosecna kvota", f"{done.kvota.mean():.2f}")
        st.line_chart(pd.Series(bank0 + done.profit.cumsum().to_numpy(), name="Bankroll"))
    else:
        st.info("Kad oznacis opklade kao pobeda/gubitak, ovde se pojavljuju ROI, hit rate i grafik bankrolla.")
