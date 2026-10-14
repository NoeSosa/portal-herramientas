"""Compatibilidad de PaddleOCR en ARM64.

Este módulo encapsula los tres arreglos conocidos para que PaddleOCR funcione
en un contenedor ARM64 sin root. Cada uno resuelve un fallo real observado:

1. **libgomp**: paddlepaddle necesita OpenMP. Sin `LD_LIBRARY_PATH` apuntando a
   la librería, `import paddle` lanza `libgomp.so.1: cannot open shared object
   file`. En el contenedor se resuelve vía apt (libgomp1) y se copia a /opt/lib.

2. **np.sctypes**: NumPy 2.x eliminó `np.sctypes`, que `imgaug` 0.4.0 usa al
   importarse. Sin el parche, `from paddleocr import PaddleOCR` falla con
   `AttributeError: module 'numpy' has no attribute 'sctypes'`.

3. **cpu_threads**: el build ARM64 de paddlepaddle no trae OpenMP, así que
   `cpu_threads` no acelera. Se documenta para no perder tiempo ajustándolo.

⚠️ Estas funciones deben llamarse **antes** de importar paddleocr.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_parche_aplicado = False


def aplicar_parche_numpy_sctypes() -> None:
    """Restaura `np.sctypes` si NumPy lo eliminó.

    Idempotente: se puede llamar múltiples veces sin efecto adverso.

    imgaug 0.4.0 (dependencia transitiva de paddleocr) accede a `np.sctypes`
    al importarse. NumPy 2.x lo removió, así que sin esto el import de
    paddleocr falla. Los valores replican los que NumPy 1.x exponía.
    """
    global _parche_aplicado

    if _parche_aplicado:
        return

    import numpy as np

    if hasattr(np, "sctypes"):
        _parche_aplicado = True
        return

    np.sctypes = {  # type: ignore[attr-defined]
        "int": [np.int8, np.int16, np.int32, np.int64],
        "uint": [np.uint8, np.uint16, np.uint32, np.uint64],
        "float": [np.float16, np.float32, np.float64],
        "complex": [np.complex64, np.complex128],
    }

    _parche_aplicado = True
    logger.debug("Parche np.sctypes aplicado (NumPy 2.x + imgaug).")


def verificar_libgomp() -> bool:
    """Comprueba que libgomp.so.1 es localizable.

    Devuelve True si se encuentra. No lanza: solo registra, para poder dar un
    mensaje útil en lugar de un ImportError opaco.
    """
    rutas = [
        ruta
        for ruta in (os.environ.get("LD_LIBRARY_PATH") or "").split(":")
        if ruta
    ]
    rutas.extend(["/opt/lib", "/usr/lib/aarch64-linux-gnu", "/usr/lib"])

    for directorio in rutas:
        candidato = os.path.join(directorio, "libgomp.so.1")
        if os.path.exists(candidato):
            logger.debug("libgomp encontrada en %s", candidato)
            return True

    logger.warning(
        "No se encontró libgomp.so.1. PaddleOCR fallará al importar. "
        "LD_LIBRARY_PATH=%r",
        os.environ.get("LD_LIBRARY_PATH"),
    )
    return False


def preparar_paddleocr() -> None:
    """Aplica todos los arreglos necesarios antes de importar paddleocr.

    Llama a esto al inicio de cualquier job que use OCR.
    """
    verificar_libgomp()
    aplicar_parche_numpy_sctypes()
