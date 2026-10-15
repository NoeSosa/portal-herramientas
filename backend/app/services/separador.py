"""Herramienta: Separador de PDFs (OCR de contadores de impresión).

Empaqueta `separador_pdf.py` —el motor topológico multimarca— como un job
asíncrono. El OCR tarda 10-20 s por página, así que un PDF de 100 páginas son
~20 minutos: imposible dentro de una petición HTTP.

Flujo:
    1. El usuario sube un PDF  → se guarda en /data/uploads
    2. Se encola un job        → responde un job_id de inmediato
    3. El worker OCR procesa   → escribe resultados en /data/outputs/<job_id>
    4. El usuario consulta     → ve el progreso y descarga el ZIP

Nota sobre el inventario: el motor puede validar los seriales contra un Excel
de inventario (`--excel`). Si no se proporciona, todos los equipos van a
`Verificar/`. Es el comportamiento esperado, no un fallo.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from app.core.config import obtener_settings

logger = logging.getLogger(__name__)

# Rutas dentro del contenedor OCR.
SEPARADOR_DIR = Path("/separador")
MOTOR = SEPARADOR_DIR / "separador_pdf.py"

# El OCR es intensivo: usar 2 procesos en paralelo (el compose limita a 2 vCPU).
WORKERS_OCR = 2
DPI = 200  # Validado: 200 DPI da el mismo resultado que 300 y es 2× más rápido.


class ErrorSeparador(RuntimeError):
    """El separador no pudo completar el trabajo."""


def ejecutar_separador(
    ruta_pdf: str,
    directorio_trabajo: str,
    *,
    ruta_excel: str | None = None,
    hoja_excel: str | None = None,
) -> dict[str, object]:
    """Procesa un PDF y devuelve un resumen del resultado.

    Args:
        ruta_pdf: ruta al PDF subido.
        directorio_trabajo: dónde dejar la salida (se crea el ZIP aquí).
        ruta_excel: inventario opcional para validar seriales.
        hoja_excel: hoja del inventario a usar.

    Returns:
        Diccionario con `ok`, `verificar`, `zip` y `link_descarga`.

    Raises:
        ErrorSeparador: si el motor falla o no produce salida.
    """
    pdf = Path(ruta_pdf)
    salida = Path(directorio_trabajo)
    salida.mkdir(parents=True, exist_ok=True)

    if not pdf.is_file():
        raise ErrorSeparador(f"No se encontró el PDF: {pdf.name}")

    if not MOTOR.is_file():
        raise ErrorSeparador(
            "El motor del separador no está instalado en este contenedor."
        )

    settings = obtener_settings()
    carpeta_resultados = salida / "resultados"

    comando = [
        # `python3`, no `python`: la imagen del worker OCR usa python3 del sistema
        # (debian + apt) porque la cola pesada comparte el venv de OpenClaw, que
        # espera `/usr/bin/python3`. En `python:*` existía el alias `python`; con
        # debian no, y el subprocess moría con FileNotFoundError.
        sys.executable or "python3",
        str(MOTOR),
        "--pdf",
        str(pdf),
        "--dir-salida",
        str(carpeta_resultados),
        "--dpi",
        str(DPI),
        "--workers",
        str(WORKERS_OCR),
    ]

    if ruta_excel and hoja_excel:
        comando.extend(["--excel", ruta_excel, "--hoja", hoja_excel])

    logger.info("Separador: iniciando %s", pdf.name)

    try:
        proceso = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            timeout=settings.job_timeout_seconds,
            cwd=str(SEPARADOR_DIR),
        )
    except subprocess.TimeoutExpired as exc:
        raise ErrorSeparador(
            f"El OCR excedió el tiempo límite ({settings.job_timeout_seconds}s)."
        ) from exc

    if proceso.returncode != 0:
        logger.error("Separador falló (código %d):\n%s", proceso.returncode, proceso.stderr[-2000:])
        raise ErrorSeparador("El OCR falló. Revisa los registros del worker.")

    if not carpeta_resultados.is_dir():
        raise ErrorSeparador("El OCR no generó ningún directorio de resultados.")

    resumen = _contar_resultados(carpeta_resultados)

    ruta_zip = salida / f"{pdf.stem}-separado.zip"
    _empaquetar(carpeta_resultados, ruta_zip)

    logger.info(
        "Separador: terminado %s → %d en carpetas, %d a verificar",
        pdf.name,
        resumen["ok"],
        resumen["verificar"],
    )

    return {
        **resumen,
        "zip": ruta_zip.name,
        "link_descarga": f"/api/separador/descargar/{salida.name}/{ruta_zip.name}",
    }


def _contar_resultados(carpeta_resultados: Path) -> dict[str, int]:
    """Cuenta los PDFs generados por carpeta de destino.

    La estructura del motor es:
        resultados/<Sede>/<archivo>.pdf
        resultados/Verificar/<archivo>.pdf
    """
    ok = 0
    verificar = 0

    for elemento in carpeta_resultados.rglob("*.pdf"):
        if "Verificar" in elemento.parts:
            verificar += 1
        else:
            ok += 1

    return {"ok": ok, "verificar": verificar}


def _empaquetar(origen: Path, destino: Path) -> None:
    """Comprime el directorio de resultados en un ZIP.

    Se excluye cualquier cosa que no sea PDF: la salida debe contener solo
    los resultados, sin restos temporales del OCR.
    """
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for archivo in sorted(origen.rglob("*.pdf")):
            # Ruta relativa dentro del ZIP, preservando las carpetas por sede.
            zf.write(archivo, archivo.relative_to(origen))


def limpiar_entrada(ruta_pdf: str) -> None:
    """Borra el PDF de entrada tras procesarlo.

    Los PDFs de contadores pueden contener datos de clientes; no tiene sentido
    conservarlos una vez generada la salida.
    """
    try:
        Path(ruta_pdf).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("No se pudo borrar el PDF de entrada %s: %s", ruta_pdf, exc)
