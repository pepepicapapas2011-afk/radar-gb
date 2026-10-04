"""Indicadores reproducibles (funciones puras, sin dependencias externas).

Convenciones (ver METODOLOGIA.md):
- Rendimiento total: usa adjusted_close del proveedor (ajustado por splits y dividendos).
- Rendimiento por precio: usa close ajustado solo por splits registrados (sin dividendos).
- Periodos en sesiones: 1d=1, 1s=5, 1m=21, 3m=63, 1a=252. En referencias cripto (24/7) se usan días: 1, 7, 30, 90, 365.
- Volatilidad: desviación estándar muestral de log-rendimientos diarios (63 sesiones) × √252 (√365 en cripto).
- Caída máxima: peor caída desde un máximo previo dentro de la ventana analizada (hasta 252 sesiones; se informa la ventana)."""
import math
from bisect import bisect_right

PERIODS_SESSIONS = {"1d": 1, "1s": 5, "1m": 21, "3m": 63, "1a": 252}
PERIODS_CRYPTO = {"1d": 1, "1s": 7, "1m": 30, "3m": 90, "1a": 365}


def split_adjusted_closes(dates, closes, splits):
    """Ajusta 'close' por splits posteriores a cada fecha. splits: lista de (fecha, razón nuevas/anteriores)."""
    if not splits:
        return list(closes)
    sp = sorted(splits)
    out = []
    for d, c in zip(dates, closes):
        f = 1.0
        for sd, ratio in sp:
            if sd > d and ratio:
                f *= ratio
        out.append(c / f if c is not None else None)
    return out


def unexplained_jumps(dates, closes, adj, splits, threshold=0.2):
    """Detecta saltos >20% en la razón adj/close que no coinciden con un split registrado.
    Indican eventos corporativos no documentados: el rendimiento por precio se marca como incierto."""
    split_days = {d for d, _ in splits or []}
    out = []
    prev = None
    for d, c, a in zip(dates, closes, adj):
        if not c or not a:
            continue
        r = a / c
        if prev and abs(r / prev - 1) > threshold and d not in split_days:
            out.append(d)
        prev = r
    return out


def period_return(values, n):
    if len(values) <= n:
        return None
    a, b = values[-1 - n], values[-1]
    if not a or b is None or a <= 0:
        return None
    return b / a - 1


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def sma_series(values, n):
    out, s = [], 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def log_returns(values):
    return [math.log(values[i] / values[i - 1]) for i in range(1, len(values)) if values[i] and values[i - 1] and values[i - 1] > 0 and values[i] > 0]


def volatility(values, window=63, annual=252):
    lr = log_returns(values[-(window + 1):])
    if len(lr) < max(20, window // 2):
        return None
    m = sum(lr) / len(lr)
    var = sum((x - m) ** 2 for x in lr) / (len(lr) - 1)
    return math.sqrt(var) * math.sqrt(annual)


def daily_vol(values, window=63):
    v = volatility(values, window, 1)
    return v


def max_drawdown(values, window=252):
    w = [v for v in values[-(window + 1):] if v]
    if len(w) < 2:
        return None, 0
    peak, mdd = w[0], 0.0
    for v in w:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd, len(w) - 1


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def trend_points(price, s20, s50, s200):
    """0–4 puntos. Si falta SMA200 la tendencia no es comparable (se devuelve None)."""
    if None in (price, s20, s50, s200):
        return None, []
    checks = [
        (price > s50, "precio arriba de su media de 50 sesiones"),
        (price > s200, "precio arriba de su media de 200 sesiones"),
        (s50 > s200, "media de 50 arriba de la de 200 (tendencia de mediano plazo al alza)"),
        (s20 > s50, "media de 20 arriba de la de 50 (impulso reciente)"),
    ]
    return sum(1 for ok, _ in checks if ok), [txt for ok, txt in checks if ok]


def value_at_or_before(series_dates, series_values, d):
    i = bisect_right(series_dates, d) - 1
    if i < 0:
        return None, None
    return series_dates[i], series_values[i]


def percentile_ranks(values):
    """Percentil (0–100) de cada valor dentro de la lista; empates comparten el promedio. None se respeta."""
    idx = [(v, i) for i, v in enumerate(values) if v is not None]
    out = [None] * len(values)
    n = len(idx)
    if n == 0:
        return out
    if n == 1:
        out[idx[0][1]] = 50.0
        return out
    idx.sort()
    i = 0
    while i < n:
        j = i
        while j + 1 < n and idx[j + 1][0] == idx[i][0]:
            j += 1
        rank = (i + j) / 2
        for k in range(i, j + 1):
            out[idx[k][1]] = 100.0 * rank / (n - 1)
        i = j + 1
    return out
