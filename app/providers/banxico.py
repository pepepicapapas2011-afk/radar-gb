"""Banco de México – API SIE. Serie SF43718: tipo de cambio FIX (pesos por dólar), publicado en días hábiles bancarios.
Token gratuito: https://www.banxico.org.mx/SieAPIRest/service/v1/token  (encabezado Bmx-Token).
Límites oficiales: 200 consultas / 5 min, 10,000 / día."""
from datetime import datetime

from ..config import config
from .http import HttpClient

SERIE_FIX = "SF43718"
_client = None


def client():
    global _client
    if _client is None:
        _client = HttpClient("banxico", min_interval_ms=1600, daily_budget=9000)
    return _client


def fix_range(start, end):
    url = f"{config.BANXICO_BASE_URL.rstrip('/')}/series/{SERIE_FIX}/datos/{start.isoformat()}/{end.isoformat()}"
    data = client().get_json(url, headers={"Bmx-Token": config.BANXICO_TOKEN, "Accept": "application/json"})
    out = []
    try:
        for d in data["bmx"]["series"][0].get("datos", []) or []:
            v = d.get("dato", "N/E").replace(",", "")
            if v in ("N/E", ""):
                continue
            out.append((datetime.strptime(d["fecha"], "%d/%m/%Y").date().isoformat(), float(v)))
    except (KeyError, IndexError, TypeError, ValueError):
        return []
    return out
