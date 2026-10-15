"""Pruebas de la herramienta «Transcribir audio o video».

Se cubren las partes deterministas: validación de entrada, traducción de
estados, seguridad de rutas y contrato de la tarea de RQ. La transcripción real
(tarda minutos y consume cuota) no se ejecuta aquí.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.transcribir import _estado_rq, _validar_job_id
from app.services import transcripcion


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


class TestValidarJobId:
    def test_acepta_hex(self) -> None:
        _validar_job_id("a1b2c3d4e5f6")

    @pytest.mark.parametrize(
        "invalido",
        ["", "x" * 65, "../../etc/passwd", "job con espacios", "job;rm -rf /"],
    )
    def test_rechaza_peligrosos(self, invalido: str) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validar_job_id(invalido)


class TestEstadoRq:
    @pytest.mark.parametrize(
        ("rq", "esperado"),
        [
            ("queued", "queued"),
            ("deferred", "queued"),
            ("started", "started"),
            ("finished", "finished"),
            ("failed", "failed"),
            ("stopped", "failed"),
            ("canceled", "failed"),
            ("inventado", "queued"),
        ],
    )
    def test_mapea_estados(self, rq: str, esperado: str) -> None:
        assert _estado_rq(rq) == esperado


class TestSufijos:
    def test_todo_tipo_tiene_sufijo(self) -> None:
        assert set(transcripcion.SUFIJO_POR_TIPO) == transcripcion.TIPOS_AUDIO_VIDEO

    def test_formatos_con_extraccion_declarados(self) -> None:
        # Los formatos con extracción de audio deben ser contenedores que este
        # portal acepta: si no, la UI avisaría de algo que nunca puede llegar.
        contenedores_aceptados = set(transcripcion.SUFIJO_POR_TIPO.values()) | {
            ".mkv",
            ".avi",
        }
        assert transcripcion.FORMATOS_CON_EXTRACCION <= contenedores_aceptados


class TestRouterTranscribir:
    def test_estado_expone_motores(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/transcribir/estado")

        assert respuesta.status_code == 200
        datos = respuesta.json()
        assert isinstance(datos["tipos"], list)
        assert "auto" in datos["motores"]

    def test_rechaza_texto_plano(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/transcribir",
            files={"archivo": ("notas.txt", b"hola", "text/plain")},
        )

        assert respuesta.status_code in (400, 503)

    def test_descarga_rechaza_traversal(self, cliente: TestClient) -> None:
        # Starlette normaliza la ruta antes de enrutar, así que un traversal
        # acaba en 404 (no existe) o en 400 (id inválido). Lo que nunca puede
        # pasar es que sirva un archivo de fuera.
        respuesta = cliente.get("/api/transcribir/descargar/..%2F..%2Fetc%2Fpasswd")

        assert respuesta.status_code in (400, 404)

    def test_job_inexistente(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/transcribir/aaaabbbbcccc")

        # 404 si Redis responde; 500 si la cola no está disponible.
        assert respuesta.status_code in (404, 500)


class TestTareaTranscripcion:
    def test_importable_por_el_worker(self) -> None:
        from app.jobs.tareas import procesar_transcripcion

        assert callable(procesar_transcripcion)

    def test_firma_estable(self) -> None:
        """Los nombres importan: RQ inyecta `job_id` y colisionaría.

        Ver el comentario en `procesar_separador`: usar `job_id` como parámetro
        propio rompe la ejecución con un TypeError de argumento faltante.
        """
        from app.jobs.tareas import procesar_transcripcion

        parametros = list(inspect.signature(procesar_transcripcion).parameters)
        assert parametros == ["ruta_entrada", "id_trabajo", "idioma", "motor"]
        assert "job_id" not in parametros

    def test_fallo_devuelve_dict_sin_lanzar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """RQ registraría la traza completa; aquí el error debe ser legible."""
        from app.core.config import Settings
        from app.jobs import tareas

        # Se apunta el trabajo a un directorio temporal: la prueba nunca debe
        # escribir en /data, que no existe fuera del contenedor.
        monkeypatch.setattr(
            tareas,
            "obtener_settings",
            lambda: Settings(
                data_dir=tmp_path,
                upload_dir=tmp_path / "uploads",
                output_dir=tmp_path / "outputs",
            ),
        )
        monkeypatch.setattr(
            "app.services.transcripcion.disponible", lambda _s=None: (False, "Sin venv.")
        )

        resultado = tareas.procesar_transcripcion(
            str(tmp_path / "audio.mp3"), "job123", None, "auto"
        )

        assert resultado["ok"] is False
        assert "Sin venv" in str(resultado["error"])


class TestIdDelJob:
    """Regresión: el id devuelto debe ser el del job de RQ.

    Antes se generaba un id corto propio y se devolvía al cliente, pero RQ
    guarda el job bajo su propio uuid: consultar el estado con el id corto
    respondía 404 aunque el trabajo hubiera terminado bien.
    """

    def test_encolar_devuelve_el_id_de_rq(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.routers import transcribir as router

        ajustes = _SettingsPrueba(tmp_path)
        ajustes.preparar_directorios()
        monkeypatch.setattr(router, "obtener_settings", lambda: ajustes)
        monkeypatch.setattr(
            router.transcripcion, "disponible", lambda _s=None: (True, "")
        )

        recibido: dict = {}

        class JobFalso:
            id = "uuid-real-de-rq-123"

        class ColaFalsa:
            def enqueue(self, *args, **kwargs):
                recibido.update(kwargs)
                return JobFalso()

        monkeypatch.setattr(router, "cola_pesada", lambda: ColaFalsa())

        respuesta = cliente.post(
            "/api/transcribir",
            files={"archivo": ("a.mp3", b"ID3\x04\x00\x00" + b"\x00" * 64, "audio/mpeg")},
        )

        assert respuesta.status_code == 202
        assert respuesta.json()["job_id"] == "uuid-real-de-rq-123"

        # El id propio debe ser el MISMO que el del job: así el worker escribe
        # la salida donde luego la busca la descarga.
        assert recibido["job_id"] == recibido["id_trabajo"]


class _SettingsPrueba:
    """Configuración mínima para no escribir en /data durante las pruebas."""

    def __init__(self, base: Path) -> None:
        self.upload_dir = base / "uploads"
        self.output_dir = base / "outputs"
        self.data_dir = base
        self.max_upload_bytes = 10 * 1024 * 1024
        self.job_timeout_seconds = 60
        self.gemini_api_key = None
        self.finanzas_usuario = "noe"

    def preparar_directorios(self) -> None:
        for ruta in (self.upload_dir, self.output_dir):
            ruta.mkdir(parents=True, exist_ok=True)
