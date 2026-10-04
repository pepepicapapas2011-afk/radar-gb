# Metodología de Radar GBM (versión 1.0)

Radar GBM es una herramienta de análisis educativo. No ejecuta operaciones y no es asesoría de inversión. La puntuación 0–100 es un indicador **comparativo** dentro de un grupo de instrumentos equivalentes; **no es una probabilidad de ganar dinero**.

## 1. Datos

| Dato | Fuente | Tipo | Notas |
|---|---|---|---|
| Catálogo BMV (incluye SIC) y EE. UU. | EODHD `exchange-symbol-list` (MX, US) | Semanal | Se excluyen OTC, fondos mutuos y warrants |
| Precios diarios | EODHD `eod` (historial) y `eod-bulk-last-day` (lote diario) | Último cierre (EOD) | No es tiempo real |
| Splits y dividendos | EODHD `splits`, `eod-bulk-last-day?type=splits/dividends` | Diario | Si hay dividendo o split, se recarga el historial ajustado |
| Tipo de cambio | Banxico SIE, serie SF43718 (FIX) | Días hábiles bancarios | Respaldo: EODHD `USDMXN.FOREX` |
| BTC y ETH | CoinGecko `market_chart` | Diario, 00:00 UTC | Solo referencia externa |
| Fundamentales, noticias, eventos | EODHD (opcional, según plan) | Caché 7 días / diario | Solo para destacados |

**Versión gratuita:** precios de Yahoo Finance (no oficial; su 'close' ya viene ajustado por splits y 'adjclose' por splits y dividendos; si Yahoo cambia el historial o hay dividendo/split, se recarga), catálogo de EE. UU. del directorio oficial de Nasdaq Trader, BMV/SIC desde semilla, detección de cotizaciones .MX y tu catálogo. Sin fundamentales. Los instrumentos con liquidez < 500 mil MXN/día se actualizan los lunes. Las cotizaciones SIC sin ISIN se agrupan con su origen por ticker.

Cada dato guarda fuente y fecha. Si falta, la página muestra "Dato no disponible".

## 2. Modalidad y verificación en GBM

GBM no ofrece una API pública ni un catálogo descargable (revisado el 3-oct-2026). Por eso:

- **Trading MX**: emisoras de la BMV con ISIN mexicano (acciones completas, según GBM).
- **Trading MX (SIC)**: valores listados en la BMV con ISIN extranjero; cotizan en MXN.
- **Trading USA**: acciones y ETFs de NYSE/Nasdaq/NYSE Arca/Cboe; GBM indica que permite fracciones.
- **Verificado**: solo si aparece en una fuente oficial de GBM (`data/gbm_verificados.csv`, p. ej. los 11 ETFs de bitcoin) o en un catálogo que el usuario importe. Todo lo demás queda "por verificar".
- **Cripto**: GBM ofrece ETFs de bitcoin listados en EE. UU. No se encontró evidencia de compra directa de criptomonedas en GBM. BTC y ETH se muestran como "Referencia externa".
- Un mismo instrumento puede tener varias cotizaciones (p. ej. `AAPL.US` en USD y `AAPL.MX` en el SIC en MXN). Se agrupan por ISIN y la ficha muestra la diferencia SIC vs origen × FIX.

## 3. Indicadores (por instrumento)

- **Rendimiento total**: con `adjusted_close` (splits y dividendos). **Por precio**: `close` ajustado solo por splits registrados. Si la razón ajustado/cierre salta más de 20% sin un split registrado, el rendimiento por precio se marca como incierto.
- **Periodos**: 1 sesión, 5 (1 semana), 21 (1 mes), 63 (3 meses), 252 (1 año). En cripto, días naturales: 1, 7, 30, 90, 365.
- **Rendimiento en MXN**: `(1 + r) × FIX_fin / FIX_inicio − 1` para instrumentos en USD (FIX en la fecha o la anterior más cercana).
- **Medias móviles**: 20, 50 y 200 sesiones sobre el precio ajustado por splits.
- **Volatilidad**: desviación estándar muestral de log-rendimientos diarios de 63 sesiones × √252 (√365 en cripto).
- **Caída máxima**: peor caída desde un máximo previo en las últimas 252 sesiones (se informa la ventana real).
- **Liquidez**: promedio de `cierre × volumen` de 20 sesiones, en su moneda y convertido a MXN.
- **Volumen relativo**: volumen de la última sesión ÷ promedio de las 20 anteriores.
- **Referencia (misma moneda, mismas fechas)**: EE. UU. → SPY; BMV local → NAFTRAC (IPC); SIC → IVV.MX (S&P 500 en MXN); ETFs cripto → BTC. Es una referencia general.
- **Cobertura**: sesiones con dato ÷ sesiones esperadas según el calendario (ventana de 252).
- **Datos atrasados**: sesiones esperadas según el calendario de la bolsa que no tienen dato.

## 4. Puntuación de investigación (0–100)

Grupos que se comparan por separado: **Acciones** (sin sector cripto), **ETFs** (sin apalancados/inversos ni cripto) y **Exposición cripto** (ETFs cripto y acciones del sector).

Componentes (percentiles dentro de los elegibles del grupo):

| Componente | Peso | Cálculo |
|---|---|---|
| Rendimiento | 30% | 0.25·p(r1m MXN) + 0.40·p(r3m MXN) + 0.35·p(r1a MXN) |
| Tendencia | 20% | puntos/4: precio>SMA50, precio>SMA200, SMA50>SMA200, SMA20>SMA50 |
| Riesgo | 25% | 0.5·(100 − p(vol63)) + 0.5·(100 − p(|caída máx.|)) |
| Liquidez | 15% | p(log liquidez MXN) |
| Calidad de datos | 10% | 100·(0.6·cobertura + 0.4·min(sesiones/252, 1)) |

**Exclusiones** (se informan por instrumento): menos de 252 sesiones; más de 3 sesiones atrasadas; cobertura < 80%; liquidez < 1 millón MXN/día; precio < 1 USD equivalente; ETF apalancado o inverso; sin rendimiento en MXN; referencias externas; (opcional) no verificado en GBM.

Se requieren al menos **5 elegibles** por grupo; si no, el grupo no muestra ranking. Se muestra una sola cotización por ISIN (la más líquida). Nunca se rellenan espacios.

## 5. Top 10 por rendimiento observado

Rendimiento total en MXN del periodo elegido entre acciones, ETFs y FIBRAS con liquidez ≥ 1 millón MXN/día, precio ≥ 1 USD, datos con ≤ 3 sesiones de atraso y sin apalancados. Es lo que **ya pasó**; no predice.

## 6. Alertas (umbrales orientativos)

Movimiento de la última sesión > máx(5%, 3 × volatilidad diaria); volumen > 3× su promedio; volatilidad anual > 60%; caída máxima > 40%; ETF apalancado con movimiento > 5%; favoritos sin dato de la sesión esperada. No se atribuye ningún movimiento a una noticia.

## 7. Calendarios

Reglas de festivos de NYSE y BMV (incluye Jueves y Viernes Santo, 2 de noviembre y 12 de diciembre para BMV). El último cierre real recibido es la fuente de verdad; festivos no previstos se agregan con `MARKET_HOLIDAYS_EXTRA`. En fines de semana y festivos el reporte indica que no hay sesiones nuevas y no presenta precios antiguos como movimientos nuevos.

## 8. Explicaciones

Se generan con plantillas a partir de las cifras calculadas. No se usa IA para generar precios ni cifras.
