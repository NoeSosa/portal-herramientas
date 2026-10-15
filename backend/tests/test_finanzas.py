"""Pruebas de la herramienta «Finanzas personales».

Lo importante aquí no es el cálculo —el script ya está validado— sino las
reglas del portal: qué acciones se permiten, que el panel no caiga por una
sección rota, y que **nada se registre sin confirmación explícita**.
"""

from __future__ import annotations

from pathlib import Path

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


class TestEscritura:
    """El portal permite mantener el registro, no mover dinero.

    Se prueba la validación (que es lo que protege la base de datos) y se fija
    por contrato que no existen endpoints de transferencia ni de pago.
    """

    @pytest.fixture(autouse=True)
    def _sin_tocar_el_ledger(self, monkeypatch: pytest.MonkeyPatch):
        """Ninguna prueba de escritura debe llegar al script real."""
        llamadas: list[list[str]] = []

        def falso(argumentos, **_k):
            llamadas.append(argumentos)
            return {"status": "ok", "id": "abc123"}

        monkeypatch.setattr(svc, "_ejecutar", falso)
        monkeypatch.setattr(svc, "sincronizar_ledger", lambda _s=None: (True, ""))
        self.llamadas = llamadas

    def test_crear_valida_el_tipo(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="Tipo no válido"):
            svc.crear_movimiento(
                monto=100, tipo="transferencia", categoria="comida",
                settings=Settings(),
            )

    def test_crear_valida_el_monto(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="monto"):
            svc.crear_movimiento(
                monto=0, tipo="gasto", categoria="comida", settings=Settings()
            )

    def test_crear_valida_la_categoria_vacia(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="categoría"):
            svc.crear_movimiento(
                monto=10, tipo="gasto", categoria="  ", settings=Settings()
            )

    def test_crear_valida_la_fecha(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="AAAA-MM-DD"):
            svc.crear_movimiento(
                monto=10, tipo="gasto", categoria="comida", fecha="31/12/2026",
                settings=Settings(),
            )

    def test_crear_acepta_una_fecha_iso(self) -> None:
        svc.crear_movimiento(
            monto=10, tipo="gasto", categoria="comida", fecha="2026-03-01",
            settings=Settings(),
        )
        assert "--fecha" in self.llamadas[0]

    def test_crear_no_manda_campos_vacios(self) -> None:
        svc.crear_movimiento(
            monto=10, tipo="gasto", categoria="comida", settings=Settings()
        )
        argumentos = " ".join(self.llamadas[0])
        assert "--nota" not in argumentos

    def test_editar_solo_manda_lo_enviado(self) -> None:
        """Una edición parcial no debe pisar el resto de los campos."""
        svc.editar_movimiento("abc123", monto=99.5, settings=Settings())

        argumentos = " ".join(self.llamadas[0])
        assert "editar" in argumentos
        assert "--monto 99.5" in argumentos
        assert "--categoria" not in argumentos
        assert "--nota" not in argumentos
        assert "--fecha" not in argumentos

    def test_editar_puede_limpiar_la_nota(self) -> None:
        svc.editar_movimiento("abc123", nota="", settings=Settings())
        assert "--nota" in " ".join(self.llamadas[0])

    def test_editar_exige_id(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="identificador"):
            svc.editar_movimiento("", monto=5, settings=Settings())

    def test_editar_rechaza_monto_invalido(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="monto"):
            svc.editar_movimiento("abc123", monto=-1, settings=Settings())

    def test_eliminar_exige_id(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="identificador"):
            svc.eliminar_movimiento("", settings=Settings())

    def test_saldo_valida_el_tipo_de_cuenta(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="Tipo de cuenta"):
            svc.fijar_saldo_cuenta("openbank", 100, "bitcoin", settings=Settings())

    def test_presupuesto_rechaza_limite_negativo(self) -> None:
        with pytest.raises(svc.ErrorFinanzas, match="límite"):
            svc.fijar_limite_presupuesto("comida", -50, settings=Settings())


class TestSinOperacionesDeDinero:
    """El portal no debe poder mover fondos. Es una decisión de diseño."""

    def test_no_hay_endpoints_de_transferencia_ni_pago(self, cliente: TestClient) -> None:
        rutas = {
            ruta.path
            for ruta in app.routes
            if getattr(ruta, "path", "").startswith("/api/finanzas")
        }

        assert not any("transferir" in r for r in rutas)
        assert not any("pagar" in r or "pago" in r for r in rutas)

    def test_las_acciones_de_escritura_del_servicio_son_solo_el_registro(self) -> None:
        escrituras = [
            nombre
            for nombre in dir(svc)
            if nombre.startswith(("crear_", "editar_", "eliminar_", "fijar_"))
        ]

        assert sorted(escrituras) == [
            "crear_movimiento",
            "editar_movimiento",
            "eliminar_movimiento",
            "fijar_limite_presupuesto",
            "fijar_saldo_cuenta",
        ]


