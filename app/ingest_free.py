"""Obtención de datos GRATIS: catálogo de EE. UU. desde Nasdaq Trader, catálogo BMV/SIC desde (a) la lista gratuita de
EODHD si hay clave, (b) la semilla data/bmv_semilla.csv, (c) tu catálogo data/catalogo_gbm.csv y (d) detección de
cotizaciones SIC en Yahoo (.MX). Precios de Yahoo Finance (no oficial, uso personal)."""
import csv
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import calendars, catalog, db
from .config import config
from .ingest import HISTORY_DAYS, _store_prices, update_crypto_refs, update_fx
from .providers import eodhd, nasdaq, yahoo
from .providers.http import ProviderError

SRC = "Yahoo Finance (no oficial)"
CHUNK = 150


def refresh_catalog_free(log, force=False):
    last = db.get_setting("catalog_refreshed_at")
    if last and not force and (db.now_utc() - datetime.fromisoformat(last)).days < config.CATALOG_REFRESH_DAYS:
        log(f"Catálogo vigente (actualizado {last[:10]}).")
        return
    sector = catalog._load_sector()
    try:
        rows = nasdaq.us_listings()
        items = [it for it in (catalog.classify(r, "US", sector) for r in rows) if it]
        for it in items:
            it["group_key"] = "T:" + it["ticker"]
        seen = catalog.upsert_instruments(items, "Nasdaq Trader (catálogo) + Yahoo (precios)")
        gone = catalog.deactivate_missing("US", seen)
        log(f"Catálogo EE. UU. (Nasdaq Trader): {len(items)} acciones y ETFs ({gone} dejaron de aparecer).")
    except ProviderError as e:
        log(f"No se pudo descargar el catálogo de EE. UU.: {e}", "warn")
    mx_items = []
    if config.EODHD_API_KEY:
        try:
            for r in eodhd.symbol_list(config.EODHD_MX_EXCHANGE):
                it = catalog.classify(r, "MX", sector)
                if it:
                    if it["gbm_modality"] == "Trading MX (SIC)":
                        it["group_key"] = "T:" + it["ticker"]
                    mx_items.append(it)
            log(f"Catálogo BMV/SIC (lista gratuita de EODHD): {len(mx_items)} instrumentos.")
        except ProviderError as e:
            log(f"Lista BMV de EODHD no disponible con tu plan o clave: {e}", "warn")
    mx_items += _seed_items()
    catalog.upsert_instruments(mx_items, "Catálogo BMV + Yahoo (precios)")
    for ps in catalog.benchmarks(0).values():
        db.ex("UPDATE instruments SET is_benchmark=1 WHERE provider_symbol=?", (ps,))
    n = catalog.apply_official_verifications()
    log(f"Verificados con fuentes oficiales de GBM: {n}.")
    db.set_setting("catalog_refreshed_at", db.iso())


def _seed_items():
    path = os.path.join(catalog.DATA_DIR, "bmv_semilla.csv")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t, tipo = r["ticker"].strip(), r["tipo"].strip()
            sic = tipo == "etf_sic"
            typ = {"accion": "stock", "fibra": "fibra"}.get(tipo, "etf")
            out.append({"provider_symbol": f"{t}.MX", "ticker": t, "exchange": "MX", "venue": "BMV", "name": r["nombre"], "isin": None,
                        "currency": "MXN", "type": typ, "category": {"stock": "accion", "fibra": "fibra"}.get(typ, "etf"),
                        "crypto_exposure": "ninguna", "gbm_modality": "Trading MX (SIC)" if sic else "Trading MX",
                        "group_key": ("T:" + t) if sic else None, "is_leveraged": 0})
    return out


def import_repo_catalog(log):
    """Si existe data/catalogo_gbm.csv (tu lista autorizada), se aplica en cada ejecución."""
    path = os.path.join(catalog.DATA_DIR, "catalogo_gbm.csv")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    # Las emisoras de tu lista que aún no existan se agregan para poder analizarlas
    import io
    for r in csv.DictReader(io.StringIO(text)):
        tk = (r.get("ticker") or "").strip().upper()
        m = (r.get("mercado") or "").lower()
        if not tk:
            continue
        if ("mx" in m or "sic" in m) and not db.q1("SELECT 1 FROM instruments WHERE provider_symbol=?", (f"{tk}.MX",)):
            catalog.upsert_instruments([{"provider_symbol": f"{tk}.MX", "ticker": tk, "exchange": "MX", "venue": "BMV", "name": r.get("nombre") or tk,
                                         "isin": None, "currency": "MXN", "type": "stock", "category": "accion", "crypto_exposure": "ninguna",
                                         "gbm_modality": "Trading MX (SIC)" if "sic" in m else "Trading MX",
                                         "group_key": ("T:" + tk) if "sic" in m else None}], "Catálogo importado + Yahoo (precios)")
    res = catalog.import_catalog_csv(text, "data/catalogo_gbm.csv")
    log(f"Catálogo autorizado (data/catalogo_gbm.csv): {res['coinciden']} verificados, {res['sin_coincidencia']} sin coincidencia.")


