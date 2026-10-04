"""Obtención de datos: catálogo, historial, actualización diaria por lotes, tipo de cambio y referencias cripto.
Separado de los cálculos (indicators/scoring) y del informe (report)."""
from datetime import date, timedelta

from . import calendars, catalog, db
from .config import config
from .providers import banxico, coingecko, eodhd
from .providers.http import BudgetExceeded, ProviderError

HISTORY_DAYS = 420  # ~290 sesiones: alcanza para 1 año + media de 200


class Log:
    def __init__(self, run_id=None, heartbeat=None):
        self.run_id = run_id
        self.warnings = []
        self.heartbeat = heartbeat or (lambda: None)

    def __call__(self, msg, level="info"):
        self.heartbeat()
        db.ex("INSERT INTO run_events(run_id,ts,level,message) VALUES(?,?,?,?)", (self.run_id, db.iso(), level, msg[:2000]))
        if level in ("warn", "error"):
            self.warnings.append(msg)
        print(f"[{level}] {msg}", flush=True)


def _store_prices(inst_id, rows, source, replace=False):
    now = db.iso()
    if replace:
        db.ex("DELETE FROM prices WHERE instrument_id=?", (inst_id,))
    db.exmany("""INSERT INTO prices(instrument_id,date,open,high,low,close,adj_close,volume,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(instrument_id,date) DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                 adj_close=excluded.adj_close, volume=excluded.volume, source=excluded.source, fetched_at=excluded.fetched_at""",
              [(inst_id, r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("adjusted_close", r.get("close")), r.get("volume"), source, now) for r in rows if r.get("close") is not None])


# ---------------------------------------------------------------- REAL (EODHD + Banxico + CoinGecko)

def refresh_catalog(log, force=False):
    last = db.get_setting("catalog_refreshed_at")
    if last and not force:
        from datetime import datetime
        age = db.now_utc() - datetime.fromisoformat(last)
        if age.days < config.CATALOG_REFRESH_DAYS:
            log(f"Catálogo vigente (actualizado {last}); siguiente revisión en {config.CATALOG_REFRESH_DAYS - age.days} días.")
            return
    sector = catalog._load_sector()
    for ex_key, ex_code in (("US", config.EODHD_US_EXCHANGE), ("MX", config.EODHD_MX_EXCHANGE)):
        try:
            rows = eodhd.symbol_list(ex_code)
        except ProviderError as e:
            log(f"No se pudo descargar el catálogo {ex_key}: {e}", "warn")
            continue
        items = []
        for r in rows:
            if ex_key == "US" and (r.get("Exchange") or "").upper() not in {v.upper() for v in config.US_VENUES}:
                continue  # fuera de bolsas principales (OTC, etc.)
            it = catalog.classify(r, ex_key, sector)
            if it:
                items.append(it)
        seen = catalog.upsert_instruments(items, "EODHD")
        gone = catalog.deactivate_missing(ex_key, seen)
        log(f"Catálogo {ex_key}: {len(items)} acciones/ETFs/FIBRAS identificados ({gone} dejaron de aparecer).")
    for ps in catalog.BENCHMARKS.values():
        db.ex("UPDATE instruments SET is_benchmark=1 WHERE provider_symbol=?", (ps,))
    n = catalog.apply_official_verifications()
    log(f"Verificados con fuentes oficiales de GBM: {n}.")
    db.set_setting("catalog_refreshed_at", db.iso())


def _backfill_one(inst):
    start = date.today() - timedelta(days=HISTORY_DAYS)
    rows = eodhd.eod_history(inst["provider_symbol"], start)
    _store_prices(inst["id"], rows, "EODHD (EOD)", replace=True)
    sp = eodhd.split_history(inst["provider_symbol"], start - timedelta(days=30))
    db.ex("DELETE FROM splits WHERE instrument_id=?", (inst["id"],))
    for s in sp:
        ratio = eodhd.parse_split(s.get("split"))
        if ratio:
            db.ex("INSERT OR REPLACE INTO splits(instrument_id,date,ratio,source) VALUES(?,?,?,?)", (inst["id"], s["date"], ratio, "EODHD"))
    db.ex("UPDATE instruments SET needs_backfill=0, last_backfill_at=? WHERE id=?", (db.iso(), inst["id"]))
    return len(rows)


