"""Cálculo de métricas por instrumento a partir de los datos guardados (sin llamadas a proveedores)."""
from datetime import date

from . import calendars, catalog, db
from . import indicators as ind

MIN_LIQ_MXN = 1_000_000  # promedio diario negociado mínimo para rankings


def fx_series(mode):
    table = "fx_demo" if mode == "demo" else "fx"
    rows = db.q(f"SELECT date, usd_mxn, source FROM {table} ORDER BY date")
    return [r["date"] for r in rows], [r["usd_mxn"] for r in rows], (rows[-1]["source"] if rows else None)


def load_series(inst_id):
    rows = db.q("SELECT date, close, adj_close, volume, source FROM prices WHERE instrument_id=? ORDER BY date", (inst_id,))
    return rows


def risk_level(vol):
    if vol is None:
        return "sin_dato"
    if vol < 0.20:
        return "bajo"
    if vol < 0.40:
        return "medio"
    return "alto"


def _fx_at(fxd, fxv, d):
    return ind.value_at_or_before(fxd, fxv, d)[1] if fxd else None


def compute_all(mode, now):
    is_demo = 1 if mode == "demo" else 0
    insts = db.q("SELECT * FROM instruments WHERE active=1 AND is_demo=? AND needs_backfill=0", (is_demo,))
    fxd, fxv, fx_src = fx_series(mode)
    expected = {m: calendars.expected_last_session(m, now).isoformat() for m in ("US", "MX", "CRYPTO")}
    # series de referencia (benchmarks)
    bench_cache = {}

    def bench_series(ps):
        if ps not in bench_cache:
            b = db.q1("SELECT id FROM instruments WHERE provider_symbol=?", (ps,))
            rows = load_series(b["id"]) if b else []
            bench_cache[ps] = ([r["date"] for r in rows], [r["adj_close"] for r in rows])
        return bench_cache[ps]

    out = []
    for inst in insts:
        rows = load_series(inst["id"])
        if len(rows) < 2:
            continue
        m = metrics_for(inst, rows, fxd, fxv, expected, bench_series, is_demo)
        m["fx_source"] = fx_src
        out.append(m)
    return out


