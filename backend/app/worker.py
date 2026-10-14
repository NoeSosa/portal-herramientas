"""Worker de la cola de trabajos (RQ).

Los trabajos pesados —OCR del separador, transcripción— no pueden correr dentro
de la petición HTTP: tardan minutos y bloquearían el servidor. Se encolan aquí
y el worker los procesa en segundo plano.

Hay **dos** workers en el compose, cada uno con su imagen y sus recursos:

    COLAS=rapida  → hw-worker       (imagen ligera: TTS, visión, canción)
    COLAS=pesada  → hw-worker-ocr   (imagen OCR: separador de PDFs)

Separarlos evita que un OCR de 20 minutos bloquee un TTS de 3 segundos.

Arranque: `python -m app.worker`
"""

from __future__ import annotations

import logging
import os

from redis import Redis
from rq import Queue, Worker

from app.core.config import obtener_settings
from app.core.logging_config import configurar_logging

logger = logging.getLogger(__name__)

# Colas por coste. Permiten dar prioridad a lo rápido sin que lo lento lo tape.
COLA_RAPIDA = "rapida"    # TTS, visión de imagen: segundos
COLA_PESADA = "pesada"    # OCR de PDFs, transcripción: minutos

_COLAS_POR_NOMBRE = {
    "rapida": COLA_RAPIDA,
    "pesada": COLA_PESADA,
}


def _colas_a_atender() -> list[str]:
    """Lee qué colas debe atender este worker desde `COLAS`.

    Por defecto atiende todas: así el worker funciona igual en desarrollo
    (un solo proceso) que en producción (dos especializados).
    """
    crudo = os.environ.get("COLAS", "").strip().lower()
    if not crudo:
        return [COLA_RAPIDA, COLA_PESADA]

    nombres: list[str] = []
    for trozo in crudo.split(","):
        nombre = _COLAS_POR_NOMBRE.get(trozo.strip())
        if nombre is None:
            logger.warning("Cola desconocida en COLAS=%r: %r", crudo, trozo.strip())
            continue
        if nombre not in nombres:
            nombres.append(nombre)

    if not nombres:
        logger.warning("COLAS=%r no produjo ninguna cola válida; usando todas.", crudo)
        return [COLA_RAPIDA, COLA_PESADA]

    return nombres


def obtener_colas() -> list[Queue]:
    """Devuelve las colas que este worker atiende, en orden de prioridad."""
    settings = obtener_settings()
    conexion = Redis.from_url(settings.redis_url)
    return [Queue(nombre, connection=conexion) for nombre in _colas_a_atender()]


def main() -> None:
    """Punto de entrada del worker."""
    configurar_logging()
    settings = obtener_settings()
    settings.preparar_directorios()

    colas = obtener_colas()
    logger.info(
        "Worker iniciado. Colas: %s. Redis: %s",
        ", ".join(c.name for c in colas),
        settings.redis_url.rsplit("@", 1)[-1],
    )

    trabajador = Worker(colas)
    trabajador.work(with_scheduler=False)


if __name__ == "__main__":
    main()
