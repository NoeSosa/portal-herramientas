"""Herramienta: Analizar imagen (Gemini Vision).

Envuelve `scripts/analyze_image.py`. Es una llamada única a la API de Gemini
(unos segundos), así que no pasa por la cola: se ejecuta en un hilo para no
bloquear el event loop.

Sobre la API key: el script la busca en `../.env` relativo a sí mismo y, si no
la encuentra, en `GEMINI_API_KEY` del entorno. La copia escribible del script
vive en `/data/runtime/scripts/`, donde no hay `.env`, así que la clave se
inyecta explícitamente en el entorno del proceso — nunca en la línea de
comandos, que sería visible en la lista de procesos del host.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import Settings, obtener_settings
from app.core.proceso import mensaje_legible
from app.services import puente

logger = logging.getLogger(__name__)

# El análisis tarda segundos; 120 s cubre una imagen grande con mala red.
TIMEOUT_ANALISIS = 120

# Tipos que la herramienta acepta. Debe ser un subconjunto de las firmas
# conocidas por `core.seguridad`.
TIPOS_IMAGEN = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

# Extensiones con las que se guarda cada tipo detectado. El script decide el
# mime por extensión, así que el sufijo importa.
SUFIJO_POR_TIPO = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Límite del prompt: acota el coste de tokens por petición.
MAX_PROMPT = 2000


class ErrorVision(RuntimeError):
    """El análisis de imagen falló."""


def disponible(settings: Settings | None = None) -> tuple[bool, str]:
    """Comprueba que hay entorno y credencial antes de aceptar una subida."""
    settings = settings or obtener_settings()

    entorno_ok, motivo = puente.entorno_openclaw_disponible(settings)
    if not entorno_ok:
        return False, motivo

    if not (settings.herramientas_scripts_dir / "analyze_image.py").is_file():
        return False, "No se encontró el script analyze_image.py."

    if not settings.gemini_api_key:
        return (
            False,
            "Falta GEMINI_API_KEY en el .env del portal: sin credencial no hay "
            "análisis de imagen.",
        )

    return True, ""


def analizar(
    ruta_imagen: Path,
    prompt: str,
    *,
    settings: Settings | None = None,
) -> str:
    """Analiza una imagen y devuelve la descripción en texto.

    Args:
        ruta_imagen: archivo ya validado y guardado en el portal.
        prompt: instrucción para el modelo (``""`` usa el default del script).

    Returns:
        Texto de la respuesta del modelo.

    Raises:
        ErrorVision: si el script falla o no devuelve texto.
    """
    settings = settings or obtener_settings()

    disponible_ok, motivo = disponible(settings)
    if not disponible_ok:
        raise ErrorVision(motivo)

    script = puente.copiar_script("analyze_image.py", settings)

    argumentos = [str(script), str(ruta_imagen)]
    if prompt.strip():
        argumentos += ["--prompt", prompt.strip()[:MAX_PROMPT]]

    try:
        resultado = puente.ejecutar_python(
            argumentos,
            cwd=script.parent,
            timeout=TIMEOUT_ANALISIS,
            entorno_extra={"GEMINI_API_KEY": settings.gemini_api_key or ""},
        )
    except Exception as exc:  # pragma: no cover - defensivo (timeout, OSError)
        logger.exception("Fallo ejecutando analyze_image.py")
        raise ErrorVision("No se pudo ejecutar el analizador de imágenes.") from exc

    texto = resultado.texto

    # El script imprime "ERROR: …" en stdout y sale con código 1 cuando Gemini
    # falla (cuota agotada, imagen ilegible…). Se propaga ese mensaje tal cual:
    # es útil para el usuario y no revela nada interno.
    if not resultado.ok or texto.startswith("ERROR:"):
        detalle = texto.removeprefix("ERROR:").strip() or mensaje_legible(
            resultado.stderr, resultado.codigo
        )
        logger.warning("Vision falló: %s", detalle)
        raise ErrorVision(detalle)

    if not texto:
        raise ErrorVision("El modelo devolvió una respuesta vacía.")

    return texto
