"""Adaptador EODHD (https://eodhd.com/financial-apis/). Endpoints usados y costo en llamadas:
- /exchange-symbol-list/{EX}            1 llamada   catálogo de una bolsa (US, MX)
- /eod/{SYM}.{EX}                       1 llamada   historial diario (close sin ajustar, adjusted_close por splits y dividendos)
- /eod-bulk-last-day/{EX}?date=         100 llamadas todos los cierres de una bolsa en una fecha (type=eod|splits|dividends)
- /splits/{SYM}.{EX}                    1 llamada   splits históricos
- /fundamentals/{SYM}.{EX}              10 llamadas fundamentales (requiere plan con fundamentales)
- /news?s=                              5 llamadas  noticias (con fecha y fuente)
- /calendar/earnings?symbols=           1 llamada   próximos reportes
Retraso: datos EOD (último cierre). Ver README para planes y costos."""
from datetime import date, timedelta

from ..config import config
from .http import HttpClient

_client = None


def client():
    global _client
    if _client is None:
        _client = HttpClient("eodhd", min_interval_ms=config.EODHD_MIN_INTERVAL_MS,
                             daily_budget=config.EODHD_DAILY_CALL_BUDGET)
    return _client


def _p(**kw):
    kw["api_token"] = config.EODHD_API_KEY
    kw.setdefault("fmt", "json")
    return kw


def _u(path):
    return config.EODHD_BASE_URL.rstrip("/") + path


def symbol_list(exchange):
    data = client().get_json(_u(f"/exchange-symbol-list/{exchange}"), _p(), cost=1,
                             cache_key=f"eodhd:list:{exchange}", cache_ttl_hours=20)
    return data or []


def eod_history(provider_symbol, start):
    data = client().get_json(_u(f"/eod/{provider_symbol}"), _p(**{"from": start.isoformat(), "period": "d", "order": "a"}), cost=1)
    return data or []


def bulk_day(exchange, day, kind="eod"):
    params = _p(date=day.isoformat())
    if kind != "eod":
        params["type"] = kind
    data = client().get_json(_u(f"/eod-bulk-last-day/{exchange}"), params, cost=100)
    return data or []


def split_history(provider_symbol, start):
    data = client().get_json(_u(f"/splits/{provider_symbol}"), _p(**{"from": start.isoformat()}), cost=1)
    return data or []


def parse_split(s):
    """'4.000000/1.000000' -> 4.0 (acciones nuevas por cada acción anterior)."""
    try:
        a, b = str(s).split("/")
        a, b = float(a), float(b)
        return a / b if a > 0 and b > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def fundamentals(provider_symbol, etf=False):
    flt = "General,ETF_Data,Technicals" if etf else "General,Highlights,Valuation,Financials::Balance_Sheet::quarterly,Technicals"
    return client().get_json(_u(f"/fundamentals/{provider_symbol}"), _p(filter=flt), cost=10,
                             cache_key=f"eodhd:fund:{provider_symbol}", cache_ttl_hours=24 * config.FUNDAMENTALS_TTL_DAYS)


def news(provider_symbol, limit=5):
    return client().get_json(_u("/news"), _p(s=provider_symbol, limit=limit, offset=0), cost=5,
                             cache_key=f"eodhd:news:{provider_symbol}:{date.today().isoformat()}", cache_ttl_hours=20) or []


def earnings_calendar(provider_symbols, today):
    data = client().get_json(_u("/calendar/earnings"),
                             _p(symbols=",".join(provider_symbols), **{"from": today.isoformat(), "to": (today + timedelta(days=60)).isoformat()}),
                             cost=1, cache_key=f"eodhd:earn:{today}:{len(provider_symbols)}:{hash(tuple(provider_symbols))}", cache_ttl_hours=20)
    if isinstance(data, dict):
        return data.get("earnings", [])
    return data or []