def backfill(log, limit=None):
    limit = limit or config.MAX_BACKFILL_PER_RUN
    todo = db.q("""SELECT * FROM instruments WHERE needs_backfill=1 AND active=1 AND is_demo=0 AND exchange IN ('US','MX')
                   ORDER BY is_benchmark DESC, (gbm_status='verificado') DESC, (crypto_exposure!='ninguna') DESC,
                   (exchange='MX') DESC, (type='etf') DESC, ticker LIMIT ?""", (limit,))
    done = errors = 0
    for k, inst in enumerate(todo):
        if k % 25 == 0:
            log.heartbeat()
        try:
            _backfill_one(inst)
            done += 1
        except BudgetExceeded as e:
            log(str(e) + " El resto queda pendiente para la siguiente ejecución.", "warn")
            break
        except ProviderError as e:
            errors += 1
            if errors <= 10:
                log(f"Historial no disponible para {inst['provider_symbol']}: {e}", "warn")
    pending = db.q1("SELECT COUNT(*) n FROM instruments WHERE needs_backfill=1 AND active=1 AND is_demo=0")["n"]
    log(f"Historial descargado para {done} instrumentos ({errors} con error). Pendientes: {pending}.")


def daily_update(log, now):
    """Agrega los cierres faltantes con consultas por lotes (1 consulta = toda la bolsa)."""
    for ex_key, ex_code in (("US", config.EODHD_US_EXCHANGE), ("MX", config.EODHD_MX_EXCHANGE)):
        expected = calendars.expected_last_session(ex_key, now)
        r = db.q1("""SELECT MAX(p.date) d FROM prices p JOIN instruments i ON i.id=p.instrument_id
                     WHERE i.exchange=? AND i.is_benchmark=1 AND i.is_demo=0""", (ex_key,))
        last = date.fromisoformat(r["d"]) if r and r["d"] else None
        if last is None:
            log(f"{ex_key}: aún sin historial de referencia; se usará descarga individual.")
            continue
        days, d = [], last
        while d < expected:
            d += timedelta(days=1)
            if calendars.is_trading_day(ex_key, d):
                days.append(d)
        if not days:
            log(f"{ex_key}: datos al día (último cierre {last}).")
            continue
        if len(days) > 5:
            db.ex("UPDATE instruments SET needs_backfill=1 WHERE exchange=? AND is_demo=0 AND active=1", (ex_key,))
            log(f"{ex_key}: faltan {len(days)} sesiones; se recargará el historial completo.", "warn")
            continue
        sym_map = {row["provider_symbol"]: row["id"] for row in db.q("SELECT id, provider_symbol FROM instruments WHERE exchange=? AND is_demo=0 AND needs_backfill=0", (ex_key,))}
        for day in days:
            try:
                rows = eodhd.bulk_day(ex_code, day, "eod")
            except ProviderError as e:
                log(f"{ex_key} {day}: no se pudo obtener el lote de cierres: {e}", "warn")
                break
            got = 0
            for row in rows:
                if row.get("date") != day.isoformat():
                    continue  # el proveedor aún no publica esa sesión (o es festivo no previsto)
                iid = sym_map.get(f"{row.get('code')}.{ex_key}")
                if iid:
                    if row.get("adjusted_close") is None and row.get("close"):
                        prev = db.q1("SELECT close, adj_close FROM prices WHERE instrument_id=? AND date<? ORDER BY date DESC LIMIT 1", (iid, row["date"]))
                        ratio = (prev["adj_close"] / prev["close"]) if prev and prev["close"] and prev["adj_close"] else 1.0
                        row = dict(row, adjusted_close=row["close"] * ratio)
                    _store_prices(iid, [row], "EODHD (EOD, lote diario)")
                    got += 1
            log(f"{ex_key} {day}: {got} cierres agregados por lote.")
            # Splits o dividendos cambian el historial ajustado: se recarga el historial de esos instrumentos.
            for kind in ("splits", "dividends"):
                try:
                    ev = eodhd.bulk_day(ex_code, day, kind)
                except ProviderError as e:
                    log(f"{ex_key} {day}: no se pudo revisar {kind}: {e}", "warn")
                    continue
                flagged = 0
                for row in ev:
                    iid = sym_map.get(f"{row.get('code')}.{ex_key}")
                    if iid:
                        db.ex("UPDATE instruments SET needs_backfill=1 WHERE id=?", (iid,))
                        flagged += 1
                if flagged:
                    log(f"{ex_key} {day}: {flagged} instrumentos con {kind}; su historial ajustado se recargará.")


