# Radar GBM

Página web en español que analiza todos los días las acciones, ETFs e instrumentos con exposición cripto de los mercados que ofrece GBM (Trading MX, SIC y Trading USA). Muestra cuáles tuvieron mayor rendimiento reciente y cuáles conviene investigar por su relación entre rendimiento y riesgo.

> Herramienta de análisis educativo. No es asesoría, no ejecuta compras ni ventas, no pide tu contraseña de GBM y no está afiliada a GBM.

## Versión GRATIS (recomendada para empezar) – 0 pesos al mes

| Pieza | Servicio | Costo |
|---|---|---|
| Ejecución diaria en la nube | GitHub Actions (repositorio público) | Gratis |
| Página web | GitHub Pages | Gratis |
| Precios | Yahoo Finance (fuente **no oficial**, uso personal) | Gratis |
| Catálogo de EE. UU. | Directorio oficial de Nasdaq Trader | Gratis |
| Catálogo BMV/SIC | Semilla `data/bmv_semilla.csv` + detección SIC en Yahoo + tu lista `data/catalogo_gbm.csv` (+ lista gratuita de EODHD si pones `EODHD_API_KEY`) | Gratis |
| Tipo de cambio | Banxico FIX (respaldo: Yahoo) | Gratis |
| BTC / ETH | CoinGecko (referencia externa) | Gratis |

Limitaciones honestas: Yahoo puede limitar o cambiar su servicio sin aviso (la página lo indica y conserva el último reporte válido); el repositorio y la página son públicos; GitHub puede atrasar las tareas programadas de 15 a 60 minutos; no hay fundamentales (crecimiento, deuda, comisiones de ETFs) y esos campos dirán "Dato no disponible"; los instrumentos poco líquidos se actualizan solo los lunes.

### Pasos (todo desde el navegador)

1. Crea una cuenta en https://github.com/signup
2. Crea un repositorio **público** llamado `radar-gbm` en https://github.com/new (sin README).
3. Descomprime `radar-gbm.zip`. En la página del repositorio elige **"uploading an existing file"** y arrastra **todo el contenido** de la carpeta (incluida la carpeta oculta `.github`; en Mac muéstrala con Cmd + Shift + punto). Pulsa **Commit changes**.
4. En el repositorio: **Settings → Pages → Source: GitHub Actions**.
5. **Settings → Secrets and variables → Actions → New repository secret** y crea:
   - `BANXICO_TOKEN` (pídelo en https://www.banxico.org.mx/SieAPIRest/service/v1/token)
   - `COINGECKO_API_KEY` (clave Demo en https://www.coingecko.com/en/api/pricing)
   - Opcional: `NOTIFY_NTFY_URL` = `https://ntfy.sh/un-tema-dificil-de-adivinar` (instala la app ntfy y suscríbete a ese tema para recibir el aviso en el celular)
6. Pestaña **Actions** → si lo pide, **habilita los workflows** → "Radar GBM - ejecución diaria" → **Run workflow**. La primera vez tarda de 20 a 60 minutos (descarga historiales; la cobertura completa se alcanza en 3–4 días y la página muestra cuántos faltan).
7. Abre `https://TU-USUARIO.github.io/radar-gbm/`

Desde ahí se actualiza sola cada día a la hora de `ajustes.json` (07:00, America/Matamoros) aunque tu computadora esté apagada. Para cambiar la hora, edita `ajustes.json` en GitHub.

### Cómo funciona la automatización gratuita

- `.github/workflows/radar-diario.yml` corre cada hora en los servidores de GitHub. Descarga la base de datos guardada (versión "radar-datos"), y solo analiza si ya pasó tu hora y no hay reporte de hoy (maneja horario de verano y reintenta hasta 3 veces si falla).
- Si analiza: genera el informe fechado, publica la página en GitHub Pages y guarda la base de datos de nuevo. Los reportes persisten entre ejecuciones.
- "Actualizar ahora" abre GitHub para pulsar **Run workflow** (mismo proceso, con espera mínima de 20 min).
- `.github/workflows/mantener-activo.yml` hace un commit vacío al mes para que GitHub no pause las tareas por inactividad.

## Versión de pago (opcional, más confiable)

Servidor propio (Fly.io ~4 USD/mes o Render ~7 USD/mes + disco) con `PRICE_PROVIDER=eodhd` y EODHD (~19.99 USD/mes): datos con licencia, fundamentales opcionales, página privada con clave. Ver `fly.toml`, `render.yaml`, `Dockerfile` y `.env.example`.

## Arquitectura

```
app/providers/  yahoo, nasdaq, eodhd, banxico, coingecko (caché, lotes, reintentos, límites)
app/ingest_free.py, app/ingest.py   obtención de datos (gratis / de pago)
app/indicators.py, app/analysis.py  cálculos reproducibles
app/scoring.py   metodología (ver METODOLOGIA.md)
app/report.py    informe, explicaciones, "qué cambió"
app/pipeline.py  proceso diario, candado, reintentos, horario
app/web.py       API + interfaz (modo servidor)
scripts/export_static.py   genera el sitio estático para GitHub Pages
static/          interfaz (funciona con servidor o como sitio estático)
tests/           pruebas con proveedores simulados
```

## Probar en tu computadora

```bash
pip install -r requirements.txt
DATA_MODE=demo python wsgi.py               # http://localhost:8000 con datos DEMO
python -m unittest discover -s tests -t .   # pruebas
```
