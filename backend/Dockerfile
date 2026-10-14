# =============================================================================
# Backend — FastAPI
# =============================================================================
# Multi-stage: las dependencias se compilan en `constructor`, la imagen final
# solo lleva lo necesario para ejecutar. Sin compiladores en producción.
# =============================================================================

# --- Etapa 1: dependencias ------------------------------------------------
FROM python:3.11-slim-bookworm AS constructor

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# --- Etapa 2: runtime -----------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Usuario sin privilegios. Nunca correr como root.
RUN groupadd --gid 1001 portal \
 && useradd --uid 1001 --gid portal --create-home --shell /usr/sbin/nologin portal

COPY --from=constructor /opt/venv /opt/venv

WORKDIR /app

COPY --chown=portal:portal app/ ./app/

# Directorios de datos. El volumen del compose se monta encima.
RUN mkdir -p /data/uploads /data/outputs \
 && chown -R portal:portal /data

USER portal

EXPOSE 8000

# Sin `--reload`: es producción. El límite de subida lo controla la app.
# --forwarded-allow-ips: detrás de nginx, respeta X-Forwarded-*.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--no-server-header"]
