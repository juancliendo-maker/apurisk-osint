# APURISK 1.0 — Imagen Docker para deploy 24/7
FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema (mínimas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python primero (capa cacheable)
COPY requirements-server.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-server.txt

# Copiar el código
COPY apurisk /app/apurisk

# Crear directorio de output (donde van snapshots, dashboard, archivo SQLite)
RUN mkdir -p /app/output

# Variables por defecto (Render/usuarios pueden sobrescribir)
ENV PORT=8080 \
    REFRESH_SECONDS=1800 \
    OUTPUT_DIR=/app/output \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthz || exit 1

# Iniciar el servidor (el scheduler arranca automáticamente en startup)
CMD ["sh", "-c", "uvicorn apurisk.server:app --host 0.0.0.0 --port ${PORT}"]
