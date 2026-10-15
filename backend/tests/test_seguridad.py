"""Pruebas de la validación de archivos.

Es el módulo más crítico del portal: es la frontera de confianza. Si esto
falla, un archivo malicioso llega a los scripts.
"""

from __future__ import annotations

import pytest

from app.core.seguridad import (
    ArchivoInvalidoError,
    detectar_tipo,
    validar_archivo,
    validar_cantidad_pdf,
)

# --- Muestras de archivos reales (cabeceras válidas) ------------------------
PDF = b"%PDF-1.7\n" + b"x" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100
MP3 = b"ID3\x03\x00" + b"x" * 100
TEXTO_PLANO = b"esto no es un archivo binario valido"

# Contenedores RIFF: mismos 4 primeros bytes, contenido distinto.
# Se distinguen por los bytes 8-12 (WAVE vs WEBP).
WAV = b"RIFF\x24\x08\x00\x00WAVEfmt " + b"x" * 100
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"x" * 100

# Cajas ISO-BMFF: MP4 y MOV comparten prefijo; MOV declara "qt  ".
MP4 = b"\x00\x00\x00\x20ftypisom" + b"x" * 100
MOV = b"\x00\x00\x00\x14ftypqt  " + b"x" * 100


class TestDetectarTipo:
    """La detección debe basarse en contenido, nunca en la extensión."""

    def test_detecta_pdf(self) -> None:
        assert detectar_tipo(PDF) == "application/pdf"

    def test_detecta_png(self) -> None:
        assert detectar_tipo(PNG) == "image/png"

    def test_detecta_jpeg(self) -> None:
        assert detectar_tipo(JPEG) == "image/jpeg"

    def test_detecta_mp3(self) -> None:
        assert detectar_tipo(MP3) == "audio/mpeg"

    def test_contenido_desconocido_devuelve_none(self) -> None:
        assert detectar_tipo(TEXTO_PLANO) is None

    def test_contenido_vacio_devuelve_none(self) -> None:
        assert detectar_tipo(b"") is None


class TestValidarArchivo:
    """Reglas de aceptación de archivos subidos."""

    def test_acepta_pdf_cuando_esta_permitido(self) -> None:
        resultado = validar_archivo(
            "documento.pdf", PDF, tipos_permitidos={"application/pdf"}, max_bytes=1024
        )
        assert resultado.tipo_detectado == "application/pdf"
        assert resultado.tamano_bytes == len(PDF)

    def test_rechaza_archivo_vacio(self) -> None:
        with pytest.raises(ArchivoInvalidoError, match="vacío"):
            validar_archivo(
                "x.pdf", b"", tipos_permitidos={"application/pdf"}, max_bytes=1024
            )

    def test_rechaza_archivo_demasiado_grande(self) -> None:
        grande = b"%PDF-1.7\n" + b"x" * 2048
        with pytest.raises(ArchivoInvalidoError, match="límite"):
            validar_archivo(
                "grande.pdf",
                grande,
                tipos_permitidos={"application/pdf"},
                max_bytes=1024,
            )

    def test_rechaza_tipo_no_permitido(self) -> None:
        with pytest.raises(ArchivoInvalidoError, match="no permitido"):
            validar_archivo(
                "imagen.png", PNG, tipos_permitidos={"application/pdf"}, max_bytes=1024
            )

    def test_rechaza_contenido_no_reconocible(self) -> None:
        with pytest.raises(ArchivoInvalidoError, match="reconocer"):
            validar_archivo(
                "falso.pdf",
                TEXTO_PLANO,
                tipos_permitidos={"application/pdf"},
                max_bytes=1024,
            )

    @pytest.mark.parametrize(
        "nombre_malicioso",
        [
            "../escape.pdf",
            "..\\escape.pdf",
            "/etc/passwd",
            "sub/dir.pdf",
            "",
            "   ",
        ],
    )
    def test_rechaza_nombres_peligrosos(self, nombre_malicioso: str) -> None:
        """El cliente no decide dónde se guarda el archivo."""
        with pytest.raises(ArchivoInvalidoError):
            validar_archivo(
                nombre_malicioso,
                PDF,
                tipos_permitidos={"application/pdf"},
                max_bytes=1024,
            )

    def test_ignora_extension_mentirosa(self) -> None:
        """Un .pdf que en realidad es PNG debe detectarse como PNG."""
        resultado = validar_archivo(
            "mentira.pdf",
            PNG,
            tipos_permitidos={"application/pdf", "image/png"},
            max_bytes=1024,
        )
        assert resultado.tipo_detectado == "image/png"


class TestValidarCantidadPdf:
    """Defensa contra PDFs que agotarían memoria en el OCR."""

    def test_acepta_dentro_del_limite(self) -> None:
        validar_cantidad_pdf(100, max_paginas=500)

    def test_acepta_justo_en_el_limite(self) -> None:
        validar_cantidad_pdf(500, max_paginas=500)

    def test_rechaza_por_encima_del_limite(self) -> None:
        with pytest.raises(ArchivoInvalidoError, match="máximo"):
            validar_cantidad_pdf(501, max_paginas=500)


class TestFormatosAmbiguos:
    """Regresión: formatos que comparten magic bytes deben distinguirse.

    `RIFF` lo usan WAV y WEBP; sin mirar los bytes 8-12, un archivo de audio
    subido a una herramienta de audio se rechazaba como "image/webp".
    """

    def test_wav_se_detecta_como_audio(self) -> None:
        assert detectar_tipo(WAV) == "audio/wav"

    def test_webp_se_detecta_como_imagen(self) -> None:
        assert detectar_tipo(WEBP) == "image/webp"

    def test_wav_pasa_validacion_de_herramienta_de_audio(self) -> None:
        resultado = validar_archivo(
            "grabacion.wav",
            WAV,
            tipos_permitidos={"audio/wav", "audio/mpeg"},
            max_bytes=1024,
        )
        assert resultado.tipo_detectado == "audio/wav"

    def test_webp_no_pasa_validacion_de_audio(self) -> None:
        with pytest.raises(ArchivoInvalidoError, match="no permitido"):
            validar_archivo(
                "imagen.webp",
                WEBP,
                tipos_permitidos={"audio/wav", "audio/mpeg"},
                max_bytes=1024,
            )

    def test_mp4_y_mov_se_distinguen(self) -> None:
        assert detectar_tipo(MP4) == "video/mp4"
        assert detectar_tipo(MOV) == "video/quicktime"

    def test_riff_desconocido_no_se_acepta(self) -> None:
        """Un RIFF que no es WAVE ni WEBP no debe pasar como ninguno de los dos."""
        assert detectar_tipo(b"RIFF\x00\x00\x00\x00AVI ") is None
