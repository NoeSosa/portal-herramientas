# =============================================================================
# Backend — FastAPI
# =============================================================================
# Multi-stage: las dependencias se compilan en `constructor`, la imagen final
# solo lleva lo necesario para ejecutar. Sin compiladores en producción.
#
# ⚠️ BASE: debian:bookworm-slim con python3 del SISTEMA (no python:3.11-slim).
#
# Motivo: el portal monta el venv de OpenClaw (`/openclaw/workspace/venv`), y
# los venv de Python llevan rutas absolutas horneadas. Ese venv espera:
#     home = /usr/bin
#     executable = /usr/bin/python3.11
# Las imágenes oficiales `python:*` instalan el intérprete en /usr/local/bin,
# así que los shebangs del venv (`#!/home/node/.openclaw/workspace/venv/bin/python`
# → `/usr/bin/python3`) fallan con "not found".
# Con debian + apt python3, /usr/bin/python3 existe y todo resuelve.
# =============================================================================

# --- Etapa 1: dependencias ------------------------------------------------
FROM debian:bookworm-slim AS constructor

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 \
      python3-venv \
      python3-pip \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .

RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# --- Etapa 2: runtime -----------------------------------------------------
FROM debian:bookworm-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:$PATH"

# Python del sistema: lo necesita el venv montado de OpenClaw para resolver
# sus shebangs. Además, `piper` viene como dependencia suya.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios. Nunca correr como root.
# uid/gid 1000: es el dueño del volumen de OpenClaw (drwx------ de 1000).
# Con otro uid el contenedor no puede leer /home/node/.openclaw.
RUN groupadd --gid 1000 portal \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin portal

COPY --from=constructor /opt/venv /opt/venv

WORKDIR /app

COPY --chown=portal:portal app/ ./app/

# Directorios de datos. El volumen del compose se monta encima.
RUN mkdir -p /data/uploads /data/outputs \
 && chown -R portal:portal /data

USER portal

EXPOSE 8000

# Sin `--reload`: es producción.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--no-server-header"]
