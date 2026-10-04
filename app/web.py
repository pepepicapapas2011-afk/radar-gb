"""API HTTP + interfaz web (Flask). Ninguna clave de proveedor se envía al navegador."""
import hmac
import json
import math
import os
import threading
from datetime import datetime

from flask import Flask, abort, jsonify, request, send_from_directory

from . import calendars, catalog, db, pipeline, scheduler
from . import indicators as ind
from .config import config

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app = Flask(__name__, static_folder=None)
_metrics_cache = {}


def _clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def ok(data, status=200):
    return app.response_class(json.dumps(_clean(data), default=str, ensure_ascii=False), status=status, mimetype="application/json")


@app.before_request
def _auth():
    p = request.path
    if not p.startswith("/api/") or p in ("/api/health", "/api/cron/tick", "/api/auth"):
        return None
    if config.APP_ACCESS_TOKEN:
        tok = request.headers.get("X-Access-Token", "")
        if not hmac.compare_digest(tok, config.APP_ACCESS_TOKEN):
            return ok({"error": "Se requiere la clave de acceso de la app."}, 401)
    return None


@app.teardown_appcontext
def _td(_e):
    pass


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


@app.get("/api/health")
def health():
    db.q1("SELECT 1")
    return ok({"ok": True, "hora": db.iso()})


@app.post("/api/auth")
def auth_check():
    tok = (request.get_json(silent=True) or {}).get("token", "")
    good = (not config.APP_ACCESS_TOKEN) or hmac.compare_digest(tok, config.APP_ACCESS_TOKEN)
    return ok({"ok": good}, 200 if good else 401)


# ------------------------------------------------------------------ estado y reportes

def _mode():
    m = request.args.get("mode")
    return m if m in ("real", "demo") else config.effective_mode()


