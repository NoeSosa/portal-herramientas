"""Router de la herramienta «Identificar canción».

Flujo síncrono: subir audio → huella contra Shazam + metadata → ficha. El
archivo subido se borra siempre al terminar.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import obtener_settings
from app.core.seguridad import ArchivoInvalidoError, validar_archivo
from app.services import cancion, puente

logger = logging.getLogger(__name__)

router = APIRouter()


class DatosCancion(BaseModel):
    """Ficha de la canción reconocida.

    Todos los campos son opcionales: Shazam devuelve el título y el artista,
    pero el álbum, el género o la letra pueden faltar según la canción.
    """

    titulo: str | None = None
    artista: str | None = None
    album: str | None = None
    genero: str | None = None
    portada: str | None = None
    anio: str | None = None
    fuente: str | None = None


class RespuestaCancion(BaseModel):
    """Resultado de la identificación."""

    identificacion: DatosCancion
    #: True si la canción aparece como registrada/venal en las fuentes de
    #: metadata. No es un juicio legal, es lo que reportan las APIs públicas.
    bajo_copyright: bool | None = None
    detalle: dict | None = None


@router.get("/cancion/estado")
async def estado_motor() -> dict[str, object]:
    """Informa si el identificador puede operar."""
    settings = obtener_settings()
    disponible, motivo = cancion.disponible(settings)

    return {
        "disponible": disponible,
        "motivo": motivo,
        "tipos": sorted(cancion.TIPOS_AUDIO),
        "max_upload_mb": settings.max_upload_mb,
    }


@router.post("/cancion", response_model=RespuestaCancion)
async def identificar_cancion(archivo: UploadFile = File(...)) -> RespuestaCancion:
    """Identifica la canción del archivo subido."""
    settings = obtener_settings()

    disponible, motivo = cancion.disponible(settings)
    if not disponible:
        logger.error("Canción no disponible: %s", motivo)
        raise HTTPException(
            status_code=503,
            detail=f"El identificador de canciones no está disponible. {motivo}",
        )

    contenido = await archivo.read()

    try:
        validado = validar_archivo(
            archivo.filename or "",
            contenido,
            tipos_permitidos=cancion.TIPOS_AUDIO,
            max_bytes=settings.max_upload_bytes,
        )
    except ArchivoInvalidoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sufijo = cancion.SUFIJO_POR_TIPO[validado.tipo_detectado]
    ruta = puente.guardar_subida(validado.contenido, sufijo, settings=settings)

    try:
        datos = await asyncio.to_thread(
            cancion.identificar, ruta, settings=settings
        )
    except cancion.SinCoincidencia as exc:
        # 422: la petición es válida, el audio es válido, pero no hay
        # coincidencia. Es un resultado, no un fallo del servidor.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except cancion.ErrorCancion as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        puente.borrar_silencioso(ruta)

    identificacion = datos.get("identificacion") or {}
    logger.info(
        "Canción identificada: %s — %s",
        identificacion.get("titulo"),
        identificacion.get("artista"),
    )

    return RespuestaCancion(
        identificacion=DatosCancion(
            **{k: v for k, v in identificacion.items() if k in DatosCancion.model_fields}
        ),
        bajo_copyright=datos.get("bajo_copyright"),
        detalle=datos,
    )
