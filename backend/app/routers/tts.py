"""Herramienta: Texto a voz (TTS).

Envuelve `scripts/tts.py` (Piper, voces en español mexicano). Es la herramienta
más simple del portal —texto entra, audio sale— y sirve como referencia para
las demás.

Nota de diseño: se ejecuta en la cola rápida porque Piper tarda segundos, no
minutos, así que no compensa la complejidad de un job asíncrono.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import obtener_settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Límite defensivo: un texto enorme saturaría la síntesis y el disco.
MAX_CARACTERES = 5000

# Voces disponibles en el script existente.
VOCES_VALIDAS = {"ald", "claude"}


class PeticionTTS(BaseModel):
    """Cuerpo de la petición de síntesis."""

    texto: str = Field(..., min_length=1, max_length=MAX_CARACTERES)
    voz: str = Field(default="ald")
    velocidad: float = Field(default=1.0, ge=0.5, le=2.0)


class RespuestaTTS(BaseModel):
    """Resultado de la síntesis."""

    archivo: str
    nombre_descarga: str
    caracteres: int


@router.post("/tts", response_model=RespuestaTTS)
async def sintetizar(peticion: PeticionTTS) -> RespuestaTTS:
    """Convierte texto a audio y devuelve la referencia de descarga."""
    settings = obtener_settings()

    if peticion.voz not in VOCES_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Voz no válida. Opciones: {', '.join(sorted(VOCES_VALIDAS))}.",
        )

    texto = peticion.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="El texto está vacío.")

    script = settings.herramientas_scripts_dir / "tts.py"
    if not script.is_file():
        raise HTTPException(
            status_code=503,
            detail="El motor de voz no está disponible en este despliegue.",
        )

    identificador = uuid.uuid4().hex[:12]
    destino = settings.output_dir / f"tts-{identificador}.wav"

    comando = [
        "python3",
        str(script),
        texto,
        "--voice",
        peticion.voz,
        "--speed",
        str(peticion.velocidad),
        "--output",
        str(destino),
    ]

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_ejecutar, comando, settings.job_timeout_seconds),
            timeout=settings.job_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail="La síntesis tardó demasiado."
        ) from exc
    except subprocess.CalledProcessError as exc:
        logger.error("TTS falló: %s\n%s", exc, exc.stderr)
        raise HTTPException(
            status_code=500, detail="Falló la síntesis de voz."
        ) from exc

    if not destino.is_file():
        raise HTTPException(
            status_code=500, detail="El motor de voz no generó ningún archivo."
        )

    logger.info("TTS generado: %s (%d caracteres)", destino.name, len(texto))

    return RespuestaTTS(
        archivo=destino.name,
        nombre_descarga=f"voz-{identificador}.wav",
        caracteres=len(texto),
    )


@router.get("/tts/{archivo}")
async def descargar(archivo: str) -> FileResponse:
    """Sirve el audio generado.

    El nombre se valida contra un patrón estricto para impedir que alguien
    pida rutas arbitrarias del sistema (path traversal).
    """
    settings = obtener_settings()

    # Solo se aceptan nombres con el formato que genera este módulo.
    if not archivo.startswith("tts-") or not archivo.endswith(".wav"):
        raise HTTPException(status_code=400, detail="Nombre de archivo no válido.")

    if "/" in archivo or "\\" in archivo or ".." in archivo:
        raise HTTPException(status_code=400, detail="Nombre de archivo no válido.")

    ruta = (settings.output_dir / archivo).resolve()

    # Confirma que la ruta resuelta sigue dentro del directorio de salidas.
    if not ruta.is_relative_to(settings.output_dir.resolve()):
        raise HTTPException(status_code=400, detail="Ruta fuera del directorio permitido.")

    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Audio no encontrado.")

    return FileResponse(
        path=ruta,
        media_type="audio/wav",
        filename=archivo,
    )


def _ejecutar(comando: list[str], timeout: int) -> None:
    """Ejecuta el script de TTS de forma síncrona (se llama en un hilo)."""
    subprocess.run(
        comando,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
