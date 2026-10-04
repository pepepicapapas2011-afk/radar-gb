"""Exporta la página como sitio estático (para GitHub Pages): copia la interfaz y genera los JSON que la página lee.
Uso: python scripts/export_static.py --out site [--repo usuario/radar-gbm]"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import db, web  # noqa: E402
from app.config import config  # noqa: E402

LIST_FIELDS = ("id", "ticker", "name", "category", "type", "exchange", "venue", "currency", "gbm_modality", "gbm_status", "last_close",
               "last_date", "price_mxn", "risk_level", "vol63", "max_dd", "liq_avg_value_mxn", "vol_ratio", "trend_points", "stale_sessions",
               "crypto_exposure", "is_leveraged", "sessions", "isin")


def r6(x):
    return None if x is None else float(f"{x:.6g}")


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(web._clean(obj), default=str, ensure_ascii=False, separators=(",", ":")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "site"))
    ap.add_argument("--repo", default=config.GITHUB_REPOSITORY)
    a = ap.parse_args()
    out = a.out
    db.init_db()
    app = web.create_app(start_scheduler=False)
    c = app.test_client()
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(out, "static"))
    with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
        html = f.read()
    flag = json.dumps({"repo": a.repo, "workflow": "radar-diario.yml"})
    html = html.replace('<script src="/static/app.js"></script>', f'<script>window.RADAR_STATIC={flag};</script><script src="static/app.js"></script>')
    html = html.replace('href="/static/styles.css"', 'href="static/styles.css"')
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    open(os.path.join(out, ".nojekyll"), "w").close()
    d = os.path.join(out, "data")
    status = c.get("/api/status").get_json()
    status["estatico"] = True
    status["generado"] = db.iso()
    write(os.path.join(d, "status.json"), status)
    write(os.path.join(d, "report_latest.json"), c.get("/api/report/latest").get_json())
    reps = c.get("/api/reports").get_json()
    write(os.path.join(d, "reports.json"), reps)
    for r in reps["reportes"]:
        write(os.path.join(d, "reports", f"{r['id']}.json"), c.get(f"/api/reports/{r['id']}").get_json())
    write(os.path.join(d, "runs.json"), c.get("/api/runs").get_json())
    write(os.path.join(d, "coverage.json"), c.get("/api/coverage").get_json())
    write(os.path.join(d, "settings.json"), c.get("/api/settings").get_json())
    rep = web._report_row(mode=config.effective_mode())
    items = []
    if rep:
        ms = web._metrics(rep["id"])
        for m in ms.values():
            row = {k: m.get(k) for k in LIST_FIELDS}
            row["ret_total"], row["ret_mxn"] = m["ret_total"], m["ret_mxn"]
            row["score"] = (m.get("score") or {}).get("total")
            row["grupo"] = m.get("group")
            row["excluido"] = bool(m.get("exclusions"))
            items.append(row)
        for iid in ms:
            resp = c.get(f"/api/instruments/{iid}")
            det = resp.get_json() if resp.status_code == 200 else None
            if not det:
                print(f"Aviso: no se pudo exportar el instrumento {iid} (HTTP {resp.status_code})")
                continue
            s = det["serie"]
            det["serie"] = {"fechas": s["fechas"], "precio": [r6(x) for x in s["precio"]], "ajustado": [r6(x) for x in s["ajustado"]], "fuente": s["fuente"]}
            write(os.path.join(d, "inst", f"{iid}.json"), det)
    write(os.path.join(d, "instruments.json"), {"items": items, "reporte": {"id": rep["id"], "fecha": rep["report_date"], "modo": rep["mode"]} if rep else None})
    print(f"Sitio exportado en {out}: {len(items)} instrumentos, {len(reps['reportes'])} reportes.")


if __name__ == "__main__":
    main()
