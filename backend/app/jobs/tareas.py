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


def procesar_separador(ruta_pdf: str, id_trabajo: str) -> dict[str, object]:
    """Ejecuta el separador de PDFs y deja el resultado en outputs/<id_trabajo>.

    Nota sobre el nombre: **no** puede llamarse `job_id`. RQ inyecta argumentos
    propios en la ejecución (`job_id`, `failure_ttl`…) y el paso de kwargs
    choca: `TypeError: missing 1 required positional argument`. Se usa
    `id_trabajo` para no colisionar.

    Esta función corre en el contenedor `hw-worker-ocr`, que es el único con
    PaddleOCR instalado. Por eso el import de `app.services.separador` es
    diferido: el worker ligero no debe cargar ese módulo.
    """
    settings = obtener_settings()
    settings.preparar_directorios()

    directorio_trabajo = settings.output_dir / id_trabajo
    directorio_trabajo.mkdir(parents=True, exist_ok=True)

    logger.info("Job %s: separando %s", id_trabajo, Path(ruta_pdf).name)

    from app.services.separador import ErrorSeparador, ejecutar_separador, limpiar_entrada

    # `limpiar_entrada` es genérica (borra cualquier archivo de entrada), pero su
    # nombre quedó atado al separador. Se reutiliza tal cual en vez de duplicar
    # la lógica de borrado.
    try:
        resultado = ejecutar_separador(
            ruta_pdf,
            str(directorio_trabajo),
        )
    except ErrorSeparador as exc:
        logger.error("Job %s falló: %s", id_trabajo, exc)
        return {"ok": False, "error": str(exc)}
    finally:
        # El PDF de entrada puede contener datos de clientes: se borra siempre,
        # haya funcionado o no.
        limpiar_entrada(ruta_pdf)

    logger.info(
        "Job %s completado: %s OK, %s a verificar",
        id_trabajo,
        resultado.get("ok"),
        resultado.get("verificar"),
    )
    return {"ok": True, **resultado}


def procesar_transcripcion(
    ruta_entrada: str,
    id_trabajo: str,
    idioma: str | None = None,
    motor: str = "auto",
) -> dict[str, object]:
    """Transcribe un audio o video y deja el texto en outputs/<id_trabajo>.

    Corre en la cola pesada junto al separador: la transcripción tarda minutos
    y necesita los mismos límites de CPU y memoria.

    Devuelve ``{"ok": False, "error": …}`` en vez de lanzar: RQ registraría
    la excepción con su traza, y aquí el mensaje debe ser legible para el
    usuario final.
    """
    settings = obtener_settings()
    settings.preparar_directorios()

    directorio = settings.output_dir / id_trabajo
    directorio.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Job %s: transcribiendo %s (idioma=%s motor=%s)",
        id_trabajo,
        Path(ruta_entrada).name,
        idioma or "auto",
        motor,
    )

    from app.services.transcripcion import ErrorTranscripcion, transcribir

    # El borrado del archivo de entrada reutiliza el helper del separador: es
    # genérico (borra cualquier archivo) aunque su nombre venga de ahí.
    from app.services.separador import limpiar_entrada

    try:
        texto = transcribir(
            Path(ruta_entrada),
            idioma=idioma,
            motor=motor,
            settings=settings,
        )
    except ErrorTranscripcion as exc:
        logger.error("Job %s falló: %s", id_trabajo, exc)
        return {"ok": False, "error": str(exc)[:400]}
    finally:
        # El audio puede contener conversaciones privadas: se borra siempre.
        limpiar_entrada(ruta_entrada)

    (directorio / "transcripcion.txt").write_text(texto + "\n", encoding="utf-8")

    logger.info("Job %s completado: %d caracteres", id_trabajo, len(texto))

    return {"ok": True, "texto": texto, "caracteres": len(texto)}
