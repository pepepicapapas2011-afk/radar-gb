"""Cliente HTTP común: reintentos con espera exponencial, respeto de Retry-After, límite de ritmo,
presupuesto diario de llamadas y caché persistente. Las claves nunca se guardan en logs ni en caché."""
import json
import re
import threading
import time
from datetime import timedelta

import requests

from .. import db


class ProviderError(Exception):
    pass


class BudgetExceeded(ProviderError):
    pass


_SECRET_RE = re.compile(r"(api_token|apikey|api_key|token|x_cg_demo_api_key)=[^&]+", re.I)


def redact(url):
    return _SECRET_RE.sub(r"\1=***", url)


class HttpClient:
    def __init__(self, provider, min_interval_ms=100, daily_budget=None, max_retries=4, timeout=30, headers=None):
        self.provider = provider
        self.min_interval = min_interval_ms / 1000.0
        self.daily_budget = daily_budget
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "RadarGBM/1.0 (herramienta personal de analisis)"})
        if headers:
            self.session.headers.update(headers)
        self._lock = threading.Lock()
        self._last = 0.0

    def _throttle(self):
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def get_json(self, url, params=None, cost=1, cache_key=None, cache_ttl_hours=0, headers=None):
        if cache_key and cache_ttl_hours > 0:
            r = db.q1("SELECT body FROM http_cache WHERE key=? AND expires_at>?", (cache_key, db.iso()))
            if r:
                return json.loads(r["body"])
        if self.daily_budget is not None and db.usage_today(self.provider) + cost > self.daily_budget:
            raise BudgetExceeded(f"{self.provider}: se alcanzó el presupuesto diario de llamadas ({self.daily_budget}).")
        last_err = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
                db.add_usage(self.provider, cost)
            except requests.RequestException as e:
                last_err = f"error de red: {type(e).__name__}"
                time.sleep(min(2 ** attempt, 30))
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise ProviderError(f"{self.provider}: respuesta no es JSON ({redact(resp.url)})")
                if cache_key and cache_ttl_hours > 0:
                    db.ex("INSERT OR REPLACE INTO http_cache(key,body,fetched_at,expires_at) VALUES(?,?,?,?)",
                          (cache_key, json.dumps(data), db.iso(), db.iso(db.now_utc() + timedelta(hours=cache_ttl_hours))))
                return data
            if resp.status_code in (429, 500, 502, 503, 504):
                ra = resp.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else min(2 ** (attempt + 1), 60)
                last_err = f"HTTP {resp.status_code}"
                time.sleep(delay)
                continue
            if resp.status_code == 404:
                return None
            raise ProviderError(f"{self.provider}: HTTP {resp.status_code} en {redact(resp.url)}")
        raise ProviderError(f"{self.provider}: sin respuesta tras {self.max_retries + 1} intentos ({last_err})")
