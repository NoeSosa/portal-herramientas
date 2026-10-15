"""Herramienta: Transcribir audio o video.

Envuelve `scripts/transcribir_av.py`: Gemini → Whisper local como respaldo. Los
videos pasan primero por `ffmpeg` para extraer la pista de audio.

Es un trabajo de minutos, así que va por la **cola pesada** (la misma del
separador): el worker OCR ya tiene los binarios y los límites de recursos.

Contrato con el worker: esta tarea devuelve un diccionario; el router lo
traduce a estado de job y nunca expone el `stderr` interno del script.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import Settings, obtener_settings
from app.core.proceso import mensaje_legible
from app.services import puente

logger = logging.getLogger(__name__)

# Una hora de audio transcrita puede tardar bastante; 40 min de margen.
TIMEOUT_TRANSCRIPCION = 2400

# Formatos aceptados. La transcripción por Whisper/Gemini trabaja sobre audio,
# así que se aceptan los contenedores de video más comunes.
TIPOS_AUDIO_VIDEO = {
    "audio/mpeg",
    "audio/wav",
    "audio/ogg",
    "audio/mp4",
    "audio/flac",
    "video/mp4",
    "video/webm",
    "video/quicktime",
}

SUFIJO_POR_TIPO = {
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}

# Formatos donde el script extrae la pista de audio antes de transcribir.
# Sirve para avisar al usuario en la UI (una extracción añade minutos).
FORMATOS_CON_EXTRACCION = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


class ErrorTranscripcion(RuntimeError):
    """La transcripción no pudo completarse."""


def disponible(settings: Settings | None = None) -> tuple[bool, str]:
    """Comprueba entorno y script antes de encolar."""
    settings = settings or obtener_settings()

    entorno_ok, motivo = puente.entorno_openclaw_disponible(settings)
    if not entorno_ok:
        return False, motivo

    if not (settings.herramientas_scripts_dir / "transcribir_av.py").is_file():
        return False, "No se encontró el script transcribir_av.py."

    return True, ""


def transcribir(
    ruta_entrada: Path,
    *,
    idioma: str | None = None,
    motor: str = "auto",
    settings: Settings | None = None,
) -> str:
    """Transcribe un audio o video y devuelve el texto.

    Args:
        ruta_entrada: archivo ya validado y guardado en el portal.
        idioma: código ISO opcional (``"es"``). Vacío = autodetección.
        motor: ``auto`` (Gemini y, si falla, Whisper), ``gemini`` o ``whisper``.
        settings: configuración a usar.

    Returns:
        Texto transcrito.

    Raises:
        ErrorTranscripcion: con un mensaje legible para el usuario.
    """
    settings = settings or obtener_settings()

    disponible_ok, motivo = disponible(settings)
    if not disponible_ok:
        raise ErrorTranscripcion(motivo)

    script = puente.copiar_script("transcribir_av.py", settings)

    argumentos = [str(script), str(ruta_entrada)]
    if idioma:
        argumentos += ["--idioma", idioma]
    if motor == "whisper":
        # El script no intenta Gemini si se fuerza el motor local.
        argumentos += ["--no-gemini"]

    try:
        resultado = puente.ejecutar_python(
            argumentos,
            cwd=script.parent,
            timeout=TIMEOUT_TRANSCRIPCION,
            entorno_extra={"GEMINI_API_KEY": settings.gemini_api_key or ""},
        )
    except Exception as exc:  # pragma: no cover - defensivo (timeout, OSError)
        logger.exception("Fallo ejecutando transcribir_av.py")
        raise ErrorTranscripcion("No se pudo ejecutar el transcriptor.") from exc

    texto = resultado.texto

    if not resultado.ok or texto.upper().startswith("ERROR"):
        detalle = mensaje_legible(resultado.stderr, resultado.codigo)
        logger.warning("Transcripción falló: %s", detalle)
        raise ErrorTranscripcion(detalle)

    if not texto:
        raise ErrorTranscripcion(
            "La transcripción quedó vacía: el archivo no tiene voz reconocible."
        )

    return texto
