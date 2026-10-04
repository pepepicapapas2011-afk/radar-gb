"""Servidor HTTP local que imita los formatos documentados de EODHD, Banxico SIE y CoinGecko.
Sirve para probar los adaptadores reales (lotes, reintentos, límites) sin internet ni claves."""
import json
import math
import random
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app import calendars

TODAY = date(2026, 10, 3)  # sábado
US = [("SPY", "SPDR S&P 500 ETF Trust", "NYSE ARCA", "ETF", "US78462F1030"),
      ("IBIT", "iShares Bitcoin Trust ETF", "NASDAQ", "ETF", "US46438F1012"),
      ("FBTC", "Fidelity Wise Origin Bitcoin Fund", "BATS", "ETF", "US3159481079"),
      ("QQQ", "Invesco QQQ Trust", "NASDAQ", "ETF", "US46090E1038"),
      ("SQQQ", "ProShares UltraPro Short QQQ -3x", "NASDAQ", "ETF", "US74347G4322"),
      ("AAPL", "Apple Inc", "NASDAQ", "Common Stock", "US0378331005"),
      ("MSFT", "Microsoft Corporation", "NASDAQ", "Common Stock", "US5949181045"),
      ("COIN", "Coinbase Global Inc", "NASDAQ", "Common Stock", "US19260Q1076"),
      ("MSTR", "Strategy Inc", "NASDAQ", "Common Stock", "US5949724083"),
      ("NVDA", "NVIDIA Corporation", "NASDAQ", "Common Stock", "US67066G1040"),
      ("OTCX", "Some OTC Co", "PINK", "Common Stock", "US0000000001")]
MX = [("NAFTRAC", "NAFTRAC ISHRS", "MX", "ETF", "MX1BNA060006"),
      ("IVV", "iShares Core S&P 500 ETF", "MX", "ETF", "US4642872000"),
      ("AAPL", "Apple Inc", "MX", "Common Stock", "US0378331005"),
      ("WALMEX", "Wal-Mart de Mexico SAB de CV", "MX", "Common Stock", "MX01WA000038"),
      ("FUNO11", "Fibra Uno Administracion SA de CV", "MX", "FUND", "MXCFFU000001")]
SPLIT = {"NVDA.US": ("2026-06-10", 10.0)}
STATE = {"calls": [], "fail_once": set(), "asof": "2026-10-02", "bulk_dividends": []}


