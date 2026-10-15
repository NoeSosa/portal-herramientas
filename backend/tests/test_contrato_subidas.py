"""Contrato de subida entre el frontend y los endpoints.

Regresión del bug «Field required»: el frontend enviaba el archivo bajo el
campo `archivos` (plural) mientras **todos** los endpoints lo declaran como
`archivo` (singular). FastAPI devolvía 422 y la UI mostraba «Field required»
justo después de la animación de subida.

Estas pruebas fijan el nombre del campo para que un renombrado en cualquiera de
los dos lados falle aquí en vez de en el navegador del usuario.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

#: Campos por endpoint que reciben archivos. El nombre es parte del contrato:
#: `frontend/src/lib/api.ts` (`subirArchivos`) debe coincidir exactamente.
ENDPOINTS_CON_ARCHIVO = [
    ("/api/separador", "application/pdf", b"%PDF-1.7\n"),
    ("/api/transcribir", "audio/mpeg", b"ID3\x04\x00\x00"),
    ("/api/vision", "image/png", b"\x89PNG\r\n\x1a\n"),
    ("/api/cancion", "audio/mpeg", b"ID3\x04\x00\x00"),
    ("/api/finanzas/comprobante", "image/png", b"\x89PNG\r\n\x1a\n"),
]


@pytest.fixture
def cliente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Cliente con el almacenamiento apuntando a un directorio temporal.

    Fuera del contenedor no existe `/data`, así que escribir la subida fallaría
    con PermissionError y la prueba no mediría lo que quiere medir (el nombre
    del campo), sino el entorno.
    """
    ajustes = _SettingsPrueba(tmp_path)
    ajustes.preparar_directorios()

    for modulo in (
        "app.routers.separador",
        "app.routers.transcribir",
        "app.routers.vision",
        "app.routers.cancion",
        "app.routers.finanzas",
    ):
        monkeypatch.setattr(f"{modulo}.obtener_settings", lambda: ajustes, raising=False)

    return TestClient(app)


class _SettingsPrueba:
    """Configuración mínima escribible en un directorio temporal."""

    def __init__(self, base: Path) -> None:
        self.upload_dir = base / "uploads"
        self.output_dir = base / "outputs"
        self.data_dir = base
        self.max_upload_bytes = 10 * 1024 * 1024
        self.job_timeout_seconds = 60
        self.max_upload_mb = 10
        self.gemini_api_key = None
        self.finanzas_usuario = "noe"
        self.herramientas_scripts_dir = base / "no-existe"

    def preparar_directorios(self) -> None:
        for ruta in (self.upload_dir, self.output_dir):
            ruta.mkdir(parents=True, exist_ok=True)


class TestCampoArchivo:
    @pytest.mark.parametrize(("ruta", "tipo", "firma"), ENDPOINTS_CON_ARCHIVO)
    def test_el_campo_se_llama_archivo(
        self, cliente: TestClient, ruta: str, tipo: str, firma: bytes
    ) -> None:
        """Con el nombre correcto la petición NO puede fallar por campo faltante."""
        contenido = firma + b"\x00" * 64

        respuesta = cliente.post(
            ruta,
            files={"archivo": ("prueba.bin", contenido, tipo)},
        )

        # El resultado de negocio varía (503 sin venv, 502 si falla el script,
        # o un error de entorno porque `/data` no existe fuera del contenedor),
        # pero nunca un 422 de validación: el archivo SÍ llegó.
        assert respuesta.status_code != 422, (
            f"{ruta} devolvió 422 con el campo 'archivo': "
            f"{respuesta.json()}. ¿Se renombró el campo en el backend?"
        )

    @pytest.mark.parametrize(("ruta", "tipo", "firma"), ENDPOINTS_CON_ARCHIVO)
    def test_el_campo_plural_no_es_valido(
        self, cliente: TestClient, ruta: str, tipo: str, firma: bytes
    ) -> None:
        """Documenta el fallo original: `archivos` produce 422 «Field required».

        Si esta prueba empieza a devolver 202/200 significa que el backend
        acepta los dos nombres, y entonces conviene decidir cuál es el bueno en
        vez de mantener los dos.
        """
        respuesta = cliente.post(
            ruta,
            files={"archivos": ("prueba.bin", firma + b"\x00" * 64, tipo)},
        )

        assert respuesta.status_code == 422
        assert "Field required" in respuesta.text