@app.get("/api/status")
def status():
    now = db.now_utc()
    mode = config.effective_mode()
    due, why, nxt = pipeline.due_status(now)
    last_ok = db.q1("SELECT * FROM runs WHERE status='exito' AND mode=? ORDER BY id DESC LIMIT 1", (mode,))
    last = db.q1("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    rep = db.q1("SELECT id, report_date, version, created_at FROM reports WHERE mode=? ORDER BY report_date DESC LIMIT 1", (mode,))
    failed_after_ok = bool(last and last["status"] == "fallo" and (not last_ok or last["id"] > last_ok["id"]))
    lk = db.q1("SELECT expires_at FROM locks WHERE name='pipeline'")
    return ok({
        "modo": mode,
        "modo_texto": "Datos reales" if mode == "real" else "DEMOSTRACIÓN (datos sintéticos)",
        "proveedores": {"precios": config.PRICE_PROVIDER, "eodhd": bool(config.EODHD_API_KEY), "banxico": bool(config.BANXICO_TOKEN), "coingecko_clave": bool(config.COINGECKO_API_KEY),
                         "fundamentales": config.EODHD_FUNDAMENTALS, "noticias": config.EODHD_NEWS,
                         "notificaciones_servidor": bool(config.NOTIFY_NTFY_URL or config.NOTIFY_WEBHOOK_URL)},
        "uso_api_hoy": {p: db.usage_today(p) for p in ("eodhd", "yahoo", "banxico", "coingecko")},
        "presupuesto_eodhd": config.EODHD_DAILY_CALL_BUDGET,
        "zona_horaria": config.TIMEZONE,
        "horario": db.get_setting("horario", config.DEFAULT_SCHEDULE),
        "proxima_ejecucion": nxt.isoformat() if nxt else None,
        "estado_programacion": why,
        "programador_interno": config.ENABLE_INTERNAL_SCHEDULER,
        "cron_externo_configurado": bool(config.CRON_SECRET),
        "en_curso": bool(lk and lk["expires_at"] > db.iso()),
        "ultima_ejecucion_exitosa": dict(last_ok) if last_ok else None,
        "ultima_ejecucion": dict(last) if last else None,
        "fallo_reciente": failed_after_ok,
        "ultimo_reporte": dict(rep) if rep else None,
        "mercados": [calendars.market_status(m, now) for m in ("MX", "US", "CRYPTO")],
        "espera_manual_seg": pipeline.manual_cooldown_left(now),
        "proteccion_acceso": bool(config.APP_ACCESS_TOKEN),
    })


def _report_row(rid=None, mode=None):
    if rid:
        return db.q1("SELECT * FROM reports WHERE id=?", (rid,))
    return db.q1("SELECT * FROM reports WHERE mode=? ORDER BY report_date DESC LIMIT 1", (mode or _mode(),))


@app.get("/api/report/latest")
def report_latest():
    r = _report_row()
    if not r:
        return ok({"reporte": None})
    s = json.loads(r["summary"])
    s.update({"id": r["id"], "version": r["version"], "created_at": r["created_at"]})
    return ok({"reporte": s})


@app.get("/api/reports")
def reports_list():
    rows = db.q("SELECT id, report_date, mode, version, created_at, summary FROM reports WHERE mode=? ORDER BY report_date DESC LIMIT 120", (_mode(),))
    out = []
    for r in rows:
        s = json.loads(r["summary"])
        out.append({"id": r["id"], "fecha": r["report_date"], "version": r["version"], "creado": r["created_at"], "modo": r["mode"],
                    "datos_nuevos": s.get("datos_nuevos"), "corte": {k: v.get("ultimo_dato") for k, v in s.get("data_cutoff", {}).items()},
                    "destacados": [x["ticker"] for g in s.get("investigar", {}).values() for x in g.get("items", [])],
                    "alertas": len(s.get("alertas", []))})
    return ok({"reportes": out})


@app.get("/api/reports/<int:rid>")
def report_get(rid):
    r = _report_row(rid)
    if not r:
        abort(404)
    s = json.loads(r["summary"])
    s.update({"id": r["id"], "version": r["version"], "created_at": r["created_at"]})
    return ok({"reporte": s})


# ------------------------------------------------------------------ instrumentos

def _metrics(rid):
    if rid not in _metrics_cache:
        rows = db.q("SELECT instrument_id, data FROM report_metrics WHERE report_id=?", (rid,))
        _metrics_cache.clear()
        _metrics_cache[rid] = {r["instrument_id"]: json.loads(r["data"]) for r in rows}
    return _metrics_cache[rid]


def _budget_info(m, budget):
    if m.get("category") == "cripto_ref":
        return {"texto": "Referencia externa: no se compra en GBM", "alcanza": False}
    price = m.get("price_mxn")
    if m.get("gbm_modality") == "Trading USA":
        return {"texto": "GBM indica que Trading USA permite fracciones; verifica el mínimo de este instrumento en la app.",
                "alcanza": True if budget else None, "fracciones": True}
    if price is None:
        return {"texto": "Dato no disponible", "alcanza": None}
    if not budget:
        return {"texto": f"1 título ≈ {price:,.2f} MXN (acciones completas en Trading MX según GBM)", "alcanza": None, "fracciones": False}
    n = int(budget // price)
    return {"texto": f"Alcanza para {n} título(s) completos de ≈ {price:,.2f} MXN" if n else f"No alcanza: 1 título ≈ {price:,.2f} MXN",
            "alcanza": n > 0, "fracciones": False}


@app.get("/api/instruments")
def instruments():
    r = _report_row(request.args.get("report_id", type=int))
    if not r:
        return ok({"items": [], "total": 0, "reporte": None})
    ms = list(_metrics(r["id"]).values())
    a = request.args
    qtxt = (a.get("q") or "").strip().lower()
    cat, mkt, cur, risk = a.get("category"), a.get("market"), a.get("currency"), a.get("risk")
    period = a.get("period", "1m")
    favs = {x["instrument_id"] for x in db.q("SELECT instrument_id FROM favorites")}
    budget = a.get("budget", type=float) or None
    gbm = a.get("gbm")
    out = []
    for m in ms:
        if qtxt and qtxt not in (m["ticker"] or "").lower() and qtxt not in (m["name"] or "").lower() and qtxt not in (m.get("isin") or "").lower():
            continue
        if cat:
            if cat == "cripto" and m["crypto_exposure"] == "ninguna":
                continue
            if cat != "cripto" and m["category"] != cat:
                continue
        if mkt and m["gbm_modality"] != mkt:
            continue
        if cur and m["currency"] != cur:
            continue
        if risk and m["risk_level"] != risk:
            continue
        if gbm and m["gbm_status"] != gbm:
            continue
        if a.get("favorites") == "1" and m["id"] not in favs:
            continue
        b = _budget_info(m, budget)
        if budget and b.get("alcanza") is False:
            continue
        out.append({**{k: m.get(k) for k in ("id", "ticker", "name", "category", "type", "exchange", "venue", "currency", "gbm_modality", "gbm_status",
                                             "last_close", "last_date", "price_mxn", "risk_level", "vol63", "max_dd", "liq_avg_value_mxn",
                                             "vol_ratio", "trend_points", "stale_sessions", "crypto_exposure", "is_leveraged", "sessions")},
                    "ret": m["ret_total"].get(period), "ret_mxn": m["ret_mxn"].get(period),
                    "score": (m.get("score") or {}).get("total"), "grupo": m.get("group"),
                    "excluido": bool(m.get("exclusions")), "favorito": m["id"] in favs, "presupuesto": b})
    sort, desc = a.get("sort", "ret_mxn"), a.get("dir", "desc") == "desc"
    present = [x for x in out if x.get(sort) is not None]
    missing = [x for x in out if x.get(sort) is None]
    numeric = all(isinstance(x.get(sort), (int, float)) for x in present)
    present.sort(key=(lambda x: x[sort]) if numeric else (lambda x: str(x[sort]).lower()), reverse=desc)
    out = present + missing  # los "Dato no disponible" siempre al final
    page, size = max(1, a.get("page", 1, type=int)), min(200, max(10, a.get("page_size", 50, type=int)))
    return ok({"items": out[(page - 1) * size: page * size], "total": len(out), "pagina": page, "tam": size,
               "reporte": {"id": r["id"], "fecha": r["report_date"], "modo": r["mode"]}, "periodo": period})


@app.get("/api/instruments/<int:iid>")
def instrument_detail(iid):
    inst = db.q1("SELECT * FROM instruments WHERE id=?", (iid,))
    if not inst:
        abort(404)
    r = _report_row(mode="demo" if inst["is_demo"] else "real")
    m = _metrics(r["id"]).get(iid) if r else None
    rows = db.q("SELECT date, close, adj_close, volume, source FROM prices WHERE instrument_id=? ORDER BY date", (iid,))
    rows = rows[-300:] if rows else []
    splits = [(s["date"], s["ratio"]) for s in db.q("SELECT date, ratio FROM splits WHERE instrument_id=?", (iid,))]
    dates = [x["date"] for x in rows]
    po = ind.split_adjusted_closes(dates, [x["close"] for x in rows], splits)
    from . import report as rep
    others = []
    gk = inst["group_key"] or inst["isin"]
    if gk:
        for o in db.q("SELECT * FROM instruments WHERE (group_key=? OR (isin IS NOT NULL AND isin=?)) AND id!=? AND is_demo=?", (gk, inst["isin"] or "-", iid, inst["is_demo"])):
            om = _metrics(r["id"]).get(o["id"]) if r else None
            item = {"id": o["id"], "provider_symbol": o["provider_symbol"], "modalidad": o["gbm_modality"], "moneda": o["currency"],
                    "precio": om["last_close"] if om else None, "fecha": om["last_date"] if om else None}
            if om and m and om.get("fx_last") and om["currency"] != m["currency"]:
                usd = m if m["currency"] == "USD" else om
                mxn = om if usd is m else m
                if usd.get("fx_last"):
                    item["diferencia_sic_vs_origen"] = mxn["last_close"] / (usd["last_close"] * usd["fx_last"]) - 1
                    item["nota"] = (f"Precio en el SIC ({mxn['last_date']}) vs precio de origen × tipo de cambio ({usd['last_date']}, FIX {usd['fx_date']}). "
                                    "Las fechas y horas de cierre pueden no coincidir.")
            others.append(item)
    fund = rep.load_fundamentals(iid)
    det = rep.category_details(m, fund) if m else None
    expl = None
    if m and m.get("score") and "posicion" in m["score"]:
        expl = rep.explain(m, None, pipeline.settings())
    return ok({
        "instrumento": dict(inst), "metricas": m, "explicacion": expl, "detalle_categoria": det,
        "serie": {"fechas": dates, "precio": po, "ajustado": [x["adj_close"] for x in rows], "volumen": [x["volume"] for x in rows],
                  "sma20": ind.sma_series(po, 20), "sma50": ind.sma_series(po, 50), "sma200": ind.sma_series(po, 200),
                  "fuente": rows[-1]["source"] if rows else None},
        "splits": splits, "otras_cotizaciones": others, "noticias": rep.load_news(iid),
        "modalidad_info": catalog.MODALITY_INFO.get(inst["gbm_modality"]),
        "favorito": bool(db.q1("SELECT 1 FROM favorites WHERE instrument_id=?", (iid,))),
        "presupuesto": _budget_info(m, db.get_setting("presupuesto_mxn")) if m else None,
    })


@app.get("/api/compare")
def compare():
    ids = [int(x) for x in (request.args.get("ids") or "").split(",") if x.strip().isdigit()][:3]
    out = []
    for iid in ids:
        inst = db.q1("SELECT * FROM instruments WHERE id=?", (iid,))
        if not inst:
            continue
        rows = db.q("SELECT date, adj_close FROM prices WHERE instrument_id=? ORDER BY date", (iid,))[-253:]
        r = _report_row(mode="demo" if inst["is_demo"] else "real")
        m = _metrics(r["id"]).get(iid) if r else None
        out.append({"id": iid, "ticker": inst["ticker"], "name": inst["name"], "currency": inst["currency"],
                    "fechas": [x["date"] for x in rows], "valores": [x["adj_close"] for x in rows], "metricas": m})
    if out:
        start = max(o["fechas"][0] for o in out if o["fechas"])
        for o in out:
            d0, v0 = ind.value_at_or_before(o["fechas"], o["valores"], start)
            o["base"] = start
            o["norm"] = [[d, v / v0 * 100] for d, v in zip(o["fechas"], o["valores"]) if v0 and d >= start]
            o.pop("valores")
            o.pop("fechas")
    return ok({"series": out, "nota": "Base 100 en la primera fecha común; rendimiento total en la moneda de cada instrumento."})


# ------------------------------------------------------------------ favoritos y configuración

@app.get("/api/favorites")
def favs():
    rows = db.q("SELECT f.instrument_id id, i.ticker, i.name, i.currency FROM favorites f JOIN instruments i ON i.id=f.instrument_id ORDER BY f.added_at")
    return ok({"favoritos": [dict(r) for r in rows]})


@app.post("/api/favorites/<int:iid>")
def fav_add(iid):
    db.ex("INSERT OR IGNORE INTO favorites(instrument_id, added_at) VALUES(?,?)", (iid, db.iso()))
    return ok({"ok": True})


@app.delete("/api/favorites/<int:iid>")
def fav_del(iid):
    db.ex("DELETE FROM favorites WHERE instrument_id=?", (iid,))
    return ok({"ok": True})


@app.get("/api/settings")
def get_settings():
    return ok(pipeline.settings())


@app.put("/api/settings")
def put_settings():
    body = request.get_json(silent=True) or {}
    errors = {}
    if "horario" in body:
        h = str(body["horario"])
        try:
            hh, mm = h.split(":")
            assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
            db.set_setting("horario", f"{int(hh):02d}:{int(mm):02d}")
        except Exception:
            errors["horario"] = "Usa el formato HH:MM (24 horas)."
    if "presupuesto_mxn" in body:
        v = body["presupuesto_mxn"]
        if v in (None, "", 0):
            db.set_setting("presupuesto_mxn", None)
        else:
            try:
                assert float(v) > 0
                db.set_setting("presupuesto_mxn", float(v))
            except Exception:
                errors["presupuesto_mxn"] = "Debe ser un número mayor a 0."
    for k, allowed in (("moneda", ("MXN", "USD")), ("horizonte", ("corto", "medio", "largo"))):
        if k in body:
            if body[k] in allowed:
                db.set_setting(k, body[k])
            else:
                errors[k] = f"Valores permitidos: {', '.join(allowed)}"
    for k in ("notificaciones", "solo_verificados"):
        if k in body:
            db.set_setting(k, bool(body[k]))
    return ok({"ok": not errors, "errores": errors, "config": pipeline.settings()}, 200 if not errors else 400)


# ------------------------------------------------------------------ ejecuciones

def _bg(trigger):
    def t():
        try:
            pipeline.run(trigger) if trigger == "manual" else pipeline.tick(trigger)
        finally:
            db.close()
    threading.Thread(target=t, daemon=True).start()


@app.post("/api/runs")
def run_now():
    left = pipeline.manual_cooldown_left()
    if left > 0:
        return ok({"ok": False, "mensaje": f"Para respetar los límites del proveedor, espera {left // 60 + 1} min antes de volver a actualizar."}, 429)
    lk = db.q1("SELECT expires_at FROM locks WHERE name='pipeline'")
    if lk and lk["expires_at"] > db.iso():
        return ok({"ok": False, "mensaje": "Ya hay una actualización en curso."}, 409)
    _bg("manual")
    return ok({"ok": True, "mensaje": "Actualización iniciada. Usa el mismo proceso que la ejecución diaria."}, 202)


@app.get("/api/runs")
def runs():
    rows = db.q("SELECT * FROM runs ORDER BY id DESC LIMIT 30")
    out = []
    for r in rows:
        ev = db.q("SELECT ts, level, message FROM run_events WHERE run_id=? ORDER BY id", (r["id"],))
        out.append({**dict(r), "eventos": [dict(e) for e in ev][-60:]})
    return ok({"ejecuciones": out})


@app.post("/api/cron/tick")
def cron_tick():
    """Para un cron externo (GitHub Actions, Render Cron, etc.). Solo ejecuta si ya toca; nunca duplica."""
    if not config.CRON_SECRET:
        return ok({"error": "CRON_SECRET no configurado en el servidor."}, 503)
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth, f"Bearer {config.CRON_SECRET}"):
        return ok({"error": "No autorizado."}, 401)
    due, why, nxt = pipeline.due_status()
    if request.args.get("wait") == "1" and due:
        return ok(pipeline.run("cron_externo"))
    if due:
        _bg("cron_externo")
        return ok({"ok": True, "mensaje": "Ejecución iniciada."}, 202)
    return ok({"ok": True, "mensaje": why, "proxima": nxt.isoformat() if nxt else None})


# ------------------------------------------------------------------ catálogo y cobertura

@app.get("/api/coverage")
def coverage():
    mode = _mode()
    return ok({"cobertura": catalog.coverage(1 if mode == "demo" else 0), "modalidades": catalog.MODALITY_INFO,
               "fuente_modalidades": catalog.MODALITY_SOURCE,
               "nota": "GBM no publica una API ni un catálogo descargable de sus instrumentos. 'Identificados' son acciones, ETFs y FIBRAS "
                       "listados en los mercados que GBM ofrece (BMV/SIC y bolsas de EE. UU.). Solo los 'verificados' tienen una fuente de GBM o "
                       "un catálogo importado por ti."})


@app.post("/api/catalog/import")
def catalog_import():
    f = request.files.get("archivo")
    if not f:
        return ok({"error": "Adjunta un archivo CSV en el campo 'archivo'."}, 400)
    raw = f.read(5_000_000)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        res = catalog.import_catalog_csv(text, f.filename or "catalogo.csv")
    except ValueError as e:
        return ok({"error": str(e)}, 400)
    return ok({"ok": True, "resultado": res, "nota": "Los cambios se reflejan en el siguiente reporte (o con 'Actualizar ahora')."})


def create_app(start_scheduler=None):
    db.init_db()
    pipeline.recover_orphans()
    if start_scheduler if start_scheduler is not None else config.ENABLE_INTERNAL_SCHEDULER:
        scheduler.start()
    return app
