"""Herramienta: Identificar canción.

Envuelve `scripts/identify_song.py`: huella de audio contra Shazam (sin API
key) más metadata de iTunes, MusicBrainz y Deezer.

Tarda entre 5 y 20 segundos según la red y las fuentes, así que se ejecuta en
un hilo y no en la cola: es más corto que un round-trip de job de RQ.

El script acepta audio **y** video (Shazam lee la pista de audio del contenedor),
por eso los tipos permitidos incluyen formatos de video.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import Settings, obtener_settings
from app.core.proceso import mensaje_legible
from app.services import puente

logger = logging.getLogger(__name__)

# Shazam + consultas HTTP a tres APIs: 90 s es holgado.
TIMEOUT_IDENTIFICACION = 90

# Tipos aceptados. Es un subconjunto de las firmas de `core.seguridad`.
TIPOS_AUDIO = {
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


class ErrorCancion(RuntimeError):
    """La identificación no pudo completarse."""


class SinCoincidencia(RuntimeError):
    """El motor funcionó pero no reconoció la canción."""


def disponible(settings: Settings | None = None) -> tuple[bool, str]:
    """Comprueba entorno y script antes de aceptar una subida."""
    settings = settings or obtener_settings()

    entorno_ok, motivo = puente.entorno_openclaw_disponible(settings)
    if not entorno_ok:
        return False, motivo

    if not (settings.herramientas_scripts_dir / "identify_song.py").is_file():
        return False, "No se encontró el script identify_song.py."

    return True, ""


def identificar(
    ruta_audio: Path,
    *,
    settings: Settings | None = None,
) -> dict:
    """Identifica la canción y devuelve el resultado estructurado.

    Siempre se pide la salida JSON del script (`--json`): la salida "bonita"
    está pensada para leerla un humano en una terminal, no para consumirla.

    Raises:
        SinCoincidencia: Shazam no reconoció el audio.
        ErrorCancion: fallo de ejecución o salida ilegible.
    """
    settings = settings or obtener_settings()

    disponible_ok, motivo = disponible(settings)
    if not disponible_ok:
        raise ErrorCancion(motivo)

    script = puente.copiar_script("identify_song.py", settings)

    try:
        resultado = puente.ejecutar_python(
            [str(script), str(ruta_audio), "--json"],
            cwd=script.parent,
            timeout=TIMEOUT_IDENTIFICACION,
        )
    except Exception as exc:  # pragma: no cover - defensivo (timeout, OSError)
        logger.exception("Fallo ejecutando identify_song.py")
        raise ErrorCancion("No se pudo ejecutar el identificador de canciones.") from exc

    datos = _extraer_json(resultado.texto)

    if datos is None:
        detalle = resultado.texto[:300] or mensaje_legible(
            resultado.stderr, resultado.codigo
        )
        logger.warning("Canción sin JSON utilizable: %s", detalle)
        raise ErrorCancion("El identificador devolvió una respuesta ilegible.")

    # El script emite {"error": "..."} cuando no encuentra el archivo o falla
    # la fuente principal.
    if "error" in datos:
        raise ErrorCancion(str(datos["error"])[:300])

    if not datos.get("identificacion"):
        raise SinCoincidencia(
            "No se reconoció la canción. Prueba con un fragmento más largo "
            "o con menos ruido de fondo."
        )

    return datos


def _extraer_json(salida: str) -> dict | None:
    """Parsea el JSON del script aunque venga acompañado de ruido.

    `identify_song.py` escribe su avance en `stderr`, pero algunas
    dependencias (shazamio) imprimen avisos en `stdout`. Se recorta al primer
    objeto JSON completo para no depender de que la salida esté limpia.
    """
    texto = (salida or "").strip()
    if not texto:
        return None

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        return None

    try:
        return json.loads(texto[inicio : fin + 1])
    except json.JSONDecodeError:
        return None
