"""Notificaciones opcionales cuando el reporte está listo (ntfy.sh o webhook genérico).
Además, la página muestra una notificación del navegador si está abierta y el usuario lo permitió."""
import requests

from .config import config


def report_ready(summary, mode):
    tops = []
    for g in summary.get("investigar", {}).values():
        tops += [x["ticker"] for x in g.get("items", [])]
    text = (f"Reporte {summary['report_date']} listo ({'DEMO' if mode == 'demo' else 'datos reales'}). "
            f"Para investigar: {', '.join(tops) or 'sin destacados'}. Alertas: {len(summary.get('alertas', []))}.")
    sent = []
    if config.NOTIFY_NTFY_URL:
        try:
            requests.post(config.NOTIFY_NTFY_URL, data=text.encode("utf-8"),
                          headers={"Title": "Radar GBM", "Click": config.PUBLIC_URL or ""}, timeout=15)
            sent.append("ntfy")
        except requests.RequestException:
            pass
    if config.NOTIFY_WEBHOOK_URL:
        try:
            requests.post(config.NOTIFY_WEBHOOK_URL, json={"text": text, "url": config.PUBLIC_URL}, timeout=15)
            sent.append("webhook")
        except requests.RequestException:
            pass
    return sent
