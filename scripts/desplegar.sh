#!/usr/bin/env bash
# =============================================================================
# Despliegue del Portal de Herramientas desde el contenedor de OpenClaw
# =============================================================================
# Reconstruye las imágenes y recrea los contenedores SIN `docker compose`
# (no está disponible en este CLI) replicando exactamente el docker-compose.yml.
#
# ⚠️ El docker-compose.yml es la fuente de verdad. Si cambia un puerto, un
#    volumen o una variable, hay que actualizar este script también.
#
# Uso:
#   bash scripts/desplegar.sh              # construye todo y recrea
#   bash scripts/desplegar.sh --solo-backend
#   bash scripts/desplegar.sh --sin-ocr    # omite la imagen pesada (2-5 min)
#   bash scripts/desplegar.sh --estado     # solo muestra el estado actual
# =============================================================================
set -euo pipefail

export DOCKER_HOST="${DOCKER_HOST:-tcp://dockerproxy:2375}"
export PATH="/home/node/.local/bin:$PATH"

DIRECTORIO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIRECTORIO"

RED="herramientas-web_portal"
VOL_DATOS="herramientas-web_datos"
VOL_OPENCLAW="agente-openclaw-sh6xwb_openclaw_data"
OPENCLAW_MONTADO="/home/node/.openclaw"

# Rutas de Python por contenedor. Ver README («Detalles del entorno que no son
# obvios»): el backend ejecuta scripts del workspace con el venv; el worker OCR
# ejecuta su propio código con el python del sistema.
PATH_CON_VENV="/home/node/.openclaw/workspace/venv/bin:/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
PATH_SIN_VENV="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"

SOLO_BACKEND=0
SIN_OCR=0
ESTADO=0

for arg in "$@"; do
  case "$arg" in
    --solo-backend) SOLO_BACKEND=1 ;;
    --sin-ocr)      SIN_OCR=1 ;;
    --estado)       ESTADO=1 ;;
    -h|--help)      sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '\n\033[1;33m==>\033[0m %s\n' "$*"; }
error() { printf '\n\033[1;31m!!\033[0m %s\n' "$*" >&2; }

if [[ $ESTADO -eq 1 ]]; then
  docker ps --filter "name=hw-" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
  exit 0
fi

# --- Comprobaciones previas -------------------------------------------------
if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  error "No hay conexión con el daemon de Docker en $DOCKER_HOST."
  exit 1
fi

if [[ ! -f .env ]]; then
  error "Falta .env en $DIRECTORIO. Copia .env.example y rellénalo."
  exit 1
fi

# --- Build ------------------------------------------------------------------
log "Construyendo backend (FastAPI + worker ligero)"
docker build -t herramientas-web-backend:local ./backend

log "Construyendo frontend (React + nginx)"
docker build -t herramientas-web-frontend:local ./frontend

if [[ $SOLO_BACKEND -eq 0 && $SIN_OCR -eq 0 ]]; then
  log "Construyendo worker OCR (PaddleOCR — tarda varios minutos)"
  docker build -t herramientas-web-ocr:local -f backend/Dockerfile.ocr ./backend
elif [[ $SIN_OCR -eq 1 ]]; then
  log "OCR omitido (--sin-ocr): se reutiliza la imagen existente"
fi

# --- Recrear contenedores ---------------------------------------------------
recrear() {
  local nombre="$1"
  if docker ps -a --format '{{.Names}}' | grep -qx "$nombre"; then
    log "Deteniendo y eliminando $nombre"
    docker stop "$nombre" >/dev/null 2>&1 || true
    docker rm "$nombre" >/dev/null 2>&1 || true
  fi
}

# ⚠️ `docker compose` crea alias DNS por NOMBRE DE SERVICIO (`backend`, `redis`,
# `worker-ocr`). Con `docker run` el contenedor solo responde a su `--name`
# (`hw-backend`), y nginx falla con 502 "backend could not be resolved".
# Aquí se recrean esos alias a mano.
conectar_red() {
  local contenedor="$1"; shift
  local alias_args=()
  for alias in "$@"; do alias_args+=(--alias "$alias"); done

  docker network disconnect "$RED" "$contenedor" >/dev/null 2>&1 || true
  docker network connect "${alias_args[@]}" "$RED" "$contenedor"
}

log "Recreando backend"
recrear hw-backend
# ⚠️ El backend es el ÚNICO con el workspace en ESCRITURA: el panel de finanzas
# edita el ledger real (el agente debe ver los cambios al instante). Los demás
# servicios lo montan en `:ro` porque no necesitan escribir.
docker run -d --name hw-backend --restart unless-stopped --network "$RED" \
  --env-file .env \
  -e REDIS_URL=redis://redis:6379/0 \
  -e PATH="$PATH_CON_VENV" \
  -v "${VOL_DATOS}:/data" \
  -v "${VOL_OPENCLAW}:${OPENCLAW_MONTADO}:rw" \
  --security-opt no-new-privileges:true --cap-drop ALL \
  herramientas-web-backend:local >/dev/null

