"""Pruebas de sustitución de equipos: db.sustituir_equipo y POST /api/equipos/sustituir.

Las pruebas de db corren con el python de sistema (db.py es stdlib). Las HTTP
necesitan FastAPI y se saltan si no está instalado.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from panel_web.app import db  # noqa: E402

try:
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner

    HAY_FASTAPI = True
except ImportError:
    HAY_FASTAPI = False

# Solo credenciales ficticias. Nunca leer .env ni contactar Entra en estas pruebas.
TEST_ENV = {
    "MS_TENANT_ID": "test-tenant", "MS_CLIENT_ID": "test-client",
    "MS_CLIENT_SECRET": "not-a-real-client-secret",
    "SESSION_SECRET": "test-only-session-key-with-at-least-32-characters",
    "ALLOWED_DOMAIN": "example.invalid", "ALLOWED_EMAILS": "",
    "ADMIN_EMAILS": "admin@example.invalid", "ALLOWED_HOSTS": "testserver",
    "DOMAIN": "testserver", "MS_AUTHORITY": "",
    "MS_REDIRECT_URI": "https://testserver/auth/callback",
    "COOKIE_SECURE": "true", "APP_ENV": "production",
}

if HAY_FASTAPI:
    with patch.dict(os.environ, TEST_ENV):
        from panel_web.app import auth

        main = importlib.import_module("panel_web.app.main")


def hoy_local() -> str:
    return datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d")


class SustitucionDBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "panel.db"
        db.inicializar(self.ruta)
        self.con = db.conectar(self.ruta)
        db.upsert_equipo(
            self.con, "VIEJA001", marca="HP", modelo="M501",
            tipo="Multifuncional", sede="Reynosa", zona="9", domicilio="Dom",
            ubicacion="Segundos Penal", piso="2", ip_fisica="10.0.0.1",
            perfil="A1", estatus="En Operación", contador_inicial="100",
        )

    def tearDown(self) -> None:
        self.con.close()
        self.tmp.cleanup()

    def fila(self, sn: str):
        return self.con.execute(
            "SELECT * FROM equipos WHERE sn=?", (sn,)
        ).fetchone()

    def test_camino_feliz_hereda_y_sobrescribe(self) -> None:
        resultado = db.sustituir_equipo(
            self.con, vieja="VIEJA001", nueva="NUEVA001", marca="Ricoh",
        )
        self.assertEqual(resultado["vieja"], "VIEJA001")
        self.assertEqual(resultado["nueva"], "NUEVA001")
        self.assertEqual(resultado["sede"], "Reynosa")
        self.assertFalse(resultado["reanudada"])
        nueva = self.fila("NUEVA001")
        self.assertEqual(nueva["marca"], "Ricoh")  # sobrescrita
        self.assertEqual(nueva["modelo"], "M501")  # heredada
        self.assertEqual(nueva["tipo"], "Multifuncional")
        self.assertEqual(nueva["perfil"], "A1")
        self.assertEqual(nueva["ip_fisica"], "10.0.0.1")
        self.assertEqual(nueva["sede"], "Reynosa")
        self.assertEqual(nueva["zona"], "9")
        self.assertEqual(nueva["domicilio"], "Dom")
        self.assertEqual(nueva["ubicacion"], "Segundos Penal")
        self.assertEqual(nueva["piso"], "2")
        self.assertEqual(nueva["estatus"], "En Operación")
        self.assertEqual(nueva["contador_inicial"], "100")
        self.assertEqual(nueva["fecha_alta"], hoy_local())
        vieja = self.fila("VIEJA001")
        self.assertEqual(vieja["estatus"], "Retirada")
        self.assertEqual(vieja["sustituto_sn"], "NUEVA001")
        self.assertEqual(vieja["fecha_baja"], hoy_local())
        self.assertEqual(db.contar_equipos(self.con), 2)
        en_cobertura = self.con.execute(
            "SELECT COUNT(*) AS n FROM cobertura_inventario"
            " WHERE periodo=? AND sn='NUEVA001'",
            (db.periodo_de(),),
        ).fetchone()["n"]
        self.assertEqual(en_cobertura, 1)

    def test_fecha_baja_explicita(self) -> None:
        db.sustituir_equipo(
            self.con, vieja="VIEJA001", nueva="NUEVA002",
            fecha_baja="2026-11-01",
        )
        self.assertEqual(self.fila("VIEJA001")["fecha_baja"], "2026-11-01")

    def test_nueva_existente_aborta_sin_escrituras(self) -> None:
        db.upsert_equipo(self.con, "NUEVA003", sede="Reynosa")
        with self.assertRaises(db.SustitucionError):
            db.sustituir_equipo(self.con, vieja="VIEJA001", nueva="NUEVA003")
        vieja = self.fila("VIEJA001")
        self.assertEqual(vieja["estatus"], "En Operación")
        self.assertIsNone(vieja["sustituto_sn"])
        self.assertEqual(db.contar_equipos(self.con), 2)

    def test_vieja_no_encontrada(self) -> None:
        with self.assertRaises(db.EquipoNoEncontrado):
            db.sustituir_equipo(self.con, vieja="NO-EXISTE-9", nueva="NUEVA004")
        self.assertEqual(db.contar_equipos(self.con), 1)

    def test_vieja_ya_retirada(self) -> None:
        db.upsert_equipo(self.con, "VIEJA001", estatus="Retirada")
        with self.assertRaises(db.SustitucionError):
            db.sustituir_equipo(self.con, vieja="VIEJA001", nueva="NUEVA005")
        self.assertIsNone(self.fila("NUEVA005"))
        self.assertEqual(db.contar_equipos(self.con), 1)

    def test_nueva_igual_vieja(self) -> None:
        with self.assertRaises(db.SustitucionError):
            db.sustituir_equipo(self.con, vieja="VIEJA001", nueva="VIEJA001")
        self.assertEqual(self.fila("VIEJA001")["estatus"], "En Operación")
        self.assertEqual(db.contar_equipos(self.con), 1)

    def test_fecha_baja_formato_invalido(self) -> None:
        with self.assertRaises(db.SustitucionError):
            db.sustituir_equipo(
                self.con, vieja="VIEJA001", nueva="NUEVA006",
                fecha_baja="01/11/2026",
            )
        self.assertEqual(db.contar_equipos(self.con), 1)

    def test_vieja_por_variante_ocr(self) -> None:
        db.upsert_equipo(self.con, "VNB0B04743", sede="Reynosa",
                         estatus="En Operación")
        resultado = db.sustituir_equipo(
            self.con, vieja="VNBOB04743", nueva="NUEVA007",
        )
        self.assertEqual(resultado["vieja"], "VNB0B04743")
        self.assertEqual(self.fila("VNB0B04743")["estatus"], "Retirada")

    def test_ambiguedad_aborta_sin_escrituras(self) -> None:
        for sn in ("SNA001", "SNA00I"):
            self.con.execute(
                "INSERT INTO equipos(sn, sn_norm, ubicacion_norm, sede, estatus)"
                " VALUES(?,?,?,?,?)",
                (sn, db.normalizar_serial(sn), "", "Reynosa", "En Operación"),
            )
        self.con.commit()
        with self.assertRaises(db.SerialAmbiguo):
            db.sustituir_equipo(self.con, vieja="SNA0O1", nueva="NUEVA008")
        self.assertIsNone(self.fila("NUEVA008"))
        self.assertEqual(db.contar_equipos(self.con), 3)
        for sn in ("SNA001", "SNA00I"):
            fila = self.fila(sn)
            self.assertEqual(fila["estatus"], "En Operación")
            self.assertIsNone(fila["sustituto_sn"])

    def test_reanudacion_completa_solo_retiro(self) -> None:
        db.upsert_equipo(self.con, "NUEVA009", marca="Distinta",
                         sede="Reynosa", estatus="En Operación")
        self.con.execute(
            "UPDATE equipos SET sustituto_sn='NUEVA009' WHERE sn='VIEJA001'"
        )
        self.con.commit()
        resultado = db.sustituir_equipo(
            self.con, vieja="VIEJA001", nueva="NUEVA009", marca="Otra",
        )
        self.assertTrue(resultado["reanudada"])
        self.assertEqual(self.fila("NUEVA009")["marca"], "Distinta")
        vieja = self.fila("VIEJA001")
        self.assertEqual(vieja["estatus"], "Retirada")
        self.assertEqual(vieja["sustituto_sn"], "NUEVA009")
        self.assertEqual(vieja["fecha_baja"], hoy_local())
        self.assertEqual(db.contar_equipos(self.con), 2)

    def test_reanudacion_con_vieja_ya_retirada_completa_fecha(self) -> None:
        db.upsert_equipo(self.con, "NUEVA010", sede="Reynosa",
                         estatus="En Operación")
        self.con.execute(
            "UPDATE equipos SET estatus='Retirada', sustituto_sn='NUEVA010',"
            " fecha_baja=NULL WHERE sn='VIEJA001'"
        )
        self.con.commit()
        resultado = db.sustituir_equipo(
            self.con, vieja="VIEJA001", nueva="NUEVA010",
        )
        self.assertTrue(resultado["reanudada"])
        vieja = self.fila("VIEJA001")
        self.assertEqual(vieja["estatus"], "Retirada")
        self.assertEqual(vieja["sustituto_sn"], "NUEVA010")
        self.assertEqual(vieja["fecha_baja"], hoy_local())
        self.assertEqual(db.contar_equipos(self.con), 2)


@unittest.skipUnless(HAY_FASTAPI, "sin FastAPI en este entorno")
class SustitucionHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        (data / "inventarios").mkdir(parents=True)
        config = replace(main.settings, data_dir=data, db_path=data / "panel.db",
                         inventory_dir=data / "inventarios", max_upload_mb=1)
        self.enterContext(patch.object(main, "settings", config))
        self.enterContext(patch.object(auth, "settings", config))
        self.enterContext(patch.object(main, "limite_subidas", auth.limite_subidas))
        main.app.middleware_stack = None
        self.client = self.enterContext(TestClient(main.app, base_url="https://testserver"))
        con = db.conectar(config.db_path)
        try:
            self.ana = db.upsert_usuario(con, "oid-ana", "ana@example.invalid", "Ana", "user")
            self.admin = db.upsert_usuario(con, "oid-admin", "admin@example.invalid", "Admin", "admin")
            db.asignar_sedes(con, int(self.ana["id"]), ["Reynosa"])
            db.upsert_equipo(con, "REY-VIEJA1", sede="Reynosa", estatus="En Operación")
            db.upsert_equipo(con, "TAM-VIEJA1", sede="Tampico", estatus="En Operación")
        finally:
            con.close()
        self.csrf = "test-csrf-token"
        self.inventory_xlsx = data / "inventarios" / "inventario_panel.xlsx"

    def sesion(self, usuario=None) -> None:
        usuario = usuario or self.ana
        payload = {"uid": usuario["id"], "email": usuario["email"], "csrf": self.csrf}
        token = TimestampSigner(main.settings.session_secret).sign(
            base64.b64encode(json.dumps(payload).encode())
        ).decode()
        self.client.cookies.clear()
        self.client.cookies.set("pdfsep_session", token)

    def headers(self):
        return {"X-CSRF-Token": self.csrf, "Origin": "https://testserver"}

    def sustituir(self, vieja: str, nueva: str):
        return self.client.post(
            "/api/equipos/sustituir", headers=self.headers(),
            json={"vieja": vieja, "nueva": nueva},
        )

    def fila(self, sn: str):
        con = db.conectar(main.settings.db_path)
        try:
            return con.execute("SELECT * FROM equipos WHERE sn=?", (sn,)).fetchone()
        finally:
            con.close()

    def test_usuario_sustituye_en_su_sede(self) -> None:
        self.sesion()
        r = self.sustituir("REY-VIEJA1", "REY-NUEVA1")
        self.assertEqual(r.status_code, 200, r.text)
        datos = r.json()
        self.assertTrue(datos["ok"])
        self.assertEqual(datos["nueva"], "REY-NUEVA1")
        self.assertFalse(datos["reanudada"])
        vieja = self.fila("REY-VIEJA1")
        self.assertEqual(vieja["estatus"], "Retirada")
        self.assertEqual(vieja["sustituto_sn"], "REY-NUEVA1")
        self.assertTrue(self.inventory_xlsx.exists())
        con = db.conectar(main.settings.db_path)
        try:
            auditoria = con.execute(
                "SELECT * FROM audit WHERE action='inventario_sustitucion'"
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(len(auditoria), 1)
        self.assertEqual(auditoria[0]["detail"], "REY-VIEJA1 -> REY-NUEVA1 · Reynosa")

    def test_usuario_sede_ajena_403(self) -> None:
        self.sesion()
        r = self.sustituir("TAM-VIEJA1", "TAM-NUEVA1")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.fila("TAM-VIEJA1")["estatus"], "En Operación")
        self.assertIsNone(self.fila("TAM-NUEVA1"))

    def test_admin_libre(self) -> None:
        self.sesion(self.admin)
        r = self.sustituir("TAM-VIEJA1", "TAM-NUEVA1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fila("TAM-VIEJA1")["estatus"], "Retirada")

    def test_nueva_existente_409(self) -> None:
        self.sesion()
        r = self.sustituir("REY-VIEJA1", "TAM-VIEJA1")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.fila("REY-VIEJA1")["estatus"], "En Operación")

    def test_vieja_ya_retirada_409(self) -> None:
        self.sesion()
        self.assertEqual(self.sustituir("REY-VIEJA1", "REY-NUEVA2").status_code, 200)
        r = self.sustituir("REY-VIEJA1", "REY-NUEVA3")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIsNone(self.fila("REY-NUEVA3"))

    def test_vieja_no_encontrada_404(self) -> None:
        self.sesion()
        r = self.sustituir("NO-EXISTE-9", "NUEVA-404")
        self.assertEqual(r.status_code, 404, r.text)

    def test_anonimo_401(self) -> None:
        r = self.sustituir("REY-VIEJA1", "REY-NUEVA9")
        self.assertEqual(r.status_code, 401, r.text)


if __name__ == "__main__":
    unittest.main()
