#!/usr/bin/env bash
# =============================================================================
# Prepara el motor del separador para la imagen OCR
# =============================================================================
# `backend/Dockerfile.ocr` hace `COPY separador/ /separador/` con contexto
# `./backend`, pero el motor vive en `separador/` (raíz del repositorio) y no se
# versiona dentro del backend. Docker no puede copiar desde fuera del contexto,
# así que este script materializa la copia `backend/separador/` antes del build.
#
# Lo llaman `scripts/desplegar.sh` y `scripts/sincronizar.sh`. También se puede
# ejecutar a mano:
#
#     bash scripts/preparar_separador.sh
#
# La copia está en `.gitignore`: se regenera en cada build y nunca se edita.
# =============================================================================
set -euo pipefail

DIRECTORIO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIGEN="$DIRECTORIO/separador"
DESTINO="$DIRECTORIO/backend/separador"

# Guarda de seguridad: el borrado debe ser exactamente la copia generada.
case "$DESTINO" in
  "$DIRECTORIO"/backend/separador) ;;
  *)
    echo "ERROR: destino inesperado para el motor: $DESTINO" >&2
    exit 1
    ;;
esac

if [[ ! -f "$ORIGEN/separador_pdf.py" ]]; then
  echo "ERROR: no se encontró el motor en $ORIGEN/separador_pdf.py" >&2
  exit 1
fi

# Aviso de deriva: el taller y la copia horneada pueden haber divergido.
if [[ -f "$DIRECTORIO/separador_pdfs/separador_pdf.py" ]] \
   && ! cmp -s "$ORIGEN/separador_pdf.py" "$DIRECTORIO/separador_pdfs/separador_pdf.py"; then
  echo "AVISO: separador/ y separador_pdfs/ difieren; la imagen OCR usará separador/." >&2
fi

rm -rf "$DESTINO"
mkdir -p "$DESTINO"
tar -C "$ORIGEN" --exclude='__pycache__' --exclude='*.pyc' -cf - . \
  | tar -C "$DESTINO" -xf -

echo "Motor del separador preparado en $DESTINO"
