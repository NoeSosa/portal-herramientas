#!/usr/bin/env python3
"""
warmup_ocr.py — Precalienta los modelos de PaddleOCR (una sola vez).
=====================================================================
Motivo (16/oct/2026, encontrado en la puesta en marcha del panel dentro del
portal de herramientas): el pipeline lanza WORKERS subprocesos OCR en paralelo.
Si los modelos NO están descargados aún, los N workers intentan bajarlos A LA VEZ
al mismo directorio (`~/.paddleocr`): uno falla con
`FileNotFoundError: .../en_PP-OCRv3_det_infer.tar` y las páginas de su rango se
pierden EN SILENCIO (el documento se procesa a medias).

Solución: descargar los modelos UNA vez, en un solo proceso, antes del primer job.

Uso (dentro del contenedor del panel; idempotente):
    HOME=/data python3 separador_pdfs/panel/warmup_ocr.py
    # o, en el host, con el venv del pipeline:
    venv-pdfseparador/bin/python separador_pdfs/panel/warmup_ocr.py

Devuelve 0 si los modelos quedaron listos (o ya estaban).
"""
import os
import sys


def principal():
    import numpy as np
    # numpy 2.x eliminó np.sctypes e imgaug 0.4 lo usa (parche obligatorio).
    if not hasattr(np, "sctypes"):
        np.sctypes = {"int": [np.int8, np.int16, np.int32, np.int64],
                      "uint": [np.uint8, np.uint16, np.uint32, np.uint64],
                      "float": [np.float16, np.float32, np.float64],
                      "complex": [np.complex64, np.complex128]}
    from paddleocr import PaddleOCR
    destino = os.path.expanduser("~/.paddleocr")
    print(f"[warmup] descargando/comprobando modelos en {destino} …", flush=True)
    # Instanciar ya fuerza la descarga de det/rec/cls.
    PaddleOCR(lang="en", use_angle_cls=False, show_log=False, cpu_threads=1)
    tam = sum(os.path.getsize(os.path.join(r, a))
              for r, _d, arch in os.walk(destino) for a in arch) if os.path.isdir(destino) else 0
    print(f"[warmup] modelos listos ({tam / 1048576:.1f} MB en {destino})", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(principal())
    except Exception as e:  # nunca debe bloquear el arranque del panel
        print(f"[warmup] aviso: no se pudieron preparar los modelos: {e}", file=sys.stderr)
        sys.exit(1)
