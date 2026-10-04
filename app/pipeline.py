"""Proceso diario completo. Lo usan por igual: la tarea programada, el cron externo, el botón 'Actualizar ahora' y la línea de comandos.
Pasos: obtener datos -> calcular indicadores -> rankings -> comparar con reporte anterior -> guardar informe -> notificar."""
import os
import socket
import traceback
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import analysis, db, ingest, notify, report, scoring
from .config import config

TZ = ZoneInfo(config.TIMEZONE)
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
AUTO_TRIGGERS = ("programada", "cron_externo")
LOCK_TTL = 900  # 15 min, se renueva durante la ejecución


def settings():
    return {
        "horario": db.get_setting("horario", config.DEFAULT_SCHEDULE),
        "presupuesto_mxn": db.get_setting("presupuesto_mxn", None),
        "moneda": db.get_setting("moneda", "MXN"),
        "horizonte": db.get_setting("horizonte", "medio"),
        "notificaciones": db.get_setting("notificaciones", False),
        "solo_verificados": db.get_setting("solo_verificados", False),
    }


def local_today(now=None):
    return (now or db.now_utc()).astimezone(TZ).date()


def run(trigger="manual", now=None, mode=None):
    now = now or db.now_utc()
    mode = mode or config.effective_mode()
    run_key = f"{local_today(now).isoformat()}:{mode}"
    if not db.acquire_lock("pipeline", OWNER, ttl_seconds=LOCK_TTL):
        return {"status": "omitida", "motivo": "Ya hay una actualización en curso."}
    run_id = None
    try:
        attempt = db.q1("SELECT COUNT(*) n FROM runs WHERE run_key=? AND trigger IN ('programada','cron_externo')", (run_key,))["n"] + 1
        cur = db.ex("INSERT INTO runs(run_key,trigger,status,mode,attempt,started_at) VALUES(?,?,?,?,?,?)",
                    (run_key, trigger, "en_curso", mode, attempt, db.iso()))
        run_id = cur.lastrowid
        log = ingest.Log(run_id, heartbeat=lambda: db.acquire_lock("pipeline", OWNER, LOCK_TTL))
        log(f"Inicio ({trigger}, modo {mode}, intento {attempt}).")
        st = settings()
        if mode == "demo":
            ingest.ingest_demo(log, now)
        else:
            ingest.ingest_real(log, now)
        metrics = analysis.compute_all(mode, now)
        if not metrics:
            raise RuntimeError("No hay instrumentos con datos suficientes para analizar.")
        log(f"Indicadores calculados para {len(metrics)} instrumentos.")
        db.exmany("UPDATE instruments SET liq_mxn=? WHERE id=?", [(m["liq_avg_value_mxn"], m["id"]) for m in metrics])
        groups = scoring.score_groups(metrics, st["solo_verificados"])
        if mode == "real" and config.PRICE_PROVIDER != "yahoo":
            hl = [m["id"] for g in groups.values() for m in g["ranking"][:3]]
            ingest.enrich_highlights(log, hl)
        favs = [r["instrument_id"] for r in db.q("SELECT instrument_id FROM favorites")]
        rid, created = report.build(mode, metrics, groups, now, run_id, log, st, favs, log.warnings[-20:])
        summary = db.q1("SELECT summary FROM reports WHERE id=?", (rid,))["summary"]
        import json
        cutoff = json.dumps(json.loads(summary)["data_cutoff"])
        db.ex("UPDATE runs SET status='exito', finished_at=?, report_id=?, data_cutoff=? WHERE id=?", (db.iso(), rid, cutoff, run_id))
        log("Ejecución terminada con éxito.")
        if created and st["notificaciones"]:
            notify.report_ready(json.loads(summary), mode)
        return {"status": "exito", "run_id": run_id, "report_id": rid, "nuevo_reporte": created}
    except Exception as e:  # se conserva el último reporte válido
        msg = f"{type(e).__name__}: {e}"
        if run_id:
            db.ex("INSERT INTO run_events(run_id,ts,level,message) VALUES(?,?,?,?)", (run_id, db.iso(), "error", msg + "\n" + traceback.format_exc()[-3000:]))
            db.ex("UPDATE runs SET status='fallo', finished_at=?, error=? WHERE id=?", (db.iso(), msg[:1000], run_id))
        print("[error]", msg, flush=True)
        return {"status": "fallo", "run_id": run_id, "error": msg}
    finally:
        db.release_lock("pipeline", OWNER)


