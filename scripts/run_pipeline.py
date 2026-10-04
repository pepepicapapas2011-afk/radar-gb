"""Ejecuta el proceso diario desde la línea de comandos (lo usa GitHub Actions).
Uso: python scripts/run_pipeline.py [--mode demo|real] [--only-if-due] [--settings ajustes.json] [--trigger manual|cli]"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db, pipeline  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--mode", choices=["demo", "real"])
p.add_argument("--only-if-due", action="store_true", help="solo ejecuta si ya toca según el horario")
p.add_argument("--settings", help="archivo JSON con horario, solo_verificados, notificaciones")
p.add_argument("--trigger", default="cli")
a = p.parse_args()
db.init_db()
pipeline.recover_orphans()
if a.settings and os.path.exists(a.settings):
    with open(a.settings, encoding="utf-8") as f:
        s = json.load(f)
    for k in ("horario", "solo_verificados", "notificaciones", "horizonte"):
        if k in s:
            db.set_setting(k, s[k])
if a.only_if_due:
    res = pipeline.tick("cron_externo")
elif a.trigger == "manual" and pipeline.manual_cooldown_left() > 0:
    res = {"status": "omitida", "motivo": "Se pidió otra actualización hace muy poco (límite del proveedor)."}
else:
    res = pipeline.run(a.trigger, mode=a.mode)
print(json.dumps(res, ensure_ascii=False))
sys.exit(0 if res.get("status") in ("exito", "sin_cambios", "omitida") else 1)
