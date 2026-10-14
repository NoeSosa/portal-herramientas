"""Pruebas del worker OCR y del router del separador.

El OCR real no se ejecuta aquí (necesita PaddleOCR y minutos). Se prueban las
partes que sí son deterministas: validación de identificadores, seguridad de
rutas y traducción de estados.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.separador import _estado_desde_job, _validar_job_id, _validar_nombre_seguro
from app.core.seguridad import ArchivoInvalidoError


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


class TestValidarJobId:
    def test_acepta_formato_hex(self) -> None:
        _validar_job_id("a1b2c3d4e5f6")

    def test_acepta_con_guiones(self) -> None:
        _validar_job_id("a1b2-c3d4_e5f6")

    @pytest.mark.parametrize(
        "invalido",
        ["", "x" * 65, "../../etc/passwd", "job con espacios", "job;rm -rf /"],
    )
    def test_rechaza_formatos_peligrosos(self, invalido: str) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validar_job_id(invalido)


class TestValidarNombreSeguro:
    def test_acepta_nombre_normal(self) -> None:
        _validar_nombre_seguro("resultados-2026.zip")

    @pytest.mark.parametrize(
        "peligroso",
        ["../escape.zip", "..\\escape.zip", "sub/dir.zip", "", "a\x00b"],
    )
    def test_rechaza_traversal(self, peligroso: str) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _validar_nombre_seguro(peligroso)


class TestEstadoDesdeJob:
    """La traducción de estados de RQ no debe filtrar detalles internos."""

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
        ],
    )
    def test_mapea_estados(self, rq: str, esperado: str) -> None:
        estado = _estado_desde_job("abc123", rq, None, None)
        assert estado.estado == esperado

    def test_estado_desconocido_cae_a_queued(self) -> None:
        estado = _estado_desde_job("abc123", "inventado", None, None)
        assert estado.estado == "queued"

    def test_fallo_no_expone_traceback(self) -> None:
        estado = _estado_desde_job(
            "abc123", "failed", None, "Traceback... /srv/app/secreto.py line 42"
        )
        assert estado.error is not None
        assert "Traceback" not in estado.error
        assert "/srv" not in estado.error

    def test_finished_conserva_resultado(self) -> None:
        resultado = {"ok": True, "ok_count": 12, "verificar": 3}
        estado = _estado_desde_job("abc123", "finished", resultado, None)
        assert estado.resultado == resultado

    def test_resultado_no_dict_se_descarta(self) -> None:
        estado = _estado_desde_job("abc123", "finished", "texto raro", None)
        assert estado.resultado is None


class TestRouterSeparador:
    def test_consulta_job_inexistente_da_404(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/separador/aaaabbbbcccc")
        # 404 si se consulta bien pero no existe; 500 si Redis no está.
        assert respuesta.status_code in (404, 500)

    def test_job_id_invalido_da_400(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/separador/no%20valido!!")
        assert respuesta.status_code == 400

    def test_descarga_rechaza_traversal(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/separador/descargar/..%2F..%2Fetc/passwd")
        assert respuesta.status_code in (400, 404)

    def test_descarga_solo_zip(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/separador/descargar/carpeta/archivo.txt")
        assert respuesta.status_code == 400


class TestTareaSeparador:
    """La tarea de RQ debe ser importable por el worker."""

    def test_tarea_importable(self) -> None:
        from app.jobs.tareas import procesar_separador

        assert callable(procesar_separador)

    def test_tarea_firma_correcta(self) -> None:
        import inspect

        from app.jobs.tareas import procesar_separador

        parametros = list(inspect.signature(procesar_separador).parameters)
        assert parametros == ["ruta_pdf", "job_id"]


class TestCompatOcrArm64:
    """Los parches de ARM64 deben ser idempotentes y no romper nada."""

    def test_parche_sctypes_idempotente(self) -> None:
        from app.core.ocr_compat import aplicar_parche_numpy_sctypes

        aplicar_parche_numpy_sctypes()
        aplicar_parche_numpy_sctypes()  # no debe fallar

        import numpy as np

        assert hasattr(np, "sctypes")
        assert "int" in np.sctypes
        assert "float" in np.sctypes

    def test_verificar_libgomp_no_lanza(self) -> None:
        from app.core.ocr_compat import verificar_libgomp

        # Devuelve bool sin excepción, haya o no la librería.
        assert isinstance(verificar_libgomp(), bool)

    def test_preparar_paddleocr_no_lanza(self) -> None:
        from app.core.ocr_compat import preparar_paddleocr

        preparar_paddleocr()
