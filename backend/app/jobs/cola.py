"""Cliente de la cola para encolar trabajos desde la API."""

from __future__ import annotations

from functools import lru_cache

from redis import Redis
from rq import Queue

from app.core.config import obtener_settings


@lru_cache(maxsize=1)
def _conexion() -> Redis:
    """Conexión a Redis, reutilizada en todo el proceso."""
    return Redis.from_url(obtener_settings().redis_url)


def cola_rapida() -> Queue:
    """Cola para trabajos de segundos (TTS, visión)."""
    from app.worker import COLA_RAPIDA

    return Queue(COLA_RAPIDA, connection=_conexion())


def cola_pesada() -> Queue:
    """Cola para trabajos de minutos (OCR, transcripción)."""
    from app.worker import COLA_PESADA

    return Queue(COLA_PESADA, connection=_conexion())
