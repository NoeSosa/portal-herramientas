"""Configuración de logging estructurado."""

from __future__ import annotations

import logging
import sys

from app.core.config import obtener_settings

_FORMATO = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configurar_logging() -> None:
    """Configura el logging raíz una sola vez.

    Escribe a stdout para que Docker recoja los logs con su driver.
    """
    settings = obtener_settings()

    raiz = logging.getLogger()
    if raiz.handlers:  # ya configurado (p. ej. reimportación)
        return

    manejador = logging.StreamHandler(sys.stdout)
    manejador.setFormatter(logging.Formatter(_FORMATO, datefmt="%Y-%m-%d %H:%M:%S"))

    raiz.addHandler(manejador)
    raiz.setLevel(settings.log_level)

    # Bajar el ruido de librerías de terceros.
    for ruidoso in ("uvicorn.access", "httpx", "httpcore", "urllib3"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
