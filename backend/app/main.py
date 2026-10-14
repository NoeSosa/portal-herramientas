"""Aplicación FastAPI del Portal de Herramientas.

Punto de entrada del backend. Monta los routers de cada herramienta y aplica
el middleware común (CORS restringido, cabeceras de seguridad, límites).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import obtener_settings
from app.core.logging_config import configurar_logging
from app.core.seguridad import ArchivoInvalidoError
from app.routers import health, separador, tts

logger = logging.getLogger(__name__)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Prepara los directorios de datos al arrancar y libera al cerrar."""
    settings = obtener_settings()
    settings.preparar_directorios()
    logger.info(
        "%s v%s iniciando (entorno=%s)",
        settings.app_name,
        __version__,
        settings.app_env,
    )
    yield
    logger.info("Backend detenido.")


def crear_aplicacion() -> FastAPI:
    """Construye y configura la aplicación FastAPI."""
    configurar_logging()
    settings = obtener_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        # En producción no exponemos los esquemas interactivos por defecto.
        docs_url="/api/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
    )

    # CORS: solo el propio frontend. En producción el navegador llega al mismo
    # origen (nginx sirve el front y proxya /api), así que esto es defensa extra.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    app.include_router(health.router, prefix="/api", tags=["sistema"])
    app.include_router(tts.router, prefix="/api", tags=["herramientas"])
    app.include_router(separador.router, prefix="/api", tags=["herramientas"])

    @app.exception_handler(ArchivoInvalidoError)
    async def _manejar_archivo_invalido(
        _request: Request, exc: ArchivoInvalidoError
    ) -> JSONResponse:
        """Convierte los errores de validación en 400 con mensaje claro."""
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = crear_aplicacion()
