/* Radar GBM – interfaz (JavaScript sin dependencias). Todo el cálculo vive en el servidor. */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const NA = "Dato no disponible";
  const PER = { "1d": "Última sesión", "1s": "1 semana", "1m": "1 mes", "3m": "3 meses", "1a": "1 año" };
  const GROUP_ICON = { acciones: "Acciones", etfs: "ETFs", cripto: "Exposición cripto" };
  const CAT = { accion: "Acción", etf: "ETF", fibra: "FIBRA", cripto_ref: "Referencia cripto" };
  const RISK = { bajo: "Bajo", medio: "Medio", alto: "Alto", sin_dato: NA };
  const state = { status: null, report: null, period: "1m", compare: [], filters: { period: "1m", sort: "ret_mxn", dir: "desc", page: 1 }, lastReportId: null };

  // ---------------------------------------------------------------- utilidades
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { return null; } }
  // ---------------------------------------------------------------- modo estático (GitHub Pages): lee JSON y filtra en el navegador
  const STATIC = window.RADAR_STATIC || null;
  const cacheJ = {};
  async function getJ(file) {
    if (!cacheJ[file]) cacheJ[file] = fetch("data/" + file + "?v=" + (state.stamp || "")).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
    try { return await cacheJ[file]; } catch (e) { delete cacheJ[file]; throw e; }
  }
  function lsJ(k, def) { try { return JSON.parse(store(k) || "null") ?? def; } catch (e) { return def; } }
  function localSettings(base) { return Object.assign({}, base, lsJ("radar_prefs", {})); }
  function budgetInfo(m, budget) {
    if (m.category === "cripto_ref") return { texto: "Referencia externa: no se compra en GBM", alcanza: false };
    const price = m.price_mxn;
    if (m.gbm_modality === "Trading USA") return { texto: "GBM indica que Trading USA permite fracciones; verifica el mínimo de este instrumento en la app.", alcanza: budget ? true : null, fracciones: true };
    if (price == null) return { texto: NA, alcanza: null };
    if (!budget) return { texto: `1 título ≈ ${price.toLocaleString("es-MX", { maximumFractionDigits: 2 })} MXN (acciones completas en Trading MX según GBM)`, alcanza: null };
    const n = Math.floor(budget / price);
    return { texto: n ? `Alcanza para ${n} título(s) completos de ≈ ${price.toFixed(2)} MXN` : `No alcanza: 1 título ≈ ${price.toFixed(2)} MXN`, alcanza: n > 0 };
  }
  function smaArr(v, n) { const out = []; let s = 0; v.forEach((x, i) => { s += x || 0; if (i >= n) s -= v[i - n] || 0; out.push(i >= n - 1 ? s / n : null); }); return out; }
  async function staticApi(path, opts) {
    const method = (opts && opts.method) || "GET";
    const u = new URL(path, location.href), p = u.pathname, q = u.searchParams;
    const favs = lsJ("radar_favs", []);
    if (p === "/api/status") {
      const s = await fetch("data/status.json?t=" + Date.now()).then((r) => r.json());
      if (state.stamp && state.stamp !== s.generado) Object.keys(cacheJ).forEach((k) => delete cacheJ[k]);
      state.stamp = s.generado;
      return Object.assign({}, s, { espera_manual_seg: 0, en_curso: false, proteccion_acceso: null });
    }
    if (p === "/api/report/latest") return getJ("report_latest.json");
    if (p === "/api/reports") return getJ("reports.json");
    if (p.startsWith("/api/reports/")) return getJ("reports/" + p.split("/").pop() + ".json");
    if (p === "/api/runs" && method === "POST") {
      window.open(`https://github.com/${STATIC.repo}/actions/workflows/${STATIC.workflow}`, "_blank", "noopener");
      return { ok: true, mensaje: "Se abrió GitHub: pulsa 'Run workflow'. El reporte nuevo aparece aquí en 5 a 30 minutos." };
    }
    if (p === "/api/runs") return getJ("runs.json");
    if (p === "/api/coverage") return getJ("coverage.json");
    if (p === "/api/settings" && method === "PUT") {
      const b = JSON.parse(opts.body || "{}"), base = await getJ("settings.json");
      const prefs = { presupuesto_mxn: b.presupuesto_mxn ? +b.presupuesto_mxn : null, moneda: b.moneda, horizonte: b.horizonte, notificaciones: b.notificaciones };
      store("radar_prefs", JSON.stringify(prefs));
      const errores = {};
      if (b.horario && b.horario !== base.horario) errores.horario = "La hora se cambia en el archivo ajustes.json de tu repositorio de GitHub (ver Ajustes).";
      if (b.solo_verificados !== undefined && b.solo_verificados !== base.solo_verificados) errores.solo_verificados = "Esta opción se cambia en ajustes.json.";
      return { ok: !Object.keys(errores).length, errores, config: localSettings(base), _status: 200 };
    }
    if (p === "/api/settings") return localSettings(await getJ("settings.json"));
    if (p === "/api/favorites") { const all = (await getJ("instruments.json")).items; return { favoritos: all.filter((x) => favs.includes(x.id)).map((x) => ({ id: x.id, ticker: x.ticker, name: x.name, currency: x.currency })) }; }
    if (p.startsWith("/api/favorites/")) {
      const id = +p.split("/").pop(); const f = favs.filter((x) => x !== id); if (method === "POST") f.push(id);
      store("radar_favs", JSON.stringify(f)); return { ok: true };
    }
    if (p === "/api/catalog/import") return { error: "En la versión gratis, agrega tu lista como data/catalogo_gbm.csv en tu repositorio de GitHub (columnas ticker,mercado). Se aplica en la siguiente ejecución." };
    if (p === "/api/instruments") {
      const data = await getJ("instruments.json"); const per = q.get("period") || "1m";
      const qt = (q.get("q") || "").toLowerCase(), cat = q.get("category"), mkt = q.get("market"), cur = q.get("currency"), risk = q.get("risk"), gbm = q.get("gbm");
      const budget = +q.get("budget") || null;
      let out = [];
      for (const m of data.items) {
        if (qt && !(m.ticker || "").toLowerCase().includes(qt) && !(m.name || "").toLowerCase().includes(qt) && !(m.isin || "").toLowerCase().includes(qt)) continue;
        if (cat) { if (cat === "cripto" ? m.crypto_exposure === "ninguna" : m.category !== cat) continue; }
        if (mkt && m.gbm_modality !== mkt) continue;
        if (cur && m.currency !== cur) continue;
        if (risk && m.risk_level !== risk) continue;
        if (gbm && m.gbm_status !== gbm) continue;
        if (q.get("favorites") === "1" && !favs.includes(m.id)) continue;
        const b = budgetInfo(m, budget);
        if (budget && b.alcanza === false) continue;
        out.push(Object.assign({}, m, { ret: m.ret_total[per], ret_mxn: m.ret_mxn[per], favorito: favs.includes(m.id), presupuesto: b }));
      }
      const sort = q.get("sort") || "ret_mxn", desc = (q.get("dir") || "desc") === "desc";
      const pres = out.filter((x) => x[sort] != null), miss = out.filter((x) => x[sort] == null);
      const numeric = pres.every((x) => typeof x[sort] === "number");
      pres.sort((a, b) => { const r = numeric ? a[sort] - b[sort] : String(a[sort]).localeCompare(String(b[sort]), "es"); return desc ? -r : r; });
      out = pres.concat(miss);
      const page = Math.max(1, +q.get("page") || 1), size = Math.min(200, Math.max(10, +q.get("page_size") || 50));
      return { items: out.slice((page - 1) * size, page * size), total: out.length, pagina: page, tam: size, reporte: data.reporte, periodo: per };
    }
    if (p.startsWith("/api/instruments/")) {
      const d = JSON.parse(JSON.stringify(await getJ("inst/" + p.split("/").pop() + ".json")));
      const s = d.serie; s.sma20 = smaArr(s.precio, 20); s.sma50 = smaArr(s.precio, 50); s.sma200 = smaArr(s.precio, 200);
      d.favorito = favs.includes(d.instrumento.id);
      if (d.metricas) d.presupuesto = budgetInfo(d.metricas, localSettings({}).presupuesto_mxn);
      return d;
    }
    if (p === "/api/compare") {
      const ids = (q.get("ids") || "").split(",").filter(Boolean).slice(0, 3);
      const ser = await Promise.all(ids.map(async (id) => { const d = await getJ("inst/" + id + ".json"); const n = d.serie.fechas.length, k = Math.max(0, n - 253); return { id: +id, ticker: d.instrumento.ticker, name: d.instrumento.name, currency: d.instrumento.currency, f: d.serie.fechas.slice(k), v: d.serie.ajustado.slice(k), metricas: d.metricas }; }));
      if (ser.length) {
        const start = ser.map((o) => o.f[0]).sort().pop();
        ser.forEach((o) => { let i0 = 0; o.f.forEach((d, i) => { if (d <= start) i0 = i; }); const v0 = o.v[i0]; o.base = start; o.norm = o.f.map((d, i) => [d, o.v[i] / v0 * 100]).filter((x) => x[0] >= start && v0); delete o.f; delete o.v; });
      }
      return { series: ser, nota: "Base 100 en la primera fecha común; rendimiento total en la moneda de cada instrumento." };
    }
    throw new Error("Ruta no disponible en la versión estática: " + p);
  }

  async function api(path, opts) {
    if (STATIC) { const r = await staticApi(path, opts || {}); if (r && r._status === undefined) r._status = 200; return r; }
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    const tok = store("radar_token");
    if (tok) headers["X-Access-Token"] = tok;
    if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
    const r = await fetch(path, Object.assign({}, opts, { headers }));
    if (r.status === 401 && !path.startsWith("/api/auth")) { askToken(); throw new Error("auth"); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok && !data.mensaje && !data.error && !data.errores) throw new Error("HTTP " + r.status);
    data._status = r.status;
    return data;
  }
  function pct(x, signed) {
    if (x === null || x === undefined || Number.isNaN(x)) return `<span class="muted">${NA}</span>`;
    const v = (x * 100).toFixed(1);
    if (signed === false) return v + "%";
    const cls = x > 0 ? "up" : x < 0 ? "down" : "";
    const arrow = x > 0 ? "▲" : x < 0 ? "▼" : "";
    return `<span class="${cls}">${arrow} ${x > 0 ? "+" : ""}${v}%</span>`;
  }
  function num(x, d) { return x === null || x === undefined ? `<span class="muted">${NA}</span>` : Number(x).toLocaleString("es-MX", { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d }); }
  function money(x, cur) {
    if (x === null || x === undefined) return `<span class="muted">${NA}</span>`;
    if (x >= 1e9) return (x / 1e9).toLocaleString("es-MX", { maximumFractionDigits: 1 }) + " mil mill. " + cur;
    if (x >= 1e6) return (x / 1e6).toLocaleString("es-MX", { maximumFractionDigits: 1 }) + " mill. " + cur;
    return num(x) + " " + cur;
  }
  function dt(iso) {
    if (!iso) return NA;
    const d = new Date(iso);
    if (isNaN(d)) return esc(iso);
    return d.toLocaleString("es-MX", { timeZone: "America/Matamoros", dateStyle: "medium", timeStyle: "short" }) + " (hora de Matamoros)";
  }
  function day(s) { if (!s) return NA; const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString("es-MX", { weekday: "short", day: "numeric", month: "short", year: "numeric" }); }
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4500); }
  function gbmBadge(s) {
    if (s === "verificado") return '<span class="badge ok" title="Confirmado con una fuente de GBM o tu catálogo importado">Verificado en GBM</span>';
    if (s === "referencia") return '<span class="badge" title="Solo referencia de mercado">Referencia externa</span>';
    return '<span class="badge warn" title="Cotiza en un mercado que GBM ofrece, pero no se confirmó individualmente">Por verificar en GBM</span>';
  }
  function demoBadge() { return state.status && state.status.modo === "demo" ? '<span class="badge demo">DEMO</span> ' : ""; }

  // ---------------------------------------------------------------- acceso
  function askToken() {
    if ($("#tokenForm")) return;
    openModal(`<h2 id="modalTitle">Clave de acceso</h2><p>Esta instalación está protegida. Escribe la clave de acceso (APP_ACCESS_TOKEN) que configuraste en el servidor.</p>
      <form id="tokenForm" class="row"><input type="password" id="tokenIn" autocomplete="current-password" style="max-width:320px"><button class="btn primary">Entrar</button></form>`);
    $("#tokenForm").onsubmit = async (e) => {
      e.preventDefault();
      const r = await fetch("/api/auth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: $("#tokenIn").value }) });
      if (r.ok) { store("radar_token", $("#tokenIn").value); closeModal(); boot(); } else toast("Clave incorrecta.");
    };
  }

  // ---------------------------------------------------------------- modal
  function openModal(html) { $("#modalBody").innerHTML = html; $("#modal").hidden = false; document.body.style.overflow = "hidden"; }
  function closeModal() { $("#modal").hidden = true; document.body.style.overflow = ""; }
  $("#modalClose").onclick = closeModal;
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  // ---------------------------------------------------------------- gráfica SVG (línea, una escala, tooltip)
  function lineChart(el, series, opts) {
    opts = opts || {};
    const W = 800, H = opts.height || 300, P = { l: 56, r: 16, t: 12, b: 28 };
    const all = series.flatMap((s) => s.points.filter((p) => p[1] != null));
    if (!all.length) { el.innerHTML = `<div class="empty">${NA}</div>`; return; }
    const xs = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p[0])))).sort();
    const xi = new Map(xs.map((x, i) => [x, i]));
    let lo = Math.min(...all.map((p) => p[1])), hi = Math.max(...all.map((p) => p[1]));
    if (lo === hi) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
    const X = (x) => P.l + (xi.get(x) / Math.max(1, xs.length - 1)) * (W - P.l - P.r);
    const Y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const ticks = 5, fmt = opts.fmt || ((v) => v.toLocaleString("es-MX", { maximumFractionDigits: v < 10 ? 2 : 0 }));
    let g = "";
    for (let i = 0; i <= ticks; i++) {
      const v = lo + ((hi - lo) * i) / ticks, y = Y(v);
      g += `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="var(--grid)" stroke-width="1"/><text x="${P.l - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${fmt(v)}</text>`;
    }
    const nl = Math.min(5, xs.length);
    for (let i = 0; i < nl; i++) {
      const x = xs[Math.round((i * (xs.length - 1)) / Math.max(1, nl - 1))];
      g += `<text x="${X(x)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === nl - 1 ? "end" : "middle"}" font-size="11" fill="var(--muted)">${esc(x.slice(2))}</text>`;
    }
    if (opts.baseline != null && opts.baseline > lo && opts.baseline < hi) g += `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(opts.baseline)}" y2="${Y(opts.baseline)}" stroke="var(--muted)" stroke-dasharray="3 3"/>`;
    let paths = "";
    series.forEach((s) => {
      let d = "", pen = false;
      s.points.forEach((p) => { if (p[1] == null) { pen = false; return; } d += (pen ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1); pen = true; });
      paths += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round"/>`;
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "Gráfica")}">${g}${paths}<line class="cross" x1="0" x2="0" y1="${P.t}" y2="${H - P.b}" stroke="var(--text-2)" stroke-width="1" opacity="0"/><rect x="${P.l}" y="${P.t}" width="${W - P.l - P.r}" height="${H - P.t - P.b}" fill="transparent"/></svg><div class="tip" hidden></div>
      <div class="legend">${series.map((s) => `<span><i class="${s.dash ? "dash" : ""}" style="border-color:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
    const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cross = el.querySelector(".cross");
    const maps = series.map((s) => new Map(s.points));
    function move(ev) {
      const r = svg.getBoundingClientRect();
      const cx = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) * (W / r.width);
      const i = Math.max(0, Math.min(xs.length - 1, Math.round(((cx - P.l) / (W - P.l - P.r)) * (xs.length - 1))));
      const x = xs[i];
      cross.setAttribute("x1", X(x)); cross.setAttribute("x2", X(x)); cross.setAttribute("opacity", ".5");
      tip.hidden = false;
      tip.innerHTML = `<b>${esc(x)}</b><br>` + series.map((s, k) => { const v = maps[k].get(x); return `<span style="color:${s.color}">■</span> ${esc(s.name)}: ${v == null ? NA : fmt(v)}`; }).join("<br>");
      const px = (X(x) / W) * r.width;
      tip.style.left = Math.min(r.width - tip.offsetWidth - 4, Math.max(0, px + 10)) + "px";
      tip.style.top = "8px";
    }
    svg.addEventListener("mousemove", move); svg.addEventListener("touchmove", move, { passive: true });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("opacity", "0"); });
  }
  const C = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  // ---------------------------------------------------------------- estado general
  async function loadStatus() {
    const s = await api("/api/status");
    state.status = s;
    $("#modeLine").innerHTML = (s.modo === "demo" ? '<span class="badge demo">DEMOSTRACIÓN · datos sintéticos</span>' : "Datos reales · cierres diarios (EOD)") + ` · ${esc(s.zona_horaria)}`;
    const pill = $("#runPill");
    if (s.en_curso) pill.textContent = "Actualizando…";
    else if (s.fallo_reciente) pill.innerHTML = "⚠ Falló la última actualización";
    else if (s.ultima_ejecucion_exitosa) pill.textContent = "Actualizado: " + new Date(s.ultima_ejecucion_exitosa.finished_at).toLocaleString("es-MX", { timeZone: "America/Matamoros", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    else pill.textContent = "Sin ejecuciones";
    $("#btnRun").disabled = s.en_curso || s.espera_manual_seg > 0;
    $("#btnRun").title = s.espera_manual_seg > 0 ? `Disponible en ${Math.ceil(s.espera_manual_seg / 60)} min (límite del proveedor)` : "Usa el mismo proceso que la ejecución diaria";
    const b = [];
    if (s.modo === "demo") b.push(`<div class="banner demo"><b>Modo demostración.</b> Las cifras son sintéticas (tickers que empiezan con DEMO) y sirven solo para ver cómo funciona la página. Para ver datos reales configura <code>EODHD_API_KEY</code> en el servidor (ver Ajustes → Fuentes).</div>`);
    if (s.fallo_reciente) b.push(`<div class="banner err"><b>La última actualización falló</b> (${esc((s.ultima_ejecucion || {}).error || "")}). Se muestra el último reporte válido${s.ultimo_reporte ? " del " + day(s.ultimo_reporte.report_date) : ""}. Revisa Historial → Ejecuciones.</div>`);
    if (STATIC) b.push(`<div class="banner info">Versión gratuita: datos de Yahoo Finance (fuente no oficial, uso personal) y página pública. Tus favoritos y preferencias se guardan solo en este navegador.</div>`);
    if (s.proteccion_acceso === false && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") b.push(`<div class="banner warn">Esta página no tiene clave de acceso. Configura <code>APP_ACCESS_TOKEN</code> en el servidor para que solo tú puedas usarla.</div>`);
    $("#banners").innerHTML = b.join("");
    if (state.lastReportId && s.ultimo_reporte && s.ultimo_reporte.id !== state.lastReportId) {
      notifyBrowser(`Reporte del ${s.ultimo_reporte.report_date} listo`);
      state.report = null;
      route();
    }
    state.lastReportId = s.ultimo_reporte ? s.ultimo_reporte.id : null;
    return s;
  }
  function notifyBrowser(text) {
    toast(text);
    try { if (store("radar_notif") === "1" && "Notification" in window && Notification.permission === "granted") new Notification("Radar GBM", { body: text }); } catch (e) { /* sin notificaciones */ }
  }
  async function loadReport(force) {
    if (!state.report || force) state.report = (await api("/api/report/latest")).reporte;
    return state.report;
  }
  $("#btnRun").onclick = async () => {
    $("#btnRun").disabled = true;
    try {
      const r = await api("/api/runs", { method: "POST" });
      toast(r.mensaje || "Listo");
      const poll = setInterval(async () => { const s = await loadStatus(); if (!s.en_curso) { clearInterval(poll); state.report = null; route(); } }, 4000);
    } catch (e) { toast("No se pudo iniciar la actualización."); }
  };

  // ---------------------------------------------------------------- vistas
  const views = {};

  views.resumen = async (el) => {
    const s = state.status, r = await loadReport();
    if (!r) { el.innerHTML = `<div class="card empty">Aún no hay reportes. Pulsa <b>Actualizar ahora</b> o espera la ejecución programada (${esc(s.horario)} h).</div>`; return; }
    const cut = r.data_cutoff || {}, cov = r.cobertura || {}, ch = r.cambios || {};
    const mk = (k) => { const c = cut[k] || {}; return `<dt>${esc(c.nombre || k)}</dt><dd>${esc(c.estado_mercado || "")} · <span class="small muted">último cierre: ${day(c.ultimo_dato)}${c.al_dia ? "" : " · <b>atrasado</b>"}</span></dd>`; };
    const refs = r.referencias || {};
    const refRow = (k, label) => { const x = refs[k] || {}; return `<tr><td>${esc(label)}<div class="tsub">${esc(x.simbolo || "")} · ${esc(x.moneda || "")}</div></td><td class="num">${pct(x.ret_1d)}</td><td class="num">${pct(x.ret_1m)}</td></tr>`; };
    el.innerHTML = `
      <div class="row spread"><h2>${demoBadge()}Reporte del ${day(r.report_date)} <span class="muted small">v${r.version}</span></h2><span class="small muted">Generado ${dt(r.generated_at)}</span></div>
      ${r.datos_nuevos === false ? `<div class="banner info" style="margin:0 0 12px">${esc(ch.texto)}</div>` : ""}
      <div class="grid">
        <div class="card"><h3>Estado del proceso</h3><dl class="kv">
          <dt>Última ejecución exitosa</dt><dd>${s.ultima_ejecucion_exitosa ? dt(s.ultima_ejecucion_exitosa.finished_at) : NA}</dd>
          <dt>Próxima ejecución</dt><dd>${dt(s.proxima_ejecucion)}</dd>
          <dt>Estado</dt><dd>${esc(s.estado_programacion)}</dd>
          <dt>Horario diario</dt><dd>${esc(s.horario)} h (${esc(s.zona_horaria)})</dd></dl></div>
        <div class="card"><h3>Mercados y corte de datos</h3><dl class="kv stack">${mk("MX")}${mk("US")}${mk("CRYPTO")}</dl></div>
        <div class="card"><h3>Cobertura del análisis</h3><dl class="kv">
          <dt>Instrumentos identificados</dt><dd>${num(cov.identificados, 0)}</dd>
          <dt>Verificados en GBM</dt><dd>${num(cov.verificados_gbm, 0)}</dd>
          <dt>Analizados con datos</dt><dd>${num(cov.analizados, 0)}</dd>
          <dt>Con puntuación</dt><dd>${num(cov.con_puntuacion, 0)}</dd>
          <dt>Excluidos de rankings</dt><dd>${num(cov.excluidos_de_rankings, 0)}</dd>
          <dt>Sin historial todavía</dt><dd>${num(cov.sin_historial_aun, 0)}</dd></dl>
          <p class="small muted">No se puede afirmar que se analiza "todo GBM": GBM no publica su catálogo. <a href="#aprende">¿Por qué?</a></p></div>
        <div class="card"><h3>Tipo de cambio</h3><div class="big">${r.fx && r.fx.valor ? num(r.fx.valor, 4) + " MXN" : NA}</div>
          <div class="small muted">por dólar · ${esc((r.fx || {}).fuente || "")} · ${day((r.fx || {}).fecha)}</div>
          <h3 style="margin-top:12px">Amplitud del día</h3><div class="small">${r.amplitud && r.amplitud.total ? `${r.amplitud.suben} suben · ${r.amplitud.bajan} bajan (de ${r.amplitud.total} líquidos, última sesión)` : NA}</div></div>
      </div>
      <div class="grid section">
        <div class="card"><h3>Referencias del mercado</h3><div class="tablewrap"><table><thead><tr><th>Referencia</th><th class="num">Última sesión</th><th class="num">1 mes</th></tr></thead><tbody>
          ${refRow("MX_LOCAL", "México (IPC)")}${refRow("MX_SIC", "S&P 500 en el SIC (MXN)")}${refRow("US", "S&P 500 (USD)")}${refRow("CRYPTO", "Bitcoin (referencia externa)")}</tbody></table></div>
          <p class="small muted">Rendimiento total en la moneda de cada referencia.</p></div>
        <div class="card"><h3>Qué cambió desde ayer</h3>${changesHtml(ch)}</div>
      </div>
      <div class="card section"><div class="row spread"><h3>Para investigar hoy</h3><a href="#rankings">Ver explicación completa →</a></div>
        <div class="grid">${Object.entries(r.investigar || {}).map(([g, v]) => `<div><b>${GROUP_ICON[g]}</b> <span class="small muted">(${v.elegibles} comparables)</span>
          ${v.items.length ? `<ol class="tight">${v.items.map((x) => `<li><a href="#" data-open="${x.id}">${esc(x.ticker)}</a> – ${esc(x.name)} <b>${x.score.total.toFixed(0)}</b>/100</li>`).join("")}</ol>` : `<p class="small muted">${esc(v.aviso || "Sin instrumentos suficientes.")}</p>`}</div>`).join("")}</div></div>
      <div class="card section"><h3>Movimientos importantes y riesgos elevados</h3>${alertsHtml(r.alertas)}</div>
      ${(r.avisos || []).length ? `<details class="card section"><summary>Avisos técnicos de la última ejecución (${r.avisos.length})</summary><ul class="tight small">${r.avisos.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>` : ""}`;
  };

  function changesHtml(ch) {
    if (!ch || !ch.hay_reporte_anterior) return `<p class="small">${esc((ch && ch.texto) || "Primer reporte.")}</p>`;
    const parts = [`<p class="small muted">${esc(ch.texto)}</p>`];
    Object.entries(ch.investigar || {}).forEach(([g, v]) => {
      if (v.entran.length || v.salen.length) parts.push(`<div class="small"><b>${GROUP_ICON[g]}:</b> ${v.entran.length ? "entran " + v.entran.map(esc).join(", ") : ""}${v.entran.length && v.salen.length ? " · " : ""}${v.salen.length ? "salen " + v.salen.map(esc).join(", ") : ""}</div>`);
    });
    const t = (ch.top10 || {})["1m"];
    if (t && (t.entran.length || t.salen.length)) parts.push(`<div class="small"><b>Top 10 de 1 mes:</b> entran ${t.entran.map(esc).join(", ") || "ninguno"}; salen ${t.salen.map(esc).join(", ") || "ninguno"}</div>`);
    if ((ch.alertas_nuevas || []).length) parts.push(`<div class="small"><b>Alertas nuevas:</b> ${ch.alertas_nuevas.map(esc).join(", ")}</div>`);
    if ((ch.favoritos || []).length) parts.push(`<div class="small"><b>Tus favoritos (última sesión):</b> ${ch.favoritos.map((f) => `${esc(f.ticker)} ${pct(f.ret_1d)}`).join(" · ")}</div>`);
    if (parts.length === 1) parts.push('<p class="small">Sin cambios en los rankings.</p>');
    return parts.join("");
  }
  function alertsHtml(al) {
    if (!al || !al.length) return `<p class="small muted">Sin alertas en este reporte.</p>`;
    return `<div class="tablewrap"><table><thead><tr><th>Instrumento</th><th>Alerta</th></tr></thead><tbody>${al.map((a) => `<tr class="clickable" data-open="${a.id}"><td><span class="tname">${a.favorito ? "★ " : ""}${esc(a.ticker)}</span><div class="tsub">${esc(a.name)}</div></td><td>${a.alertas.map((x) => `<div>${x.tipo === "riesgo" ? "⚠ " : x.tipo === "movimiento" ? "↕ " : "• "}${esc(x.texto)}</div>`).join("")}</td></tr>`).join("")}</tbody></table></div>
      <p class="small muted">Umbrales orientativos (no son diagnósticos): movimiento diario mayor a 5% o a 3 veces su variación normal, volumen 3 veces su promedio, volatilidad anual mayor a 60%, caída máxima mayor a 40%. No se atribuye ningún movimiento a una noticia.</p>`;
  }

  views.rankings = async (el) => {
    const r = await loadReport();
    if (!r) { el.innerHTML = '<div class="card empty">Aún no hay reportes.</div>'; return; }
    const p = state.period, t = r.top10[p];
    el.innerHTML = `
      <div class="card"><div class="row spread"><h2>${demoBadge()}Top 10 por rendimiento observado</h2><div class="chips" id="perChips">${Object.keys(PER).map((k) => `<button class="chip ${k === p ? "on" : ""}" data-per="${k}" type="button">${PER[k]}</button>`).join("")}</div></div>
        <p class="small muted">Rendimiento total en pesos (MXN) durante ${esc(t.periodo)}, entre ${t.candidatos} instrumentos líquidos con datos al día. Sin ETFs apalancados. Una cotización por instrumento. <b>Lo que ya subió no garantiza que siga subiendo.</b></p>
        ${t.items.length ? `<div class="tablewrap"><table><thead><tr><th>#</th><th>Instrumento</th><th class="num">Rend. MXN</th><th class="num hide-sm">En su moneda</th><th class="hide-sm">Periodo</th><th class="hide-sm">Riesgo</th></tr></thead><tbody>
          ${t.items.map((x, i) => `<tr class="clickable" data-open="${x.id}"><td>${i + 1}</td><td><span class="tname">${esc(x.ticker)}</span> <span class="badge">${CAT[x.category] || ""}</span><div class="tsub">${esc(x.name)} · ${esc(x.gbm_modality)}</div></td><td class="num">${pct(x.ret_mxn[p])}</td><td class="num hide-sm">${pct(x.ret_total[p])} ${esc(x.currency)}</td><td class="hide-sm small">${x.fechas ? esc(x.fechas[0]) + " → " + esc(x.fechas[1]) : NA}</td><td class="hide-sm">${RISK[x.risk_level] || NA}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No hay suficientes datos para este periodo.</div>`}
      </div>
      ${Object.entries(r.investigar).map(([g, v]) => `<div class="section"><h2>Top 3 · ${esc(v.nombre)}</h2><p class="small muted">${v.elegibles} instrumentos comparables cumplen los criterios. Puntuación 0–100 = comparación dentro de este grupo, <b>no</b> probabilidad de ganar.</p>
        ${v.items.length ? v.items.map((x) => highlightCard(x)).join("") : `<div class="card empty">${esc(v.aviso || "Sin instrumentos suficientes con datos fiables. No se rellenan espacios.")}</div>`}</div>`).join("")}
      <p class="small muted section">${esc(r.rendimiento_mostrado)} <a href="#aprende">Metodología completa</a>.</p>`;
    el.querySelectorAll("[data-per]").forEach((b) => (b.onclick = () => { state.period = b.dataset.per; views.rankings(el); }));
  };

  function highlightCard(x) {
    const e = x.explicacion, c = x.score.componentes;
    const det = x.detalle_categoria && x.detalle_categoria.datos && Object.keys(x.detalle_categoria.datos).length
      ? `<details open><summary>Datos de la categoría</summary><dl class="kv small">${Object.entries(x.detalle_categoria.datos).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl><div class="small muted">Fuente: ${esc(x.detalle_categoria.fuente || NA)}</div></details>` : "";
    const news = (x.noticias || []).length ? `<details><summary>Noticias y próximos eventos</summary><ul class="tight small">${x.noticias.map((n) => `<li>${esc(n.date || "")} · ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)} <span class="muted">(${esc(n.source)})</span></li>`).join("")}</ul><p class="small muted">No se atribuye automáticamente un movimiento de precio a estas noticias.</p></details>` : "";
    return `<div class="card highlight">
      <div class="hl-head"><div><span class="muted">#${x.score.posicion}</span> <a href="#" data-open="${x.id}" class="tname">${esc(x.ticker)}</a> · ${esc(x.name)}<div class="row small" style="margin-top:4px">${gbmBadge(x.gbm_status)} <span class="badge">${esc(x.gbm_modality)}</span> <span class="badge">${esc(x.currency)}</span></div></div>
        <div class="score">${x.score.total.toFixed(0)}<small>/100</small></div></div>
      <div class="bars">${["rendimiento", "tendencia", "riesgo", "liquidez", "calidad"].map((k) => `<div class="bar"><span>${k[0].toUpperCase() + k.slice(1)}</span><div class="track"><div class="fill" style="width:${c[k]}%"></div></div><span class="num">${c[k].toFixed(0)}</span></div>`).join("")}</div>
      <p><b>Qué es:</b> ${esc(e.que_es)}</p>
      <p><b>Por qué aparece:</b> ${esc(e.por_que)}</p>
      <details open><summary>Datos que lo respaldan</summary><ul class="tight small">${e.datos.map((d) => `<li>${esc(d)}</li>`).join("")}</ul></details>
      <details open><summary>Principales riesgos</summary><ul class="tight small">${e.riesgos.map((d) => `<li>${esc(d)}</li>`).join("")}</ul></details>
      <p class="small"><b>Qué cambió:</b> ${esc(e.cambio)}</p>
      <p class="small"><b>Horizonte:</b> ${esc(e.horizonte)}</p>
      ${det}${news}
      <div class="small muted">Datos al ${day(x.last_date)} · cierre (EOD).</div></div>`;
  }

  views.explorar = async (el) => {
    const f = state.filters;
    el.innerHTML = `<h2>${demoBadge()}Explorar instrumentos</h2>
      <div class="filters">
        <label class="f">Buscar<input id="fq" type="search" placeholder="Ticker, nombre o ISIN" value="${esc(f.q || "")}"></label>
        <label class="f">Categoría<select id="fcat"><option value="">Todas</option><option value="accion">Acciones</option><option value="etf">ETFs</option><option value="fibra">FIBRAS</option><option value="cripto">Exposición cripto</option><option value="cripto_ref">Referencias cripto</option></select></label>
        <label class="f">Mercado (modalidad GBM)<select id="fmkt"><option value="">Todos</option><option>Trading MX</option><option>Trading MX (SIC)</option><option>Trading USA</option><option>Referencia externa</option></select></label>
        <label class="f">Moneda<select id="fcur"><option value="">Todas</option><option>MXN</option><option>USD</option></select></label>
        <label class="f">Riesgo (volatilidad)<select id="frisk"><option value="">Todos</option><option value="bajo">Bajo (&lt;20%)</option><option value="medio">Medio (20–40%)</option><option value="alto">Alto (&gt;40%)</option></select></label>
        <label class="f">Periodo<select id="fper">${Object.entries(PER).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
        <label class="f">Verificación GBM<select id="fgbm"><option value="">Todos</option><option value="verificado">Solo verificados</option><option value="pendiente">Por verificar</option></select></label>
        <label class="f">Presupuesto<select id="fbud"><option value="">Sin filtro</option><option value="1">Solo lo que alcanza con mi presupuesto</option></select></label>
      </div>
      <div id="tbl"></div>`;
    [["fcat", "category"], ["fmkt", "market"], ["fcur", "currency"], ["frisk", "risk"], ["fper", "period"], ["fgbm", "gbm"], ["fbud", "bud"]].forEach(([id, k]) => { const s = $("#" + id); s.value = f[k] || (k === "period" ? "1m" : ""); s.onchange = () => { f[k] = s.value; f.page = 1; loadTable(); }; });
    let tq; $("#fq").oninput = (e) => { clearTimeout(tq); tq = setTimeout(() => { f.q = e.target.value; f.page = 1; loadTable(); }, 300); };
    loadTable();
  };
  async function loadTable() {
    const f = state.filters, s = await api("/api/settings");
    if (f.bud && !s.presupuesto_mxn) { toast("Define tu presupuesto en Ajustes."); }
    const qs = new URLSearchParams({ q: f.q || "", category: f.category || "", market: f.market || "", currency: f.currency || "", risk: f.risk || "", period: f.period || "1m", gbm: f.gbm || "", sort: f.sort, dir: f.dir, page: f.page, page_size: 50 });
    if (f.bud && s.presupuesto_mxn) qs.set("budget", s.presupuesto_mxn);
    const d = await api("/api/instruments?" + qs);
    const useMxn = s.moneda !== "USD";
    const th = (k, label, cls) => `<th class="sortable ${cls || ""}" data-sort="${k}">${label}${f.sort === k ? (f.dir === "desc" ? " ▼" : " ▲") : ""}</th>`;
    const tbl = $("#tbl");
    if (!tbl) return;
    if (!d.items.length) { tbl.innerHTML = `<div class="card empty">Sin resultados con estos filtros.</div>`; return; }
    tbl.innerHTML = `<p class="small muted">${d.total} instrumentos · reporte del ${day(d.reporte.fecha)} · rendimiento total ${useMxn ? "convertido a MXN" : "en la moneda de cada instrumento"} (${PER[d.periodo]}).</p>
      <div class="tablewrap"><table><thead><tr><th></th>${th("ticker", "Instrumento")}${th(useMxn ? "ret_mxn" : "ret", "Rend.", "num")}${th("score", "Puntuación", "num hide-sm")}${th("vol63", "Volatilidad", "num hide-sm")}${th("max_dd", "Caída máx.", "num hide-sm")}${th("liq_avg_value_mxn", "Liquidez diaria", "num hide-sm")}${th("last_close", "Último precio", "num")}</tr></thead><tbody>
      ${d.items.map((x) => `<tr class="clickable" data-open="${x.id}"><td><button class="star ${x.favorito ? "on" : ""}" data-fav="${x.id}" aria-label="Favorito" type="button">${x.favorito ? "★" : "☆"}</button></td>
        <td><span class="tname">${esc(x.ticker)}</span> <span class="badge">${CAT[x.category] || ""}</span>${x.is_leveraged ? ' <span class="badge warn">Apalancado</span>' : ""}<div class="tsub">${esc(x.name)}</div><div class="tsub">${esc(x.gbm_modality)} · ${x.gbm_status === "verificado" ? "verificado" : x.gbm_status === "referencia" ? "referencia" : "por verificar"}${x.stale_sessions > 0 ? " · <b>dato atrasado</b>" : ""}</div>${f.bud ? `<div class="tsub">${esc(x.presupuesto.texto)}</div>` : ""}</td>
        <td class="num">${pct(useMxn ? x.ret_mxn : x.ret)}</td><td class="num hide-sm">${x.score == null ? `<span class="muted small" title="${x.excluido ? "Excluido de rankings" : ""}">—</span>` : x.score.toFixed(0)}</td>
        <td class="num hide-sm">${pct(x.vol63, false)}</td><td class="num hide-sm">${pct(x.max_dd)}</td><td class="num hide-sm">${money(x.liq_avg_value_mxn, "MXN")}</td>
        <td class="num">${num(x.last_close)} ${esc(x.currency)}<div class="tsub">${esc(x.last_date)}</div></td></tr>`).join("")}</tbody></table></div>
      <div class="pager"><button class="btn sm" id="pPrev" ${d.pagina <= 1 ? "disabled" : ""} type="button">← Anterior</button><span class="small">Página ${d.pagina} de ${Math.max(1, Math.ceil(d.total / d.tam))}</span><button class="btn sm" id="pNext" ${d.pagina * d.tam >= d.total ? "disabled" : ""} type="button">Siguiente →</button></div>`;
    tbl.querySelectorAll("th[data-sort]").forEach((h) => (h.onclick = () => { const k = h.dataset.sort; if (f.sort === k) f.dir = f.dir === "desc" ? "asc" : "desc"; else { f.sort = k; f.dir = k === "ticker" ? "asc" : "desc"; } loadTable(); }));
    $("#pPrev").onclick = () => { f.page--; loadTable(); };
    $("#pNext").onclick = () => { f.page++; loadTable(); };
  }

  // ---------------------------------------------------------------- ficha
  async function openInstrument(id) {
    openModal('<div class="empty">Cargando…</div>');
    const d = await api("/api/instruments/" + id);
    const i = d.instrumento, m = d.metricas;
    if (!m) { $("#modalBody").innerHTML = `<h2 id="modalTitle">${esc(i.ticker)} · ${esc(i.name)}</h2><p>${NA}: este instrumento aún no tiene historial analizado.</p>`; return; }
    const relRow = (p) => { const r = m.rel[p]; return r ? `<tr><td>${PER[p]}</td><td class="num">${pct(r.instrumento)}</td><td class="num">${pct(r.referencia)}</td><td class="num">${pct(r.diferencia)}</td></tr>` : `<tr><td>${PER[p]}</td><td colspan="3" class="muted">${NA}</td></tr>`; };
    const ex = d.explicacion;
    $("#modalBody").innerHTML = `
      <div class="row spread"><div><h2 id="modalTitle" style="margin:0">${i.is_demo ? '<span class="badge demo">DEMO</span> ' : ""}${esc(i.ticker)} · ${esc(i.name)}</h2>
        <div class="row small" style="margin-top:4px">${gbmBadge(i.gbm_status)}<span class="badge">${esc(i.gbm_modality)}</span><span class="badge">${CAT[i.category] || ""}</span><span class="badge">${esc(i.venue || i.exchange)} · ${esc(i.currency)}</span>${i.isin ? `<span class="badge">ISIN ${esc(i.isin)}</span>` : ""}${i.is_leveraged ? '<span class="badge warn">Apalancado/inverso</span>' : ""}</div></div>
        <div class="row"><button class="star ${d.favorito ? "on" : ""}" id="mFav" type="button" aria-label="Favorito">${d.favorito ? "★" : "☆"}</button><button class="btn sm" id="mCmp" type="button">Comparar</button></div></div>
      <div class="grid section">
        <div class="card"><div class="small muted">Último precio (${m.data_source && m.data_source.includes("CoinGecko") ? "00:00 UTC" : "cierre"})</div><div class="big">${num(m.last_close)} ${esc(i.currency)}</div>
          <div class="small">${day(m.last_date)} · ${pct(m.ret_total["1d"])} vs ${esc(m.prev_date)}</div>
          ${i.currency === "USD" && m.price_mxn ? `<div class="small muted">≈ ${num(m.price_mxn)} MXN (tipo de cambio ${num(m.fx_last, 4)} del ${esc(m.fx_date)})</div>` : ""}
          ${m.stale_sessions > 0 ? `<div class="small down">Sin dato de ${m.stale_sessions} sesión(es) esperada(s) (esperada: ${esc(m.expected_last_session)}).</div>` : ""}</div>
        <div class="card small"><b>Tipo de dato:</b> último cierre (EOD), no es tiempo real.<br><b>Fuente de precios:</b> ${esc(m.data_source)}<br><b>Modalidad GBM:</b> ${esc(d.modalidad_info || "")}<br><b>Verificación GBM:</b> ${esc(i.gbm_source || "Pendiente: confírmalo en la app de GBM.")}${i.gbm_verified_at ? " (" + esc(i.gbm_verified_at) + ")" : ""}
          ${d.presupuesto ? `<br><b>Presupuesto:</b> ${esc(d.presupuesto.texto)}` : ""}</div>
      </div>
      <div class="card section"><div class="row spread"><h3>Precio y medias móviles</h3><div class="chips" id="rng"><button class="chip" data-n="63" type="button">3 meses</button><button class="chip" data-n="126" type="button">6 meses</button><button class="chip on" data-n="252" type="button">1 año</button></div></div><div class="chart" id="pxChart"></div>
        <p class="small muted">Precio ajustado solo por splits (sin dividendos). Medias de 20, 50 y 200 sesiones.</p></div>
      <div class="grid section">
        <div class="card"><h3>Rendimientos</h3><div class="tablewrap"><table><thead><tr><th>Periodo</th><th class="num">Total (${esc(i.currency)})</th><th class="num">Solo precio</th><th class="num">En MXN</th></tr></thead><tbody>
          ${Object.keys(PER).map((p) => `<tr><td>${PER[p]}${m.period_dates[p] ? `<div class="tsub">${esc(m.period_dates[p][0])} → ${esc(m.period_dates[p][1])}</div>` : ""}</td><td class="num">${pct(m.ret_total[p])}</td><td class="num">${pct(m.ret_price[p])}</td><td class="num">${pct(m.ret_mxn[p])}</td></tr>`).join("")}</tbody></table></div>
          ${m.price_return_uncertain ? '<p class="small down">Se detectaron ajustes no explicados por splits registrados; el rendimiento solo por precio puede ser incierto.</p>' : ""}</div>
        <div class="card"><h3>Indicadores</h3><dl class="kv small">
          <dt>Tendencia</dt><dd>${m.trend_points == null ? NA : m.trend_points + " de 4 señales"}</dd>
          <dt>Media 20 / 50 / 200</dt><dd>${num(m.sma20)} / ${num(m.sma50)} / ${num(m.sma200)}</dd>
          <dt>Volatilidad anual (63 ses.)</dt><dd>${pct(m.vol63, false)} · riesgo ${RISK[m.risk_level]}</dd>
          <dt>Caída máxima (${m.dd_window} ses.)</dt><dd>${pct(m.max_dd)}</dd>
          <dt>Liquidez diaria promedio</dt><dd>${money(m.liq_avg_value_native, i.currency)}</dd>
          <dt>Volumen vs promedio 20</dt><dd>${m.vol_ratio == null ? NA : num(m.vol_ratio, 1) + " veces"}</dd>
          <dt>Sesiones con datos</dt><dd>${m.sessions} · cobertura ${pct(m.coverage, false)}</dd>
          <dt>Puntuación</dt><dd>${m.score ? m.score.total.toFixed(0) + "/100" + (m.score.posicion ? " (#" + m.score.posicion + " de " + m.score.elegibles + ")" : " (se muestra otra cotización del mismo instrumento en el ranking)") : "Sin puntuación"}</dd></dl>
          ${m.exclusions && m.exclusions.length ? `<p class="small"><b>Fuera de los rankings de investigación porque:</b> ${m.exclusions.map(esc).join("; ")}.</p>` : ""}</div>
      </div>
      <div class="card section"><h3>Comparación con su referencia (${esc(m.benchmark)}, misma moneda)</h3><div class="tablewrap"><table><thead><tr><th>Periodo</th><th class="num">Instrumento</th><th class="num">Referencia</th><th class="num">Diferencia</th></tr></thead><tbody>${relRow("1m")}${relRow("3m")}${relRow("1a")}</tbody></table></div><p class="small muted">Referencia general del mercado; puede no representar la exposición exacta del instrumento.</p></div>
      ${d.detalle_categoria && Object.keys(d.detalle_categoria.datos || {}).length ? `<div class="card section"><h3>Información de la categoría</h3><dl class="kv small">${Object.entries(d.detalle_categoria.datos).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl><div class="small muted">Fuente: ${esc(d.detalle_categoria.fuente || NA)}</div></div>` : ""}
      ${ex ? `<div class="card section"><h3>Explicación</h3><p><b>Qué es:</b> ${esc(ex.que_es)}</p><p><b>Por qué tiene esta puntuación:</b> ${esc(ex.por_que)}</p><ul class="tight small">${ex.riesgos.map((r) => `<li>${esc(r)}</li>`).join("")}</ul><p class="small"><b>Horizonte:</b> ${esc(ex.horizonte)}</p></div>` : ""}
      ${d.otras_cotizaciones.length ? `<div class="card section"><h3>Otras cotizaciones del mismo instrumento</h3><ul class="tight small">${d.otras_cotizaciones.map((o) => `<li><a href="#" data-open="${o.id}">${esc(o.provider_symbol)}</a> · ${esc(o.modalidad)} · ${o.precio == null ? NA : num(o.precio) + " " + esc(o.moneda)} (${esc(o.fecha || "")})${o.diferencia_sic_vs_origen != null ? ` · diferencia SIC vs origen: ${pct(o.diferencia_sic_vs_origen)}<div class="muted">${esc(o.nota)}</div>` : ""}</li>`).join("")}</ul></div>` : ""}
      ${d.noticias.length ? `<div class="card section"><h3>Noticias y eventos</h3><ul class="tight small">${d.noticias.map((n) => `<li>${esc(n.date || "")} · ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)} (${esc(n.source)})</li>`).join("")}</ul><p class="small muted">No se atribuye automáticamente un movimiento a una noticia.</p></div>` : ""}
      ${d.splits.length ? `<p class="small muted">Splits registrados: ${d.splits.map((s) => `${esc(s[0])} (${s[1]}:1)`).join(", ")}</p>` : ""}`;
    const s = d.serie;
    function draw(n) {
      const k = Math.max(0, s.fechas.length - n), sl = (a) => s.fechas.slice(k).map((f, j) => [f, a[k + j]]);
      lineChart($("#pxChart"), [
        { name: "Precio", points: sl(s.precio), color: C("--series-1"), width: 2 },
        { name: "Media 20", points: sl(s.sma20), color: C("--muted"), width: 1.5, dash: "4 3" },
        { name: "Media 50", points: sl(s.sma50), color: C("--series-2"), width: 1.5 },
        { name: "Media 200", points: sl(s.sma200), color: C("--series-3"), width: 1.5 },
      ], { label: "Precio de " + i.ticker });
    }
    draw(252);
    $("#rng").querySelectorAll("[data-n]").forEach((b) => (b.onclick = () => { $("#rng").querySelectorAll(".chip").forEach((c) => c.classList.remove("on")); b.classList.add("on"); draw(+b.dataset.n); }));
    $("#mFav").onclick = async () => { await toggleFav(id, d.favorito); d.favorito = !d.favorito; $("#mFav").textContent = d.favorito ? "★" : "☆"; $("#mFav").classList.toggle("on", d.favorito); };
    $("#mCmp").onclick = () => { addCompare(id); closeModal(); location.hash = "#comparar"; };
  }
  async function toggleFav(id, on) { await api("/api/favorites/" + id, { method: on ? "DELETE" : "POST" }); toast(on ? "Quitado de favoritos" : "Agregado a favoritos"); }
  function addCompare(id) { id = +id; if (state.compare.includes(id)) return; if (state.compare.length >= 3) { toast("Máximo 3 instrumentos; se reemplaza el primero."); state.compare.shift(); } state.compare.push(id); store("radar_cmp", JSON.stringify(state.compare)); }

  views.comparar = async (el) => {
    el.innerHTML = `<h2>${demoBadge()}Comparador (hasta 3)</h2><div class="card"><label class="f">Agregar instrumento<input id="cq" type="search" placeholder="Escribe un ticker o nombre"></label><div id="cres" class="chips" style="margin-top:6px"></div><div id="csel" class="chips" style="margin-top:10px"></div></div><div id="cout"></div>`;
    let t;
    $("#cq").oninput = (e) => { clearTimeout(t); t = setTimeout(async () => { const q = e.target.value.trim(); if (!q) { $("#cres").innerHTML = ""; return; } const d = await api("/api/instruments?" + new URLSearchParams({ q, page_size: 10, sort: "liq_avg_value_mxn" })); $("#cres").innerHTML = d.items.map((x) => `<button class="chip" data-add="${x.id}" type="button">+ ${esc(x.ticker)} <span class="muted">${esc(x.gbm_modality)}</span></button>`).join(""); $("#cres").querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => { addCompare(b.dataset.add); views.comparar(el); })); }, 250); };
    if (!state.compare.length) { $("#cout").innerHTML = '<div class="card empty section">Agrega instrumentos para compararlos.</div>'; return; }
    const d = await api("/api/compare?ids=" + state.compare.join(","));
    const cols = [C("--series-1"), C("--series-2"), C("--series-3")];
    $("#csel").innerHTML = d.series.map((x, k) => `<button class="chip on" style="background:${cols[k]};border-color:${cols[k]}" data-rm="${x.id}" type="button">${esc(x.ticker)} ×</button>`).join("");
    $("#csel").querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { state.compare = state.compare.filter((i) => i !== +b.dataset.rm); store("radar_cmp", JSON.stringify(state.compare)); views.comparar(el); }));
    const rows = [["Rend. 1 mes (MXN)", (m) => pct(m.ret_mxn["1m"])], ["Rend. 3 meses (MXN)", (m) => pct(m.ret_mxn["3m"])], ["Rend. 1 año (MXN)", (m) => pct(m.ret_mxn["1a"])], ["Rend. 1 año (su moneda)", (m) => pct(m.ret_total["1a"])], ["Volatilidad anual", (m) => pct(m.vol63, false)], ["Caída máxima", (m) => pct(m.max_dd)], ["Tendencia (de 4)", (m) => m.trend_points == null ? NA : m.trend_points], ["Liquidez diaria", (m) => money(m.liq_avg_value_mxn, "MXN")], ["Puntuación", (m) => (m.score ? m.score.total.toFixed(0) + " (" + GROUP_ICON[m.score.grupo] + ")" : "—")], ["Último dato", (m) => esc(m.last_date)], ["Modalidad GBM", (m) => esc(m.gbm_modality)]];
    $("#cout").innerHTML = `<div class="card section"><h3>Rendimiento total, base 100</h3><div class="chart" id="cchart"></div><p class="small muted">${esc(d.nota)} Cada línea está en su propia moneda.</p></div>
      <div class="tablewrap section"><table><thead><tr><th></th>${d.series.map((x) => `<th class="num">${esc(x.ticker)} <span class="muted">${esc(x.currency)}</span></th>`).join("")}</tr></thead><tbody>${rows.map(([l, f]) => `<tr><td>${l}</td>${d.series.map((x) => `<td class="num">${x.metricas ? f(x.metricas) : NA}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="small muted">Las puntuaciones solo son comparables dentro del mismo grupo.</p>`;
    lineChart($("#cchart"), d.series.map((x, k) => ({ name: x.ticker, points: x.norm, color: cols[k] })), { baseline: 100, label: "Comparación base 100", fmt: (v) => v.toFixed(1) });
  };

  views.favoritos = async (el) => {
    const d = await api("/api/instruments?favorites=1&page_size=200&sort=ticker&dir=asc&period=1m");
    el.innerHTML = `<h2>${demoBadge()}Favoritos</h2>${d.items.length ? `<div class="tablewrap"><table><thead><tr><th></th><th>Instrumento</th><th class="num">1 mes (MXN)</th><th class="num hide-sm">Puntuación</th><th class="num hide-sm">Riesgo</th><th class="num">Último precio</th></tr></thead><tbody>
      ${d.items.map((x) => `<tr class="clickable" data-open="${x.id}"><td><button class="star on" data-fav="${x.id}" type="button" aria-label="Quitar">★</button></td><td><span class="tname">${esc(x.ticker)}</span><div class="tsub">${esc(x.name)}</div></td><td class="num">${pct(x.ret_mxn)}</td><td class="num hide-sm">${x.score == null ? "—" : x.score.toFixed(0)}</td><td class="num hide-sm">${RISK[x.risk_level]}</td><td class="num">${num(x.last_close)} ${esc(x.currency)}<div class="tsub">${esc(x.last_date)}</div></td></tr>`).join("")}</tbody></table></div>` : '<div class="card empty">Marca instrumentos con ☆ para seguirlos aquí. Tus favoritos siempre aparecen en las alertas y en "Qué cambió".</div>'}`;
  };

  views.historial = async (el) => {
    const [rs, ru] = await Promise.all([api("/api/reports"), api("/api/runs")]);
    el.innerHTML = `<h2>${demoBadge()}Historial de reportes</h2>
      ${rs.reportes.length ? `<div class="tablewrap"><table><thead><tr><th>Fecha</th><th class="hide-sm">Corte de datos</th><th>Para investigar</th><th class="num">Alertas</th></tr></thead><tbody>${rs.reportes.map((r) => `<tr class="clickable" data-report="${r.id}"><td>${day(r.fecha)} <span class="muted small">v${r.version}</span>${r.datos_nuevos === false ? '<div class="tsub">sin sesiones nuevas</div>' : ""}</td><td class="hide-sm small">MX ${esc(r.corte.MX || "—")} · US ${esc(r.corte.US || "—")}</td><td class="small">${r.destacados.map(esc).join(", ") || "—"}</td><td class="num">${r.alertas}</td></tr>`).join("")}</tbody></table></div>` : '<div class="card empty">Sin reportes todavía.</div>'}
      <h2 class="section">Ejecuciones</h2>
      ${ru.ejecuciones.map((x) => `<details class="card"><summary>${x.status === "exito" ? "✔" : x.status === "fallo" ? "✖" : "…"} ${dt(x.started_at)} · ${esc(x.trigger)} · ${esc(x.status)} · ${esc(x.mode)}${x.attempt > 1 ? " · intento " + x.attempt : ""}</summary>${x.error ? `<p class="down small">${esc(x.error)}</p>` : ""}<ul class="tight small">${x.eventos.map((e) => `<li class="${e.level === "error" ? "down" : ""}">${esc(e.ts.slice(11, 19))} [${esc(e.level)}] ${esc(e.message.split("\n")[0])}</li>`).join("")}</ul></details>`).join("") || '<div class="card empty">Sin ejecuciones.</div>'}`;
    el.querySelectorAll("[data-report]").forEach((tr) => (tr.onclick = async () => {
      const r = (await api("/api/reports/" + tr.dataset.report)).reporte;
      openModal(`<h2 id="modalTitle">Reporte del ${day(r.report_date)} <span class="muted small">v${r.version}</span></h2><p class="small muted">Generado ${dt(r.generated_at)} · corte MX ${esc(r.data_cutoff.MX.ultimo_dato)} · US ${esc(r.data_cutoff.US.ultimo_dato)}</p>
        <div class="card"><h3>Qué cambió</h3>${changesHtml(r.cambios)}</div>
        ${Object.entries(r.investigar).map(([g, v]) => `<div class="section"><h3>${esc(v.nombre)}</h3>${v.items.length ? v.items.map(highlightCard).join("") : `<p class="small muted">${esc(v.aviso || "Sin datos suficientes.")}</p>`}</div>`).join("")}
        <div class="card section"><h3>Alertas</h3>${alertsHtml(r.alertas)}</div>`);
    }));
  };

  views.ajustes = async (el) => {
    const [s, st, cov] = await Promise.all([api("/api/settings"), loadStatus(), api("/api/coverage")]);
    const notifOk = "Notification" in window;
    el.innerHTML = `<h2>Ajustes</h2>
      <form class="card" id="setf"><h3>Preferencias</h3><div class="filters">
        <label class="f">Hora de actualización diaria (${esc(st.zona_horaria)})<input type="time" id="sH" value="${esc(s.horario)}" required ${STATIC ? "disabled" : ""}></label>
        <label class="f">Presupuesto (MXN)<input type="number" id="sB" min="0" step="100" value="${s.presupuesto_mxn || ""}" placeholder="Ej. 5000"></label>
        <label class="f">Moneda de rendimientos en tablas<select id="sM"><option value="MXN">Convertido a pesos (MXN)</option><option value="USD">Moneda original de cada instrumento</option></select></label>
        <label class="f">Horizonte de interés<select id="sZ"><option value="corto">Corto (semanas)</option><option value="medio">Medio (3 a 12 meses)</option><option value="largo">Largo (más de 1 año)</option></select></label></div>
        ${STATIC ? `<p class="small">La hora y la opción de "solo verificados" se cambian editando <a href="https://github.com/${esc(STATIC.repo)}/edit/main/ajustes.json" target="_blank" rel="noopener">ajustes.json en tu repositorio</a>. Tus demás preferencias se guardan en este navegador.</p>` : ""}
        <label class="check"><input type="checkbox" id="sV" ${s.solo_verificados ? "checked" : ""} ${STATIC ? "disabled" : ""}> Usar solo instrumentos verificados en GBM para los rankings de investigación</label>
        <label class="check"><input type="checkbox" id="sN" ${s.notificaciones ? "checked" : ""}> Avisarme cuando el reporte esté listo (servidor: ${st.proveedores.notificaciones_servidor ? "ntfy/webhook configurado" : "sin canal configurado"})</label>
        ${notifOk ? `<button class="btn sm" type="button" id="sNB">Activar notificaciones del navegador</button> <span class="small muted">Funcionan mientras la página está abierta.</span>` : ""}
        <p class="small muted">El cambio de horario aplica desde la siguiente ejecución. La zona America/Matamoros sigue el horario de verano de EE. UU.; el sistema ajusta las horas automáticamente.</p>
        <div class="row"><button class="btn primary">Guardar</button><span id="sMsg" class="small"></span></div></form>
      <div class="card section"><h3>Automatización</h3><dl class="kv small">
        ${STATIC ? `<dt>Ejecución automática</dt><dd>GitHub Actions revisa cada hora y ejecuta a tu hora (<a href="https://github.com/${esc(STATIC.repo)}/actions" target="_blank" rel="noopener">ver ejecuciones</a>)</dd>`
          : `<dt>Programador interno del servidor</dt><dd>${st.programador_interno ? "Activo (revisa cada 30 s)" : "Desactivado"}</dd>
        <dt>Cron externo de respaldo</dt><dd>${st.cron_externo_configurado ? "Endpoint habilitado (CRON_SECRET)" : "No configurado"}</dd>`}
        <dt>Próxima ejecución</dt><dd>${dt(st.proxima_ejecucion)}</dd>
        <dt>Consultas de datos hoy</dt><dd>${STATIC ? "Yahoo: " + (st.uso_api_hoy.yahoo || 0) : st.uso_api_hoy.eodhd + " de " + st.presupuesto_eodhd + " (EODHD)"}</dd></dl>
        <p class="small muted">La actualización corre en el servidor aunque cierres esta página o apagues tu computadora.</p></div>
      <div class="card section"><h3>Fuentes de datos configuradas</h3><dl class="kv small">
        <dt>Precios</dt><dd>${st.proveedores.precios === "yahoo" ? "Yahoo Finance (gratis, no oficial)" : st.proveedores.eodhd ? "EODHD" : "Falta EODHD_API_KEY"}</dd>
        <dt>Tipo de cambio (Banxico)</dt><dd>${st.proveedores.banxico ? "Configurado" : "Falta BANXICO_TOKEN (se usa un respaldo)"}</dd>
        <dt>Cripto (CoinGecko)</dt><dd>${st.proveedores.coingecko_clave ? "Con clave Demo" : "Sin clave (límites más bajos)"}</dd>
        <dt>Fundamentales / noticias</dt><dd>${st.proveedores.fundamentales ? "Sí" : "No"} / ${st.proveedores.noticias ? "Sí" : "No"}</dd></dl>
        <p class="small muted">Las claves se guardan solo en el servidor (variables de entorno). Nunca se piden ni se muestran aquí, y nunca se pide tu contraseña de GBM.</p></div>
      <form class="card section" id="impf"><h3>Importar catálogo autorizado de GBM</h3>
        <p class="small">GBM no publica su catálogo. Si tienes una lista autorizada (por ejemplo, la que copies de la app), súbela como CSV con columnas <code>ticker</code> y <code>mercado</code> (Trading MX, SIC o Trading USA). Los que coincidan quedarán como <b>verificados</b> con la fecha de importación.</p>
        ${STATIC ? `<p class="small">En la versión gratis: sube tu CSV a tu repositorio como <a href="https://github.com/${esc(STATIC.repo)}/tree/main/data" target="_blank" rel="noopener">data/catalogo_gbm.csv</a>. Se aplica en la siguiente ejecución.</p>` : `<div class="row"><input type="file" id="impFile" accept=".csv,text/csv" style="max-width:320px"><button class="btn">Importar</button></div><div id="impMsg" class="small"></div>`}
        <p class="small muted">Verificados actualmente: ${cov.cobertura.verificados_gbm} de ${cov.cobertura.identificados} identificados.</p></form>`;
    $("#sM").value = s.moneda; $("#sZ").value = s.horizonte;
    $("#setf").onsubmit = async (e) => {
      e.preventDefault();
      const r = await api("/api/settings", { method: "PUT", json: { horario: $("#sH").value, presupuesto_mxn: $("#sB").value || null, moneda: $("#sM").value, horizonte: $("#sZ").value, solo_verificados: $("#sV").checked, notificaciones: $("#sN").checked } });
      $("#sMsg").textContent = r.ok ? "Guardado." : Object.values(r.errores).join(" ");
      if (STATIC) toast("Preferencias guardadas en este navegador.");
      loadStatus();
    };
    if ($("#sNB")) $("#sNB").onclick = async () => { const p = await Notification.requestPermission(); store("radar_notif", p === "granted" ? "1" : "0"); toast(p === "granted" ? "Notificaciones activadas en este navegador." : "Permiso no concedido."); };
    if ($("#impf") && STATIC) $("#impf").onsubmit = (e) => e.preventDefault();
    else $("#impf").onsubmit = async (e) => {
      e.preventDefault();
      const f = $("#impFile").files[0]; if (!f) return;
      const fd = new FormData(); fd.append("archivo", f);
      const r = await api("/api/catalog/import", { method: "POST", body: fd });
      $("#impMsg").textContent = r.ok ? `Filas: ${r.resultado.filas} · coinciden: ${r.resultado.coinciden} · sin coincidencia: ${r.resultado.sin_coincidencia}. ${r.nota}` : r.error;
    };
  };

  views.aprende = async (el) => {
    const cov = await api("/api/coverage");
    el.innerHTML = `<h2>Aprende y metodología</h2>
      <div class="card"><h3>Términos básicos</h3><dl class="gloss">
        <dt>Acción</dt><dd>Una parte pequeña de una empresa. Si a la empresa le va bien, su precio puede subir; si le va mal, bajar.</dd>
        <dt>ETF</dt><dd>Un fondo que cotiza en bolsa como una acción, pero adentro tiene muchos activos (por ejemplo, las 500 empresas del S&P 500). Cobra una comisión anual.</dd>
        <dt>FIBRA</dt><dd>Fideicomiso mexicano de bienes raíces que cotiza en bolsa y reparte parte de sus rentas.</dd>
        <dt>SIC</dt><dd>Sistema Internacional de Cotizaciones: permite comprar en pesos, en la Bolsa Mexicana, valores extranjeros. El precio en pesos puede diferir un poco del precio en su mercado de origen.</dd>
        <dt>Rendimiento total vs. por precio</dt><dd>El total incluye dividendos (como si se reinvirtieran). Por precio solo mide cuánto cambió el precio. Los rankings usan el total, convertido a pesos.</dd>
        <dt>Volatilidad</dt><dd>Qué tanto sube y baja el precio. 20% anual es moderado; arriba de 40% es alto. Mayor volatilidad = más riesgo de caídas fuertes.</dd>
        <dt>Caída máxima</dt><dd>La peor bajada desde un punto alto hasta un punto bajo en el periodo. Si es −30%, alguien que compró en el máximo llegó a perder 30%.</dd>
        <dt>Liquidez</dt><dd>Cuánto dinero se negocia al día. Con más liquidez es más fácil comprar o vender a un precio justo.</dd>
        <dt>Media móvil</dt><dd>Promedio de los últimos N cierres (20, 50 o 200). Si el precio está arriba de su media de 200, suele leerse como tendencia de largo plazo al alza.</dd>
        <dt>Tipo de cambio FIX</dt><dd>Tipo de cambio oficial que publica el Banco de México cada día hábil. Se usa para convertir dólares a pesos.</dd>
        <dt>Puntuación 0–100</dt><dd>Compara un instrumento con otros del mismo grupo. No es la probabilidad de ganar dinero ni una recomendación.</dd></dl></div>
      <div class="card section"><h3>Cómo se calcula la puntuación (versión 1.0)</h3>
        <ul class="tight small">
          <li><b>Rendimiento 30%:</b> percentil dentro del grupo del rendimiento total en MXN de 1 mes (25%), 3 meses (40%) y 1 año (35%).</li>
          <li><b>Tendencia 20%:</b> señales positivas de 4: precio &gt; media 50; precio &gt; media 200; media 50 &gt; media 200; media 20 &gt; media 50.</li>
          <li><b>Riesgo 25%:</b> menor volatilidad (63 sesiones) y menor caída máxima (252 sesiones) puntúan más.</li>
          <li><b>Liquidez 15%:</b> percentil del monto promedio negociado al día (20 sesiones) en MXN.</li>
          <li><b>Calidad de datos 10%:</b> 60% cobertura de sesiones + 40% largo del historial.</li>
          <li><b>Grupos:</b> acciones, ETFs y exposición cripto se comparan por separado. Se muestra una sola cotización por instrumento (la más líquida).</li>
          <li><b>Exclusiones:</b> menos de 252 sesiones, datos atrasados más de 3 sesiones, cobertura &lt; 80%, liquidez &lt; 1 millón MXN/día, precio &lt; 1 USD, ETFs apalancados o inversos, sin rendimiento en MXN. Se necesitan al menos 5 comparables por grupo; si no, se muestran menos o ninguno.</li>
        </ul></div>
      <div class="card section"><h3>Cobertura de GBM</h3><p class="small">${esc(cov.nota)}</p><ul class="tight small">${Object.entries(cov.modalidades).map(([k, v]) => `<li><b>${esc(k)}:</b> ${esc(v)}</li>`).join("")}</ul>
        <p class="small">Cripto: GBM ofrece ETFs de bitcoin listados en EE. UU. (fuente oficial de GBM). No se encontró evidencia de compra directa de criptomonedas en GBM; Bitcoin y Ethereum se muestran solo como referencias externas. Fuente: <a href="${esc(cov.fuente_modalidades[1])}" target="_blank" rel="noopener">${esc(cov.fuente_modalidades[0])}</a>.</p></div>
      <div class="card section small muted">${esc((state.report || {}).aviso_legal || "")}</div>`;
  };

  // ---------------------------------------------------------------- router
  function route() {
    const tab = (location.hash || "#resumen").slice(1).split("?")[0];
    const v = views[tab] ? tab : "resumen";
    document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === v));
    const el = $("#view");
    el.innerHTML = '<div class="empty">Cargando…</div>';
    views[v](el).catch((e) => { if (e.message !== "auth") el.innerHTML = `<div class="card empty">No se pudo cargar esta sección (${esc(e.message)}).</div>`; });
  }
  document.addEventListener("click", async (e) => {
    const fav = e.target.closest("[data-fav]");
    if (fav) { e.preventDefault(); e.stopPropagation(); const on = fav.classList.contains("on"); await toggleFav(fav.dataset.fav, on); fav.classList.toggle("on"); fav.textContent = on ? "☆" : "★"; return; }
    const o = e.target.closest("[data-open]");
    if (o) { e.preventDefault(); openInstrument(o.dataset.open); }
  });
  window.addEventListener("hashchange", route);

  if (STATIC) $("#btnRun").textContent = "Actualizar ahora (GitHub)";
  async function boot() {
    try { state.compare = JSON.parse(store("radar_cmp") || "[]"); } catch (e) { state.compare = []; }
    try {
      await loadStatus();
      $("#foot").innerHTML = "Radar GBM es una herramienta de análisis educativo. No es asesoría de inversión, no ejecuta compras ni ventas y no está afiliada a GBM. Rendimientos pasados no garantizan rendimientos futuros. Datos de cierre (EOD): EODHD, Banco de México y Datos de CoinGecko.";
      route();
      setInterval(() => loadStatus().catch(() => {}), 60000);
    } catch (e) { if (e.message !== "auth") $("#view").innerHTML = '<div class="card empty">No se pudo conectar con el servidor.</div>'; }
  }
  boot();
})();
