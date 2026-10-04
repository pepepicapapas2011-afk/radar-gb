"""Generación del informe diario: rankings, explicaciones en lenguaje sencillo, alertas y cambios vs el reporte anterior.
Las explicaciones se arman con plantillas a partir de los datos calculados (no se usa IA para cifras)."""
import hashlib
import json
from collections import Counter

from . import calendars, catalog, db, scoring
from .indicators import PERIODS_SESSIONS

PERIOD_LABEL = {"1d": "última sesión", "1s": "1 semana", "1m": "1 mes", "3m": "3 meses", "1a": "1 año"}
DISCLAIMER = ("Radar GBM es una herramienta de análisis educativo. No es asesoría de inversión ni una recomendación de compra o venta. "
              "Los rendimientos pasados no garantizan rendimientos futuros. La puntuación 0–100 compara instrumentos dentro de su grupo; "
              "no es una probabilidad de ganar dinero. Verifica disponibilidad, precios y costos en la app de GBM antes de invertir.")
KEEP_FULL_METRICS = 30


def pct(x, signed=True):
    if x is None:
        return "Dato no disponible"
    return f"{x*100:+.1f}%" if signed else f"{x*100:.1f}%"


def money(x, cur):
    if x is None:
        return "Dato no disponible"
    if x >= 1e9:
        return f"{x/1e9:,.1f} mil millones {cur}"
    if x >= 1e6:
        return f"{x/1e6:,.1f} millones {cur}"
    return f"{x:,.2f} {cur}"


TYPE_DESC = {"stock": "una acción (parte de una empresa)", "etf": "un ETF (fondo que cotiza en bolsa y agrupa varios activos)",
             "fibra": "una FIBRA (fideicomiso de bienes raíces que cotiza en bolsa)", "crypto_ref": "una referencia de precio de criptomoneda"}


def load_fundamentals(iid):
    r = db.q1("SELECT data, source, fetched_at FROM fundamentals WHERE instrument_id=?", (iid,))
    if not r:
        return None
    try:
        return {"data": json.loads(r["data"]), "source": r["source"], "fetched_at": r["fetched_at"]}
    except ValueError:
        return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def category_details(m, fund):
    """Información específica por categoría. Si no hay datos fiables: 'Dato no disponible'."""
    d = {}
    src = f"{fund['source']} (consultado {fund['fetched_at'][:10]})" if fund else None
    f = fund["data"] if fund else {}
    if m["type"] == "stock" and m["crypto_exposure"] == "ninguna":
        h, v = f.get("Highlights") or {}, f.get("Valuation") or {}
        bs = ((f.get("Financials") or {}).get("Balance_Sheet") or {}).get("quarterly") or {}
        last_bs = bs[max(bs)] if bs else {}
        debt = _num(last_bs.get("shortLongTermDebtTotal")) or _num(last_bs.get("longTermDebt"))
        eq = _num(last_bs.get("totalStockholderEquity"))
        d = {"Crecimiento de ingresos (trimestre vs año anterior)": pct(_num(h.get("QuarterlyRevenueGrowthYOY"))),
             "Crecimiento de utilidades (trimestre vs año anterior)": pct(_num(h.get("QuarterlyEarningsGrowthYOY"))),
             "Deuda / capital": f"{debt/eq:.2f} veces" if debt is not None and eq else "Dato no disponible",
             "P/U (precio entre utilidad)": f"{_num(h.get('PERatio')):.1f}" if _num(h.get("PERatio")) else "Dato no disponible",
             "VE/EBITDA": f"{_num(v.get('EnterpriseValueEbitda')):.1f}" if _num(v.get("EnterpriseValueEbitda")) else "Dato no disponible"}
    elif m["type"] == "etf" and m["crypto_exposure"] != "etf_cripto":
        e = f.get("ETF_Data") or {}
        top = e.get("Top_10_Holdings") or {}
        weights = [(_num(x.get("Assets_%")), x.get("Name") or k) for k, x in top.items()] if isinstance(top, dict) else []
        weights = [w for w in weights if w[0] is not None]
        conc = sum(w for w, _ in weights) if weights else None
        er = _num(e.get("NetExpenseRatio"))
        d = {"Índice o estrategia": e.get("Index_Name") or "Dato no disponible",
             "Comisión anual de administración": _expense(er),
             "Principales posiciones": ", ".join(f"{n} ({w:.1f}%)" for w, n in sorted(weights, reverse=True)[:5]) or "Dato no disponible",
             "Concentración (peso de las 10 mayores)": f"{conc:.1f}%" if conc is not None else "Dato no disponible",
             "Tipo de exposición": _exposure_text(e)}
    elif m["crypto_exposure"] in ("etf_cripto", "accion_sector_cripto", "referencia_externa"):
        name = (m["name"] or "").lower()
        under = "Bitcoin" if ("bitcoin" in name or "btc" in name) else ("Ether (Ethereum)" if "ether" in name or "eth" in name else "Activos digitales (revisar prospecto)")
        if m["crypto_exposure"] == "etf_cripto":
            est = "Fideicomiso (trust) listado en EE. UU." if "trust" in name else "ETF listado en EE. UU."
            est += " Revisa en su prospecto si mantiene el activo directamente (spot) o usa futuros."
        elif m["crypto_exposure"] == "accion_sector_cripto":
            est, under = "Acción de una empresa cuyo negocio depende del sector cripto. No es una criptomoneda.", under if under != "Activos digitales (revisar prospecto)" else "Negocio ligado a criptomonedas"
        else:
            est = "Precio de referencia del mercado cripto (CoinGecko). No se afirma que se compre directamente en GBM."
        d = {"Activo subyacente": under, "Estructura del producto": est,
             "Volatilidad anual (63 sesiones)": pct(m["vol63"], False),
             "Riesgos relevantes": ("Depende del precio de las criptomonedas y además tiene riesgos propios de la empresa (deuda, costos, regulación)."
                                    if m["crypto_exposure"] == "accion_sector_cripto" else
                                    "Cambios bruscos de precio, regulación, custodia del activo, diferencias entre el ETF y el precio del activo, y el tipo de cambio USD/MXN.")}
    return {"datos": d, "fuente": src or ("Cálculo propio con precios del proveedor" if d else None)}


