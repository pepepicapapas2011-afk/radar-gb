"""Punto de entrada del servidor web: gunicorn wsgi:app  (o python wsgi.py en desarrollo)."""
import os

from app.web import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