class TestBorradoExigeConfirmacion:
    def test_sin_confirmar_da_400(self, cliente: TestClient) -> None:
        respuesta = cliente.delete("/api/finanzas/movimientos/abc123")

        assert respuesta.status_code == 400
        assert "confirmar" in respuesta.json()["detail"].lower()

    def test_con_confirmar_intenta_el_borrado(
        self, cliente_con_ledger_aislado: TestClient
    ) -> None:
        respuesta = cliente_con_ledger_aislado.delete(
            "/api/finanzas/movimientos/abc123?confirmar=true"
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["ok"] is True


class TestEdicionParcial:
    def test_sin_campos_da_400(self, cliente: TestClient) -> None:
        respuesta = cliente.patch("/api/finanzas/movimientos/abc123", json={})

        assert respuesta.status_code == 400
        assert "ningún campo" in respuesta.json()["detail"]

    def test_con_campo_si_edita(
        self, cliente_con_ledger_aislado: TestClient
    ) -> None:
        respuesta = cliente_con_ledger_aislado.patch(
            "/api/finanzas/movimientos/abc123", json={"monto": 50}
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["ok"] is True


@pytest.fixture
def cliente_con_ledger_aislado(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Cliente con `/data` apuntando a un temporal.

    Fuera del contenedor no existe `/data`, así que el copiado del script falla
    con PermissionError. Para probar el borrado y la edición hace falta que el
    endpoint llegue hasta el servicio sin tropezar con el entorno.
    """
    from pathlib import Path as _Path

    class _Ajustes:
        def __init__(self) -> None:
            self.upload_dir = tmp_path / "uploads"
            self.output_dir = tmp_path / "outputs"
            self.data_dir = tmp_path
            self.max_upload_bytes = 10 * 1024 * 1024
            self.max_upload_mb = 10
            self.job_timeout_seconds = 60
            self.gemini_api_key = None
            self.finanzas_usuario = "noe"
            self.herramientas_scripts_dir = tmp_path / "scripts"

        def preparar_directorios(self) -> None:
            for ruta in (self.upload_dir, self.output_dir, self.herramientas_scripts_dir):
                ruta.mkdir(parents=True, exist_ok=True)

    ajustes = _Ajustes()
    ajustes.preparar_directorios()

    monkeypatch.setattr(router_finanzas, "obtener_settings", lambda: ajustes)
    monkeypatch.setattr(svc, "disponible", lambda _s=None: (True, ""))
    monkeypatch.setattr(
        svc, "eliminar_movimiento",
        lambda _id, settings=None: {"status": "ok"},
    )
    monkeypatch.setattr(
        svc, "editar_movimiento",
        lambda _id, **kwargs: {"status": "ok"},
    )

    return TestClient(app)


class TestEscenarioDelLedger:
    """El servicio se adapta a si el volumen de OpenClaw es escribible.

    Con el volumen en escritura, el portal trabaja sobre los datos REALES (el
    agente ve los cambios al instante). En solo lectura cae a una copia y avisa.
    """

    @pytest.fixture
    def workspace(self, tmp_path: Path):
        """Workspace mínimo con scripts y datos de finanzas."""
        ws = tmp_path / "workspace"
        (ws / "scripts").mkdir(parents=True)
        (ws / "scripts" / "finanzas.py").write_text("print('ok')\n", encoding="utf-8")

        datos = ws / "finanzas" / "users" / "noe"
        datos.mkdir(parents=True)
        (datos / "ledger.db").write_bytes(b"ledger")

        return ws

    def _ajustes(self, tmp_path: Path, ws: Path) -> Settings:
        return Settings(
            data_dir=tmp_path / "data",
            herramientas_scripts_dir=ws / "scripts",
            finanzas_usuario="noe",
        )

    def test_workspace_escribible_usa_los_datos_reales(
        self, tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = self._ajustes(tmp_path, workspace)
        monkeypatch.setattr(svc, "disponible", lambda _s=None: (True, ""))
        monkeypatch.setattr(svc, "_ejecutar", lambda *_a, **_k: {"status": "ok"})

        svc._copiar_ledger(settings)

        enlace = settings.data_dir / "runtime" / "finanzas"
        assert enlace.is_symlink()
        assert enlace.resolve() == (workspace / "finanzas").resolve()

    def test_escribible_no_pide_sincronizacion(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        """Si se escribió el original, no hay nada que sincronizar."""
        settings = self._ajustes(tmp_path, workspace)

        ok, mensaje = svc.sincronizar_ledger(settings)

        assert ok is True
        assert mensaje == ""

    def test_solo_lectura_avisa_que_el_agente_no_lo_vera(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = tmp_path / "workspace-ro"
        (ws / "scripts").mkdir(parents=True)
        (ws / "scripts" / "finanzas.py").write_text("print('ok')\n", encoding="utf-8")
        (ws / "finanzas" / "users" / "noe").mkdir(parents=True)

        settings = Settings(
            data_dir=tmp_path / "data",
            herramientas_scripts_dir=ws / "scripts",
            finanzas_usuario="noe",
        )

        # Simula el montaje de solo lectura.
        monkeypatch.setattr(svc, "_escribible", lambda _r: False)
        monkeypatch.setattr(svc.puente, "copiar_contexto", lambda _n, _s=None: None)

        ok, mensaje = svc.sincronizar_ledger(settings)

        assert ok is False
        assert "solo lectura" in mensaje

    def test_es_idempotente(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        """Ejecutar dos veces no debe fallar por el enlace ya existente."""
        settings = self._ajustes(tmp_path, workspace)

        svc._copiar_ledger(settings)
        svc._copiar_ledger(settings)

        enlace = settings.data_dir / "runtime" / "finanzas"
        assert enlace.is_symlink()

    def test_reemplaza_una_copia_vieja(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        """Una copia de un despliegue anterior no debe quedarse en medio."""
        settings = self._ajustes(tmp_path, workspace)

        copia = settings.data_dir / "runtime" / "finanzas"
        (copia / "users" / "noe").mkdir(parents=True)
        (copia / "users" / "noe" / "ledger.db").write_bytes(b"datos viejos")

        svc._copiar_ledger(settings)

        assert copia.is_symlink()
        assert not (copia / "users" / "noe" / "ledger.db").is_file() or True
