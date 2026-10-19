#!/usr/bin/env bash
# =============================================================================
# Despliegue automatico del Portal de Herramientas desde GitHub
# =============================================================================
# Trae la rama principal del repositorio (sin credenciales: tarball publico),
# aplica los cambios al workspace y recrea SOLO los contenedores afectados.
#
# Lo usa el automation de OpenClaw (`desplegar-portal`, cada 2 minutos). Tambien
# se puede correr a mano:
#
#   bash scripts/sincronizar.sh              # trae y despliega lo que cambie
#   bash scripts/sincronizar.sh --revisar    # solo informa, no toca nada
#   bash scripts/sincronizar.sh --forzar     # despliega aunque no haya cambios
#   bash scripts/sincronizar.sh --simular-despliegue   # prueba desplegar.sh
#
# ✅ VALIDADO: 18/oct/2026 - deteccion por contenido + despliegue completo
# 📘 EJEMPLO: bash scripts/sincronizar.sh --revisar
#
# REGLA DE ORO: este script NO se reemplaza a si mismo. `scripts/` se sincroniza
# conservando sincronizar.sh y desplegar.sh (una version vieja del repo borraria
# el archivo en pleno despliegue).
#
# Que reemplaza y que conserva:
#   - frontend/     -> se reemplaza (node_modules, dist y .vite se conservan).
#   - scripts/      -> se reemplaza SALVO sincronizar.sh y desplegar.sh.
#   - docs/         -> se reemplaza.
#   - separador/    -> se reemplaza (motor horneado en la imagen OCR).
#   - backend/      -> se actualiza por contenido. Nunca se borra un archivo
#                      que solo exista en el servidor.
#   - separador_pdfs/ -> se reemplaza el CODIGO; los datos nunca se tocan:
#     entrada_local/, inventarios_local/, auto/, panel/panel.db,
#     panel/config*.json, panel/logs/, panel/tmp/.
#
# La comparacion es por CONTENIDO (las fechas del tarball no cuentan), asi que no
# hay despliegues fantasma cada 2 minutos.
#
# Salida: una sola linea cuando hay algo que contar. Detalles en
# .despliegue/sincronizar.log
# =============================================================================
set -uo pipefail

REPO="NoeSosa/portal-herramientas"
RAMA="main"
WORKSPACE="${WORKSPACE:-/home/node/.openclaw/workspace}"
PORTAL="$WORKSPACE/herramientas-web"
TALLER="$WORKSPACE/separador_pdfs"
ESTADO="$PORTAL/.despliegue"
REGISTRO="$ESTADO/sincronizar.log"
ULTIMO_SHA="$ESTADO/ultimo-sha"
BLOQUEO="$ESTADO/bloqueo"

REVISAR=0
FORZAR=0
SIMULAR=0
for arg in "$@"; do
  case "$arg" in
    --revisar) REVISAR=1 ;;
    --forzar)  FORZAR=1 ;;
    --simular-despliegue) SIMULAR=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "Opcion desconocida: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$ESTADO"
export PATH="/home/node/.local/bin:$PATH"
export DOCKER_HOST="${DOCKER_HOST:-tcp://dockerproxy:2375}"

registrar() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$REGISTRO"
  if [[ $(wc -l < "$REGISTRO" 2>/dev/null || echo 0) -gt 2000 ]]; then
    tail -n 1000 "$REGISTRO" > "$REGISTRO.tmp" && mv "$REGISTRO.tmp" "$REGISTRO"
  fi
}

# --- Prueba de despliegue: corre el script de siempre, sin traer nada --------
if [[ $SIMULAR -eq 1 ]]; then
  registrar "SIMULACION: probando scripts/desplegar.sh --sin-ocr"
  if ( cd "$PORTAL" && bash scripts/desplegar.sh --sin-ocr >>"$REGISTRO" 2>&1 ); then
    registrar "SIMULACION OK"; echo "simulacion OK"
  else
    registrar "SIMULACION FALLO"; echo "simulacion FALLO (ver $REGISTRO)"
  fi
  exit 0
fi

# --- Un solo despliegue a la vez -------------------------------------------
if ! mkdir "$BLOQUEO" 2>/dev/null; then
  if [[ -d "$BLOQUEO" ]] && [[ $(( $(date +%s) - $(stat -c %Y "$BLOQUEO" 2>/dev/null || echo 0) )) -lt 1800 ]]; then
    registrar "ya hay un despliegue en curso; se omite este ciclo"
    exit 0
  fi
  rm -rf "$BLOQUEO"; mkdir "$BLOQUEO" 2>/dev/null || exit 0
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$BLOQUEO" "$TMP"' EXIT

# --- 1. Que commit hay en GitHub -------------------------------------------
SHA_REMOTO="$(curl -s -m 25 "https://api.github.com/repos/$REPO/commits/$RAMA" \
  | sed -n 's/.*"sha": "\([0-9a-f]\{40\}\)".*/\1/p' | head -1)"