def _apply_result(inst, res, mode):
    """Guarda el resultado de Yahoo. mode='backfill' reemplaza el historial; 'update' agrega sesiones nuevas."""
    if isinstance(res, Exception) or res is None:
        n = (inst["fail_count"] or 0) + 1
        if n >= 3 and res is None:
            db.ex("UPDATE instruments SET fail_count=?, active=0, data_note=? WHERE id=?", (n, "Sin datos en Yahoo tras 3 intentos", inst["id"]))
        else:
            db.ex("UPDATE instruments SET fail_count=? WHERE id=?", (n, inst["id"]))
        return "error" if isinstance(res, Exception) else "sin_datos"
    rows, events, _meta = res
    if not rows:
        return "sin_datos"
    if mode == "backfill":
        _store_prices(inst["id"], rows, SRC, replace=True)
        db.ex("DELETE FROM splits WHERE instrument_id=?", (inst["id"],))  # el 'close' de Yahoo ya viene ajustado por splits
        db.ex("UPDATE instruments SET needs_backfill=0, fail_count=0, last_backfill_at=?, data_note=NULL WHERE id=?", (db.iso(), inst["id"]))
        return "ok"
    last = db.q1("SELECT MAX(date) d FROM prices WHERE instrument_id=?", (inst["id"],))["d"]
    stored = {r["date"]: r for r in db.q("SELECT date, close, adj_close FROM prices WHERE instrument_id=? AND date>=?", (inst["id"], rows[0]["date"]))}
    for r in rows:  # si Yahoo cambió el historial (dividendo o split), se recarga completo
        o = stored.get(r["date"])
        if o and o["adj_close"] and r["adjusted_close"] and (abs(r["adjusted_close"] / o["adj_close"] - 1) > 0.002 or abs(r["close"] / o["close"] - 1) > 0.002):
            db.ex("UPDATE instruments SET needs_backfill=1 WHERE id=?", (inst["id"],))
            return "recargar"
    if any(d > (last or "") for d in events["dividends"]) or any(d > (last or "") for d, _ in events["splits"]):
        db.ex("UPDATE instruments SET needs_backfill=1 WHERE id=?", (inst["id"],))
        return "recargar"
    new = [r for r in rows if r["date"] > (last or "")]
    if new:
        _store_prices(inst["id"], new, SRC)
    db.ex("UPDATE instruments SET fail_count=0 WHERE id=?", (inst["id"],))
    return "ok" if new else "sin_nuevos"


def _run_batches(log, insts, start_of, mode):
    stats = {}
    for i in range(0, len(insts), CHUNK):
        chunk = insts[i:i + CHUNK]
        log.heartbeat()
        start = min(start_of(x) for x in chunk)  # un solo inicio por lote: más paralelismo, pocos días extra
        res = yahoo.many([yahoo.yahoo_symbol(x) for x in chunk], start)
        by_sym = {inst["id"]: res.get(yahoo.yahoo_symbol(inst)) for inst in chunk}
        errors = 0
        for inst in chunk:
            st = _apply_result(inst, by_sym.get(inst["id"]), mode)
            stats[st] = stats.get(st, 0) + 1
            errors += st == "error"
        if errors > len(chunk) * 0.6:
            log(f"Yahoo rechazó {errors} de {len(chunk)} consultas seguidas; se detiene para no ser bloqueado. El resto queda pendiente.", "warn")
            stats["detenido"] = True
            break
    return stats