def metrics_for(inst, rows, fxd, fxv, expected, bench_series, is_demo=0):
    market = inst["exchange"]
    crypto = market == "CRYPTO"
    periods = ind.PERIODS_CRYPTO if crypto else ind.PERIODS_SESSIONS
    annual = 365 if crypto else 252
    dates = [r["date"] for r in rows]
    closes = [r["close"] for r in rows]
    adj = [r["adj_close"] if r["adj_close"] else r["close"] for r in rows]
    vols = [r["volume"] for r in rows]
    splits = [(s["date"], s["ratio"]) for s in db.q("SELECT date, ratio FROM splits WHERE instrument_id=?", (inst["id"],))]
    price_only = ind.split_adjusted_closes(dates, closes, splits)
    jumps = ind.unexplained_jumps(dates, closes, adj, splits)

    last_date = dates[-1]
    m = {k: inst[k] for k in ("id", "provider_symbol", "ticker", "name", "exchange", "venue", "currency", "type", "category",
                              "crypto_exposure", "gbm_modality", "gbm_status", "gbm_source", "gbm_verified_at", "is_leveraged", "isin", "is_benchmark", "group_key")}
    m["data_source"] = rows[-1]["source"]
    m["last_date"] = last_date
    m["last_close"] = closes[-1]
    m["prev_date"] = dates[-2]
    m["sessions"] = len(rows)
    m["expected_last_session"] = expected[market]
    exp = date.fromisoformat(expected[market])
    ld = date.fromisoformat(last_date)
    m["stale_sessions"] = 0 if ld >= exp else (calendars.trading_days_between(market, ld, exp))
    m["return_basis"] = "total (con dividendos, adjusted_close)"
    m["price_return_uncertain"] = bool(jumps)
    m["unexplained_jumps"] = jumps[-3:]

    m["ret_total"], m["ret_price"], m["ret_mxn"], m["period_dates"] = {}, {}, {}, {}
    for p, n in periods.items():
        if crypto and p != "1d":
            # en cripto se usan días naturales: buscar el punto n días antes
            target = date.fromordinal(ld.toordinal() - n).isoformat()
            sd, sv = ind.value_at_or_before(dates, adj, target)
            r = (adj[-1] / sv - 1) if sv and sd and (date.fromisoformat(target) - date.fromisoformat(sd)).days <= 3 else None
            rp = r
        else:
            r = ind.period_return(adj, n)
            rp = None if jumps and any(j > dates[-1 - n] for j in jumps if len(dates) > n) else ind.period_return(price_only, n)
            sd = dates[-1 - n] if len(dates) > n else None
        m["ret_total"][p] = r
        m["ret_price"][p] = rp
        m["period_dates"][p] = [sd, last_date] if r is not None else None
        if r is None:
            m["ret_mxn"][p] = None
        elif inst["currency"] == "MXN":
            m["ret_mxn"][p] = r
        else:
            f0, f1 = _fx_at(fxd, fxv, sd), _fx_at(fxd, fxv, last_date)
            m["ret_mxn"][p] = (1 + r) * (f1 / f0) - 1 if f0 and f1 else None

    m["sma20"], m["sma50"], m["sma200"] = ind.sma(price_only, 20), ind.sma(price_only, 50), ind.sma(price_only, 200)
    m["trend_points"], m["trend_reasons"] = ind.trend_points(price_only[-1], m["sma20"], m["sma50"], m["sma200"])
    m["vol63"] = ind.volatility(adj, 63, annual)
    m["vol252"] = ind.volatility(adj, 252, annual)
    m["daily_vol"] = ind.volatility(adj, 63, 1)
    m["max_dd"], m["dd_window"] = ind.max_drawdown(adj, 365 if crypto else 252)
    m["risk_level"] = risk_level(m["vol63"])

    vals = [c * v for c, v in zip(closes[-20:], vols[-20:]) if c is not None and v is not None]
    m["liq_avg_value_native"] = sum(vals) / len(vals) if len(vals) >= 10 else None
    fx_last = _fx_at(fxd, fxv, last_date)
    m["fx_last"] = fx_last
    m["fx_date"] = ind.value_at_or_before(fxd, fxv, last_date)[0] if fxd else None
    if m["liq_avg_value_native"] is None:
        m["liq_avg_value_mxn"] = None
    elif inst["currency"] == "MXN":
        m["liq_avg_value_mxn"] = m["liq_avg_value_native"]
    else:
        m["liq_avg_value_mxn"] = m["liq_avg_value_native"] * fx_last if fx_last else None
    prev_vols = [v for v in vols[-21:-1] if v]
    m["last_volume"] = vols[-1]
    m["vol_ratio"] = (vols[-1] / (sum(prev_vols) / len(prev_vols))) if prev_vols and vols[-1] is not None and len(prev_vols) >= 10 else None
    m["price_mxn"] = closes[-1] if inst["currency"] == "MXN" else (closes[-1] * fx_last if fx_last else None)
    m["price_usd_eq"] = closes[-1] if inst["currency"] == "USD" else (closes[-1] / fx_last if fx_last else None)

    # cobertura de datos en la ventana analizada
    win = dates[-252:] if not crypto else dates[-365:]
    if crypto:
        expected_n = (date.fromisoformat(win[-1]) - date.fromisoformat(win[0])).days + 1
    else:
        expected_n = calendars.trading_days_between(market, date.fromordinal(date.fromisoformat(win[0]).toordinal() - 1), date.fromisoformat(win[-1]))
    m["coverage"] = min(1.0, len(win) / expected_n) if expected_n else None

    # comparación con referencia en la misma moneda, mismas fechas
    bps = catalog.benchmark_for(inst, is_demo)
    m["benchmark"] = bps
    m["rel"] = {}
    bd, bv = bench_series(bps)
    for p in ("1m", "3m", "1a"):
        pdts = m["period_dates"].get(p)
        if not pdts or not bd or bps == inst["provider_symbol"]:
            m["rel"][p] = None
            continue
        s_d, s_v = ind.value_at_or_before(bd, bv, pdts[0])
        e_d, e_v = ind.value_at_or_before(bd, bv, pdts[1])
        if s_v and e_v and s_d and e_d and (date.fromisoformat(pdts[1]) - date.fromisoformat(e_d)).days <= 4:
            rb = e_v / s_v - 1
            m["rel"][p] = {"instrumento": m["ret_total"][p], "referencia": rb, "diferencia": m["ret_total"][p] - rb,
                           "fechas_referencia": [s_d, e_d]}
        else:
            m["rel"][p] = None
    return m
