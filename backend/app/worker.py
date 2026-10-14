"""Worker de la cola de trabajos (RQ).

Los trabajos pesados —OCR del separador, transcripción— no pueden correr dentro
de la petición HTTP: tardan minutos y bloquearían el servidor. Se encolan aquí
y el worker los procesa en segundo plano.

Arranque: `python -m app.worker`  (lo hace el servicio `worker` del compose).
"""

from __future__ import annotations

import logging

from redis import Redis
from rq import Queue, Worker

from app.core.config import obtener_settings
from app.core.logging_config import configurar_logging

logger = logging.getLogger(__name__)

# Colas por coste. Permite dar prioridad a lo rápido sin que lo lento lo tape.
COLA_RAPIDA = "rapida"    # TTS, visión de imagen: segundos
COLA_PESADA = "pesada"    # OCR de PDFs, transcripción: minutos


def obtener_colas() -> list[Queue]:
    """Devuelve las colas que este worker atiende, en orden de prioridad."""
    settings = obtener_settings()
    conexion = Redis.from_url(settings.redis_url)
    return [Queue(COLA_RAPIDA, connection=conexion), Queue(COLA_PESADA, connection=conexion)]


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
