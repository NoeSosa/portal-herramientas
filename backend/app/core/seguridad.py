"""Validación de archivos subidos.

Este módulo es la frontera de confianza del portal: todo archivo que llega del
exterior pasa por aquí antes de tocar disco o alimentar un script.

Regla: **nunca** confiar en la extensión ni en el Content-Type que envía el
cliente. Se verifican los *magic bytes* reales del contenido.

Detalle importante: varios formatos comparten prefijo. `RIFF` es el contenedor
tanto de WAV como de WEBP, y `\x00\x00\x00`/`ftyp` lo comparten MP4, M4A y MOV.
Por eso cada firma puede llevar una **comprobación adicional** sobre bytes
concretos del encabezado; sin ella, un WAV subiría como "image/webp".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Firmas de archivo reales (magic bytes) por tipo permitido.
#
# Cada entrada es una lista de prefijos válidos; basta con que uno coincida.
# Cuando un prefijo lo comparten dos tipos (RIFF, ftyp), la detección mira el
# orden de este diccionario, así que el tipo más específico va primero.
FIRMAS_PERMITIDAS: dict[str, list[bytes]] = {
    "application/pdf": [b"%PDF-"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "audio/mpeg": [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
    "audio/ogg": [b"OggS"],
    "audio/flac": [b"fLaC"],
    "audio/wav": [b"RIFF"],
    "image/webp": [b"RIFF"],
    "audio/mp4": [b"\x00\x00\x00", b"ftyp"],
    "video/mp4": [b"\x00\x00\x00", b"ftyp"],
    "video/webm": [b"\x1a\x45\xdf\xa3"],
    "video/quicktime": [b"\x00\x00\x00", b"ftyp", b"moov"],
}

#: Bytes 8–12 de un contenedor RIFF que dicen qué lleva dentro.
#: Sin esto, WAV y WEBP son indistinguibles: ambos empiezan por `RIFF`.
FORMATO_RIFF: dict[bytes, str] = {
    b"WAVE": "audio/wav",
    b"WEBP": "image/webp",
}

#: Marca de MP4/MOV en el byte 4 de los contenedores basados en cajas.
MARCA_FTYP = b"ftyp"

# Límite defensivo para los primeros bytes que se inspeccionan.
BYTES_INSPECCION = 64


class ArchivoInvalidoError(ValueError):
    """El archivo no cumple las reglas de validación."""


@dataclass(frozen=True)
class ArchivoValidado:
    """Resultado de validar un archivo: datos y tipo real detectado."""

    nombre: str
    tipo_detectado: str
    tamano_bytes: int
    contenido: bytes


def detectar_tipo(contenido: bytes) -> str | None:
    """Devuelve el MIME real según los magic bytes, o None si no se reconoce.

    Cuando el prefijo es ambiguo se consultan los bytes siguientes:

    * `RIFF` → bytes 8–12: `WAVE` (audio) o `WEBP` (imagen).
    * cajas ISO (`....ftyp` o `....moov`) → contenedor MP4/MOV.
    """
    cabecera = contenido[:BYTES_INSPECCION]

    # Contenedores RIFF: hay que mirar dentro para saber si es audio o imagen.
    if cabecera.startswith(b"RIFF"):
        formato = cabecera[8:12]
        return FORMATO_RIFF.get(formato)

    # Cajas ISO: el tamaño ocupa los primeros 4 bytes y luego viene la marca.
    if len(cabecera) >= 12 and cabecera[4:8] in (MARCA_FTYP, b"moov"):
        return _tipo_contenedor_iso(cabecera)

    for mime, firmas in FIRMAS_PERMITIDAS.items():
        if any(cabecera.startswith(firma) for firma in firmas):
            return mime

    return None


def _tipo_contenedor_iso(cabecera: bytes) -> str:
    """Distingue MP4/M4A de MOV dentro de un contenedor ISO-BMFF.

    Los archivos de Apple declaran `qt  ` en la marca de compatibilidad; el
    resto se considera MP4 (que cubre también M4A, audio en contenedor MP4).
    """
    return "video/quicktime" if b"qt  " in cabecera else "video/mp4"


def validar_archivo(
    nombre: str,
    contenido: bytes,
    *,
    tipos_permitidos: set[str],
    max_bytes: int,
) -> ArchivoValidado:
    """Valida nombre, tamaño y contenido real de un archivo subido.

    Args:
        nombre: nombre original declarado por el cliente (informativo).
        contenido: bytes completos del archivo.
        tipos_permitidos: conjunto de MIME aceptados para esta herramienta.
        max_bytes: tamaño máximo permitido en bytes.

    Returns:
        ArchivoValidado con el tipo real detectado.

    Raises:
        ArchivoInvalidoError: si el archivo no cumple alguna regla.
    """
    if not contenido:
        raise ArchivoInvalidoError("El archivo está vacío.")

    if len(contenido) > max_bytes:
        limite_mb = max_bytes // (1024 * 1024)
        raise ArchivoInvalidoError(
            f"El archivo excede el límite de {limite_mb} MB."
        )

    if not nombre or not nombre.strip():
        raise ArchivoInvalidoError("El archivo no tiene nombre.")

    # Rechazar rutas en el nombre: el cliente no decide dónde se guarda.
    if any(sep in nombre for sep in ("/", "\\", "..", "\x00")):
        raise ArchivoInvalidoError("El nombre del archivo contiene caracteres no permitidos.")

    tipo_real = detectar_tipo(contenido)
    if tipo_real is None:
        raise ArchivoInvalidoError(
            "No se pudo reconocer el tipo real del archivo. "
            "¿Está corrupto o es un formato no soportado?"
        )

    if tipo_real not in tipos_permitidos:
        permitidos_legibles = ", ".join(sorted(tipos_permitidos))
        raise ArchivoInvalidoError(
            f"Tipo de archivo no permitido ({tipo_real}). "
            f"Se aceptan: {permitidos_legibles}."
        )

    logger.info(
        "Archivo validado: nombre=%r tipo=%s bytes=%d",
        nombre,
        tipo_real,
        len(contenido),
    )
    return ArchivoValidado(
        nombre=nombre,
        tipo_detectado=tipo_real,
        tamano_bytes=len(contenido),
        contenido=contenido,
    )


def validar_cantidad_pdf(paginas: int, max_paginas: int) -> None:
    """Comprueba que un PDF no exceda el número de páginas permitido.

    Defensa contra PDFs gigantes que agotarían memoria en el OCR.
    """
    if paginas > max_paginas:
        raise ArchivoInvalidoError(
            f"El PDF tiene {paginas} páginas y el máximo son {max_paginas}."
        )
