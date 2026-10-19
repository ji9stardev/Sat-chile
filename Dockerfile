# SAT-Maule: API FastAPI + PWA en una sola imagen.
# Render y Koyeb inyectan la variable PORT; localmente se usa 8000.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
# Red hidrográfica del Maule (OpenStreetMap) para la semilla. Generar con scripts/import_osm_waterways.py
COPY data ./data

# Usuario sin privilegios.
RUN useradd --create-home --uid 10001 satmaule && chown -R satmaule /app
USER satmaule

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=4)"

# Un solo worker: el monitor de zonas en proceso no debe ejecutarse dos veces en paralelo.
CMD ["sh", "-c", "exec uvicorn src.presentation.api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --workers 1"]