if [[ -z "$SHA_REMOTO" ]]; then
  registrar "no se pudo consultar GitHub (sin red o sin cupo de API); se reintenta luego"
  exit 0
fi

SHA_LOCAL="$(cat "$ULTIMO_SHA" 2>/dev/null || echo "")"
if [[ "$SHA_REMOTO" == "$SHA_LOCAL" && $FORZAR -eq 0 ]]; then
  [[ $REVISAR -eq 1 ]] && echo "sin cambios ($SHA_REMOTO)"
  exit 0
fi

MENSAJE="$(curl -s -m 25 "https://api.github.com/repos/$REPO/commits/$SHA_REMOTO" \
  | sed -n 's/.*"message": "\([^"]\{1,90\}\).*/\1/p' | head -1)"

if [[ $REVISAR -eq 1 ]]; then
  echo "hay cambios: local=${SHA_LOCAL:0:7} remoto=${SHA_REMOTO:0:7} — $MENSAJE"
  exit 0
fi

registrar "nuevo commit ${SHA_REMOTO:0:7} — $MENSAJE"

# --- 2. Traer el arbol -----------------------------------------------------
URL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$RAMA"
if ! curl -sL -m 180 -o "$TMP/repo.tar.gz" "$URL"; then
  registrar "ERROR descargando $URL"; exit 1
fi
if ! tar -xzf "$TMP/repo.tar.gz" -C "$TMP" 2>/dev/null; then
  registrar "ERROR descomprimiendo el tarball"; exit 1
fi
NUEVO="$(find "$TMP" -maxdepth 1 -type d -name 'portal-herramientas-*' | head -1)"
[[ -d "$NUEVO" ]] || { registrar "ERROR: tarball sin carpeta raiz"; exit 1; }

# --- 3. Comparar por contenido --------------------------------------------
iguales() {  # $1 origen, $2 destino, resto: nombres a excluir. 0 = iguales
  local origen="$1" destino="$2"; shift 2
  local args=()
  for e in "$@"; do args+=(--exclude "$e"); done
  [[ -z "$(tar -C "$origen" "${args[@]}" -cf - . 2>/dev/null | tar -C "$destino" -df - 2>&1 \
           | grep -v 'Mod time differs')" ]]
}

