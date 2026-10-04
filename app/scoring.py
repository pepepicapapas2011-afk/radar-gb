"""Metodología de rankings (versión 1.0). Documentada en METODOLOGIA.md.

La puntuación 0–100 es un indicador COMPARATIVO dentro de un grupo equivalente (acciones, ETFs o
instrumentos con exposición cripto). No es una probabilidad de ganar dinero ni una recomendación."""
from . import indicators as ind
from .analysis import MIN_LIQ_MXN

METHOD_VERSION = "1.0"
WEIGHTS = {"rendimiento": 0.30, "tendencia": 0.20, "riesgo": 0.25, "liquidez": 0.15, "calidad": 0.10}
PERF_WEIGHTS = {"1m": 0.25, "3m": 0.40, "1a": 0.35}
MIN_ELIGIBLE = 5
GROUPS = {
    "acciones": "Acciones (sin empresas del sector cripto)",
    "etfs": "ETFs (sin apalancados/inversos ni ETFs cripto)",
    "cripto": "Instrumentos con exposición a cripto (ETFs cripto y acciones del sector)",
}


def group_of(m):
    if m["category"] == "cripto_ref":
        return None
    if m["crypto_exposure"] in ("etf_cripto", "accion_sector_cripto"):
        return "cripto"
    if m["category"] == "accion":
        return "acciones"
    if m["category"] == "etf":
        return "etfs"
    return None  # FIBRAS y otros: se muestran en tablas y Top 10, no en estos rankings


def exclusions(m, only_verified=False):
    r = []
    if m["category"] == "cripto_ref":
        r.append("referencia externa (no es un instrumento de GBM)")
    if m["sessions"] < 252:
        r.append("historial menor a 1 año (252 sesiones)")
    if m["stale_sessions"] > 3:
        r.append(f"datos atrasados ({m['stale_sessions']} sesiones sin dato)")
    if m["coverage"] is None or m["coverage"] < 0.8:
        r.append("cobertura de datos menor a 80%")
    if m["liq_avg_value_mxn"] is None or m["liq_avg_value_mxn"] < MIN_LIQ_MXN:
        r.append("liquidez baja o desconocida (< 1 millón MXN diarios)")
    if m["price_usd_eq"] is not None and m["price_usd_eq"] < 1:
        r.append("precio menor a 1 USD equivalente")
    if m["is_leveraged"]:
        r.append("ETF apalancado o inverso (riesgo muy alto)")
    if m["trend_points"] is None:
        r.append("sin media de 200 sesiones")
    if m["vol63"] is None or m["max_dd"] is None:
        r.append("sin datos suficientes de riesgo")
    if m["ret_mxn"].get("3m") is None:
        r.append("rendimiento en MXN no disponible (falta precio o tipo de cambio)")
    if m["gbm_status"] == "no_disponible":
        r.append("no disponible en GBM")
    if only_verified and m["gbm_status"] != "verificado":
        r.append("no verificado en GBM (filtro activo)")
    return r


def score_groups(metrics, only_verified=False):
    """Asigna puntuación a cada métrica elegible. Devuelve {grupo: {'elegibles': n, 'ranking': [m...]}}."""
    for m in metrics:
        m["group"] = group_of(m)
        m["exclusions"] = exclusions(m, only_verified)
        m["score"] = None
    result = {}
    for g in GROUPS:
        el = [m for m in metrics if m["group"] == g and not m["exclusions"]]
        result[g] = {"nombre": GROUPS[g], "elegibles": len(el), "ranking": []}
        if len(el) < MIN_ELIGIBLE:
            result[g]["aviso"] = f"Solo {len(el)} instrumentos cumplen los criterios; se necesitan al menos {MIN_ELIGIBLE} para comparar."
            continue
        pr = {p: ind.percentile_ranks([m["ret_mxn"][p] for m in el]) for p in PERF_WEIGHTS}
        pvol = ind.percentile_ranks([m["vol63"] for m in el])
        pdd = ind.percentile_ranks([abs(m["max_dd"]) for m in el])
        import math
        pliq = ind.percentile_ranks([math.log(m["liq_avg_value_mxn"]) for m in el])
        for i, m in enumerate(el):
            wsum = sum(w for p, w in PERF_WEIGHTS.items() if pr[p][i] is not None)
            perf = sum(pr[p][i] * w for p, w in PERF_WEIGHTS.items() if pr[p][i] is not None) / wsum if wsum else 0
            comps = {
                "rendimiento": perf,
                "tendencia": m["trend_points"] / 4 * 100,
                "riesgo": 0.5 * (100 - pvol[i]) + 0.5 * (100 - pdd[i]),
                "liquidez": pliq[i],
                "calidad": 100 * (0.6 * min(1.0, m["coverage"]) + 0.4 * min(1.0, m["sessions"] / 252)),
            }
            total = sum(comps[k] * WEIGHTS[k] for k in WEIGHTS)
            m["score"] = {"total": round(total, 1), "componentes": {k: round(v, 1) for k, v in comps.items()},
                          "grupo": g, "elegibles": len(el)}
        ranked = sorted(el, key=lambda m: (-m["score"]["total"], m["provider_symbol"]))
        ranked = dedupe_isin(ranked)
        for pos, m in enumerate(ranked, 1):
            m["score"]["posicion"] = pos
        result[g]["ranking"] = ranked
    return result


