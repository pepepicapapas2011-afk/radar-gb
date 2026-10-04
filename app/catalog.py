"""Catálogo de instrumentos: clasificación, modalidad de GBM, verificación y cobertura.

GBM no publica una API ni un catálogo descargable de sus instrumentos (verificado el 2026-10-03), por eso:
- El universo se arma con las listas de la BMV (incluye SIC) y de las bolsas de EE. UU. del proveedor de datos.
- Cada instrumento queda "pendiente" de verificar en GBM, salvo que aparezca en una fuente oficial de GBM
  (data/gbm_verificados.csv) o en un catálogo autorizado que el usuario importe (CSV)."""
import csv
import io
import os
import re

from . import db

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

LEVERAGED_RE = re.compile(r"(\b[1-5](\.\d)?x\b|-[1-5]x\b|\bultrapro\b|proshares ultra\b|proshares short\b|\binverse\b|\bleveraged\b|\bdaily\b.*\b(bull|bear)\b)", re.I)
CRYPTO_ETF_RE = re.compile(r"(bitcoin|\bbtc\b|ether(eum)?\b|\beth\b|crypto|blockchain|solana|\bxrp\b|digital asset)", re.I)

MODALITY_INFO = {
    "Trading MX": "Acciones, ETFs y FIBRAS de la BMV/BIVA. Según GBM se compran acciones completas.",
    "Trading MX (SIC)": "Valores extranjeros listados en el SIC, cotizan en pesos. Según GBM se operan en Trading MX (acciones completas).",
    "Trading USA": "Mercado de EE. UU. en dólares. Según GBM permite fracciones o acciones completas.",
    "Referencia externa": "Solo referencia de mercado. No se afirma que se compre directamente en GBM.",
}
MODALITY_SOURCE = ("GBM – ¿Qué productos tiene GBM?", "https://gbm.com/faqs/que-productos-tiene-gbm")


def _load_sector():
    path = os.path.join(DATA_DIR, "sector_cripto.csv")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["ticker"].strip().upper(): r["motivo"] for r in csv.DictReader(f)}


def classify(row, exchange, sector=None):
    """row: fila de exchange-symbol-list (Code, Name, Exchange, Currency, Type, Isin)."""
    sector = sector if sector is not None else _load_sector()
    typ = (row.get("Type") or "").lower()
    name = row.get("Name") or ""
    code = (row.get("Code") or "").upper()
    isin = (row.get("Isin") or "").upper() or None
    if "etf" in typ:
        t, cat = "etf", "etf"
    elif "common" in typ or "preferred" in typ:
        t, cat = "stock", "accion"
    elif "fund" in typ and "fibra" in name.lower():
        t, cat = "fibra", "fibra"
    else:
        return None  # fondos mutuos, warrants, bonos: fuera del alcance
    if cat == "accion" and "fibra" in name.lower():
        t, cat = "fibra", "fibra"
    exposure = "ninguna"
    if t == "etf" and CRYPTO_ETF_RE.search(name):
        exposure = "etf_cripto"
    elif t == "stock" and code.split(".")[0] in sector:
        exposure = "accion_sector_cripto"
    if exchange == "MX":
        if isin and not isin.startswith("MX"):
            modality = "Trading MX (SIC)"
        elif isin:
            modality = "Trading MX"
        else:
            modality = "Trading MX (SIC)" if t == "etf" else "Trading MX"
    else:
        modality = "Trading USA"
    return {
        "provider_symbol": f"{code}.{exchange}", "ticker": code, "exchange": exchange,
        "venue": row.get("Exchange"), "name": name, "isin": isin, "currency": row.get("Currency") or ("MXN" if exchange == "MX" else "USD"),
        "type": t, "category": cat, "crypto_exposure": exposure, "gbm_modality": modality,
        "is_leveraged": 1 if (t == "etf" and LEVERAGED_RE.search(name)) else 0,
    }


