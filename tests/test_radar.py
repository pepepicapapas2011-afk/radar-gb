"""Pruebas: cálculos, calendarios, horario de verano, proceso completo (demo y real simulado),
duplicados, fallos, límites del proveedor y persistencia tras reinicio.
Ejecutar: python -m unittest discover -s tests -v"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))

from app import calendars, catalog, db, indicators as ind, pipeline, scoring  # noqa: E402
from app.config import config  # noqa: E402
from app.providers import banxico, coingecko, eodhd  # noqa: E402
from app.providers.http import BudgetExceeded, HttpClient  # noqa: E402

UTC = timezone.utc
TZ = ZoneInfo("America/Matamoros")


def fresh_db():
    path = os.path.join(tempfile.mkdtemp(), "radar.db")
    os.environ["DATABASE_PATH"] = path
    config.reload()
    db.close()
    db.init_db()
    return path


class TestIndicators(unittest.TestCase):
    def test_returns_and_sma(self):
        v = [100, 101, 102, 103, 104, 110]
        self.assertAlmostEqual(ind.period_return(v, 1), 110 / 104 - 1)
        self.assertAlmostEqual(ind.period_return(v, 5), 0.10)
        self.assertIsNone(ind.period_return(v, 6))
        self.assertAlmostEqual(ind.sma(v, 3), (103 + 104 + 110) / 3)
        self.assertEqual(ind.sma_series([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_volatility_known(self):
        # log-rendimientos alternos ±1%: desviación muestral conocida
        v = [100.0]
        for i in range(100):
            v.append(v[-1] * math.exp(0.01 if i % 2 == 0 else -0.01))
        lr = [0.01 if i % 2 == 0 else -0.01 for i in range(63)]
        m = sum(lr) / 63
        sd = math.sqrt(sum((x - m) ** 2 for x in lr) / 62)
        self.assertAlmostEqual(ind.volatility(v, 63, 252), sd * math.sqrt(252), places=10)
        self.assertIsNone(ind.volatility([1, 2, 3], 63))

    def test_drawdown(self):
        mdd, n = ind.max_drawdown([100, 120, 90, 95, 130, 117])
        self.assertAlmostEqual(mdd, 90 / 120 - 1)
        self.assertEqual(n, 5)

    def test_split_adjustment(self):
        dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
        closes = [400, 404, 101]  # split 4:1 el día 3
        po = ind.split_adjusted_closes(dates, closes, [("2026-01-03", 4.0)])
        self.assertEqual(po, [100, 101, 101])
        adj = [100, 101, 101]
        self.assertEqual(ind.unexplained_jumps(dates, closes, adj, [("2026-01-03", 4.0)]), [])
        self.assertEqual(ind.unexplained_jumps(dates, closes, adj, []), ["2026-01-03"])

    def test_trend_requires_sma200(self):
        self.assertEqual(ind.trend_points(10, 9, 8, None), (None, []))
        self.assertEqual(ind.trend_points(10, 9, 8, 7)[0], 4)

    def test_percentiles(self):
        self.assertEqual(ind.percentile_ranks([3, 1, 2, None]), [100.0, 0.0, 50.0, None])
        self.assertEqual(ind.percentile_ranks([5, 5]), [50.0, 50.0])

    def test_value_at_or_before(self):
        self.assertEqual(ind.value_at_or_before(["2026-01-01", "2026-01-05"], [1, 2], "2026-01-04"), ("2026-01-01", 1))
        self.assertEqual(ind.value_at_or_before(["2026-01-01"], [1], "2025-12-31"), (None, None))


class TestCalendars(unittest.TestCase):
    def test_holidays_2026(self):
        self.assertEqual(calendars.easter(2026), date(2026, 4, 5))
        us = calendars.holidays("US", 2026)
        for d in ("2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"):
            self.assertIn(date.fromisoformat(d), us, d)
        mx = calendars.holidays("MX", 2026)
        for d in ("2026-02-02", "2026-03-16", "2026-04-02", "2026-04-03", "2026-09-16", "2026-11-02", "2026-11-16", "2026-12-12"):
            self.assertIn(date.fromisoformat(d), mx, d)

    def test_weekend_uses_friday_close(self):
        sat = datetime(2026, 10, 3, 12, 0, tzinfo=TZ).astimezone(UTC)
        self.assertEqual(calendars.expected_last_session("US", sat), date(2026, 10, 2))
        self.assertEqual(calendars.expected_last_session("MX", sat), date(2026, 10, 2))
        self.assertEqual(calendars.market_status("MX", sat)["status"], "cerrado")

    def test_monday_7am_still_friday(self):
        mon = datetime(2026, 10, 5, 7, 0, tzinfo=TZ).astimezone(UTC)
        self.assertEqual(calendars.expected_last_session("US", mon), date(2026, 10, 2))
        self.assertEqual(calendars.market_status("US", mon)["status"], "pre_apertura")

    def test_holiday_mx_day_of_dead(self):
        tue = datetime(2026, 11, 3, 7, 0, tzinfo=TZ).astimezone(UTC)  # 2 nov lunes festivo BMV
        self.assertEqual(calendars.expected_last_session("MX", tue), date(2026, 10, 30))
        self.assertEqual(calendars.expected_last_session("US", tue), date(2026, 11, 2))

    def test_crypto_daily(self):
        sun = datetime(2026, 10, 4, 7, 0, tzinfo=TZ).astimezone(UTC)
        self.assertEqual(calendars.expected_last_session("CRYPTO", sun), date(2026, 10, 3))


class TestScheduleDST(unittest.TestCase):
    def test_7am_offsets_across_dst(self):
        # Matamoros sigue el horario de verano de EE. UU. (CDT UTC-5 / CST UTC-6)
        self.assertEqual(pipeline.scheduled_at(date(2026, 3, 7), "07:00").astimezone(UTC).hour, 13)
        self.assertEqual(pipeline.scheduled_at(date(2026, 3, 9), "07:00").astimezone(UTC).hour, 12)
        self.assertEqual(pipeline.scheduled_at(date(2026, 11, 2), "07:00").astimezone(UTC).hour, 13)

    def test_nonexistent_and_ambiguous_times(self):
        gap = pipeline.scheduled_at(date(2026, 3, 8), "02:30")  # no existe
        self.assertEqual((gap.hour, gap.minute), (3, 30))
        amb = pipeline.scheduled_at(date(2026, 11, 1), "01:30")  # ocurre dos veces
        self.assertEqual(amb.astimezone(UTC).hour, 6)  # primera ocurrencia (CDT)


class TestDemoPipeline(unittest.TestCase):
    def setUp(self):
        self.path = fresh_db()
        os.environ["DATA_MODE"] = "demo"
        config.reload()

    def test_full_run_duplicates_failure_and_restart(self):
        now = datetime(2026, 10, 3, 7, 1, tzinfo=TZ).astimezone(UTC)
        due, why, _ = pipeline.due_status(now)
        self.assertTrue(due, why)
        r1 = pipeline.run("programada", now=now)
        self.assertEqual(r1["status"], "exito", r1)
        self.assertTrue(r1["nuevo_reporte"])
        s = json.loads(db.q1("SELECT summary FROM reports").fetchone() if False else db.q1("SELECT summary FROM reports")["summary"])
        self.assertEqual(s["mode"], "demo")
        self.assertEqual(s["data_cutoff"]["US"]["ultimo_dato"], "2026-10-02")  # sábado: último cierre del viernes
        self.assertGreaterEqual(len(s["investigar"]["acciones"]["items"]), 1)
        for g in s["investigar"].values():
            for it in g["items"]:
                self.assertTrue(0 <= it["score"]["total"] <= 100)
                for k in ("que_es", "por_que", "datos", "riesgos", "cambio", "horizonte"):
                    self.assertTrue(it["explicacion"][k])
        excl = {m["ticker"]: m for m in (json.loads(r["data"]) for r in db.q("SELECT data FROM report_metrics"))}
        self.assertTrue(any("atrasados" in e for e in excl["DEMOSTALE"]["exclusions"]))
        self.assertTrue(any("historial" in e for e in excl["DEMONEW"]["exclusions"]))
        self.assertTrue(any("apalancado" in e for e in excl["DEMO3X"]["exclusions"]))
        self.assertTrue(any("liquidez" in e for e in excl["DEMOILLQ"]["exclusions"]))
        self.assertFalse(excl["DEMOSPLIT"]["price_return_uncertain"])
        self.assertAlmostEqual(excl["DEMOSPLIT"]["ret_price"]["1a"], excl["DEMOSPLIT"]["ret_total"]["1a"], places=9)
        # ya no toca: misma fecha
        due, why, nxt = pipeline.due_status(now)
        self.assertFalse(due)
        self.assertEqual(nxt.astimezone(TZ).date(), date(2026, 10, 4))
        # segunda ejecución el mismo día: no duplica
        r2 = pipeline.run("manual", now=now)
        self.assertFalse(r2["nuevo_reporte"])
        self.assertEqual(db.q1("SELECT COUNT(*) n FROM reports")["n"], 1)
        # domingo: sin sesiones nuevas, se marca y se compara
        sun = datetime(2026, 10, 4, 7, 1, tzinfo=TZ).astimezone(UTC)
        pipeline.run("programada", now=sun)
        s2 = json.loads(db.q1("SELECT summary FROM reports WHERE report_date='2026-10-04'")["summary"])
        self.assertFalse(s2["datos_nuevos"])
        self.assertEqual(s2["data_cutoff"]["MX"]["ultimo_dato"], "2026-10-02")
        # fallo: se conserva el último reporte válido y se reintenta
        mon = datetime(2026, 10, 5, 7, 1, tzinfo=TZ).astimezone(UTC)
        from app import ingest
        orig = ingest.ingest_demo
        ingest.ingest_demo = lambda log, now: (_ for _ in ()).throw(RuntimeError("proveedor caído"))
        try:
            rf = pipeline.run("programada", now=mon)
        finally:
            ingest.ingest_demo = orig
        self.assertEqual(rf["status"], "fallo")
        db.ex("UPDATE runs SET finished_at=? WHERE id=?", (db.iso(mon), rf["run_id"]))  # reloj simulado
        self.assertEqual(db.q1("SELECT report_date FROM reports ORDER BY report_date DESC LIMIT 1")["report_date"], "2026-10-04")
        due, why, nxt = pipeline.due_status(mon)
        self.assertFalse(due)
        self.assertIn("Reintento", why)
        later = datetime(2026, 10, 5, 7, 30, tzinfo=TZ).astimezone(UTC)
        self.assertTrue(pipeline.due_status(later)[0])
        self.assertEqual(pipeline.run("programada", now=later)["status"], "exito")
        # persistencia: un proceso nuevo lee los reportes del disco
        db.close()
        out = subprocess.run([sys.executable, "-c", "from app import db; db.init_db(); print(db.q1('SELECT COUNT(*) n FROM reports')['n'])"],
                             cwd=ROOT, env=dict(os.environ, DATABASE_PATH=self.path), capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "3", out.stderr)

    def test_lock_prevents_concurrent_runs(self):
        self.assertTrue(db.acquire_lock("pipeline", "otro-proceso", 600))
        r = pipeline.run("manual")
        self.assertEqual(r["status"], "omitida")
        db.release_lock("pipeline", "otro-proceso")


class TestRealPipelineWithFakeProviders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import fake_providers
        cls.fp = fake_providers
        cls.srv, cls.base = fake_providers.start()

    def setUp(self):
        fresh_db()
        os.environ.update({"DATA_MODE": "real", "PRICE_PROVIDER": "eodhd", "EODHD_API_KEY": "TESTKEY", "EODHD_BASE_URL": self.base + "/api",
                           "BANXICO_TOKEN": "BXTOKEN", "BANXICO_BASE_URL": self.base + "/banxico", "COINGECKO_BASE_URL": self.base + "/cg",
                           "EODHD_FUNDAMENTALS": "1", "EODHD_NEWS": "1", "EODHD_MIN_INTERVAL_MS": "0"})
        config.reload()
        eodhd._client = banxico._client = coingecko._client = None
        banxico.client().min_interval = 0
        coingecko.client().min_interval = 0

    def tearDown(self):
        for k in ("EODHD_API_KEY", "EODHD_FUNDAMENTALS", "EODHD_NEWS", "BANXICO_TOKEN", "DATA_MODE", "EODHD_DAILY_CALL_BUDGET", "PRICE_PROVIDER"):
            os.environ.pop(k, None)
        config.reload()
        eodhd._client = banxico._client = coingecko._client = None

    def test_real_flow(self):
        self.fp.STATE["asof"] = "2026-10-01"
        self.fp.STATE["fail_once"].add("/api/eod/SPY.US")  # 429 -> debe reintentar
        fri = datetime(2026, 10, 2, 7, 5, tzinfo=TZ).astimezone(UTC)
        r = pipeline.run("programada", now=fri)
        self.assertEqual(r["status"], "exito", r)
        cov = catalog.coverage(0)
        insts = {x["provider_symbol"]: x for x in db.q("SELECT * FROM instruments WHERE is_demo=0")}
        self.assertNotIn("OTCX.US", insts)  # OTC fuera
        self.assertEqual(insts["IBIT.US"]["gbm_status"], "verificado")
        self.assertEqual(insts["IBIT.US"]["crypto_exposure"], "etf_cripto")
        self.assertEqual(insts["COIN.US"]["crypto_exposure"], "accion_sector_cripto")
        self.assertEqual(insts["SQQQ.US"]["is_leveraged"], 1)
        self.assertEqual(insts["QQQ.US"]["is_leveraged"], 0)
        self.assertEqual(insts["AAPL.MX"]["gbm_modality"], "Trading MX (SIC)")
        self.assertEqual(insts["WALMEX.MX"]["gbm_modality"], "Trading MX")
        self.assertEqual(insts["FUNO11.MX"]["category"], "fibra")
        self.assertEqual(insts["BTC-REF"]["gbm_status"], "referencia")
        self.assertGreaterEqual(cov["verificados_gbm"], 2)
        self.assertGreaterEqual(self.fp.STATE["calls"].count("/api/eod/SPY.US"), 2)
        fx = db.q1("SELECT * FROM fx ORDER BY date DESC LIMIT 1")
        self.assertIn("Banxico", fx["source"])
        s = json.loads(db.q1("SELECT summary FROM reports WHERE mode='real'")["summary"])
        self.assertEqual(s["data_cutoff"]["US"]["ultimo_dato"], "2026-10-01")
        m = {json.loads(x["data"])["provider_symbol"]: json.loads(x["data"]) for x in db.q("SELECT data FROM report_metrics")}
        self.assertFalse(m["NVDA.US"]["price_return_uncertain"])  # split documentado
        self.assertAlmostEqual(m["NVDA.US"]["ret_price"]["1a"] / m["NVDA.US"]["ret_total"]["1a"], 1, delta=0.2)
        # SIC vs origen: misma ISIN, misma moneda de referencia
        self.assertEqual(m["AAPL.MX"]["benchmark"], "IVV.MX")
        self.assertEqual(m["AAPL.US"]["benchmark"], "SPY.US")
        self.assertEqual(m["IBIT.US"]["benchmark"], "BTC-REF")
        # rendimiento en MXN usa FIX
        self.assertIsNotNone(m["AAPL.US"]["ret_mxn"]["1m"])
        # día siguiente: actualización por lote (100 llamadas por bolsa), sin descargar historial otra vez
        self.fp.STATE["asof"] = "2026-10-02"
        self.fp.STATE["calls"].clear()
        self.fp.STATE["bulk_dividends"] = ["MSFT"]
        sat = datetime(2026, 10, 3, 7, 5, tzinfo=TZ).astimezone(UTC)
        r = pipeline.run("programada", now=sat)
        self.assertEqual(r["status"], "exito", r)
        self.assertIn("/api/eod-bulk-last-day/US", self.fp.STATE["calls"])
        self.assertIn("/api/eod/MSFT.US", self.fp.STATE["calls"])  # dividendo -> recarga historial ajustado
        self.assertNotIn("/api/eod/AAPL.US", self.fp.STATE["calls"])
        s = json.loads(db.q1("SELECT summary FROM reports WHERE report_date='2026-10-03'")["summary"])
        self.assertEqual(s["data_cutoff"]["US"]["ultimo_dato"], "2026-10-02")
        self.assertTrue(s["datos_nuevos"])
        self.fp.STATE["bulk_dividends"] = []

    def test_budget_limit(self):
        c = HttpClient("eodhd_test", min_interval_ms=0, daily_budget=5)
        with self.assertRaises(BudgetExceeded):
            c.get_json(self.base + "/api/eod-bulk-last-day/US", {"api_token": "TESTKEY"}, cost=100)

    def test_missing_key_fails_cleanly_and_keeps_report(self):
        os.environ["EODHD_API_KEY"] = ""
        config.reload()
        r = pipeline.run("programada")
        self.assertEqual(r["status"], "fallo")
        self.assertIn("EODHD_API_KEY", r["error"])

    def test_catalog_import(self):
        self.fp.STATE["asof"] = "2026-10-01"
        pipeline.run("cli", now=datetime(2026, 10, 2, 7, 5, tzinfo=TZ).astimezone(UTC))
        res = catalog.import_catalog_csv("ticker,mercado\nAAPL,SIC\nWALMEX,Trading MX\nNOEXISTE,Trading USA\n", "mi_lista.csv")
        self.assertEqual(res["coinciden"], 2)
        self.assertEqual(res["sin_coincidencia"], 1)
        self.assertEqual(db.q1("SELECT gbm_status FROM instruments WHERE provider_symbol='AAPL.MX'")["gbm_status"], "verificado")


class TestYahooFree(unittest.TestCase):
    """Versión gratis: catálogo Nasdaq + semilla BMV + Yahoo (simulados), detección SIC, dividendos y exportación estática."""

    @classmethod
    def setUpClass(cls):
        from tests import fake_providers
        cls.fp = fake_providers
        cls.srv, cls.base = fake_providers.start()

    def setUp(self):
        fresh_db()
        os.environ.update({"DATA_MODE": "auto", "PRICE_PROVIDER": "yahoo", "YAHOO_BASE_URL": self.base + "/yahoo",
                           "NASDAQ_SYMDIR_URL": self.base + "/nasdaq", "YAHOO_MIN_INTERVAL_MS": "0", "BANXICO_TOKEN": "",
                           "COINGECKO_BASE_URL": self.base + "/cg", "EODHD_API_KEY": "", "SIC_PROBE_PER_RUN": "5", "NASDAQ_MIN_ROWS": "5"})
        config.reload()
        from app.providers import yahoo
        yahoo._session = None
        coingecko._client = None
        coingecko.client().min_interval = 0
        self.fp.STATE.update({"asof": "2026-10-01", "bulk_dividends": [], "yahoo_down": False})

    def tearDown(self):
        for k in ("PRICE_PROVIDER", "YAHOO_BASE_URL", "NASDAQ_SYMDIR_URL", "SIC_PROBE_PER_RUN", "DATA_MODE"):
            os.environ.pop(k, None)
        config.reload()

    def test_free_flow_and_static_export(self):
        self.assertEqual(config.effective_mode(), "real")
        fri = datetime(2026, 10, 2, 7, 5, tzinfo=TZ).astimezone(UTC)
        r = pipeline.run("programada", now=fri)
        self.assertEqual(r["status"], "exito", r)
        insts = {x["provider_symbol"]: x for x in db.q("SELECT * FROM instruments WHERE is_demo=0")}
        self.assertIn("BRK.B.US", insts)
        self.assertNotIn("ZZZW.US", insts)       # warrant excluido
        self.assertNotIn("TEST.US", insts)       # emisión de prueba excluida
        self.assertNotIn("ABC$A.US", insts)      # preferente excluida
        self.assertEqual(insts["NAFTRACISHRS.MX"]["is_benchmark"], 1)
        self.assertEqual(insts["IVV.MX"]["gbm_modality"], "Trading MX (SIC)")
        self.assertEqual(insts["WALMEX.MX"]["gbm_modality"], "Trading MX")
        self.assertEqual(insts["FUNO11.MX"]["category"], "fibra")
        self.assertEqual(insts["IBIT.US"]["gbm_status"], "verificado")
        fx = db.q1("SELECT source FROM fx LIMIT 1")
        self.assertIn("Yahoo", fx["source"])
        m = {json.loads(x["data"])["provider_symbol"]: json.loads(x["data"]) for x in db.q("SELECT data FROM report_metrics")}
        self.assertEqual(m["NVDA.US"]["ret_price"]["1a"] is not None, True)
        self.assertFalse(m["NVDA.US"]["price_return_uncertain"])  # el close de Yahoo ya viene ajustado por splits
        self.assertEqual(m["WALMEX.MX"]["benchmark"], "NAFTRACISHRS.MX")
        self.assertIsNotNone(m["AAPL.US"]["ret_mxn"]["1m"])
        s = json.loads(db.q1("SELECT summary FROM reports")["summary"])
        self.assertIn("Yahoo", s["fuentes"][0]["nombre"])
        # día siguiente: solo agrega la sesión nueva; dividendo -> recarga historial; detección SIC
        self.fp.STATE.update({"asof": "2026-10-02", "bulk_dividends": ["MSFT"]})
        sat = datetime(2026, 10, 3, 7, 5, tzinfo=TZ).astimezone(UTC)
        r = pipeline.run("programada", now=sat)
        self.assertEqual(r["status"], "exito", r)
        last = db.q1("SELECT MAX(date) d FROM prices p JOIN instruments i ON i.id=p.instrument_id WHERE i.provider_symbol='AAPL.US'")["d"]
        self.assertEqual(last, "2026-10-02")
        msft = db.q1("SELECT needs_backfill, last_backfill_at FROM instruments WHERE provider_symbol='MSFT.US'")
        self.assertEqual(msft["needs_backfill"], 0)  # se recargó en la misma ejecución
        sic = db.q("SELECT provider_symbol FROM instruments WHERE gbm_modality='Trading MX (SIC)' AND data_source LIKE '%SIC detectada%'")
        self.assertGreaterEqual(len(sic), 1)
        # exportación estática
        out = os.path.join(tempfile.mkdtemp(), "site")
        res = subprocess.run([sys.executable, "scripts/export_static.py", "--out", out, "--repo", "usuario/radar-gbm"], cwd=ROOT,
                             env=dict(os.environ, ENABLE_INTERNAL_SCHEDULER="0"), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        for f in ("index.html", "data/status.json", "data/report_latest.json", "data/instruments.json", "data/reports.json", "data/runs.json"):
            self.assertTrue(os.path.exists(os.path.join(out, f)), f)
        with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
            self.assertIn("RADAR_STATIC", f.read())
        self.assertGreater(len(os.listdir(os.path.join(out, "data", "inst"))), 10)
        self.__class__.site = out

    def test_yahoo_blocked_keeps_last_report(self):
        fri = datetime(2026, 10, 2, 7, 5, tzinfo=TZ).astimezone(UTC)
        self.assertEqual(pipeline.run("programada", now=fri)["status"], "exito")
        self.fp.STATE.update({"asof": "2026-10-02", "yahoo_down": True})
        os.environ["YAHOO_MIN_INTERVAL_MS"] = "0"
        from app.providers import yahoo
        import time as _t
        orig = _t.sleep
        _t.sleep = lambda x: None
        try:
            r = pipeline.run("programada", now=datetime(2026, 10, 3, 7, 5, tzinfo=TZ).astimezone(UTC))
        finally:
            _t.sleep = orig
        # el reporte nuevo marca datos atrasados en vez de inventar; el anterior sigue guardado
        self.assertIn(r["status"], ("exito", "fallo"))
        self.assertGreaterEqual(db.q1("SELECT COUNT(*) n FROM reports")["n"], 1)
        ev = " ".join(x["message"] for x in db.q("SELECT message FROM run_events"))
        self.assertIn("Yahoo rechazó", ev)


class TestWebApi(unittest.TestCase):
    def setUp(self):
        fresh_db()
        os.environ["DATA_MODE"] = "demo"
        os.environ["CRON_SECRET"] = "s3cret"
        config.reload()
        from app import web
        self.app = web.create_app(start_scheduler=False).test_client()

    def tearDown(self):
        os.environ.pop("CRON_SECRET", None)
        os.environ.pop("APP_ACCESS_TOKEN", None)
        config.reload()

    def test_endpoints(self):
        self.assertEqual(self.app.post("/api/cron/tick").status_code, 401)
        r = self.app.post("/api/cron/tick?wait=1", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(r.status_code, 200)
        st = self.app.get("/api/status").get_json()
        self.assertEqual(st["modo"], "demo")
        self.assertIsNotNone(st["ultimo_reporte"])
        self.assertNotIn("TESTKEY", json.dumps(st))
        lst = self.app.get("/api/instruments?category=etf&sort=vol63&dir=asc").get_json()
        vols = [x["vol63"] for x in lst["items"] if x["vol63"] is not None]
        self.assertEqual(vols, sorted(vols))
        iid = lst["items"][0]["id"]
        d = self.app.get(f"/api/instruments/{iid}").get_json()
        self.assertIn("serie", d)
        self.assertEqual(self.app.post(f"/api/favorites/{iid}").status_code, 200)
        self.assertEqual(len(self.app.get("/api/favorites").get_json()["favoritos"]), 1)
        bad = self.app.put("/api/settings", json={"horario": "25:99"})
        self.assertEqual(bad.status_code, 400)
        ok = self.app.put("/api/settings", json={"horario": "06:45", "presupuesto_mxn": 1000})
        self.assertEqual(ok.get_json()["config"]["horario"], "06:45")
        bud = self.app.get("/api/instruments?budget=1000&page_size=200").get_json()
        for x in bud["items"]:
            self.assertNotEqual(x["presupuesto"]["alcanza"], False)
        cmp_ = self.app.get(f"/api/compare?ids={iid},{lst['items'][1]['id']}").get_json()
        self.assertEqual(cmp_["series"][0]["norm"][0][1], 100.0)

    def test_access_token(self):
        os.environ["APP_ACCESS_TOKEN"] = "clave"
        config.reload()
        self.assertEqual(self.app.get("/api/status").status_code, 401)
        self.assertEqual(self.app.get("/api/status", headers={"X-Access-Token": "clave"}).status_code, 200)
        self.assertEqual(self.app.get("/api/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
