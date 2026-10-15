"""Pruebas de la herramienta «Analizar imagen».

No se llama a Gemini: eso consumiría cuota real. Se prueban las partes
deterministas —validación de entrada, disponibilidad del motor y traducción de
errores— con el ejecutor de scripts sustituido por un doble.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services import vision


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


@pytest.fixture
def imagen_png() -> bytes:
    """PNG mínimo válido (cabecera real, no una extensión disfrazada)."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class TestDisponibilidad:
    def test_sin_clave_no_esta_disponible(self) -> None:
        settings = Settings(gemini_api_key=None)
        disponible, motivo = vision.disponible(settings)

        assert disponible is False
        assert "GEMINI_API_KEY" in motivo

    def test_sin_entorno_avisa_del_montaje(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = Settings(gemini_api_key="clave-de-prueba")
        monkeypatch.setattr(
            "app.services.puente.entorno_openclaw_disponible",
            lambda _s=None: (False, "Sin venv."),
        )

        disponible, motivo = vision.disponible(settings)

        assert disponible is False
        assert motivo == "Sin venv."


class TestSufijos:
    def test_todo_tipo_aceptado_tiene_sufijo(self) -> None:
        # Un tipo sin sufijo reventaría al guardar la subida.
        assert set(vision.SUFIJO_POR_TIPO) == vision.TIPOS_IMAGEN


class TestRouterVision:
    def test_estado_expone_modelo_y_tipos(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/vision/estado")

        assert respuesta.status_code == 200
        datos = respuesta.json()
        assert "disponible" in datos
        assert "modelo" in datos
        assert isinstance(datos["tipos"], list)

    def test_rechaza_no_imagen(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/vision",
            files={"archivo": ("notas.txt", b"hola mundo", "text/plain")},
        )

        # 400 si el motor está disponible; 503 si el despliegue no lo tiene.
        assert respuesta.status_code in (400, 503)

    def test_rechaza_png_disfrazado(self, cliente: TestClient) -> None:
        """La extensión no basta: el contenido manda."""
        respuesta = cliente.post(
            "/api/vision",
            files={"archivo": ("foto.png", b"esto no es un png", "image/png")},
        )

        assert respuesta.status_code in (400, 503)


class TestAnalizar:
    def test_propaga_el_error_del_script(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Un 'ERROR:' en stdout debe llegar como ErrorVision, no como éxito."""
        settings = Settings(gemini_api_key="clave-de-prueba")

        monkeypatch.setattr(vision, "disponible", lambda _s=None: (True, ""))
        monkeypatch.setattr(vision.puente, "copiar_script", lambda _n, _s=None: tmp_path / "a.py")

        class Resultado:
            stdout = "ERROR: cuota agotada"
            stderr = ""
            codigo = 1
            texto = "ERROR: cuota agotada"

        monkeypatch.setattr(
            vision.puente, "ejecutar_python", lambda *a, **k: type("R", (), {
                "stdout": "ERROR: cuota agotada",
                "stderr": "",
                "codigo": 1,
                "texto": "ERROR: cuota agotada",
                "ok": False,
            })()
        )

        with pytest.raises(vision.ErrorVision, match="cuota agotada"):
            vision.analizar(tmp_path / "img.png", "", settings=settings)

    def test_devuelve_texto_limpio(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        settings = Settings(gemini_api_key="clave-de-prueba")

        monkeypatch.setattr(vision, "disponible", lambda _s=None: (True, ""))
        monkeypatch.setattr(vision.puente, "copiar_script", lambda _n, _s=None: tmp_path / "a.py")
        monkeypatch.setattr(
            vision.puente, "ejecutar_python", lambda *a, **k: type("R", (), {
                "stdout": "  Un gato sobre una mesa.  \n",
                "stderr": "[Vision] ok",
                "codigo": 0,
                "texto": "Un gato sobre una mesa.",
                "ok": True,
            })()
        )

        assert vision.analizar(tmp_path / "img.png", "describe", settings=settings) == (
            "Un gato sobre una mesa."
        )

    def test_salida_vacia_es_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        settings = Settings(gemini_api_key="clave-de-prueba")

        monkeypatch.setattr(vision, "disponible", lambda _s=None: (True, ""))
        monkeypatch.setattr(vision.puente, "copiar_script", lambda _n, _s=None: tmp_path / "a.py")
        monkeypatch.setattr(
            vision.puente, "ejecutar_python", lambda *a, **k: type("R", (), {
                "stdout": "", "stderr": "", "codigo": 0, "texto": "", "ok": True,
            })()
        )

        with pytest.raises(vision.ErrorVision, match="respuesta vacía"):
            vision.analizar(tmp_path / "img.png", "", settings=settings)
