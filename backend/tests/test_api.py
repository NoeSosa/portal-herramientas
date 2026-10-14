"""Pruebas de los endpoints de la API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def cliente() -> TestClient:
    """Cliente de pruebas contra la app real."""
    return TestClient(app)


class TestHealth:
    def test_health_responde_ok(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/health")
        assert respuesta.status_code == 200
        assert respuesta.json()["status"] == "ok"

    def test_info_expone_limites(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/info")
        assert respuesta.status_code == 200
        datos = respuesta.json()
        assert "limites" in datos
        assert "capacidades" in datos
        assert isinstance(datos["limites"]["max_upload_mb"], int)


class TestTTS:
    def test_voz_invalida_devuelve_400(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/tts", json={"texto": "hola", "voz": "noexiste"}
        )
        assert respuesta.status_code == 400
        assert "Voz no válida" in respuesta.json()["detail"]

    def test_texto_vacio_devuelve_422(self, cliente: TestClient) -> None:
        """Pydantic rechaza el texto vacío antes de llegar a la lógica."""
        respuesta = cliente.post("/api/tts", json={"texto": "", "voz": "ald"})
        assert respuesta.status_code == 422

    def test_texto_demasiado_largo_devuelve_422(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/tts", json={"texto": "x" * 50_000, "voz": "ald"}
        )
        assert respuesta.status_code == 422


class TestDescargaTTS:
    """El endpoint de descarga no debe permitir leer rutas arbitrarias."""

    @pytest.mark.parametrize(
        "nombre",
        [
            "../../etc/passwd",
            "noempieza-por-tts.wav",
            "tts-algo.txt",
            "tts-algo.wav/../../x",
        ],
    )
    def test_rechaza_nombres_no_validos(
        self, cliente: TestClient, nombre: str
    ) -> None:
        respuesta = cliente.get(f"/api/tts/{nombre}")
        assert respuesta.status_code in (400, 404)

    def test_archivo_inexistente_devuelve_404(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/tts/tts-noexiste123.wav")
        assert respuesta.status_code == 404