def _expense(er):
    """El proveedor puede dar la comisión como fracción (0.00095) o como porcentaje (0.0945).
    Las comisiones reales de ETFs están entre 0.01% y 3%, así que un valor < 0.03 se interpreta como fracción."""
    if er is None or er <= 0:
        return "Dato no disponible"
    return f"{(er * 100 if er < 0.03 else er):.2f}%"


def _exposure_text(e):
    al = e.get("Asset_Allocation") or {}
    try:
        parts = sorted(((float(v.get("Net_Assets_%") or 0), k) for k, v in al.items()), reverse=True)[:3]
        return ", ".join(f"{k} {w:.0f}%" for w, k in parts if w) or "Dato no disponible"
    except (AttributeError, ValueError, TypeError):
        return "Dato no disponible"


def load_news(iid):
    rows = db.q("SELECT kind, date, title, url, source FROM news WHERE instrument_id=? ORDER BY date DESC LIMIT 6", (iid,))
    return [dict(r) for r in rows]


def explain(m, prev_pos, settings):
    g = m["score"]["grupo"]
    comps = m["score"]["componentes"]
    names = {"rendimiento": "su rendimiento reciente en pesos", "tendencia": "su tendencia de precio", "riesgo": "un riesgo menor que sus pares",
             "liquidez": "su liquidez (se negocia mucho)", "calidad": "la calidad de sus datos"}
    best = sorted(comps, key=lambda k: -comps[k] * scoring.WEIGHTS[k])[:2]
    worst = min(comps, key=lambda k: comps[k])
    que = f"{m['name']} ({m['ticker']}) es {TYPE_DESC.get(m['type'], 'un instrumento')} que cotiza en {m['venue'] or m['exchange']} en {m['currency']}. Modalidad en GBM: {m['gbm_modality']}"
    que += " (verificado con fuente de GBM)." if m["gbm_status"] == "verificado" else " (pendiente de verificar en la app de GBM)."
    por = (f"Obtiene {m['score']['total']:.0f} de 100 y queda en el lugar {m['score']['posicion']} entre {m['score']['elegibles']} "
           f"{scoring.GROUPS[g].split(' (')[0].lower()} comparables. Lo que más lo impulsa: {names[best[0]]} y {names[best[1]]}. "
           f"Su punto más débil frente a sus pares: {names[worst]}.")
    datos = [f"Rendimiento total en pesos: 1 mes {pct(m['ret_mxn']['1m'])}, 3 meses {pct(m['ret_mxn']['3m'])}, 1 año {pct(m['ret_mxn']['1a'])}.",
             f"En su moneda ({m['currency']}): 3 meses {pct(m['ret_total']['3m'])}; solo por precio (sin dividendos) {pct(m['ret_price']['3m'])}.",
             f"Tendencia: {m['trend_points']} de 4 señales positivas" + (f" ({'; '.join(m['trend_reasons'])})." if m['trend_reasons'] else "."),
             f"Volatilidad anual: {pct(m['vol63'], False)}. Caída máxima en {m['dd_window']} sesiones: {pct(m['max_dd'])}.",
             f"Liquidez: se negocian en promedio {money(m['liq_avg_value_mxn'], 'MXN')} al día (20 sesiones)."]
    rel = m["rel"].get("3m")
    if rel:
        datos.append(f"Contra su referencia ({m['benchmark']}, misma moneda, 3 meses): {pct(rel['diferencia'])} de diferencia.")
    riesgos = []
    if m["vol63"] and m["vol63"] > 0.4:
        riesgos.append("Su precio se mueve mucho (volatilidad alta): puede bajar fuerte en poco tiempo.")
    if m["max_dd"] is not None and m["max_dd"] < -0.25:
        riesgos.append(f"En el último año llegó a caer {pct(m['max_dd'])} desde su máximo.")
    if m["currency"] == "USD":
        riesgos.append("Cotiza en dólares: si el peso se fortalece, tu rendimiento en pesos baja.")
    if m["gbm_modality"] == "Trading MX (SIC)":
        riesgos.append("En el SIC el precio en pesos puede separarse un poco del precio en su mercado de origen y puede haber menos compradores y vendedores.")
    if m["crypto_exposure"] != "ninguna":
        riesgos.append("Depende del mercado cripto, que es muy volátil y está menos regulado.")
    if m["type"] == "stock" and m["crypto_exposure"] == "ninguna":
        riesgos.append("Es una sola empresa: sus resultados o noticias pueden mover mucho el precio.")
    if m["type"] == "etf":
        riesgos.append("Revisa qué índice sigue, su comisión anual y qué tan concentrado está.")
    if m["gbm_status"] != "verificado":
        riesgos.append("No se pudo confirmar de forma individual que esté disponible en GBM: revísalo en la app.")
    if m["ret_mxn"]["3m"] is not None and m["ret_mxn"]["3m"] > 0.3:
        riesgos.append("Subió mucho en poco tiempo; las subidas fuertes no garantizan que siga subiendo.")
    if prev_pos is None:
        cambio = "Entra al ranking hoy (no aparecía entre los primeros del reporte anterior)."
    else:
        dp = prev_pos["posicion"] - m["score"]["posicion"]
        ds = m["score"]["total"] - prev_pos["total"]
        cambio = ("Mantiene su lugar" if dp == 0 else (f"Sube {dp} lugar(es)" if dp > 0 else f"Baja {-dp} lugar(es)")) + \
                 f" (antes {prev_pos['posicion']}.º); puntuación {ds:+.1f} puntos."
    horizonte = {"corto": "Tu horizonte es corto (semanas). Este análisis usa datos de 1 a 12 meses, así que úsalo solo como contexto.",
                 "medio": "Relevante para investigar con horizonte de 3 a 12 meses. No sirve para operar en el mismo día.",
                 "largo": "Útil como punto de partida para un horizonte de más de un año; complementa con los fundamentales de la empresa o del fondo."}
    return {"que_es": que, "por_que": por, "datos": datos, "riesgos": riesgos[:5], "cambio": cambio,
            "horizonte": horizonte.get(settings.get("horizonte", "medio"), horizonte["medio"])}