# ------------------------------------------------------------- programación (zona America/Matamoros)

def scheduled_at(day, hhmm):
    """Fecha-hora local de la ejecución de 'day'. Maneja cambios de horario:
    si la hora no existe (salto de primavera) se recorre a la primera hora válida; si existe dos veces, se usa la primera."""
    h, m = (int(x) for x in hhmm.split(":"))
    naive = datetime(day.year, day.month, day.day, h, m)
    local = naive.replace(tzinfo=TZ, fold=0)
    roundtrip = local.astimezone(ZoneInfo("UTC")).astimezone(TZ)
    if roundtrip.replace(tzinfo=None) != naive:
        local = roundtrip  # hora inexistente: se recorre hacia adelante
    return local


def due_status(now=None):
    """¿Toca ejecutar? Devuelve (debe_ejecutar, motivo, próxima_ejecución)."""
    now = now or db.now_utc()
    mode = config.effective_mode()
    hhmm = db.get_setting("horario", config.DEFAULT_SCHEDULE)
    today = local_today(now)
    sched = scheduled_at(today, hhmm)
    key = f"{today.isoformat()}:{mode}"
    runs = db.q("SELECT status, finished_at, started_at FROM runs WHERE run_key=? AND trigger IN ('programada','cron_externo') ORDER BY id", (key,))
    ok = any(r["status"] == "exito" for r in runs)
    running = any(r["status"] == "en_curso" for r in runs)
    tomorrow = scheduled_at(today + timedelta(days=1), hhmm)
    if now < sched:
        return False, "Aún no es la hora programada.", sched
    if ok:
        return False, "La ejecución de hoy ya terminó con éxito.", tomorrow
    if running:
        return False, "Hay una ejecución en curso.", tomorrow
    fails = [r for r in runs if r["status"] == "fallo"]
    if len(fails) >= config.RUN_MAX_ATTEMPTS:
        return False, f"Se agotaron los {config.RUN_MAX_ATTEMPTS} intentos de hoy; se conserva el último reporte válido.", tomorrow
    if fails:
        retry_at = datetime.fromisoformat(fails[-1]["finished_at"]) + timedelta(minutes=config.RUN_RETRY_MINUTES * len(fails))
        if now < retry_at:
            return False, "Reintento programado tras un fallo.", retry_at.astimezone(TZ)
        return True, "Reintento tras fallo.", now.astimezone(TZ)
    return True, "Hora programada alcanzada.", now.astimezone(TZ)


def recover_orphans():
    """Ejecuciones que quedaron 'en curso' por un reinicio: se marcan como fallidas (cuentan como intento)."""
    lk = db.q1("SELECT expires_at FROM locks WHERE name='pipeline'")
    if lk and lk["expires_at"] > db.iso():
        return 0
    cur = db.ex("UPDATE runs SET status='fallo', finished_at=?, error='Interrumpida por reinicio del servicio' WHERE status='en_curso'", (db.iso(),))
    return cur.rowcount


def tick(trigger="programada"):
    due, why, _ = due_status()
    if due:
        return run(trigger)
    return {"status": "sin_cambios", "motivo": why}


def manual_cooldown_left(now=None):
    now = now or db.now_utc()
    r = db.q1("SELECT started_at FROM runs WHERE trigger='manual' ORDER BY id DESC LIMIT 1")
    if not r:
        return 0
    left = datetime.fromisoformat(r["started_at"]) + timedelta(minutes=config.MANUAL_COOLDOWN_MINUTES) - now
    return max(0, int(left.total_seconds()))
