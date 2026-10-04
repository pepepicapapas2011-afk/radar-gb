FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATABASE_PATH=/data/radar.db PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY data/*.csv ./data/
COPY scripts ./scripts
COPY wsgi.py .
RUN mkdir -p /data
EXPOSE 8000
# Un solo proceso (con hilos) para que el programador interno no se duplique; el candado en la BD protege de todos modos.
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 120"]
