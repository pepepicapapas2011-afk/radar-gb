"""Yahoo Finance (GRATIS, NO OFICIAL). Endpoint público de gráficas v8 que usan su sitio y librerías como yfinance.
Aviso: Yahoo indica que sus datos son para uso personal; puede limitar o cambiar el servicio sin aviso.
Respuesta: chart.result[0] con timestamp[], indicators.quote[0] (open/high/low/close/volume),
indicators.adjclose[0].adjclose y events.dividends / events.splits.
- 'close' de Yahoo ya viene ajustado por splits (no por dividendos); 'adjclose' por splits y dividendos.
- Si está instalado curl_cffi (viene con yfinance), se usa para imitar un navegador y reducir bloqueos."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from .. import db
from ..config import config
from .http import ProviderError, redact

_lock = threading.Lock()
_last = [0.0]
_session = None


def _get_session():
    global _session
    if _session is None:
        try:
            if config.YAHOO_BASE_URL.startswith("https://query"):
                from curl_cffi import requests as creq  # type: ignore
                _session = creq.Session(impersonate="chrome")
            else:
                raise ImportError
        except ImportError:
            _session = requests.Session()
            _session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                                     "Accept": "application/json"})
    return _session


def _throttle():
    with _lock:
        wait = _last[0] + config.YAHOO_MIN_INTERVAL_MS / 1000 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


def yahoo_symbol(inst):
    t = inst["ticker"]
    if inst["exchange"] == "MX":
        return f"{t}.MX"
    return t.replace(".", "-").replace("/", "-")


def _get(url, params, retries=4):
    s = _get_session()
    err = None
    for a in range(retries + 1):
        _throttle()
        try:
            r = s.get(url, params=params, timeout=30)
        except Exception as e:  # red
            err = type(e).__name__
            time.sleep(min(2 ** a, 30))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        if r.status_code in (429, 500, 502, 503, 504):
            err = f"HTTP {r.status_code}"
            ra = r.headers.get("Retry-After")
            time.sleep(float(ra) if ra and ra.isdigit() else min(5 * 2 ** a, 90))
            continue
        raise ProviderError(f"yahoo: HTTP {r.status_code} en {redact(url)}")
    raise ProviderError(f"yahoo: sin respuesta tras {retries + 1} intentos ({err})")


def chart(symbol, start, end=None):
    """Devuelve (filas, eventos, meta) o None si Yahoo no tiene el símbolo."""
    end = end or (datetime.now(timezone.utc) + timedelta(days=1))
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(end.timestamp())
    data = _get(f"{config.YAHOO_BASE_URL.rstrip('/')}/v8/finance/chart/{symbol}",
                {"period1": p1, "period2": p2, "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"})
    try:
        res = (data or {}).get("chart", {}).get("result")
        if not res:
            return None
        r = res[0]
        meta = r.get("meta", {})
        off = meta.get("gmtoffset", 0) or 0
        ts = r.get("timestamp") or []
        q = (r.get("indicators", {}).get("quote") or [{}])[0]
        adj = ((r.get("indicators", {}).get("adjclose") or [{}])[0]).get("adjclose") or [None] * len(ts)
        rows = []
        for i, t in enumerate(ts):
            c = (q.get("close") or [None] * len(ts))[i]
            if c is None:
                continue
            d = datetime.fromtimestamp(t + off, tz=timezone.utc).date().isoformat()
            rows.append({"date": d, "open": (q.get("open") or [None] * len(ts))[i], "high": (q.get("high") or [None] * len(ts))[i],
                         "low": (q.get("low") or [None] * len(ts))[i], "close": c,
                         "adjusted_close": adj[i] if adj[i] is not None else c, "volume": (q.get("volume") or [None] * len(ts))[i]})
        # el último punto del día en curso puede ser un precio intradía: se descarta si el mercado no ha cerrado
        if rows and meta.get("currentTradingPeriod"):
            reg = meta["currentTradingPeriod"].get("regular", {})
            now = time.time()
            if reg.get("start") and reg.get("end") and reg["start"] <= now < reg["end"] + 3600:
                last_day = datetime.fromtimestamp(reg["start"] + off, tz=timezone.utc).date().isoformat()
                if rows[-1]["date"] == last_day:
                    rows.pop()
        dedup = {}
        for x in rows:
            dedup[x["date"]] = x
        rows = [dedup[k] for k in sorted(dedup)]
        ev = r.get("events", {}) or {}
        divs = sorted(datetime.fromtimestamp(int(k) + off, tz=timezone.utc).date().isoformat() for k in (ev.get("dividends") or {}))
        splits = []
        for k, v in (ev.get("splits") or {}).items():
            try:
                splits.append((datetime.fromtimestamp(int(k) + off, tz=timezone.utc).date().isoformat(), float(v["numerator"]) / float(v["denominator"])))
            except (KeyError, ValueError, ZeroDivisionError, TypeError):
                pass
        return rows, {"dividends": divs, "splits": sorted(splits)}, {"currency": meta.get("currency"), "tz": meta.get("exchangeTimezoneName")}
    except (AttributeError, IndexError, TypeError, ValueError) as e:
        raise ProviderError(f"yahoo: formato inesperado para {symbol}: {e}")


def many(symbols, start, threads=None):
    """Descarga en paralelo (con límite de ritmo compartido). Devuelve {símbolo: resultado | Exception}."""
    out = {}

    def one(sym):
        try:
            return sym, chart(sym, start)
        except Exception as e:  # se informa por símbolo
            return sym, e

    with ThreadPoolExecutor(max_workers=max(1, threads or config.YAHOO_THREADS)) as ex:
        for sym, res in ex.map(one, symbols):
            out[sym] = res
    db.add_usage("yahoo", len(symbols))
    return out
