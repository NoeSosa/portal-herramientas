"""Tareas que ejecutan los workers de RQ.

Cada función aquí es una unidad de trabajo que RQ puede encolar. Deben ser
importables por el worker (RQ las resuelve por nombre cualificado) y no pueden
depender de estado en memoria del proceso que las encoló.

Convención: las tareas **no** lanzan excepciones hacia el usuario con detalles
internos. Devuelven un diccionario con `ok` o dejan que RQ registre el fallo,
que el router traduce a un mensaje genérico.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import obtener_settings

logger = logging.getLogger(__name__)


def procesar_separador(ruta_pdf: str, job_id: str) -> dict[str, object]:
    """Ejecuta el separador de PDFs y deja el resultado en outputs/<job_id>.

    Esta función corre en el contenedor `hw-worker-ocr`, que es el único con
    PaddleOCR instalado. Por eso el import de `app.services.separador` es
    diferido: el worker ligero no debe cargar ese módulo.
    """
    settings = obtener_settings()
    settings.preparar_directorios()

    directorio_trabajo = settings.output_dir / job_id
    directorio_trabajo.mkdir(parents=True, exist_ok=True)

    logger.info("Job %s: separando %s", job_id, Path(ruta_pdf).name)

    from app.services.separador import ErrorSeparador, ejecutar_separador, limpiar_entrada

    try:
        resultado = ejecutar_separador(
            ruta_pdf,
            str(directorio_trabajo),
        )
    except ErrorSeparador as exc:
        logger.error("Job %s falló: %s", job_id, exc)
        return {"ok": False, "error": str(exc)}
    finally:
        # El PDF de entrada puede contener datos de clientes: se borra siempre,
        # haya funcionado o no.
        limpiar_entrada(ruta_pdf)

    logger.info(
        "Job %s completado: %s OK, %s a verificar",
        job_id,
        resultado.get("ok"),
        resultado.get("verificar"),
    )
    return {"ok": True, **resultado}
