"""Configuración leída solo de variables de entorno (las claves nunca llegan al navegador)."""
import os


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


class Config:
    def __init__(self):
        self.reload()

    def reload(self):
        e = os.environ.get
        self.DATABASE_PATH = e("DATABASE_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "radar.db"))
        self.TIMEZONE = "America/Matamoros"
        self.DEFAULT_SCHEDULE = e("DEFAULT_SCHEDULE", "07:00")
        # Proveedores
        self.EODHD_API_KEY = e("EODHD_API_KEY", "")
        self.EODHD_BASE_URL = e("EODHD_BASE_URL", "https://eodhd.com/api")
        self.EODHD_MX_EXCHANGE = e("EODHD_MX_EXCHANGE", "MX")
        self.EODHD_US_EXCHANGE = e("EODHD_US_EXCHANGE", "US")
        self.EODHD_DAILY_CALL_BUDGET = _int("EODHD_DAILY_CALL_BUDGET", 90000)
        self.EODHD_MIN_INTERVAL_MS = _int("EODHD_MIN_INTERVAL_MS", 60)  # ~1000 req/min máx
        self.EODHD_FUNDAMENTALS = _bool("EODHD_FUNDAMENTALS", False)  # requiere plan con fundamentales
        self.EODHD_NEWS = _bool("EODHD_NEWS", False)
        self.US_VENUES = [v.strip() for v in e("US_VENUES", "NYSE,NASDAQ,NYSE ARCA,NYSE MKT,BATS,AMEX").split(",") if v.strip()]
        self.MAX_BACKFILL_PER_RUN = _int("MAX_BACKFILL_PER_RUN", 1500)
        self.CATALOG_REFRESH_DAYS = _int("CATALOG_REFRESH_DAYS", 7)
        self.FUNDAMENTALS_TTL_DAYS = _int("FUNDAMENTALS_TTL_DAYS", 7)
        self.BANXICO_TOKEN = e("BANXICO_TOKEN", "")
        self.BANXICO_BASE_URL = e("BANXICO_BASE_URL", "https://www.banxico.org.mx/SieAPIRest/service/v1")
        self.COINGECKO_API_KEY = e("COINGECKO_API_KEY", "")
        self.COINGECKO_BASE_URL = e("COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")
        # Proveedor de precios: yahoo (gratis, no oficial, uso personal) | eodhd (de pago, con licencia)
        self.PRICE_PROVIDER = e("PRICE_PROVIDER", "yahoo").lower()
        self.YAHOO_BASE_URL = e("YAHOO_BASE_URL", "https://query1.finance.yahoo.com")
        self.YAHOO_MIN_INTERVAL_MS = _int("YAHOO_MIN_INTERVAL_MS", 150)
        self.YAHOO_THREADS = _int("YAHOO_THREADS", 4)
        self.NASDAQ_SYMDIR_URL = e("NASDAQ_SYMDIR_URL", "https://www.nasdaqtrader.com/dynamic/SymDir")
        self.SIC_PROBE_PER_RUN = _int("SIC_PROBE_PER_RUN", 300)
        self.GITHUB_REPOSITORY = e("GITHUB_REPOSITORY", "")
        # Modo: real | demo | auto (auto = real con Yahoo, o con EODHD si hay clave)
        self.DATA_MODE = e("DATA_MODE", "auto").lower()
        # Seguridad y automatización
        self.APP_ACCESS_TOKEN = e("APP_ACCESS_TOKEN", "")
        self.CRON_SECRET = e("CRON_SECRET", "")
        self.ENABLE_INTERNAL_SCHEDULER = _bool("ENABLE_INTERNAL_SCHEDULER", True)
        self.RUN_MAX_ATTEMPTS = _int("RUN_MAX_ATTEMPTS", 3)
        self.RUN_RETRY_MINUTES = _int("RUN_RETRY_MINUTES", 15)
        self.MANUAL_COOLDOWN_MINUTES = _int("MANUAL_COOLDOWN_MINUTES", 20)
        # Notificaciones opcionales
        self.NOTIFY_NTFY_URL = e("NOTIFY_NTFY_URL", "")  # ej. https://ntfy.sh/mi-tema-secreto
        self.NOTIFY_WEBHOOK_URL = e("NOTIFY_WEBHOOK_URL", "")
        self.PUBLIC_URL = e("PUBLIC_URL", "")

    def effective_mode(self):
        if self.DATA_MODE in ("real", "demo"):
            return self.DATA_MODE
        if self.PRICE_PROVIDER == "yahoo":
            return "real"
        return "real" if self.EODHD_API_KEY else "demo"


config = Config()
