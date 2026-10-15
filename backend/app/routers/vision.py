"""Router de la herramienta «Analizar imagen».

Flujo síncrono (segundos): subir imagen → analizar → devolver texto. No usa la
cola de trabajos porque una llamada a Gemini no justifica el coste de un job.

El archivo subido se borra siempre, haya funcionado el análisis o no: son
imágenes del usuario y no tienen por qué quedarse en el servidor.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import obtener_settings
from app.core.seguridad import ArchivoInvalidoError, validar_archivo
from app.services import puente, vision

logger = logging.getLogger(__name__)

router = APIRouter()

# Prompt por defecto del portal. El script tiene el suyo, pero aquí se deja
# explícito para que la UI muestre lo mismo que se envía.
PROMPT_POR_DEFECTO = "Describe esta imagen de forma clara y concisa"


class RespuestaVision(BaseModel):
    """Resultado del análisis."""

    texto: str
    caracteres: int


@router.get("/vision/estado")
async def estado_motor() -> dict[str, object]:
    """Informa si la herramienta puede operar y con qué tipos de archivo."""
    settings = obtener_settings()
    disponible, motivo = vision.disponible(settings)

    return {
        "disponible": disponible,
        "motivo": motivo,
        "modelo": settings.gemini_model,
        "tipos": sorted(vision.TIPOS_IMAGEN),
        "max_upload_mb": settings.max_upload_mb,
        "prompt_por_defecto": PROMPT_POR_DEFECTO,
    }


@router.post("/vision", response_model=RespuestaVision)
async def analizar_imagen(
    archivo: UploadFile = File(...),
    prompt: str = Form(default=""),
) -> RespuestaVision:
    """Analiza una imagen y devuelve la descripción del modelo."""
    settings = obtener_settings()

    disponible, motivo = vision.disponible(settings)
    if not disponible:
        logger.error("Vision no disponible: %s", motivo)
        raise HTTPException(
            status_code=503,
            detail=f"El análisis de imagen no está disponible. {motivo}",
        )

    contenido = await archivo.read()

    try:
        validado = validar_archivo(
            archivo.filename or "",
            contenido,
            tipos_permitidos=vision.TIPOS_IMAGEN,
            max_bytes=settings.max_upload_bytes,
        )
    except ArchivoInvalidoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sufijo = vision.SUFIJO_POR_TIPO[validado.tipo_detectado]
    ruta = puente.guardar_subida(validado.contenido, sufijo, settings=settings)

    try:
        texto = await asyncio.to_thread(
            vision.analizar, ruta, prompt or PROMPT_POR_DEFECTO, settings=settings
        )
    except vision.ErrorVision as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # La imagen no se conserva: el portal no es un almacén de archivos.
        puente.borrar_silencioso(ruta)

    logger.info(
        "Vision completada: tipo=%s bytes=%d respuesta=%d caracteres",
        validado.tipo_detectado,
        validado.tamano_bytes,
        len(texto),
    )

    return RespuestaVision(texto=texto, caracteres=len(texto))
