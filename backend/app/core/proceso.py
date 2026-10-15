"""Utilidades de proceso: entorno saneado y manejo de fallos de scripts.

Los scripts del workspace se escribieron para correr dentro del contenedor de
OpenClaw, donde existen cosas que aquí no (variables del agente, `chromedriver`
en el PATH, el `HOME` del agente…). Este módulo entrega un entorno mínimo y
predecible para que su comportamiento sea el mismo en cada ejecución.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Claves del proceso que sí se heredan. Todo lo demás se descarta: el entorno
# del portal no debe filtrarse a los scripts.
CLAVES_HEREDADAS = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")

# PATH base. Incluye el venv de OpenClaw porque algunos scripts invocan sus
# propios binarios (`piper`, `ffmpeg`) por nombre en vez de por ruta absoluta.
PATH_BASE = (
    "/home/node/.openclaw/workspace/venv/bin:"
    "/opt/venv/bin:"
    "/usr/local/bin:/usr/bin:/bin"
)


def entorno_limpio(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Construye el entorno de ejecución de un script.

    Args:
        extra: variables adicionales (p. ej. `GEMINI_API_KEY`). Se aplican
            después de las heredadas, así que pueden sobrescribirlas.

    Returns:
        Diccionario listo para `subprocess.run(env=...)`.
    """
    entorno = {clave: os.environ[clave] for clave in CLAVES_HEREDADAS if clave in os.environ}
    entorno["PATH"] = PATH_BASE
    entorno.setdefault("HOME", "/home/node")
    entorno["PYTHONUNBUFFERED"] = "1"

    if extra:
        entorno.update({k: v for k, v in extra.items() if v})

    return entorno


def mensaje_legible(stderr: str, codigo: int, maximo: int = 400) -> str:
    """Extrae la última línea útil del `stderr` de un script.

    Los scripts registran su avance en `stderr` y el error real al final. Se
    devuelve solo esa parte, sin rutas internas largas ni trazas completas.
    """
    lineas = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    if not lineas:
        return f"El script terminó con código {codigo}."

    for linea in reversed(lineas):
        if linea.upper().startswith("ERROR") or "error:" in linea.lower():
            return linea[:maximo]

    return lineas[-1][:maximo]


def existe_binario(nombre: str, extra: Path | None = None) -> bool:
    """True si un binario es localizable en el PATH base o en `extra`."""
    if extra is not None and (extra / nombre).exists():
        return True
    return any(
        (Path(directorio) / nombre).exists() for directorio in PATH_BASE.split(":")
    )
