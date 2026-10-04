"""Programador interno: revisa cada 30 s si ya toca la ejecución diaria (hora editable, zona America/Matamoros).
Es seguro con varios procesos: la ejecución usa un candado en la base de datos y una clave por día.
Si el servidor estuvo apagado a la hora programada, ejecuta en cuanto vuelve (recuperación del mismo día).
Como respaldo, un cron externo (GitHub Actions) puede llamar POST /api/cron/tick cada hora."""
import threading
import time

from . import db, pipeline

_started = False


def _loop():
    while True:
        try:
            due, _why, _next = pipeline.due_status()
            if due:
                pipeline.run("programada")
        except Exception as e:  # nunca detener el programador
            print("[scheduler] error:", e, flush=True)
        finally:
            db.close()
        time.sleep(30)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="radar-scheduler", daemon=True).start()
