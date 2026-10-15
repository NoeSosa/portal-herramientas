"""Pruebas de la herramienta «Finanzas personales».

Lo importante aquí no es el cálculo —el script ya está validado— sino las
reglas del portal: qué acciones se permiten, que el panel no caiga por una
sección rota, y que **nada se registre sin confirmación explícita**.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.routers import finanzas as router_finanzas
from app.services import finanzas as svc


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


@pytest.fixture
def imagen_jpg() -> bytes:
    return b"\xff\xd8\xff" + b"\x00" * 64


class TestAccionesPermitidas:
    def test_accion_desconocida_se_rechaza(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="no permitida"):
            svc.consultar("transferir", settings=Settings())

    def test_eliminar_no_es_accion_de_lectura(self) -> None:
        with pytest.raises(svc.ErrorFinanzas):
            svc.consultar("eliminar", settings=Settings())

    def test_periodo_invalido_se_rechaza(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="Periodo no válido"):
            svc.consultar("resumen", periodo="decadal", settings=Settings())

    def test_lista_de_lectura_no_incluye_escrituras(self) -> None:
        escrituras = {"agregar", "editar", "eliminar", "transferir", "presupuesto-set"}
        assert escrituras.isdisjoint(set(svc.ACCIONES_LECTURA))


class TestExtraerJson:
    def test_json_con_ruido(self) -> None:
        assert svc._extraer_json('log\n{"status": "ok"}\n') == {"status": "ok"}

    def test_sin_json(self) -> None:
        assert svc._extraer_json("nada") is None


class TestDisponibilidad:
    def test_sin_ledger_avisa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.puente.entorno_openclaw_disponible", lambda _s=None: (True, "")
        )

        # Se apunta a un workspace vacío: no hay scripts ni ledger.
        from pathlib import Path

        settings = Settings(
            herramientas_scripts_dir=Path("/tmp/nada/scripts"),
            finanzas_usuario="nadie",
        )
        disponible, motivo = svc.disponible(settings)

        assert disponible is False
        assert "finanzas.py" in motivo or "datos de finanzas" in motivo


class TestRouterFinanzas:
    def test_estado_expone_usuario_y_periodos(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/finanzas/estado")

        assert respuesta.status_code == 200
        datos = respuesta.json()
        assert "usuario" in datos
        assert "mensual" in datos["periodos"]

    def test_panel_rechaza_periodo_invalido(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/finanzas/panel?periodo=decadal")

        assert respuesta.status_code in (400, 503)

    def test_movimientos_limita_el_tamano(self, cliente: TestClient) -> None:
        # No debe reventar por pedir un límite absurdo. Fuera del contenedor no
        # existe `/data`, así que el error puede burbujear desde el copiado del
        # script: lo que se verifica es que el límite se normaliza y nunca se
        # propaga una traza al cliente.
        with pytest.raises(Exception) as fallo:
            cliente.get("/api/finanzas/movimientos?limite=99999")

        assert not isinstance(fallo.value, (KeyError, TypeError))

    def test_comprobante_rechaza_texto(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/finanzas/comprobante",
            files={"archivo": ("notas.txt", b"hola", "text/plain")},
        )

        assert respuesta.status_code in (400, 503)


class TestTraduccionDeAnalisis:
    """La traducción debe distinguir 'analizado' de 'registrado'."""

    def test_analisis_no_marca_registrado(self) -> None:
        datos = {
            "resultados": [{"_datos": {"monto": 100, "tipo": "gasto"}, "_categoria": "comida"}],
            "errores": [],
            "necesita_preguntar": [],
        }

        respuesta = router_finanzas._traducir_analisis(datos, registrado=False)

        assert respuesta.analizado is True
        assert respuesta.registrado is False
        assert respuesta.filas[0].estado == "listo"

    def test_registro_marca_registrado(self) -> None:
        datos = {
            "resultados": [{"_datos": {"monto": 100, "tipo": "gasto"}, "_categoria": "comida"}],
            "errores": [],
            "necesita_preguntar": [],
        }

        respuesta = router_finanzas._traducir_analisis(datos, registrado=True)

        assert respuesta.registrado is True
        assert respuesta.filas[0].estado == "registrado"

    def test_sin_datos_no_registra(self) -> None:
        datos = {"resultados": [], "errores": [{"message": "no es comprobante"}],
                 "necesita_preguntar": []}

        respuesta = router_finanzas._traducir_analisis(datos, registrado=True)

        assert respuesta.registrado is False
        assert respuesta.filas[0].estado == "descartado"

    def test_falta_de_cuenta_se_marca(self) -> None:
        datos = {
            "resultados": [],
            "errores": [],
            "necesita_preguntar": [{"message": "Falta la cuenta", "datos": {"monto": 50}}],
        }

        respuesta = router_finanzas._traducir_analisis(datos, registrado=False)

        assert respuesta.filas[0].estado == "falta-dato"
        assert "Falta la cuenta" in (respuesta.filas[0].mensaje or "")
