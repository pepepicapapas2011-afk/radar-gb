"""CoinGecko (referencia externa de BTC y ETH; NO son instrumentos comprables en GBM).
Plan Demo gratuito: encabezado x-cg-demo-api-key, historial de 365 días, granularidad diaria (00:00 UTC).
Se requiere atribución "Datos de CoinGecko" en la interfaz."""
from datetime import datetime, timezone

from ..config import config
from .http import HttpClient

COINS = {"BTC": ("bitcoin", "Bitcoin"), "ETH": ("ethereum", "Ethereum")}
_client = None


def client():
    global _client
    if _client is None:
        headers = {"x-cg-demo-api-key": config.COINGECKO_API_KEY} if config.COINGECKO_API_KEY else None
        _client = HttpClient("coingecko", min_interval_ms=2500, daily_budget=300, headers=headers)
    return _client


def daily_series(coin_id, days=365):
    data = client().get_json(f"{config.COINGECKO_BASE_URL.rstrip('/')}/coins/{coin_id}/market_chart",
                             {"vs_currency": "usd", "days": days, "interval": "daily"})
    if not data:
        return []
    vols = {int(t): v for t, v in data.get("total_volumes", [])}
    by_day = {}
    for t, p in data.get("prices", []):
        d = datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()
        by_day[d.isoformat()] = (p, vols.get(int(t)))
    today = datetime.now(timezone.utc).date().isoformat()
    by_day.pop(today, None)  # el punto del día en curso no es un cierre
    return sorted((d, p, v) for d, (p, v) in by_day.items())
