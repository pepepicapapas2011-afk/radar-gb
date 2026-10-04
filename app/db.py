"""Base de datos persistente (SQLite en disco). Todo lo que la app necesita sobrevivir a reinicios vive aquí."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from .config import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_symbol TEXT NOT NULL UNIQUE,   -- p. ej. AAPL.US, AAPL.MX, BTC-REF
  ticker TEXT NOT NULL,
  exchange TEXT NOT NULL,                 -- US, MX, CRYPTO
  venue TEXT,                             -- NYSE, NASDAQ, BMV/SIC...
  name TEXT,
  isin TEXT,
  currency TEXT,
  type TEXT,                              -- stock, etf, fibra, fund, crypto_ref
  category TEXT,                          -- accion, etf, fibra, cripto_ref
  crypto_exposure TEXT DEFAULT 'ninguna', -- ninguna, etf_cripto, accion_sector_cripto, referencia_externa
  gbm_modality TEXT,                      -- Trading MX, Trading MX (SIC), Trading USA, Referencia externa
  gbm_status TEXT DEFAULT 'pendiente',    -- verificado, pendiente, no_disponible, referencia
  gbm_source TEXT,
  gbm_verified_at TEXT,
  data_source TEXT,
  is_leveraged INTEGER DEFAULT 0,
  is_benchmark INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1,
  is_demo INTEGER DEFAULT 0,
  needs_backfill INTEGER DEFAULT 1,
  last_backfill_at TEXT,
  first_seen_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_instr_isin ON instruments(isin);
CREATE INDEX IF NOT EXISTS ix_instr_demo ON instruments(is_demo, active);

CREATE TABLE IF NOT EXISTS prices (
  instrument_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL,
  source TEXT, fetched_at TEXT,
  PRIMARY KEY (instrument_id, date)
);

CREATE TABLE IF NOT EXISTS splits (
  instrument_id INTEGER NOT NULL, date TEXT NOT NULL, ratio REAL NOT NULL, source TEXT,
  PRIMARY KEY (instrument_id, date)
);

CREATE TABLE IF NOT EXISTS fx (
  date TEXT PRIMARY KEY, usd_mxn REAL NOT NULL, source TEXT, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS fx_demo (
  date TEXT PRIMARY KEY, usd_mxn REAL NOT NULL, source TEXT, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS fundamentals (
  instrument_id INTEGER PRIMARY KEY, data TEXT, source TEXT, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS news (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument_id INTEGER, kind TEXT, date TEXT, title TEXT, url TEXT, source TEXT, fetched_at TEXT,
  UNIQUE(instrument_id, kind, date, title)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_key TEXT NOT NULL,          -- fecha local + modo
  trigger TEXT NOT NULL,          -- programada, manual, cron_externo, cli
  status TEXT NOT NULL,           -- en_curso, exito, fallo, omitida
  mode TEXT NOT NULL,             -- real, demo
  attempt INTEGER DEFAULT 1,
  started_at TEXT, finished_at TEXT,
  data_cutoff TEXT,
  report_id INTEGER,
  error TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_key ON runs(run_key);

CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, ts TEXT, level TEXT, message TEXT
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_date TEXT NOT NULL,       -- fecha local (America/Matamoros)
  mode TEXT NOT NULL,              -- real | demo (nunca se mezclan)
  version INTEGER DEFAULT 1,
  run_id INTEGER,
  created_at TEXT,
  data_fingerprint TEXT,
  summary TEXT,                    -- JSON
  UNIQUE(report_date, mode)
);

CREATE TABLE IF NOT EXISTS report_metrics (
  report_id INTEGER NOT NULL, instrument_id INTEGER NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (report_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS favorites (instrument_id INTEGER PRIMARY KEY, added_at TEXT);
CREATE TABLE IF NOT EXISTS locks (name TEXT PRIMARY KEY, owner TEXT, expires_at TEXT);
CREATE TABLE IF NOT EXISTS api_usage (day TEXT, provider TEXT, calls INTEGER DEFAULT 0, PRIMARY KEY(day, provider));
CREATE TABLE IF NOT EXISTS http_cache (key TEXT PRIMARY KEY, body TEXT, fetched_at TEXT, expires_at TEXT);
CREATE TABLE IF NOT EXISTS catalog_imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, imported_at TEXT, filename TEXT, rows INTEGER, matched INTEGER, unmatched INTEGER, notes TEXT
);
"""


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now_utc()).isoformat(timespec="seconds")


def conn():
    path = config.DATABASE_PATH
    c = getattr(_local, "conn", None)
    if c is not None and getattr(_local, "path", None) == path:
        return c
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    _local.conn, _local.path = c, path
    return c


def close():
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


MIGRATIONS = [
    "ALTER TABLE instruments ADD COLUMN group_key TEXT",
    "ALTER TABLE instruments ADD COLUMN liq_mxn REAL",
    "ALTER TABLE instruments ADD COLUMN fail_count INTEGER DEFAULT 0",
    "ALTER TABLE instruments ADD COLUMN sic_probed_at TEXT",
    "ALTER TABLE instruments ADD COLUMN data_note TEXT",
    "CREATE INDEX IF NOT EXISTS ix_instr_group ON instruments(group_key)",
]


def init_db():
    c = conn()
    c.executescript(SCHEMA)
    for m in MIGRATIONS:
        try:
            c.execute(m)
        except sqlite3.OperationalError:
            pass  # ya aplicada


def q(sql, params=()):
    return conn().execute(sql, params).fetchall()


def q1(sql, params=()):
    return conn().execute(sql, params).fetchone()


def ex(sql, params=()):
    return conn().execute(sql, params)


def exmany(sql, rows):
    c = conn()
    c.execute("BEGIN")
    try:
        c.executemany(sql, rows)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


class transaction:
    def __enter__(self):
        conn().execute("BEGIN IMMEDIATE")
        return conn()

    def __exit__(self, et, ev, tb):
        conn().execute("ROLLBACK" if et else "COMMIT")
        return False


def get_setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except Exception:
        return r["value"]


def set_setting(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, json.dumps(value)))


def acquire_lock(name, owner, ttl_seconds):
    """Candado con caducidad: evita ejecuciones simultáneas incluso con varios procesos."""
    from datetime import timedelta
    now = now_utc()
    with transaction() as c:
        r = c.execute("SELECT owner, expires_at FROM locks WHERE name=?", (name,)).fetchone()
        if r and r["expires_at"] > iso(now) and r["owner"] != owner:
            return False
        c.execute("INSERT INTO locks(name,owner,expires_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, expires_at=excluded.expires_at",
                  (name, owner, iso(now + timedelta(seconds=ttl_seconds))))
    return True


def release_lock(name, owner):
    ex("DELETE FROM locks WHERE name=? AND owner=?", (name, owner))


def add_usage(provider, calls):
    day = now_utc().date().isoformat()
    ex("INSERT INTO api_usage(day,provider,calls) VALUES(?,?,?) ON CONFLICT(day,provider) DO UPDATE SET calls=calls+excluded.calls",
       (day, provider, calls))


def usage_today(provider):
    r = q1("SELECT calls FROM api_usage WHERE day=? AND provider=?", (now_utc().date().isoformat(), provider))
    return r["calls"] if r else 0