def update_fx(log):
    r = db.q1("SELECT MAX(date) d FROM fx")
    start = (date.fromisoformat(r["d"]) - timedelta(days=10)) if r and r["d"] else date.today() - timedelta(days=HISTORY_DAYS)
    rows, source = [], None
    if config.BANXICO_TOKEN:
        try:
            rows, source = banxico.fix_range(start, date.today()), "Banxico SIE – FIX (SF43718)"
        except ProviderError as e:
            log(f"Banxico no respondió: {e}", "warn")
    if not rows and config.PRICE_PROVIDER == "yahoo":
        from .providers import yahoo
        try:
            res = yahoo.chart("MXN=X", start)
            if res:
                rows, source = [(x["date"], x["close"]) for x in res[0]], "Yahoo Finance (USD/MXN, no oficial)"
        except ProviderError as e:
            log(f"Tipo de cambio de Yahoo no disponible: {e}", "warn")
    if not rows and config.EODHD_API_KEY and config.PRICE_PROVIDER != "yahoo":
        try:
            data = eodhd.eod_history("USDMXN.FOREX", start)
            rows, source = [(x["date"], x["close"]) for x in data if x.get("close")], "EODHD (USDMXN.FOREX, cierre)"
        except ProviderError as e:
            log(f"Tipo de cambio alterno no disponible: {e}", "warn")
    if rows:
        db.exmany("INSERT OR REPLACE INTO fx(date,usd_mxn,source,fetched_at) VALUES(?,?,?,?)", [(d, v, source, db.iso()) for d, v in rows])
        log(f"Tipo de cambio actualizado ({source}): {len(rows)} datos.")
    else:
        log("Tipo de cambio USD/MXN no disponible: los importes convertidos mostrarán 'Dato no disponible'.", "warn")


def update_crypto_refs(log):
    catalog.ensure_crypto_refs(0)
    for sym, (cid, _name) in coingecko.COINS.items():
        inst = db.q1("SELECT id FROM instruments WHERE provider_symbol=?", (f"{sym}-REF",))
        try:
            series = coingecko.daily_series(cid, 365)
        except ProviderError as e:
            log(f"CoinGecko {sym}: {e}", "warn")
            continue
        _store_prices(inst["id"], [{"date": d, "close": p, "adjusted_close": p, "volume": v} for d, p, v in series],
                      "CoinGecko (precio 00:00 UTC)", replace=True)
        db.ex("UPDATE instruments SET needs_backfill=0, last_backfill_at=? WHERE id=?", (db.iso(), inst["id"]))
        log(f"Referencia {sym}: {len(series)} días.")


def enrich_highlights(log, inst_ids):
    """Fundamentales, noticias y próximos reportes solo para los destacados (ahorra llamadas)."""
    if not inst_ids:
        return
    insts = [db.q1("SELECT * FROM instruments WHERE id=?", (i,)) for i in inst_ids]
    insts = [i for i in insts if i and i["exchange"] in ("US", "MX") and not i["is_demo"]]
    if config.EODHD_FUNDAMENTALS:
        for inst in insts:
            try:
                data = eodhd.fundamentals(inst["provider_symbol"], etf=inst["type"] == "etf")
                if data:
                    import json
                    db.ex("INSERT OR REPLACE INTO fundamentals(instrument_id,data,source,fetched_at) VALUES(?,?,?,?)",
                          (inst["id"], json.dumps(data), "EODHD Fundamentals", db.iso()))
            except ProviderError as e:
                log(f"Fundamentales no disponibles para {inst['provider_symbol']}: {e}", "warn")
    if config.EODHD_NEWS:
        for inst in insts:
            try:
                for n in eodhd.news(inst["provider_symbol"], 3):
                    db.ex("INSERT OR IGNORE INTO news(instrument_id,kind,date,title,url,source,fetched_at) VALUES(?,?,?,?,?,?,?)",
                          (inst["id"], "noticia", (n.get("date") or "")[:19], n.get("title"), n.get("link"), "EODHD News", db.iso()))
            except ProviderError as e:
                log(f"Noticias no disponibles para {inst['provider_symbol']}: {e}", "warn")
        try:
            us = [i["provider_symbol"] for i in insts if i["type"] == "stock"]
            if us:
                for ev in eodhd.earnings_calendar(us, date.today()):
                    inst = next((i for i in insts if i["provider_symbol"] == ev.get("code")), None)
                    if inst:
                        db.ex("INSERT OR IGNORE INTO news(instrument_id,kind,date,title,url,source,fetched_at) VALUES(?,?,?,?,?,?,?)",
                              (inst["id"], "evento", ev.get("report_date"), f"Reporte de resultados ({ev.get('before_after_market') or 'horario no indicado'})",
                               None, "EODHD Calendario", db.iso()))
        except ProviderError as e:
            log(f"Calendario de eventos no disponible: {e}", "warn")


def ingest_real(log, now):
    if config.PRICE_PROVIDER == "yahoo":
        from .ingest_free import ingest_yahoo
        return ingest_yahoo(log, now)
    if not config.EODHD_API_KEY:
        raise ProviderError("Falta EODHD_API_KEY: no hay fuente de precios reales configurada.")
    refresh_catalog(log)
    daily_update(log, now)
    backfill(log)
    update_fx(log)
    update_crypto_refs(log)


# ---------------------------------------------------------------- DEMO (datos sintéticos, siempre separados)

def ingest_demo(log, now):
    from . import demo
    demo.generate(now)
    log("Modo DEMOSTRACIÓN: se generaron datos sintéticos (no son precios reales).", "warn")