def _series(sym, ex):
    r = random.Random(sym)
    mk = "US" if ex == "US" else "MX"
    days, d = [], TODAY
    while len(days) < 300:
        if calendars.is_trading_day(mk, d) and d < TODAY:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    p, out = 100 + r.random() * 100, []
    for i, dd in enumerate(days):
        p *= math.exp(r.gauss(0.0004, 0.015))
        close = p
        if sym in SPLIT and dd.isoformat() < SPLIT[sym][0]:
            close = p * SPLIT[sym][1]
        out.append({"date": dd.isoformat(), "open": close, "high": close * 1.01, "low": close * 0.99, "close": round(close, 4),
                    "adjusted_close": round(p * 0.99 ** ((len(days) - i) / 252), 4), "volume": int(1e6 * (1 + r.random()))})
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        STATE["calls"].append(u.path)
        if u.path in STATE["fail_once"]:
            STATE["fail_once"].discard(u.path)
            return self._send({"error": "rate"}, 429, {"Retry-After": "0"})
        parts = u.path.strip("/").split("/")
        if parts[0] == "api":
            if q.get("api_token") != "TESTKEY":
                return self._send({"error": "unauthorized"}, 401)
            kind = parts[1]
            if kind == "exchange-symbol-list":
                src = US if parts[2] == "US" else MX
                cur = "USD" if parts[2] == "US" else "MXN"
                return self._send([{"Code": c, "Name": n, "Country": "", "Exchange": e, "Currency": cur, "Type": t, "Isin": i} for c, n, e, t, i in src])
            if kind == "eod":
                sym = parts[2]
                if sym == "USDMXN.FOREX":
                    return self._send([{"date": x["date"], "close": 18 + i * 0.001} for i, x in enumerate(_series("FX", "US"))])
                code, ex = sym.rsplit(".", 1)
                s = _series(sym, ex)
                return self._send([x for x in s if q.get("from", "0") <= x["date"] <= STATE["asof"]])
            if kind == "eod-bulk-last-day":
                ex = parts[2]
                d = q.get("date", STATE["asof"])
                if q.get("type") == "dividends":
                    return self._send([{"code": c, "date": d} for c in STATE["bulk_dividends"]])
                if q.get("type") == "splits" or d > STATE["asof"]:
                    return self._send([])
                rows = []
                for c, *_ in (US if ex == "US" else MX):
                    for x in _series(f"{c}.{ex}", ex):
                        if x["date"] == d:
                            rows.append(dict(x, code=c, exchange_short_name=ex))
                return self._send(rows)
            if kind == "splits":
                if parts[2] in SPLIT:
                    d, r = SPLIT[parts[2]]
                    return self._send([{"date": d, "split": f"{r:.6f}/1.000000"}])
                return self._send([])
            if kind == "fundamentals":
                if parts[2].startswith(("SPY", "QQQ", "IVV", "NAFTRAC")):
                    return self._send({"General": {"Code": parts[2]}, "ETF_Data": {"Index_Name": "S&P 500", "NetExpenseRatio": "0.0945",
                                       "Top_10_Holdings": {"AAPL.US": {"Name": "Apple", "Assets_%": 7.1}, "MSFT.US": {"Name": "Microsoft", "Assets_%": 6.5}}}})
                return self._send({"Highlights": {"QuarterlyRevenueGrowthYOY": 0.08, "QuarterlyEarningsGrowthYOY": 0.11, "PERatio": 30.2},
                                   "Valuation": {"EnterpriseValueEbitda": 22.1},
                                   "Financials": {"Balance_Sheet": {"quarterly": {"2026-06-30": {"shortLongTermDebtTotal": "100", "totalStockholderEquity": "80"}}}}})
            if kind == "news":
                return self._send([{"date": "2026-10-02T12:00:00+00:00", "title": f"Nota sobre {q.get('s')}", "link": "https://example.com/n"}])
            if kind == "calendar":
                return self._send({"earnings": [{"code": "AAPL.US", "report_date": "2026-10-29", "before_after_market": "AfterMarket"}]})
        if parts[0] == "nasdaq":
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            if parts[1] == "nasdaqlisted.txt":
                lines = ["Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"]
                lines += [f"{c}|{n}|Q|N|N|100|{'Y' if t == 'ETF' else 'N'}|N" for c, n, e, t, i in US if e == "NASDAQ"]
                lines += ["ZZZW|Something Corp - Warrant|G|N|N|100|N|N", "TEST|Test Issue Corp|G|Y|N|100|N|N"]
            else:
                lines = ["ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol"]
                code = {"NYSE": "N", "NYSE ARCA": "P", "BATS": "Z"}
                lines += [f"{c}|{n}|{code[e]}|{c}|{'Y' if t == 'ETF' else 'N'}|100|N|{c}" for c, n, e, t, i in US if e in code]
                lines += ["BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B", "ABC$A|Preferred Thing|N|ABC$A|N|100|N|ABC$A"]
            lines.append("File Creation Time: 1003202608:00|||||||")
            self.wfile.write("\n".join(lines).encode())
            return
        if parts[0] == "yahoo" and parts[1:4] == ["v8", "finance", "chart"]:
            sym = parts[4]
            if sym.startswith("ZZ") or STATE.get("yahoo_down"):
                return self._send({"chart": {"result": None, "error": {"code": "Not Found"}}}, 404 if not STATE.get("yahoo_down") else 429, {"Retry-After": "0"})
            if sym == "MXN=X":
                ser = [dict(x, close=18 + i * 0.001, adjusted_close=18 + i * 0.001) for i, x in enumerate(_series("FX", "US"))]
            elif sym.endswith(".MX"):
                ser = _series(sym, "MX")
            else:
                ser = _series(sym.replace("-", ".") + ".US", "US")
                if sym.replace("-", ".") + ".US" in SPLIT:  # Yahoo entrega 'close' ya ajustado por splits
                    d, r = SPLIT[sym + ".US"]
                    ser = [dict(x, close=x["close"] / r if x["date"] < d else x["close"]) for x in ser]
            p1, p2 = int(q.get("period1", 0)), int(q.get("period2", 4e9))
            ser = [x for x in ser if x["date"] <= STATE["asof"]]
            ts, rows = [], []
            for x in ser:
                t = int(datetime.fromisoformat(x["date"] + "T13:30:00+00:00").timestamp())
                if p1 <= t <= p2:
                    ts.append(t); rows.append(x)
            ev = {}
            if sym.replace("-", ".") in STATE["bulk_dividends"] and rows:
                ev["dividends"] = {str(ts[-1]): {"amount": 0.5, "date": ts[-1]}}
            if sym + ".US" in SPLIT:
                d, r = SPLIT[sym + ".US"]
                t = int(datetime.fromisoformat(d + "T13:30:00+00:00").timestamp())
                ev["splits"] = {str(t): {"date": t, "numerator": r, "denominator": 1, "splitRatio": f"{int(r)}:1"}}
            res = {"meta": {"currency": "MXN" if sym.endswith(".MX") else "USD", "gmtoffset": -14400, "exchangeTimezoneName": "America/New_York"},
                   "timestamp": ts, "events": ev,
                   "indicators": {"quote": [{k: [x[k] for x in rows] for k in ("open", "high", "low", "close", "volume")}],
                                  "adjclose": [{"adjclose": [x["adjusted_close"] for x in rows]}]}}
            return self._send({"chart": {"result": [res], "error": None}})
        if parts[0] == "banxico":
            if self.headers.get("Bmx-Token") != "BXTOKEN":
                return self._send({"error": "token"}, 401)
            d0, d1 = date.fromisoformat(parts[4]), date.fromisoformat(parts[5])
            datos, d = [], d0
            while d <= d1:
                if d.weekday() < 5 and d < TODAY:
                    datos.append({"fecha": d.strftime("%d/%m/%Y"), "dato": f"{18.2 + (d.toordinal() % 50) / 100:.4f}"})
                d += timedelta(days=1)
            return self._send({"bmx": {"series": [{"idSerie": "SF43718", "titulo": "FIX", "datos": datos}]}})
        if parts[0] == "cg":
            base = datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=timezone.utc)
            pts = [[int((base - timedelta(days=i)).timestamp() * 1000), 60000 + i * 10] for i in range(365, -1, -1)]
            return self._send({"prices": pts, "market_caps": pts, "total_volumes": [[t, 3e10] for t, _ in pts]})
        self._send({"error": "not found"}, 404)


def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