# Alias DNS que espera nginx y el resto del stack (ver `conectar_red`).
conectar_red hw-backend backend

log "Recreando worker ligero (TTS, visión, canción)"
recrear hw-worker
docker run -d --name hw-worker --restart unless-stopped --network "$RED" \
  --env-file .env \
  -e REDIS_URL=redis://redis:6379/0 -e COLAS=rapida \
  -e PATH="$PATH_CON_VENV" \
  -v "${VOL_DATOS}:/data" \
  -v "${VOL_OPENCLAW}:${OPENCLAW_MONTADO}:ro" \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --cpus 1.0 --memory 1g \
  herramientas-web-backend:local python -m app.worker >/dev/null

conectar_red hw-worker worker

if [[ $SOLO_BACKEND -eq 0 && $SIN_OCR -eq 0 ]]; then
  log "Recreando worker OCR (separador + transcripción)"
  recrear hw-worker-ocr
  docker run -d --name hw-worker-ocr --restart unless-stopped --network "$RED" \
    --env-file .env \
    -e REDIS_URL=redis://redis:6379/0 -e COLAS=pesada -e LD_LIBRARY_PATH=/opt/lib \
    -e PATH="$PATH_SIN_VENV" \
    -v "${VOL_DATOS}:/data" \
    -v "${VOL_OPENCLAW}:${OPENCLAW_MONTADO}:ro" \
    --security-opt no-new-privileges:true --cap-drop ALL \
    --cpus 2.0 --memory 4g \
    herramientas-web-ocr:local python3 -m app.worker >/dev/null

  conectar_red hw-worker-ocr worker-ocr
fi

log "Recreando panel del separador (modo local, solo red interna)"
recrear hw-panel
docker run -d --name hw-panel --restart unless-stopped --network "$RED" \
  -e PANEL_CONFIG=/home/node/.openclaw/workspace/separador_pdfs/panel/config.docker.json \
  -e PANEL_PY_PANEL=/usr/bin/python3 \
  -e PANEL_PY_SEPARADOR=/home/node/.openclaw/workspace/venv-pdfseparador/bin/python \
  -e PANEL_LD_LIBRARY_PATH=/opt/lib -e PYTHONUNBUFFERED=1 \
  -e HOME=/data -e SEPARADOR_WORKERS=2 \
  -e PATH="$PATH_SIN_VENV" \
  -v "${VOL_DATOS}:/data" \
  -v "${VOL_OPENCLAW}:${OPENCLAW_MONTADO}:rw" \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --cpus 2.0 --memory 4g \
  herramientas-web-ocr:local sh -c "\
    python3 /home/node/.openclaw/workspace/separador_pdfs/panel/warmup_ocr.py || true; \
    exec python3 /home/node/.openclaw/workspace/separador_pdfs/panel/panel_server.py" >/dev/null

# Alias DNS que usa nginx para servir el panel en /panel/ (ver nginx.conf).
conectar_red hw-panel panel

log "Recreando frontend (nginx)"
recrear hw-frontend
docker run -d --name hw-frontend --restart unless-stopped --network "$RED" \
  -p 127.0.0.1:8080:80 \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --cap-add NET_BIND_SERVICE --cap-add CHOWN --cap-add SETGID --cap-add SETUID \
  --read-only --tmpfs /var/cache/nginx --tmpfs /var/run --tmpfs /tmp \
  herramientas-web-frontend:local >/dev/null

# --- Verificación -----------------------------------------------------------
log "Esperando a que el backend responda"
for intento in $(seq 1 20); do
  if docker exec hw-backend python -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)
" >/dev/null 2>&1; then
    echo "    backend OK"
    break
  fi
  sleep 3
  if [[ $intento -eq 20 ]]; then
    error "El backend no respondió en 60 s. Revisa: docker logs hw-backend"
    exit 1
  fi
done

log "Comprobando el portal a través de nginx"
if docker exec hw-frontend wget -qO- http://127.0.0.1/api/health >/dev/null 2>&1; then
  echo "    nginx → backend OK"
else
  error "nginx no alcanza al backend (/api/health falla). Revisa los alias de red."
  exit 1
fi

log "Estado final"
docker ps --filter "name=hw-" --format 'table {{.Names}}\t{{.Status}}'

log "Comprobando el panel del separador a través de nginx"
if docker exec hw-frontend wget -qO- http://127.0.0.1/panel/api/health >/dev/null 2>&1; then
  echo "    nginx → panel OK  (http://127.0.0.1:8080/panel/)"
else
  error "nginx no alcanza al panel (/panel/api/health falla). Revisa el alias 'panel'."
fi

cat <<'FIN'

Portal desplegado. Para usarlo desde tu máquina:

    ssh -L 8080:127.0.0.1:8080 -i tu-llave.key ubuntu@<servidor>
    → http://localhost:8080

Comprobaciones útiles:
    docker logs -f hw-worker-ocr      # OCR y transcripción
    docker logs -f hw-backend         # peticiones HTTP
FIN