def compact(m, period=None):
    c = {k: m.get(k) for k in ("id", "provider_symbol", "ticker", "name", "category", "type", "currency", "exchange", "gbm_modality",
                               "gbm_status", "last_close", "last_date", "risk_level", "crypto_exposure", "price_mxn")}
    c["ret_mxn"], c["ret_total"] = m["ret_mxn"], m["ret_total"]
    if period:
        c["periodo"] = period
        c["fechas"] = m["period_dates"].get(period)
    if m.get("score"):
        c["score"] = m["score"]
    return c


def fingerprint(cutoffs, n):
    return hashlib.sha256(json.dumps([cutoffs, n], sort_keys=True).encode()).hexdigest()[:16]


def previous_report(mode, report_date):
    r = db.q1("SELECT * FROM reports WHERE mode=? AND report_date<? ORDER BY report_date DESC LIMIT 1", (mode, report_date))
    if not r:
        return None
    s = json.loads(r["summary"])
    s["_id"] = r["id"]
    return s


def build(mode, metrics, groups, now, run_id, log, settings, favorites, warnings):
    tz_now = now.astimezone(__import__("zoneinfo").ZoneInfo("America/Matamoros"))
    report_date = tz_now.date().isoformat()
    prev = previous_report(mode, report_date)
    prev_rank = {}
    if prev:
        for g, info in prev.get("ranking_resumen", {}).items():
            for x in info:
                prev_rank[(g, x["id"])] = x

    by_id = {m["id"]: m for m in metrics}
    # cortes de datos por mercado (último dato de la referencia)
    cutoffs = {}
    for mk in ("US", "MX", "CRYPTO"):
        ds = [m["last_date"] for m in metrics if m["exchange"] == mk and (m["is_benchmark"] or mk == "CRYPTO")]
        if not ds:
            ds = [m["last_date"] for m in metrics if m["exchange"] == mk]
        st = calendars.market_status(mk, now)
        cutoffs[mk] = {"ultimo_dato": max(ds) if ds else None, "sesion_esperada": st["expected_last_session"],
                       "estado_mercado": st["label"], "nombre": st["name"],
                       "al_dia": bool(ds) and max(ds) >= st["expected_last_session"]}
    new_by_market = {mk: True for mk in cutoffs}
    if prev:
        new_by_market = {mk: (prev.get("data_cutoff", {}).get(mk) or {}).get("ultimo_dato") != cutoffs[mk]["ultimo_dato"] for mk in cutoffs}
    new_data = new_by_market["US"] or new_by_market["MX"]  # sesiones nuevas en bolsas (cripto se muestra aparte)

    investigar = {}
    ranking_resumen = {}
    for g, info in groups.items():
        items = []
        for m in info["ranking"][:3]:
            fund = load_fundamentals(m["id"])
            item = compact(m)
            item["explicacion"] = explain(m, prev_rank.get((g, m["id"])), settings)
            item["detalle_categoria"] = category_details(m, fund)
            item["noticias"] = load_news(m["id"])
            items.append(item)
        investigar[g] = {"nombre": info["nombre"], "elegibles": info["elegibles"], "aviso": info.get("aviso"), "items": items}
        ranking_resumen[g] = [{"id": m["id"], "ticker": m["ticker"], "posicion": m["score"]["posicion"], "total": m["score"]["total"]}
                              for m in info["ranking"][:25]]

    top10 = {}
    for p in PERIODS_SESSIONS:
        items, n = scoring.top_returns(metrics, p)
        top10[p] = {"periodo": PERIOD_LABEL[p], "candidatos": n, "items": [compact(m, p) for m in items]}

    al = scoring.alerts(metrics, set(favorites))
    analyzed = [m for m in metrics if m["category"] != "cripto_ref"]
    reasons = Counter(r for m in analyzed for r in m["exclusions"])
    cov = catalog.coverage(1 if mode == "demo" else 0)
    cov.update({"analizados": len(analyzed), "con_puntuacion": sum(1 for m in analyzed if m.get("score")),
                "excluidos_de_rankings": sum(1 for m in analyzed if m["exclusions"]), "motivos_exclusion": dict(reasons.most_common())})

    # resumen del día
    bench = {}
    for key, ps in catalog.benchmarks(1 if mode == "demo" else 0).items():
        b = next((m for m in metrics if m["provider_symbol"] == ps), None)
        bench[key] = {"simbolo": ps, "nombre": b["name"] if b else None, "ultimo_dato": b["last_date"] if b else None,
                      "ret_1d": b["ret_total"]["1d"] if b else None, "ret_1m": b["ret_total"]["1m"] if b else None,
                      "moneda": b["currency"] if b else None} if b else {"simbolo": ps, "nombre": None}
    liquid = [m for m in analyzed if (m["liq_avg_value_mxn"] or 0) >= scoring.MIN_LIQ_MXN and m["stale_sessions"] == 0 and m["ret_total"]["1d"] is not None]
    breadth = {"suben": sum(1 for m in liquid if m["ret_total"]["1d"] > 0), "bajan": sum(1 for m in liquid if m["ret_total"]["1d"] < 0), "total": len(liquid)}
    fxm = next((m for m in metrics if m["fx_last"]), None)
    fx = {"valor": fxm["fx_last"], "fecha": fxm["fx_date"], "fuente": fxm["fx_source"]} if fxm else {"valor": None, "fuente": None}

    # cambios desde el reporte anterior
    cambios = {"hay_reporte_anterior": bool(prev), "datos_nuevos": new_data,
               "fecha_anterior": prev.get("report_date") if prev else None, "investigar": {}, "top10": {}, "alertas_nuevas": [], "favoritos": []}
    if prev:
        for g in investigar:
            now_ids = [x["id"] for x in investigar[g]["items"]]
            old_ids = [x["id"] for x in prev.get("investigar", {}).get(g, {}).get("items", [])]
            cambios["investigar"][g] = {"entran": [by_id[i]["ticker"] for i in now_ids if i not in old_ids],
                                        "salen": [x["ticker"] for x in prev["investigar"].get(g, {}).get("items", []) if x["id"] not in now_ids]}
        for p in top10:
            now_ids = [x["id"] for x in top10[p]["items"]]
            old = prev.get("top10", {}).get(p, {}).get("items", [])
            cambios["top10"][p] = {"entran": [x["ticker"] for x in top10[p]["items"] if x["id"] not in [o["id"] for o in old]],
                                   "salen": [o["ticker"] for o in old if o["id"] not in now_ids]}
        old_alert = {a["id"] for a in prev.get("alertas", [])}
        cambios["alertas_nuevas"] = [a["ticker"] for a in al if a["id"] not in old_alert]
    for fid in favorites:
        m = by_id.get(fid)
        if m:
            cambios["favoritos"].append({"id": fid, "ticker": m["ticker"], "ret_1d": m["ret_total"]["1d"], "fecha": m["last_date"],
                                         "score": m["score"]["total"] if m.get("score") else None})
    cambios["nuevos_por_mercado"] = new_by_market
    if not new_data:
        cambios["texto"] = ("No hay sesiones nuevas de bolsa desde el reporte anterior (mercados cerrados o sin datos nuevos del proveedor). "
                            "Las cifras de acciones y ETFs son las del último cierre; no son movimientos nuevos."
                            + (" Las referencias cripto sí se actualizaron." if new_by_market["CRYPTO"] else ""))
    elif not prev:
        cambios["texto"] = "Este es el primer reporte: aún no hay contra qué comparar."
    else:
        cambios["texto"] = "Comparado contra el reporte del " + prev["report_date"] + "."

    summary = {
        "report_date": report_date, "mode": mode, "generated_at": db.iso(now), "method_version": scoring.METHOD_VERSION,
        "data_cutoff": cutoffs, "datos_nuevos": new_data, "fx": fx, "cobertura": cov, "referencias": bench, "amplitud": breadth,
        "top10": top10, "investigar": investigar, "ranking_resumen": ranking_resumen, "alertas": al, "cambios": cambios,
        "avisos": warnings, "aviso_legal": DISCLAIMER,
        "rendimiento_mostrado": "Rendimiento total (incluye dividendos reinvertidos según el proveedor). Los rankings usan el rendimiento convertido a pesos (MXN) para comparar instrumentos en distintas monedas.",
        "fuentes": _sources(mode),
    }
    fp = fingerprint(cutoffs, len(metrics))
    existing = db.q1("SELECT id, version, data_fingerprint FROM reports WHERE report_date=? AND mode=?", (report_date, mode))
    if existing and existing["data_fingerprint"] == fp:
        log(f"Ya existe el reporte del {report_date} con los mismos datos; no se duplica.")
        db.ex("UPDATE reports SET summary=?, run_id=? WHERE id=?", (json.dumps(summary, default=str), run_id, existing["id"]))
        return existing["id"], False
    with db.transaction() as c:
        if existing:
            version = existing["version"] + 1
            c.execute("UPDATE reports SET version=?, run_id=?, created_at=?, data_fingerprint=?, summary=? WHERE id=?",
                      (version, run_id, db.iso(now), fp, json.dumps(summary, default=str), existing["id"]))
            rid = existing["id"]
            c.execute("DELETE FROM report_metrics WHERE report_id=?", (rid,))
        else:
            cur = c.execute("INSERT INTO reports(report_date,mode,version,run_id,created_at,data_fingerprint,summary) VALUES(?,?,?,?,?,?,?)",
                            (report_date, mode, 1, run_id, db.iso(now), fp, json.dumps(summary, default=str)))
            rid = cur.lastrowid
        c.executemany("INSERT INTO report_metrics(report_id,instrument_id,data) VALUES(?,?,?)",
                      [(rid, m["id"], json.dumps(m, default=str)) for m in metrics])
    # retención: métricas completas solo de los últimos N reportes
    old = db.q("SELECT id FROM reports WHERE mode=? ORDER BY report_date DESC LIMIT -1 OFFSET ?", (mode, KEEP_FULL_METRICS))
    for r in old:
        db.ex("DELETE FROM report_metrics WHERE report_id=?", (r["id"],))
    log(f"Reporte {report_date} guardado (id {rid}).")
    return rid, True


