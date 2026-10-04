"""Datos de DEMOSTRACIÓN. Son sintéticos, con tickers ficticios que empiezan con "DEMO" y nombres
marcados [DEMO]. Se guardan con is_demo=1 y sus reportes con mode='demo'; nunca se mezclan con datos reales."""
import math
import random
import zlib
from datetime import date, timedelta

from . import calendars, catalog, db

SRC = "DEMO (sintético)"


def _instruments():
    L = []

    def add(ps, name, ex, typ, cat, cur="USD", isin=None, mod=None, expo="ninguna", lev=0, bench=0, drift=0.08, vol=0.25,
            vol_base=2e6, price=50, **extra):
        L.append(dict(provider_symbol=ps, ticker=ps.split(".")[0], exchange=ex, venue="DEMO", name=name + " [DEMO]", isin=isin,
                      currency=cur, type=typ, category=cat, crypto_exposure=expo,
                      gbm_modality=mod or ("Trading USA" if ex == "US" else "Trading MX"), gbm_status="pendiente",
                      is_leveraged=lev, is_benchmark=bench, _drift=drift, _vol=vol, _vb=vol_base, _p=price, **extra))

    add("DEMO-IDX-USD.US", "Índice Demo EE. UU. (ETF de referencia)", "US", "etf", "etf", bench=1, drift=0.09, vol=0.16, vol_base=6e7, price=500)
    add("DEMO-IDX-MX.MX", "Índice Demo México (ETF de referencia)", "MX", "etf", "etf", "MXN", "MXDEMOIDX001", bench=1, drift=0.06, vol=0.15, vol_base=3e6, price=55)
    add("DEMO-IDX-SIC.MX", "Índice Demo EE. UU. en SIC (referencia)", "MX", "etf", "etf", "MXN", "USDEMOIDX001", "Trading MX (SIC)", bench=1, drift=0.09, vol=0.17, vol_base=4e5, price=9500)
    for i, ch in enumerate("ABCDEFGHIJKL"):
        add(f"DEMO{ch}.US", f"Empresa Demo {ch}", "US", "stock", "accion", isin=f"USDEMO00000{i:02d}",
            drift=-0.15 + 0.06 * i, vol=0.2 + 0.03 * (i % 5), vol_base=1e6 * (1 + i), price=20 + 15 * i)
    for i in range(1, 9):
        add(f"DEMOETF{i}.US", f"ETF Demo {i}", "US", "etf", "etf", isin=f"USDEMOETF0{i:02d}", drift=0.02 + 0.02 * i, vol=0.12 + 0.02 * i,
            vol_base=5e6, price=40 + 10 * i)
    add("DEMO3X.US", "ETF Demo 3X Apalancado Diario", "US", "etf", "etf", lev=1, drift=0.2, vol=0.6, vol_base=8e6, price=30)
    for i in range(1, 4):
        add(f"DEMOBTC{i}.US", f"ETF Demo Bitcoin {i}", "US", "etf", "etf", expo="etf_cripto", isin=f"USDEMOBTC0{i:02d}",
            drift=0.35, vol=0.5 + 0.03 * i, vol_base=1e7, price=40 + 5 * i, _corr="btc")
    add("DEMOMIN.US", "Minera Demo Cripto", "US", "stock", "accion", expo="accion_sector_cripto", drift=0.3, vol=0.8, vol_base=6e6, price=15)
    add("DEMOEXC.US", "Casa de Cambio Demo Cripto", "US", "stock", "accion", expo="accion_sector_cripto", drift=0.2, vol=0.6, vol_base=4e6, price=200)
    for i, ch in enumerate("MNOPQR"):
        add(f"DEMO{ch}MX.MX", f"Emisora Demo México {ch}", "MX", "stock", "accion", "MXN", f"MXDEMO{i:06d}", drift=-0.05 + 0.04 * i,
            vol=0.2 + 0.04 * i, vol_base=2e6, price=30 + 20 * i)
    add("DEMOFIBRA.MX", "FIBRA Demo Inmobiliaria", "MX", "fibra", "fibra", "MXN", "MXDEMOFIB001", drift=0.04, vol=0.18, vol_base=1e6, price=28)
    # Cotizaciones SIC de instrumentos demo de EE. UU. (mismo ISIN, precio en MXN)
    add("DEMOC.MX", "Empresa Demo C (SIC)", "MX", "stock", "accion", "MXN", "USDEMO0000002", "Trading MX (SIC)", vol_base=3e5, _mirror="DEMOC.US")
    add("DEMOETF3.MX", "ETF Demo 3 (SIC)", "MX", "etf", "etf", "MXN", "USDEMOETF003", "Trading MX (SIC)", vol_base=2e5, _mirror="DEMOETF3.US")
    # Casos especiales para probar manejo de datos
    add("DEMONEW.US", "Empresa Demo Recién Listada", "US", "stock", "accion", drift=0.5, vol=0.5, price=12, _len=90)
    add("DEMOSTALE.US", "Empresa Demo Sin Datos Recientes", "US", "stock", "accion", _stale=12)
    add("DEMOSPLIT.US", "Empresa Demo con Split 4:1", "US", "stock", "accion", drift=0.25, vol=0.3, price=400, _split=4.0)
    add("DEMOGAP.MX", "Emisora Demo con Huecos de Datos", "MX", "stock", "accion", "MXN", "MXDEMOGAP001", _gaps=0.3)
    add("DEMOILLQ.MX", "Emisora Demo Poco Líquida", "MX", "stock", "accion", "MXN", "MXDEMOILQ001", vol_base=800, price=12, drift=0.6)
    return L


