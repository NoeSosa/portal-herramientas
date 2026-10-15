"""Pruebas de la herramienta «Identificar canción».

Sin red: se sustituye el ejecutor de scripts por un doble que devuelve salidas
representativas (JSON limpio, con ruido, sin coincidencia).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services import cancion


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


@pytest.fixture
def audio_mp3() -> bytes:
    """MP3 mínimo válido (cabecera ID3)."""
    return b"ID3\x04\x00\x00" + b"\x00" * 64


def _resultado(stdout: str, stderr: str = "", codigo: int = 0):
    """Doble de `puente.ejecutar_python`."""
    return type(
        "R",
        (),
        {
            "stdout": stdout,
            "stderr": stderr,
            "codigo": codigo,
            "texto": stdout.strip(),
            "ok": codigo == 0,
        },
    )()


class TestExtraerJson:
    def test_json_limpio(self) -> None:
        assert cancion._extraer_json('{"a": 1}') == {"a": 1}

    def test_json_con_ruido_delante(self) -> None:
        salida = 'aviso: algo\n{"identificacion": {"titulo": "X"}}\n'
        assert cancion._extraer_json(salida) == {"identificacion": {"titulo": "X"}}

    def test_salida_no_json(self) -> None:
        assert cancion._extraer_json("no hay json aquí") is None

    def test_salida_vacia(self) -> None:
        assert cancion._extraer_json("") is None

    def test_llaves_incompletas(self) -> None:
        assert cancion._extraer_json('{"a": 1') is None


class TestSufijos:
    def test_todo_tipo_aceptado_tiene_sufijo(self) -> None:
        assert set(cancion.SUFIJO_POR_TIPO) == cancion.TIPOS_AUDIO


class TestDisponibilidad:
    def test_sin_entorno_avisa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.puente.entorno_openclaw_disponible",
            lambda _s=None: (False, "Sin venv."),
        )
        disponible, motivo = cancion.disponible(Settings())

        assert disponible is False
        assert motivo == "Sin venv."


class TestIdentificar:
    @pytest.fixture(autouse=True)
    def _entorno(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setattr(cancion, "disponible", lambda _s=None: (True, ""))
        monkeypatch.setattr(
            cancion.puente, "copiar_script", lambda _n, _s=None: tmp_path / "identify_song.py"
        )

    def test_devuelve_identificacion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setattr(
            cancion.puente,
            "ejecutar_python",
            lambda *a, **k: _resultado(
                '{"identificacion": {"titulo": "Tema", "artista": "Grupo"},'
                ' "bajo_copyright": true}'
            ),
        )

        datos = cancion.identificar(tmp_path / "a.mp3", settings=Settings())

        assert datos["identificacion"]["titulo"] == "Tema"
        assert datos["bajo_copyright"] is True

    def test_sin_coincidencia_es_error_propio(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setattr(
            cancion.puente,
            "ejecutar_python",
            lambda *a, **k: _resultado('{"identificacion": null}'),
        )

        with pytest.raises(cancion.SinCoincidencia):
            cancion.identificar(tmp_path / "a.mp3", settings=Settings())

    def test_error_del_script_se_propaga(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setattr(
            cancion.puente,
            "ejecutar_python",
            lambda *a, **k: _resultado('{"error": "No se encuentra el archivo"}'),
        )

        with pytest.raises(cancion.ErrorCancion, match="No se encuentra"):
            cancion.identificar(tmp_path / "a.mp3", settings=Settings())

    def test_salida_ilegible(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setattr(
            cancion.puente, "ejecutar_python", lambda *a, **k: _resultado("boom", codigo=1)
        )

        with pytest.raises(cancion.ErrorCancion, match="ilegible"):
            cancion.identificar(tmp_path / "a.mp3", settings=Settings())


class TestRouterCancion:
    def test_estado_expone_tipos(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/cancion/estado")

        assert respuesta.status_code == 200
        assert isinstance(respuesta.json()["tipos"], list)

    def test_rechaza_texto_plano(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/cancion",
            files={"archivo": ("notas.txt", b"no es audio", "text/plain")},
        )

        assert respuesta.status_code in (400, 503)

    def test_rechaza_mp3_disfrazado(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/cancion",
            files={"archivo": ("tema.mp3", b"contenido falso", "audio/mpeg")},
        )

        assert respuesta.status_code in (400, 503)