def upsert_instruments(items, data_source, is_demo=0):
    now = db.iso()
    seen = []
    for it in items:
        gk = it.get("group_key") or it.get("isin") or it["provider_symbol"]
        db.ex("""INSERT INTO instruments(provider_symbol,ticker,exchange,venue,name,isin,currency,type,category,crypto_exposure,
                 gbm_modality,gbm_status,data_source,is_leveraged,is_benchmark,active,is_demo,needs_backfill,first_seen_at,updated_at,group_key)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,1,?,?,?)
                 ON CONFLICT(provider_symbol) DO UPDATE SET name=excluded.name, venue=excluded.venue, isin=COALESCE(excluded.isin, instruments.isin),
                 currency=excluded.currency, type=excluded.type, category=excluded.category, crypto_exposure=excluded.crypto_exposure,
                 gbm_modality=excluded.gbm_modality, is_leveraged=excluded.is_leveraged, data_source=excluded.data_source,
                 is_benchmark=MAX(instruments.is_benchmark, excluded.is_benchmark), active=1, updated_at=excluded.updated_at,
                 group_key=COALESCE(instruments.group_key, excluded.group_key)""",
              (it["provider_symbol"], it["ticker"], it["exchange"], it.get("venue"), it.get("name"), it.get("isin"), it.get("currency"),
               it["type"], it["category"], it.get("crypto_exposure", "ninguna"), it.get("gbm_modality"),
               it.get("gbm_status", "pendiente"), data_source, it.get("is_leveraged", 0), it.get("is_benchmark", 0), is_demo, now, now, gk))
        seen.append(it["provider_symbol"])
    return seen


def deactivate_missing(exchange, seen, is_demo=0):
    if not seen:
        return 0
    seen_set = set(seen)
    rows = db.q("SELECT id, provider_symbol FROM instruments WHERE exchange=? AND is_demo=? AND active=1 AND is_benchmark=0", (exchange, is_demo))
    gone = [r["id"] for r in rows if r["provider_symbol"] not in seen_set]
    for i in gone:
        db.ex("UPDATE instruments SET active=0, updated_at=? WHERE id=?", (db.iso(), i))
    return len(gone)


def apply_official_verifications():
    """Marca como verificados los instrumentos citados en fuentes oficiales de GBM."""
    path = os.path.join(DATA_DIR, "gbm_verificados.csv")
    n = 0
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cur = db.ex("""UPDATE instruments SET gbm_status='verificado', gbm_modality=?, gbm_source=?, gbm_verified_at=?
                           WHERE provider_symbol=? AND is_demo=0""",
                        (r["gbm_modality"], f"{r['fuente']} | {r['url_fuente']}", r["fecha_verificacion"], r["provider_symbol"]))
            n += cur.rowcount
    return n


def import_catalog_csv(text, filename="catalogo.csv"):
    """Importa un catálogo autorizado (p. ej., exportado o capturado desde la app de GBM).
    Columnas aceptadas: ticker (obligatoria), mercado/modalidad (Trading MX | SIC | Trading USA), nombre (opcional), fecha (opcional)."""
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
    tcol = cols.get("ticker") or cols.get("emisora") or cols.get("simbolo") or cols.get("símbolo")
    mcol = cols.get("mercado") or cols.get("modalidad")
    dcol = cols.get("fecha") or cols.get("fecha_verificacion")
    if not tcol:
        raise ValueError("El CSV necesita una columna 'ticker'.")
    rows = matched = 0
    unmatched = []
    today = db.now_utc().date().isoformat()
    for r in reader:
        rows += 1
        tk = (r.get(tcol) or "").strip().upper().replace(" ", "")
        if not tk:
            continue
        m = (r.get(mcol) or "").strip().lower() if mcol else ""
        if "usa" in m or "global" in m:
            candidates, modality = [f"{tk}.US"], "Trading USA"
        elif "sic" in m:
            candidates, modality = [f"{tk}.MX"], "Trading MX (SIC)"
        elif "mx" in m:
            candidates, modality = [f"{tk}.MX"], "Trading MX"
        else:
            candidates, modality = [f"{tk}.MX", f"{tk}.US"], None
        hit = False
        for ps in candidates:
            inst = db.q1("SELECT id, gbm_modality FROM instruments WHERE provider_symbol=? AND is_demo=0", (ps,))
            if inst:
                db.ex("UPDATE instruments SET gbm_status='verificado', gbm_modality=?, gbm_source=?, gbm_verified_at=? WHERE id=?",
                      (modality or inst["gbm_modality"], f"Catálogo importado: {filename}", (r.get(dcol) or today) if dcol else today, inst["id"]))
                hit = True
        if hit:
            matched += 1
        else:
            unmatched.append(tk)
    db.ex("INSERT INTO catalog_imports(imported_at,filename,rows,matched,unmatched,notes) VALUES(?,?,?,?,?,?)",
          (db.iso(), filename, rows, matched, len(unmatched), ",".join(unmatched[:200])))
    return {"filas": rows, "coinciden": matched, "sin_coincidencia": len(unmatched), "ejemplos_sin_coincidencia": unmatched[:20]}


