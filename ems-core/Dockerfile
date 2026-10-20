# ems-core – alle Eigenentwicklungen des EMS in einem Image (Rolle per Startbefehl)
# Build-Kontext ist das Projektverzeichnis (Mapping + Tests gehören zum Build).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY ems-core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest==8.3.4

COPY config/mapping /app/mapping
COPY ems-core/ems /app/ems
COPY ems-core/tests /app/tests
# Build bricht ab, wenn die Tests mit den aufgezeichneten Daten fehlschlagen
RUN python -m pytest -q /app/tests && rm -rf /app/tests /root/.cache

USER 1000:1000
ENTRYPOINT ["python", "-m", "ems"]
