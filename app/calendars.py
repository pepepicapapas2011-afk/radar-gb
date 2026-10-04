"""Calendarios de mercado (reglas de días festivos de NYSE y BMV) y estado de cada mercado.

Las reglas son aproximaciones documentadas. La fuente de verdad del "último cierre" es siempre el
último dato real recibido; el calendario solo sirve para saber qué sesión se esperaba y detectar
datos atrasados. Festivos extra se pueden agregar con MARKET_HOLIDAYS_EXTRA="MX:2026-11-02,US:2026-..."."""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKETS = {
    "US": {"name": "Bolsas de EE. UU. (NYSE/Nasdaq)", "tz": "America/New_York", "open": time(9, 30), "close": time(16, 0),
           "data_ready_minutes": 120},
    "MX": {"name": "Bolsa Mexicana (BMV, incluye SIC)", "tz": "America/Mexico_City", "open": time(8, 30), "close": time(15, 0),
           "data_ready_minutes": 120},
    "CRYPTO": {"name": "Cripto (mercado 24/7, cierre diario 00:00 UTC)", "tz": "UTC", "open": None, "close": None,
               "data_ready_minutes": 15},
}


def easter(y):
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3
    # Algoritmo anónimo gregoriano (Meeus/Jones/Butcher)
    h =(19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def nth_weekday(y, m, weekday, n):
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_weekday(y, m, weekday):
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _extra(market):
    out = set()
    for item in os.environ.get("MARKET_HOLIDAYS_EXTRA", "").split(","):
        item = item.strip()
        if ":" in item:
            mk, ds = item.split(":", 1)
            if mk.strip().upper() == market:
                try:
                    out.add(date.fromisoformat(ds.strip()))
                except ValueError:
                    pass
    return out


def holidays(market, y):
    if market == "US":
        hs = {
            nth_weekday(y, 1, 0, 3), nth_weekday(y, 2, 0, 3), easter(y) - timedelta(days=2),
            last_weekday(y, 5, 0), _observed(date(y, 7, 4)), nth_weekday(y, 9, 0, 1),
            nth_weekday(y, 11, 3, 4), _observed(date(y, 12, 25)),
        }
        ny = date(y, 1, 1)
        if ny.weekday() != 5:  # NYSE no recorre Año Nuevo si cae en sábado
            hs.add(_observed(ny))
        if y >= 2022:
            hs.add(_observed(date(y, 6, 19)))
        return hs | _extra("US")
    if market == "MX":
        hs = {
            date(y, 1, 1), nth_weekday(y, 2, 0, 1), nth_weekday(y, 3, 0, 3),
            easter(y) - timedelta(days=3), easter(y) - timedelta(days=2),
            date(y, 5, 1), date(y, 9, 16), date(y, 11, 2), nth_weekday(y, 11, 0, 3),
            date(y, 12, 12), date(y, 12, 25),
        }
        if (y - 2024) % 6 == 0:  # transmisión del Poder Ejecutivo Federal
            hs.add(date(y, 10, 1))
        return hs | _extra("MX")
    return set()


def is_trading_day(market, d):
    if market == "CRYPTO":
        return True
    return d.weekday() < 5 and d not in holidays(market, d.year)


def previous_trading_day(market, d):
    d -= timedelta(days=1)
    while not is_trading_day(market, d):
        d -= timedelta(days=1)
    return d


def trading_days_between(market, start, end):
    """Sesiones esperadas en (start, end]."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_trading_day(market, d):
            n += 1
    return n


def expected_last_session(market, now_utc):
    """Última sesión cuyo cierre ya debería estar publicado por el proveedor."""
    m = MARKETS[market]
    tz = ZoneInfo(m["tz"])
    now_local = now_utc.astimezone(tz)
    if market == "CRYPTO":
        # el día UTC anterior queda cerrado a las 00:00 UTC
        ready = now_local - timedelta(minutes=m["data_ready_minutes"])
        return ready.date() - timedelta(days=1)
    d = now_local.date()
    ready_at = datetime.combine(d, m["close"], tz) + timedelta(minutes=m["data_ready_minutes"])
    if is_trading_day(market, d) and now_local >= ready_at:
        return d
    return previous_trading_day(market, d)


def market_status(market, now_utc):
    m = MARKETS[market]
    if market == "CRYPTO":
        return {"market": market, "name": m["name"], "status": "abierto_24_7", "label": "Opera 24/7",
                "expected_last_session": expected_last_session(market, now_utc).isoformat()}
    tz = ZoneInfo(m["tz"])
    nl = now_utc.astimezone(tz)
    d = nl.date()
    if not is_trading_day(market, d):
        reason = "fin de semana" if d.weekday() >= 5 else "día festivo"
        status, label = "cerrado", f"Cerrado hoy ({reason})"
    elif nl.time() < m["open"]:
        status, label = "pre_apertura", f"Aún no abre (abre {m['open'].strftime('%H:%M')} hora local)"
    elif nl.time() < m["close"]:
        status, label = "abierto", "Abierto ahora"
    else:
        status, label = "cerrado", "Cerrado (sesión de hoy terminada)"
    return {"market": market, "name": m["name"], "status": status, "label": label,
            "local_time": nl.strftime("%Y-%m-%d %H:%M"), "tz": m["tz"],
            "expected_last_session": expected_last_session(market, now_utc).isoformat()}