def daily_update(log, now):
    today_local = now.astimezone(ZoneInfo("America/Matamoros")).date()
    monday = today_local.weekday() == 0
    favs = {r["instrument_id"] for r in db.q("SELECT instrument_id FROM favorites")}
    for ex in ("US", "MX"):
        expected = calendars.expected_last_session(ex, now).isoformat()
        rows = db.q("""SELECT i.*, (SELECT MAX(date) FROM prices p WHERE p.instrument_id=i.id) last_date FROM instruments i
                       WHERE i.exchange=? AND i.active=1 AND i.is_demo=0 AND i.needs_backfill=0""", (ex,))
        todo, weekly_skipped = [], 0
        for r in rows:
            if not r["last_date"] or r["last_date"] >= expected:
                continue
            daily = (r["liq_mxn"] is None or r["liq_mxn"] >= 500_000 or r["gbm_status"] == "verificado" or r["is_benchmark"]
                     or r["crypto_exposure"] != "ninguna" or r["id"] in favs)
            stale_week = r["last_date"] < (date.fromisoformat(expected) - timedelta(days=7)).isoformat()
            if daily or monday or stale_week:
                todo.append(r)
            else:
                weekly_skipped += 1
        if not todo:
            log(f"{ex}: datos al día (sesión esperada {expected}).")
            continue
        stats = _run_batches(log, todo, lambda r: date.fromisoformat(r["last_date"]) - timedelta(days=7), "update")
        log(f"{ex}: actualización diaria de {len(todo)} instrumentos: {stats}. Poco líquidos que se actualizan los lunes: {weekly_skipped}.")


def backfill(log):
    todo = db.q("""SELECT * FROM instruments WHERE needs_backfill=1 AND active=1 AND is_demo=0 AND exchange IN ('US','MX')
                   ORDER BY is_benchmark DESC, (gbm_status='verificado') DESC, (crypto_exposure!='ninguna') DESC, (exchange='MX') DESC,
                   (type='etf') DESC, ticker LIMIT ?""", (config.MAX_BACKFILL_PER_RUN,))
    if not todo:
        return
    start = date.today() - timedelta(days=HISTORY_DAYS)
    stats = _run_batches(log, todo, lambda r: start, "backfill")
    pending = db.q1("SELECT COUNT(*) n FROM instruments WHERE needs_backfill=1 AND active=1 AND is_demo=0")["n"]
    log(f"Historial descargado: {stats}. Pendientes para siguientes ejecuciones: {pending}.")


def sic_probe(log):
    """Busca en Yahoo la cotización SIC (.MX) de instrumentos líquidos de EE. UU. que aún no la tienen."""
    cutoff = (db.now_utc() - timedelta(days=30)).isoformat()
    cands = db.q("""SELECT * FROM instruments u WHERE u.exchange='US' AND u.active=1 AND u.is_demo=0 AND COALESCE(u.liq_mxn,0) >= 20000000
                    AND (u.sic_probed_at IS NULL OR u.sic_probed_at < ?)
                    AND NOT EXISTS (SELECT 1 FROM instruments m WHERE m.exchange='MX' AND m.ticker=u.ticker)
                    ORDER BY u.liq_mxn DESC LIMIT ?""", (cutoff, config.SIC_PROBE_PER_RUN))
    if not cands:
        return
    start = date.today() - timedelta(days=20)
    res = yahoo.many([f"{c['ticker'].replace('.', '-')}.MX" for c in cands], start)
    found = 0
    recent = (date.today() - timedelta(days=12)).isoformat()
    for c in cands:
        db.ex("UPDATE instruments SET sic_probed_at=? WHERE id=?", (db.iso(), c["id"]))
        r = res.get(f"{c['ticker'].replace('.', '-')}.MX")
        if isinstance(r, tuple) and r[0] and r[0][-1]["date"] >= recent and (r[2].get("currency") in (None, "MXN")):
            catalog.upsert_instruments([{"provider_symbol": f"{c['ticker']}.MX", "ticker": c["ticker"], "exchange": "MX", "venue": "BMV (SIC)",
                                         "name": c["name"], "isin": None, "currency": "MXN", "type": c["type"], "category": c["category"],
                                         "crypto_exposure": c["crypto_exposure"], "gbm_modality": "Trading MX (SIC)", "is_leveraged": c["is_leveraged"],
                                         "group_key": c["group_key"] or "T:" + c["ticker"]}], "Yahoo (cotización SIC detectada)")
            found += 1
    catalog.apply_official_verifications()
    log(f"Detección SIC: revisados {len(cands)}, encontrados {found} (su historial se descarga en la siguiente fase).")


def ingest_yahoo(log, now):
    refresh_catalog_free(log)
    import_repo_catalog(log)
    daily_update(log, now)
    backfill(log)
    sic_probe(log)
    backfill(log)  # historial de cotizaciones SIC recién detectadas (si queda cupo)
    update_fx(log)
    update_crypto_refs(log)
