/* LIVE GAMES widget: dugme "Live" sa crvenom pulsirajucom tackom + panel sa mecevima uzivo, tipom i objasnjenjem.
   Ubaci pred </body> u index.html:  <script src="live.js"></script>
   Cita http://localhost:8765/live.json (fb_live.py --serve), a ako ga nema, data/live.json iz repoa. Ne zavisi od ostatka sajta.
   Dugme se ubacuje u element #qf (brzi filteri) ako postoji, inace u #tabs, inace plutajuce gore desno; rucno: LiveGames.mount("#moj-element"). */
(function () {
  if (window.LiveGames) return;
  const SRC = ["http://localhost:8765/live.json", "data/live.json"], FRESH = 240; // sekundi: starije od toga = tacka je siva
  let data = null, panel = null, btn = null, shown = false, openId = null;
  const css = `.lv-btn{display:inline-flex;align-items:center;gap:8px;cursor:pointer;font:inherit;font-weight:700;color:inherit;background:transparent;border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:6px 14px}
.lv-btn.lv-float{position:fixed;top:12px;right:12px;z-index:9998;background:#111c2e}
.lv-dot{width:10px;height:10px;border-radius:50%;background:#7a8699;flex:none}.lv-on .lv-dot{background:#ff2d2d;animation:lvp 1.3s infinite}
@keyframes lvp{0%{box-shadow:0 0 0 0 rgba(255,45,45,.75)}70%{box-shadow:0 0 0 10px rgba(255,45,45,0)}100%{box-shadow:0 0 0 0 rgba(255,45,45,0)}}
@media(prefers-reduced-motion:reduce){.lv-on .lv-dot{animation:none}}
.lv-cnt{background:#ff2d2d;color:#fff;border-radius:999px;padding:0 7px;font-size:.8em}
.lv-ov{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.65);display:flex;align-items:flex-start;justify-content:center;overflow:auto;padding:16px}
.lv-panel{background:#0e1726;color:#e8edf5;border:1px solid #243049;border-radius:14px;width:min(760px,100%);padding:14px}
.lv-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}.lv-head h2{margin:0;font-size:1.2rem;flex:1}.lv-sub{color:#93a0b5;font-size:.85rem}
.lv-card{border:1px solid #243049;border-radius:12px;padding:10px 12px;margin:8px 0;background:#121d31}
.lv-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.lv-min{background:#ff2d2d;color:#fff;border-radius:6px;padding:1px 8px;font-weight:700;font-size:.85rem}
.lv-teams{flex:1;min-width:160px;font-weight:700}.lv-score{font-size:1.4rem;font-weight:800}
.lv-tip{margin-top:8px;padding:8px 10px;border-radius:10px;background:rgba(34,197,94,.13);border:1px solid #22c55e}.lv-tip.lv-no{background:rgba(147,160,181,.1);border-color:#34415a;color:#93a0b5}
.lv-ev{color:#22c55e;font-weight:800}.lv-card button.lv-why{margin-top:8px;cursor:pointer;font:inherit;color:inherit;background:transparent;border:1px solid #34415a;border-radius:8px;padding:4px 10px}
.lv-card ul{margin:8px 0 0;padding-left:18px}.lv-card li{margin:5px 0}.lv-risk li{color:#f5c542}.lv-x{cursor:pointer;font:inherit;color:inherit;background:transparent;border:1px solid #34415a;border-radius:8px;padding:4px 10px}`;
  const h = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e };
  const age = () => data && data.updated ? Math.round(Date.now() / 1000 - data.updated) : null;
  const rows = () => ((data && data.matches) || []).filter(m => m.score).sort((a, b) => ((b.tip ? b.tip.ev : -1) - (a.tip ? a.tip.ev : -1)));
  async function load() {
    for (const u of SRC) {
      try {
        const c = new AbortController(), t = setTimeout(() => c.abort(), u.startsWith("http://localhost") ? 1500 : 6000);
        const r = await fetch(u + (u.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store", signal: c.signal });
        clearTimeout(t);
        if (r.ok) { data = await r.json(); break }
      } catch (e) { }
    }
    paint();
  }
  function paintBtn() {
    if (!btn) return;
    const n = rows().length, a = age(), live = n > 0 && a != null && a <= FRESH;
    btn.classList.toggle("lv-on", live);
    btn.replaceChildren(h("span", "lv-dot"), h("span", "", "Live Games"), ...(n ? [h("span", "lv-cnt", String(n))] : []));
    btn.title = data ? (live ? n + " uživo, osveženo pre " + a + " s" : "Nema svežih podataka uživo" + (a != null ? " (stari " + Math.round(a / 60) + " min)" : "")) : "Live feed nije dostupan";
  }
  function card(m) {
    const c = h("div", "lv-card"), r = h("div", "lv-row");
    r.append(h("span", "", m.sport === "fudbal" ? "⚽" : "🏀"), h("span", "lv-min", typeof m.minute === "number" ? m.minute + "'" : (m.minute || "uživo")),
      h("span", "lv-teams", m.h + " – " + m.a), h("span", "lv-score", m.score[0] + " : " + m.score[1]));
    c.append(r);
    const t = m.tip, tip = h("div", "lv-tip" + (t ? "" : " lv-no"));
    if (t) { tip.append(h("b", "", "Preporuka: " + t.label + " @ " + t.odds + "  "), h("span", "lv-ev", "EV " + (t.ev > 0 ? "+" : "") + (t.ev * 100).toFixed(1) + "%"), h("span", "lv-sub", "  šansa " + Math.round(t.p * 100) + "%")) }
    else tip.textContent = "Nema value tipa za ovaj meč u ovom trenutku.";
    c.append(tip);
    const b = h("button", "lv-why", openId === m.id ? "Sakrij" : "Zašto?"); b.type = "button";
    b.onclick = () => { openId = openId === m.id ? null : m.id; paintPanel() };
    c.append(b);
    if (openId === m.id) {
      const u = h("ul"); (m.why || []).forEach(x => u.append(h("li", "", x))); c.append(u);
      if ((m.alts || []).length) c.append(h("div", "lv-sub", "Alternative: " + m.alts.map(a => a.label + " @ " + a.odds + " (EV " + (a.ev * 100).toFixed(1) + "%)").join("; ")));
      const k = h("ul", "lv-risk"); (m.risk || []).forEach(x => k.append(h("li", "", x))); c.append(k);
    }
    return c;
  }
  function paintPanel() {
    if (!panel) return;
    const p = panel.firstChild, a = age(), L = rows();
    p.replaceChildren();
    const hd = h("div", "lv-head"), x = h("button", "lv-x", "Zatvori"); x.type = "button"; x.onclick = hide;
    hd.append(h("h2", "", "Live Games"), h("span", "lv-sub", a != null ? "osveženo pre " + (a < 90 ? a + " s" : Math.round(a / 60) + " min") : ""), x);
    p.append(hd);
    if (!data) p.append(h("div", "lv-sub", "Live feed nije dostupan. Pokreni: py fb_live.py --loop 45 --serve (ili proveri da li je data/live.json u repou)."));
    else if (!L.length) p.append(h("div", "lv-sub", "Trenutno nema mečeva uživo koje feed prati."));
    L.forEach(m => p.append(card(m)));
    p.append(h("div", "lv-sub", "Tipovi su statistička procena iz nekalibrisanog modela, ne garancija. Kvote se menjaju za sekunde."));
  }
  function show() { if (shown) return; panel = h("div", "lv-ov"); panel.append(h("div", "lv-panel")); panel.addEventListener("click", e => { if (e.target === panel) hide() }); document.body.append(panel); shown = true; paintPanel(); load() }
  function hide() { if (panel) panel.remove(); panel = null; shown = false }
  function paint() { paintBtn(); if (shown) paintPanel() }
  function mount(sel) {
    if (btn) btn.remove();
    btn = h("button", "lv-btn", ""); btn.type = "button"; btn.onclick = show;
    const host = (sel && document.querySelector(sel)) || document.getElementById("qf") || document.getElementById("tabs");
    if (host) host.append(btn); else { btn.classList.add("lv-float"); document.body.append(btn) }
    paintBtn();
  }
  const st = document.createElement("style"); st.textContent = css; document.head.append(st);
  document.addEventListener("keydown", e => { if (e.key === "Escape") hide() });
  const start = () => { mount(); load(); setInterval(() => { if (!document.hidden) load() }, 30000) };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start) : start();
  window.LiveGames = { mount, show, hide, reload: load };
})();