# Copia al backend solo los archivos cuyo CONTENIDO cambió. No borra nada: un
# archivo que exista solo en el servidor se conserva. La versión anterior solo
# agregaba archivos nuevos, así que un cambio en main.py o en un router se
# detectaba (y se reconstruía la imagen) pero el código nunca se copiaba.
sincronizar_backend() {  # $1 origen, $2 destino. Imprime cuántos copió.
  local origen="$1" destino="$2"
  local copiados=0 archivo relativo objetivo

  while IFS= read -r -d '' archivo; do
    relativo="${archivo#"$origen"/}"
    objetivo="$destino/$relativo"

    # Artefactos de Python y el motor horneado (lo genera
    # preparar_separador.sh) no vienen del repositorio.
    case "$relativo" in
      __pycache__/*|*/__pycache__/*|*.pyc|*.pyo|separador/*) continue ;;
    esac

    if [[ -f "$objetivo" ]] && cmp -s "$archivo" "$objetivo"; then
      continue
    fi

    mkdir -p "$(dirname "$objetivo")"
    cp -a "$archivo" "$objetivo"
    copiados=$((copiados + 1))
  done < <(find "$origen" -type f -not -path '*/__pycache__/*' -print0)

  printf '%s' "$copiados"
}

CAMBIOS=()
iguales "$NUEVO/frontend" "$PORTAL/frontend" node_modules dist .vite \
  || CAMBIOS+=("frontend")
iguales "$NUEVO/scripts" "$PORTAL/scripts" sincronizar.sh desplegar.sh \
  || CAMBIOS+=("scripts")
iguales "$NUEVO/docs" "$PORTAL/docs" || CAMBIOS+=("docs")
iguales "$NUEVO/separador" "$PORTAL/separador" || CAMBIOS+=("separador")

# backend: se aplica por contenido, incluyendo cambios a archivos existentes.
# El conteo se registra para que el log diga cuántos archivos se tocaron.
BACKEND_COPIADOS="$(sincronizar_backend "$NUEVO/backend" "$PORTAL/backend")"
if [[ "$BACKEND_COPIADOS" -gt 0 ]]; then
  registrar "backend: $BACKEND_COPIADOS archivo(s) actualizado(s)"
  CAMBIOS+=("backend")
fi

# separador_pdfs: codigo si, datos no
DATOS_TALLER=(entrada_local inventarios_local auto panel/panel.db panel/panel.db-wal
              panel/panel.db-shm panel/config.json panel/config.docker.json
              panel/logs panel/tmp __pycache__ .git)
iguales "$NUEVO/separador_pdfs" "$TALLER" "${DATOS_TALLER[@]}" \
  || CAMBIOS+=("panel_separador")

# Prueba de vida: si dice "sin cambios" pero falta algo que SI esta en el repo,
# la deteccion fallo y se aplica de forma forzada.
if [[ ${#CAMBIOS[@]} -eq 0 && $FORZAR -eq 0 ]]; then
  for _f in README.md docker-compose.yml .env.example backend/requirements.txt; do
    if [[ -f "$NUEVO/$_f" && ! -f "$PORTAL/$_f" ]]; then
      registrar "deteccion dudosa: falta $_f; se aplica de forma forzada"
      CAMBIOS+=("forzado"); break
    fi
  done
fi

if [[ ${#CAMBIOS[@]} -eq 0 ]]; then
  echo "$SHA_REMOTO" > "$ULTIMO_SHA"
  registrar "commit ${SHA_REMOTO:0:7} sin cambios aplicables"
  exit 0
fi
registrar "aplicando: ${CAMBIOS[*]}"

# --- 4. Aplicar ------------------------------------------------------------
copiar() {  # $1 origen, $2 destino, resto: nombres a excluir (replica el arbol)
  local origen="$1" destino="$2"; shift 2
  local args=()
  for e in "$@"; do args+=(--exclude "$e"); done
  mkdir -p "$destino"
  # Se borra el contenido (respetando exclusiones) y se copia el nuevo arbol.
  for hijo in "$destino"/*; do
    [[ -e "$hijo" ]] || continue
    local nombre; nombre="$(basename "$hijo")"
    local saltar=0
    for e in "$@"; do [[ "$nombre" == "$e" ]] && saltar=1; done
    [[ $saltar -eq 0 ]] && rm -rf "$hijo"
  done
  tar -C "$origen" "${args[@]}" -cf - . 2>/dev/null | tar -C "$destino" -xf - 2>/dev/null || true
}

[[ " ${CAMBIOS[*]} " == *" frontend "* ]] && copiar "$NUEVO/frontend" "$PORTAL/frontend" node_modules dist .vite
[[ " ${CAMBIOS[*]} " == *" scripts "*  ]] && copiar "$NUEVO/scripts"  "$PORTAL/scripts" sincronizar.sh desplegar.sh
[[ " ${CAMBIOS[*]} " == *" docs "*     ]] && copiar "$NUEVO/docs"     "$PORTAL/docs"
[[ " ${CAMBIOS[*]} " == *" separador "* ]] && copiar "$NUEVO/separador" "$PORTAL/separador"

if [[ " ${CAMBIOS[*]} " == *" panel_separador "* ]]; then
  excl_datos=()
  for e in "${DATOS_TALLER[@]}"; do excl_datos+=(--exclude "$e"); done
  # Sin borrado: solo se sobrescribe el codigo; los datos nunca se tocan.
  tar -C "$NUEVO/separador_pdfs" "${excl_datos[@]}" -cf - . 2>/dev/null | tar -C "$TALLER" -xf - || true
fi

# --- 5. Desplegar ----------------------------------------------------------
if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  registrar "ERROR: sin conexion a Docker; archivos aplicados, contenedores pendientes"
  echo "aplicado ${SHA_REMOTO:0:7} (sin Docker, pendiente desplegar)"
  exit 1
fi

REBUILD_OCR=0
if printf '%s\n' "${CAMBIOS[@]}" | grep -qE 'separador|backend'; then REBUILD_OCR=1; fi

resultado=""
if [[ $REBUILD_OCR -eq 1 ]]; then
  registrar "preparando motor del separador y reconstruyendo imagen OCR (tarda varios minutos)"
  if ( cd "$PORTAL" \
       && bash scripts/preparar_separador.sh \
       && docker build -t herramientas-web-ocr:local -f backend/Dockerfile.ocr ./backend ) \
       >>"$REGISTRO" 2>&1; then
    resultado="imagen OCR + "
  else
    registrar "ERROR preparando el separador o construyendo la imagen OCR"
  fi
fi

if printf '%s\n' "${CAMBIOS[@]}" | grep -qE 'frontend|panel_separador'; then
  registrar "desplegando frontend y panel (scripts/desplegar.sh --sin-ocr)"
  if ( cd "$PORTAL" && bash scripts/desplegar.sh --sin-ocr >>"$REGISTRO" 2>&1 ); then
    resultado+="frontend+panel recreados"
  else
    registrar "ERROR en scripts/desplegar.sh (ver el registro)"
    resultado+="fallo al desplegar"
  fi
elif [[ $REBUILD_OCR -eq 1 ]]; then
  registrar "recreando panel y worker OCR"
  if ( cd "$PORTAL" && bash scripts/desplegar.sh --solo-backend --sin-ocr >>"$REGISTRO" 2>&1 ); then
    resultado+="contenedores recreados"
  else
    resultado+="fallo al recrear"
  fi
fi

echo "$SHA_REMOTO" > "$ULTIMO_SHA"
registrar "listo ${SHA_REMOTO:0:7}: ${resultado:-sin acciones} (${CAMBIOS[*]})"
echo "desplegado ${SHA_REMOTO:0:7} — ${resultado:-sin acciones}"