def _rng(key):
    return random.Random(zlib.crc32(key.encode()))


def _sessions(market, end, n):
    out, d = [], end
    while len(out) < n:
        if calendars.is_trading_day(market, d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _walk(key, days, drift, vol, p0, annual=252, shocks=None):
    r = _rng(key)
    p, out = p0, []
    for i, d in enumerate(days):
        z = r.gauss(0, 1)
        if shocks is not None:
            z = 0.7 * shocks[i % len(shocks)] + 0.71 * z
        p *= math.exp((drift - 0.5 * vol * vol) / annual + vol / math.sqrt(annual) * z)
        out.append(p)
    return out


def generate(now):
    insts = _instruments()
    catalog.upsert_instruments([{k: v for k, v in x.items() if not k.startswith("_")} for x in insts], SRC, is_demo=1)
    catalog.ensure_crypto_refs(is_demo=1)
    ids = {r["provider_symbol"]: r["id"] for r in db.q("SELECT id, provider_symbol FROM instruments WHERE is_demo=1")}
    # Referencias cripto (todos los días, 24/7)
    end_c = calendars.expected_last_session("CRYPTO", now)
    cdays = [end_c - timedelta(days=i) for i in range(364, -1, -1)]
    rb = _rng("btc-shocks")
    btc_shocks = [rb.gauss(0, 1) for _ in range(400)]
    for sym, p0, dr, vo in (("BTC-REF-DEMO", 60000, 0.4, 0.55), ("ETH-REF-DEMO", 3000, 0.3, 0.7)):
        px = _walk(sym, cdays, dr, vo, p0, 365, btc_shocks if sym.startswith("BTC") else None)
        _replace(ids[sym], [(d, p, p, 3e10) for d, p in zip(cdays, px)])
    # FX demo
    end_mx = calendars.expected_last_session("MX", now)
    fdays = _sessions("MX", end_mx, 300)
    fx = _walk("fx", fdays, 0.0, 0.10, 18.5)
    db.ex("DELETE FROM fx_demo")
    db.exmany("INSERT INTO fx_demo(date,usd_mxn,source,fetched_at) VALUES(?,?,?,?)", [(d.isoformat(), v, SRC, db.iso()) for d, v in zip(fdays, fx)])
    fxm = {d.isoformat(): v for d, v in zip(fdays, fx)}
    series = {}
    for x in insts:
        if x.get("_mirror"):
            continue
        mk = x["exchange"]
        end = calendars.expected_last_session(mk, now) - timedelta(days=x.get("_stale", 0))
        n = x.get("_len", 300)
        days = _sessions(mk, end, n)
        shocks = btc_shocks if x.get("_corr") == "btc" else None
        px = _walk(x["provider_symbol"], days, x["_drift"], x["_vol"], x["_p"], 252, shocks)
        r = _rng(x["provider_symbol"] + "v")
        rows = []
        div_factor = 1.0
        for i, (d, p) in enumerate(zip(days, px)):
            if x.get("_gaps") and r.random() < x["_gaps"] and i < len(days) - 3:
                continue
            close = p
            if x.get("_split") and i < len(days) // 2:
                close = p * x["_split"]  # antes del split el precio nominal era 4 veces mayor
            vol = x["_vb"] * math.exp(r.gauss(0, 0.4)) * (3.5 if i == len(days) - 1 and x["provider_symbol"] == "DEMOE.US" else 1)
            rows.append((d, close, p * (0.985 if x["type"] in ("etf", "fibra") else 1.0) ** ((len(days) - i) / 252), vol))
        series[x["provider_symbol"]] = rows
        _replace(ids[x["provider_symbol"]], rows)
        if x.get("_split"):
            sd = days[len(days) // 2]
            db.ex("DELETE FROM splits WHERE instrument_id=?", (ids[x["provider_symbol"]],))
            db.ex("INSERT INTO splits(instrument_id,date,ratio,source) VALUES(?,?,?,?)", (ids[x["provider_symbol"]], sd.isoformat(), x["_split"], SRC))
    for x in insts:
        if not x.get("_mirror"):
            continue
        base = series[x["_mirror"]]
        r = _rng(x["provider_symbol"])
        rows = []
        for d, c, a, _v in base:
            f = fxm.get(d.isoformat())
            if f and calendars.is_trading_day("MX", d):
                prem = 1 + r.uniform(-0.004, 0.006)
                rows.append((d, c * f * prem, a * f * prem, x["_vb"] * math.exp(r.gauss(0, 0.5))))
        _replace(ids[x["provider_symbol"]], rows)
    db.ex("UPDATE instruments SET needs_backfill=0 WHERE is_demo=1")


def _replace(iid, rows):
    db.ex("DELETE FROM prices WHERE instrument_id=?", (iid,))
    db.exmany("INSERT INTO prices(instrument_id,date,close,adj_close,volume,source,fetched_at) VALUES(?,?,?,?,?,?,?)",
              [(iid, d.isoformat() if isinstance(d, date) else d, c, a, v, SRC, db.iso()) for d, c, a, v in rows])