def ensure_crypto_refs(is_demo=0):
    items = []
    for sym, name in (("BTC", "Bitcoin"), ("ETH", "Ethereum")):
        items.append({"provider_symbol": f"{sym}-REF" + ("-DEMO" if is_demo else ""), "ticker": sym, "exchange": "CRYPTO",
                      "venue": "Referencia de mercado", "name": f"{name} (referencia externa)" + (" [DEMO]" if is_demo else ""),
                      "isin": None, "currency": "USD", "type": "crypto_ref", "category": "cripto_ref",
                      "crypto_exposure": "referencia_externa", "gbm_modality": "Referencia externa", "gbm_status": "referencia"})
    upsert_instruments(items, "CoinGecko" if not is_demo else "DEMO (sintético)", is_demo)
    db.ex("UPDATE instruments SET gbm_status='referencia' WHERE exchange='CRYPTO'")


BENCHMARKS_EODHD = {
    # moneda/mercado -> instrumento de referencia (rendimiento total, misma moneda)
    "US": "SPY.US",          # S&P 500 en USD
    "MX_LOCAL": "NAFTRAC.MX",  # IPC (BMV) en MXN
    "MX_SIC": "IVV.MX",      # S&P 500 cotizado en el SIC, en MXN
    "CRYPTO": "BTC-REF",     # bitcoin (referencia externa) en USD
}


BENCHMARKS_YAHOO = dict(BENCHMARKS_EODHD, MX_LOCAL="NAFTRACISHRS.MX")  # en Yahoo el NAFTRAC es NAFTRACISHRS.MX
BENCHMARKS_DEMO = {"US": "DEMO-IDX-USD.US", "MX_LOCAL": "DEMO-IDX-MX.MX", "MX_SIC": "DEMO-IDX-SIC.MX", "CRYPTO": "BTC-REF-DEMO"}


def benchmarks(is_demo=0):
    from .config import config
    if is_demo:
        return BENCHMARKS_DEMO
    return BENCHMARKS_YAHOO if config.PRICE_PROVIDER == "yahoo" else BENCHMARKS_EODHD


BENCHMARKS = BENCHMARKS_EODHD  # compatibilidad


def benchmark_for(inst, is_demo=0):
    return benchmarks(is_demo)[_bkey(inst)]


def _bkey(inst):
    if inst["exchange"] == "CRYPTO" or inst["crypto_exposure"] == "etf_cripto":
        return "CRYPTO"
    if inst["exchange"] == "US":
        return "US"
    return "MX_SIC" if inst["gbm_modality"] == "Trading MX (SIC)" else "MX_LOCAL"


def coverage(is_demo=0):
    r = db.q1("""SELECT COUNT(*) total,
                 SUM(CASE WHEN gbm_status='verificado' THEN 1 ELSE 0 END) verificados,
                 SUM(CASE WHEN gbm_status='pendiente' THEN 1 ELSE 0 END) pendientes,
                 SUM(CASE WHEN gbm_status='referencia' THEN 1 ELSE 0 END) referencias,
                 SUM(CASE WHEN needs_backfill=1 THEN 1 ELSE 0 END) sin_historial
                 FROM instruments WHERE active=1 AND is_demo=?""", (is_demo,))
    by = db.q("SELECT gbm_modality m, category c, COUNT(*) n FROM instruments WHERE active=1 AND is_demo=? GROUP BY 1,2", (is_demo,))
    last_import = db.q1("SELECT * FROM catalog_imports ORDER BY id DESC LIMIT 1")
    weekly = db.q1("""SELECT COUNT(*) n FROM instruments WHERE active=1 AND is_demo=? AND liq_mxn IS NOT NULL AND liq_mxn < 500000
                      AND gbm_status!='verificado' AND is_benchmark=0 AND crypto_exposure='ninguna'""", (is_demo,))["n"]
    inactive = db.q1("SELECT COUNT(*) n FROM instruments WHERE active=0 AND is_demo=? AND data_note IS NOT NULL", (is_demo,))["n"]
    return {"actualizacion_semanal_baja_liquidez": weekly, "sin_datos_en_fuente": inactive,
            "identificados": r["total"] or 0, "verificados_gbm": r["verificados"] or 0, "pendientes_verificar": r["pendientes"] or 0,
            "referencias_externas": r["referencias"] or 0, "sin_historial_aun": r["sin_historial"] or 0,
            "por_modalidad": [dict(x) for x in by], "ultima_importacion": dict(last_import) if last_import else None}
