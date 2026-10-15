"""Pruebas de la administración del ledger.

Lo crítico aquí es que el portal escriba los archivos **con el formato exacto**
que espera `finanzas.py`. Si el portal inventara una clave, el agente dejaría de
entender los datos: es la única forma de corromper los datos del usuario.

Se prueba contra archivos reales en un directorio temporal, así que los datos de
producción no se tocan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services import administracion as admin


@pytest.fixture
def finanzas(tmp_path: Path) -> Settings:
    """Usuario de finanzas con archivos vacíos, como los crea el script."""
    datos = tmp_path / "workspace" / "finanzas" / "users" / "noe"
    datos.mkdir(parents=True)

    for archivo in ("cuentas.json", "tarjetas.json", "presupuesto.json",
                    "metas.json", "recordatorios.json"):
        (datos / archivo).write_text("{}", encoding="utf-8")

    return Settings(
        data_dir=tmp_path / "data",
        herramientas_scripts_dir=tmp_path / "workspace" / "scripts",
        finanzas_usuario="noe",
    )


def _leer(settings: Settings, archivo: str) -> dict:
    ruta = (
        settings.herramientas_scripts_dir.parent
        / "finanzas" / "users" / settings.finanzas_usuario / archivo
    )
    return json.loads(ruta.read_text(encoding="utf-8"))


class TestCuentas:
    def test_crear_y_leer(self, finanzas: Settings) -> None:
        admin.guardar_cuenta("Openbank", 1000.5, "debito", settings=finanzas)

        cuentas = admin.listar_cuentas(finanzas)["cuentas"]
        assert cuentas["openbank"] == {"saldo": 1000.5, "tipo": "debito"}

    def test_normaliza_el_nombre_igual_que_los_script(self, finanzas: Settings) -> None:
        """`finanzas.py` hace .lower(): el portal debe usar la misma clave."""
        admin.guardar_cuenta("Mi Cuenta Nueva", 10, settings=finanzas)

        assert "mi_cuenta_nueva" in admin.listar_cuentas(finanzas)["cuentas"]

    def test_rechaza_tipo_invalido(self, finanzas: Settings) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="Tipo de cuenta"):
            admin.guardar_cuenta("x", 1, "bitcoin", settings=finanzas)

    def test_borrar(self, finanzas: Settings) -> None:
        admin.guardar_cuenta("temporal", 5, settings=finanzas)
        admin.borrar_cuenta("temporal", settings=finanzas)

        assert "temporal" not in admin.listar_cuentas(finanzas)["cuentas"]

    def test_borrar_inexistente(self, finanzas: Settings) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="no existe"):
            admin.borrar_cuenta("fantasma", settings=finanzas)

    def test_duplicar(self, finanzas: Settings) -> None:
        admin.guardar_cuenta("original", 100, "debito", settings=finanzas)
        admin.duplicar_cuenta("original", "copia", settings=finanzas)

        cuentas = admin.listar_cuentas(finanzas)["cuentas"]
        assert cuentas["copia"] == cuentas["original"]

    def test_duplicar_a_nombre_existente(self, finanzas: Settings) -> None:
        admin.guardar_cuenta("a", 1, settings=finanzas)
        admin.guardar_cuenta("b", 2, settings=finanzas)

        with pytest.raises(admin.ErrorAdministracion, match="ya existe"):
            admin.duplicar_cuenta("a", "b", settings=finanzas)

    def test_total(self, finanzas: Settings) -> None:
        admin.guardar_cuenta("a", 10.5, settings=finanzas)
        admin.guardar_cuenta("b", 20.25, settings=finanzas)

        assert admin.listar_cuentas(finanzas)["total"] == 30.75


class TestPresupuestos:
    def test_guardar_con_el_formato_del_script(self, finanzas: Settings) -> None:
        """El script espera {'limite': x, 'periodo': 'mensual'}."""
        admin.guardar_presupuesto("comida", 2000, settings=finanzas)

        guardado = _leer(finanzas, "presupuesto.json")
        assert guardado["comida"] == {"limite": 2000.0, "periodo": "mensual"}

    def test_rechaza_limite_negativo(self, finanzas: Settings) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="negativo"):
            admin.guardar_presupuesto("x", -1, settings=finanzas)

    def test_borrar(self, finanzas: Settings) -> None:
        admin.guardar_presupuesto("comida", 100, settings=finanzas)
        admin.borrar_presupuesto("comida", settings=finanzas)

        assert admin.listar_presupuestos(finanzas)["presupuesto"] == {}


class TestMetas:
    def test_guardar_con_fecha(self, finanzas: Settings) -> None:
        admin.guardar_meta("viaje", 5000, 500, "2026-12-31", settings=finanzas)

        metas = admin.listar_metas(finanzas)["metas"]
        assert metas["viaje"] == {
            "objetivo": 5000.0,
            "ahorrado": 500.0,
            "fecha_limite": "2026-12-31",
        }

    def test_rechaza_objetivo_cero(self, finanzas: Settings) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="mayor que cero"):
            admin.guardar_meta("x", 0, settings=finanzas)

    def test_rechaza_fecha_mala(self, finanzas: Settings) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="AAAA-MM-DD"):
            admin.guardar_meta("x", 100, fecha_limite="31/12/2026", settings=finanzas)

    def test_borrar(self, finanzas: Settings) -> None:
        admin.guardar_meta("x", 100, settings=finanzas)
        admin.borrar_meta("x", settings=finanzas)

        assert admin.listar_metas(finanzas)["metas"] == {}


class TestRecordatorios:
    def test_guardar_con_el_formato_del_script(self, finanzas: Settings) -> None:
        admin.guardar_recordatorio("renta", 3600, 1, "Renta mensual", settings=finanzas)

        guardado = _leer(finanzas, "recordatorios.json")
        assert guardado["renta"] == {
            "monto": 3600.0,
            "dia": 1,
            "nota": "Renta mensual",
        }

    @pytest.mark.parametrize("dia", [0, 32, -1])
    def test_rechaza_dia_fuera_de_rango(self, finanzas: Settings, dia: int) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="día"):
            admin.guardar_recordatorio("x", 100, dia, settings=finanzas)

    def test_borrar(self, finanzas: Settings) -> None:
        admin.guardar_recordatorio("x", 100, 15, settings=finanzas)
        admin.borrar_recordatorio("x", settings=finanzas)

        assert admin.listar_recordatorios(finanzas)["recordatorios"] == {}


class TestTarjetas:
    def test_crear_conserva_las_compras(self, finanzas: Settings) -> None:
        admin.guardar_tarjeta("klar", 5000, 24, 3, settings=finanzas)
        admin.guardar_compra("klar", "Laptop", 12000, 12, settings=finanzas)

        # Reconfigurar el límite no debe borrar la compra.
        admin.guardar_tarjeta("klar", 7000, 24, 3, settings=finanzas)

        tarjetas = admin.listar_tarjetas(finanzas)["tarjetas"]
        assert tarjetas["klar"]["limite"] == 7000.0
        assert len(tarjetas["klar"]["compras"]) == 1

    def test_calcula_la_mensualidad(self, finanzas: Settings) -> None:
        admin.guardar_tarjeta("klar", settings=finanzas)
        datos = admin.guardar_compra("klar", "TV", 12000, 12, settings=finanzas)

        assert datos["mensualidad"] == 1000.0

    def test_compra_de_contado(self, finanzas: Settings) -> None:
        admin.guardar_tarjeta("klar", settings=finanzas)
        admin.guardar_compra("klar", "Café", 80, 1, settings=finanzas)

        compras = admin.listar_tarjetas(finanzas)["tarjetas"]["klar"]["compras"]
        assert compras[0]["mensualidad"] == 80.0
        assert compras[0]["restantes"] == 1

    def test_editar_no_toca_restantes(self, finanzas: Settings) -> None:
        """Editar el plazo no debe regalar pagos ya hechos."""
        admin.guardar_tarjeta("klar", settings=finanzas)
        datos = admin.guardar_compra("klar", "TV", 12000, 12, settings=finanzas)

        tarjetas = admin.listar_tarjetas(finanzas)["tarjetas"]
        tarjetas["klar"]["compras"][0]["restantes"] = 5
        # Se escribe a mano para simular pagos ya hechos.
        _escribir_directo(finanzas, "tarjetas.json", tarjetas)

        admin.guardar_compra(
            "klar", "TV editada", 11000, 10, compra_id=datos["compra"],
            settings=finanzas,
        )

        compras = admin.listar_tarjetas(finanzas)["tarjetas"]["klar"]["compras"]
        assert compras[0]["restantes"] == 5
        assert compras[0]["mensualidad"] == 1100.0

    def test_duplicar_compra(self, finanzas: Settings) -> None:
        admin.guardar_tarjeta("klar", settings=finanzas)
        datos = admin.guardar_compra("klar", "TV", 1200, 12, settings=finanzas)

        nuevo = admin.duplicar_compra("klar", datos["compra"], settings=finanzas)

        compras = admin.listar_tarjetas(finanzas)["tarjetas"]["klar"]["compras"]
        assert len(compras) == 2
        assert nuevo["compra"] != datos["compra"]
        assert "(copia)" in compras[1]["concepto"]

    def test_borrar_compra(self, finanzas: Settings) -> None:
        admin.guardar_tarjeta("klar", settings=finanzas)
        datos = admin.guardar_compra("klar", "TV", 1200, 12, settings=finanzas)

        admin.borrar_compra("klar", datos["compra"], settings=finanzas)

        assert admin.listar_tarjetas(finanzas)["tarjetas"]["klar"]["compras"] == []

    def test_compra_en_tarjeta_inexistente(self, finanzas: Settings) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="no existe"):
            admin.guardar_compra("fantasma", "x", 100, settings=finanzas)

    def test_borrar_tarjeta_se_lleva_las_compras(self, finanzas: Settings) -> None:
        admin.guardar_tarjeta("klar", settings=finanzas)
        admin.guardar_compra("klar", "TV", 1200, 12, settings=finanzas)

        admin.borrar_tarjeta("klar", settings=finanzas)

        assert admin.listar_tarjetas(finanzas)["tarjetas"] == {}

    def test_rechaza_plazo_cero(self, finanzas: Settings) -> None:
        admin.guardar_tarjeta("klar", settings=finanzas)
        with pytest.raises(admin.ErrorAdministracion, match="plazo"):
            admin.guardar_compra("klar", "x", 100, 0, settings=finanzas)


class TestIntegridad:
    """Lo que protege los datos del agente."""

    def test_rechaza_archivo_corrupto(self, finanzas: Settings) -> None:
        ruta = (
            finanzas.herramientas_scripts_dir.parent
            / "finanzas" / "users" / "noe" / "cuentas.json"
        )
        ruta.write_text("{esto no es json", encoding="utf-8")

        with pytest.raises(admin.ErrorAdministracion, match="corrupto"):
            admin.listar_cuentas(finanzas)

    def test_no_sobrescribe_un_archivo_corrupto(self, finanzas: Settings) -> None:
        """Si el archivo está roto, se falla ANTES de escribir: nada de perder datos."""
        ruta = (
            finanzas.herramientas_scripts_dir.parent
            / "finanzas" / "users" / "noe" / "cuentas.json"
        )
        original = "{esto no es json"
        ruta.write_text(original, encoding="utf-8")

        with pytest.raises(admin.ErrorAdministracion):
            admin.guardar_cuenta("nueva", 100, settings=finanzas)

        assert ruta.read_text(encoding="utf-8") == original

    def test_deja_respaldo_antes_de_escribir(self, finanzas: Settings) -> None:
        ruta = (
            finanzas.herramientas_scripts_dir.parent
            / "finanzas" / "users" / "noe" / "cuentas.json"
        )
        ruta.write_text('{"previa": {"saldo": 1, "tipo": "debito"}}', encoding="utf-8")

        admin.guardar_cuenta("nueva", 100, settings=finanzas)

        respaldo = ruta.with_suffix(".json.bak")
        assert respaldo.is_file()
        assert json.loads(respaldo.read_text(encoding="utf-8")) == {
            "previa": {"saldo": 1, "tipo": "debito"}
        }

    def test_escritura_valida_json(self, finanzas: Settings) -> None:
        admin.guardar_cuenta("a", 1.5, settings=finanzas)
        admin.guardar_cuenta("b", 2.5, settings=finanzas)

        guardado = _leer(finanzas, "cuentas.json")
        assert set(guardado) == {"a", "b"}

    def test_nombre_vacio(self, finanzas: Settings) -> None:
        with pytest.raises(admin.ErrorAdministracion, match="vacío"):
            admin.guardar_cuenta("   ", 1, settings=finanzas)


def _escribir_directo(settings: Settings, archivo: str, datos: dict) -> None:
    """Escribe un archivo saltándose el servicio (para preparar escenarios)."""
    ruta = (
        settings.herramientas_scripts_dir.parent
        / "finanzas" / "users" / settings.finanzas_usuario / archivo
    )
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


class TestOperacionesQueMuevenDinero:
    """Transferir y pagar exigen confirmación: mueven saldos de verdad.

    Se comprueba que el guardia esté puesto; la ejecución real se valida aparte
    contra el stack, porque toca el ledger en producción.
    """

    @pytest.fixture
    def cliente(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    def test_transferir_sin_confirmar(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/finanzas/transferir",
            json={"origen": "openbank", "destino": "efectivo", "monto": 10},
        )

        assert respuesta.status_code == 400
        assert "confirmar" in respuesta.json()["detail"].lower()

    def test_pagar_sin_confirmar(self, cliente: TestClient) -> None:
        # Cuerpo válido: si faltara un campo, FastAPI devolvería 422 antes de
        # llegar a la comprobación de confirmación.
        respuesta = cliente.post(
            "/api/finanzas/tarjetas/klar/pagar", json={"tarjeta": "klar"}
        )

        assert respuesta.status_code == 400
        assert "confirmar" in respuesta.json()["detail"].lower()

    def test_transferir_valida_monto_positivo(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/finanzas/transferir?confirmar=true",
            json={"origen": "a", "destino": "b", "monto": -5},
        )

        assert respuesta.status_code == 422


class TestEndpointsDeAdministracion:
    """Los endpoints existen y validan antes de tocar datos."""

    @pytest.fixture
    def cliente(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    def test_tarjeta_con_corte_invalido(self, cliente: TestClient) -> None:
        respuesta = cliente.put(
            "/api/finanzas/tarjetas/klar", json={"nombre": "klar", "corte": 40}
        )

        assert respuesta.status_code == 422

    def test_meta_con_objetivo_cero(self, cliente: TestClient) -> None:
        respuesta = cliente.put(
            "/api/finanzas/metas/viaje", json={"nombre": "viaje", "objetivo": 0}
        )

        assert respuesta.status_code == 422

    def test_recordatorio_con_dia_invalido(self, cliente: TestClient) -> None:
        respuesta = cliente.put(
            "/api/finanzas/recordatorios/x",
            json={"nombre": "x", "monto": 10, "dia": 45},
        )

        assert respuesta.status_code == 422

    def test_compras_de_tarjeta_requiere_monto(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            "/api/finanzas/tarjetas/klar/compras",
            json={"concepto": "algo", "total": 0},
        )

        assert respuesta.status_code == 422