def _sources(mode):
    from .config import config
    if mode == "demo":
        return [{"nombre": "Datos sintéticos de demostración", "url": None, "nota": "No son precios reales."}]
    if config.PRICE_PROVIDER == "yahoo":
        return [
            {"nombre": "Yahoo Finance – precios diarios (fuente gratuita NO oficial, uso personal)", "url": "https://finance.yahoo.com/", "nota": "Último cierre; puede tener errores o dejar de responder."},
            {"nombre": "Nasdaq Trader – directorio de símbolos de EE. UU.", "url": "https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs", "nota": "Catálogo de NYSE, Nasdaq, NYSE Arca, NYSE American y Cboe."},
            {"nombre": "Banco de México – SIE, tipo de cambio FIX (SF43718)", "url": "https://www.banxico.org.mx/SieAPIRest/service/v1/", "nota": "Respaldo: USD/MXN de Yahoo."},
            {"nombre": "Datos de CoinGecko – referencias BTC y ETH", "url": "https://www.coingecko.com/", "nota": "Precio diario a las 00:00 UTC. Referencia externa."},
            {"nombre": "GBM – productos y modalidades", "url": catalog.MODALITY_SOURCE[1], "nota": "Trading MX (acciones completas, SIC) y Trading USA (fracciones)."},
            {"nombre": "GBM – ETFs de Bitcoin disponibles", "url": "https://gbm.com/media/the-academy/acceso-a-etfs-de-bitcoin-a-traves-de-gbm/", "nota": "Fuente de los ETFs cripto verificados."},
        ]
    return [
        {"nombre": "EODHD – precios EOD, catálogos de bolsas, splits/dividendos", "url": "https://eodhd.com/financial-apis/", "nota": "Datos de cierre (EOD), no en tiempo real."},
        {"nombre": "Banco de México – SIE, tipo de cambio FIX (SF43718)", "url": "https://www.banxico.org.mx/SieAPIRest/service/v1/", "nota": "Publicado en días hábiles bancarios."},
        {"nombre": "Datos de CoinGecko – referencias BTC y ETH", "url": "https://www.coingecko.com/", "nota": "Precio diario a las 00:00 UTC. Referencia externa."},
        {"nombre": "GBM – productos y modalidades", "url": catalog.MODALITY_SOURCE[1], "nota": "Trading MX (acciones completas, SIC) y Trading USA (fracciones)."},
        {"nombre": "GBM – ETFs de Bitcoin disponibles", "url": "https://gbm.com/media/the-academy/acceso-a-etfs-de-bitcoin-a-traves-de-gbm/", "nota": "Fuente de los ETFs cripto verificados."},
    ]