def dedupe_isin(items, key_liq=True):
    """Una sola cotización por instrumento (mismo ISIN): se queda la de mayor liquidez local."""
    best = {}
    order = []
    for m in items:
        k = m.get("group_key") or m["isin"] or m["provider_symbol"]
        if k not in best:
            best[k] = m
            order.append(k)
        elif (m["liq_avg_value_mxn"] or 0) > (best[k]["liq_avg_value_mxn"] or 0):
            best[k] = m
    kept = {id(best[k]) for k in order}
    return [m for m in items if id(m) in kept]


def top_returns(metrics, period, n=10):
    cands = [m for m in metrics if m["category"] in ("accion", "etf", "fibra") and not m["is_leveraged"]
             and m["ret_mxn"].get(period) is not None and m["stale_sessions"] <= 3
             and (m["liq_avg_value_mxn"] or 0) >= MIN_LIQ_MXN and (m["price_usd_eq"] or 0) >= 1
             and m["gbm_status"] != "no_disponible"]
    cands.sort(key=lambda m: -m["ret_mxn"][period])
    return dedupe_isin(cands)[:n], len(cands)


def alerts(metrics, favorites=(), limit=40):
    out = []
    for m in metrics:
        relevant = m["id"] in favorites or m["gbm_status"] == "verificado" or m["category"] == "cripto_ref" or (
            (m["liq_avg_value_mxn"] or 0) >= MIN_LIQ_MXN and (m["price_usd_eq"] or 0) >= 1)
        if not relevant:
            continue
        a = []
        r1 = m["ret_total"].get("1d")
        dv = m["daily_vol"]
        if r1 is not None and m["stale_sessions"] == 0 and abs(r1) > max(0.05, 3 * (dv or 0)):
            a.append(("movimiento", f"Movimiento fuerte en la última sesión ({r1*100:+.1f}% el {m['last_date']})", abs(r1) * 100))
        if m["vol_ratio"] is not None and m["vol_ratio"] > 3 and m["stale_sessions"] == 0:
            a.append(("volumen", f"Volumen {m['vol_ratio']:.1f} veces su promedio de 20 sesiones", m["vol_ratio"] * 3))
        if m["vol63"] is not None and m["vol63"] > 0.60:
            a.append(("riesgo", f"Volatilidad anual alta ({m['vol63']*100:.0f}%)", m["vol63"] * 20))
        if m["max_dd"] is not None and m["max_dd"] < -0.40:
            a.append(("riesgo", f"Caída máxima de {m['max_dd']*100:.0f}% en el periodo analizado ({m['dd_window']} sesiones)", abs(m["max_dd"]) * 25))
        if m["is_leveraged"] and r1 is not None and abs(r1) > 0.05:
            a.append(("riesgo", "ETF apalancado o inverso: amplifica movimientos diarios", 15))
        if m["id"] in favorites and m["stale_sessions"] > 0:
            a.append(("datos", f"Sin dato de la sesión esperada ({m['expected_last_session']}); último dato {m['last_date']}", 5))
        if a:
            out.append({"id": m["id"], "provider_symbol": m["provider_symbol"], "ticker": m["ticker"], "name": m["name"],
                        "category": m["category"], "alertas": [{"tipo": t, "texto": tx} for t, tx, _ in a],
                        "severidad": round(max(s for *_, s in a), 1), "favorito": m["id"] in favorites})
    out.sort(key=lambda x: (-x["favorito"], -x["severidad"]))
    return out[:limit]
