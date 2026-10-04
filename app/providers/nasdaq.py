"""Directorio oficial de símbolos de Nasdaq Trader (gratis, se actualiza a diario):
- nasdaqlisted.txt: Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
- otherlisted.txt:  ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
  Exchange: A=NYSE American, N=NYSE, P=NYSE Arca, Z=Cboe BZX, V=IEX
Especificación: https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs"""
import os
import re

import requests

from ..config import config
from .http import ProviderError

VENUE = {"A": "NYSE MKT", "N": "NYSE", "P": "NYSE ARCA", "Z": "BATS", "V": "IEX"}
# Se excluyen valores que no son acciones comunes ni ETFs (warrants, units, derechos, preferentes, notas)
EXCLUDE = re.compile(r"(\bwarrants?\b|\bunits?\b|\brights?\b|\bpreferred\b|\bpfd\b|\bnotes? due\b|\bdebentures?\b|\bsubordinated\b|% (fixed|series)|\bwhen issued\b)", re.I)


def _fetch(name):
    try:
        r = requests.get(f"{config.NASDAQ_SYMDIR_URL.rstrip('/')}/{name}", timeout=60, headers={"User-Agent": "RadarGBM/1.0"})
    except requests.RequestException as e:
        raise ProviderError(f"nasdaq: error de red ({type(e).__name__})")
    if r.status_code != 200:
        raise ProviderError(f"nasdaq: HTTP {r.status_code} en {name}")
    return r.text.splitlines()


def us_listings():
    """Lista de acciones y ETFs de NYSE, Nasdaq, NYSE Arca, NYSE American y Cboe (formato de exchange-symbol-list)."""
    out = []
    lines = _fetch("nasdaqlisted.txt")
    for ln in lines[1:]:
        p = ln.split("|")
        if len(p) < 8 or ln.startswith("File Creation Time") or p[3] == "Y":
            continue
        sym, name, etf = p[0], p[1], p[6] == "Y"
        if not etf and EXCLUDE.search(name):
            continue
        out.append({"Code": sym, "Name": name, "Exchange": "NASDAQ", "Currency": "USD", "Type": "ETF" if etf else "Common Stock", "Isin": None})
    lines = _fetch("otherlisted.txt")
    for ln in lines[1:]:
        p = ln.split("|")
        if len(p) < 8 or ln.startswith("File Creation Time") or p[6] == "Y":
            continue
        sym, name, exch, etf = p[0], p[1], p[2], p[4] == "Y"
        if "$" in sym or (not etf and EXCLUDE.search(name)):
            continue
        out.append({"Code": sym, "Name": name, "Exchange": VENUE.get(exch, exch), "Currency": "USD", "Type": "ETF" if etf else "Common Stock", "Isin": None})
    if len(out) < int(os.environ.get("NASDAQ_MIN_ROWS", "1000")):
        raise ProviderError("nasdaq: el directorio de símbolos llegó incompleto")
    return out
