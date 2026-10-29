"""Pruebas de aislamiento y cola sin depender de FastAPI ni de red."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from panel_web.app import db  # noqa: E402


class AislamientoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "panel.db"
        db.inicializar(self.ruta)
        self.con = db.conectar(self.ruta)
        self.ana = db.upsert_usuario(self.con, "oid-ana", "ana@empresa.com", "Ana", "user")
        self.beto = db.upsert_usuario(self.con, "oid-beto", "beto@empresa.com", "Beto", "user")

    def tearDown(self) -> None:
        self.con.close()
        self.tmp.cleanup()

    def crear(self, uid: int, email: str, job_id: str, paginas: int = 10) -> None:
        db.crear_job(
            self.con,
            job_id=job_id,
            owner_id=uid,
            owner_email=email,
            filename=f"{job_id}.pdf",
            stored_name=f"{job_id}.pdf",
            size=1000,
            pages=paginas,
            client_ip="127.0.0.1",
        )

    def test_cada_usuario_solo_ve_lo_suyo(self) -> None:
        self.crear(int(self.ana["id"]), self.ana["email"], "job-ana")
        self.crear(int(self.beto["id"]), self.beto["email"], "job-beto")

        de_ana = db.listar_jobs(self.con, int(self.ana["id"]))
        self.assertEqual([j["id"] for j in de_ana], ["job-ana"])
        self.assertIsNotNone(db.job_por_id(self.con, "job-ana", int(self.ana["id"])))
        self.assertIsNone(db.job_por_id(self.con, "job-ana", int(self.beto["id"])))
        self.assertFalse(db.borrar_job(self.con, "job-ana", int(self.beto["id"])))
        self.assertIsNotNone(db.job_por_id(self.con, "job-ana", int(self.ana["id"])))

    def test_conteo_de_usuarios_para_limite_de_cinco(self) -> None:
        self.assertEqual(db.contar_usuarios(self.con), 2)

    def test_cuota_diaria_cuenta_solo_al_dueno(self) -> None:
        self.crear(int(self.ana["id"]), self.ana["email"], "a1", 100)
        self.crear(int(self.beto["id"]), self.beto["email"], "b1", 200)
        self.assertEqual(db.contar_paginas_hoy(self.con, int(self.ana["id"])), 100)
        self.assertEqual(db.contar_paginas_hoy(self.con, int(self.beto["id"])), 200)

    def test_inventario_por_sede(self) -> None:
        db.upsert_equipo(self.con, "ABC123", marca="HP", sede="Monterrey",
                         zona="Norte", estatus="Activo",
                         ubicacion="Centro de Justicia", piso="2")
        db.upsert_equipo(self.con, "XYZ999", marca="Ricoh", sede="Guadalajara")
        equipos = db.listar_equipos(self.con, sede="Monterrey")
        self.assertEqual(equipos[0]["ubicacion"], "Centro de Justicia")
        self.assertEqual(equipos[0]["piso"], "2")
        self.assertEqual(db.contar_equipos(self.con), 2)
        self.assertEqual(db.contar_equipos(self.con, sede="Monterrey"), 1)
        self.assertEqual(db.valores_equipos(self.con, "sede"),
                         ["Guadalajara", "Monterrey"])
        resumen = db.resumen_sedes(self.con)
        self.assertEqual(resumen[0]["equipos"], 1)
        self.assertTrue(db.borrar_equipo(self.con, "abc123"))
        self.assertEqual(db.contar_equipos(self.con), 1)

    def test_borrado_masivo_por_filtro(self) -> None:
        db.upsert_equipo(self.con, "A1", sede="Reynosa")
        db.upsert_equipo(self.con, "A2", sede="Reynosa")
        db.upsert_equipo(self.con, "A3", sede="Tampico")
        self.assertEqual(db.borrar_equipos(self.con, sede="Reynosa"), 2)
        self.assertEqual(db.contar_equipos(self.con), 1)
        self.assertEqual(db.borrar_equipos(self.con), 1)

    def test_paginas_del_job(self) -> None:
        self.crear(int(self.ana["id"]), self.ana["email"], "job-pag", 2)
        db.upsert_pagina(self.con, "job-pag", 1, sn="ABC123", sede="Monterrey",
                         estado="OK", texto="serial ABC123", worker="0-1",
                         slot=0, pid=10, ms=900, ts=1.0)
        db.upsert_pagina(self.con, "job-pag", 2, estado="POR_VERIFICAR",
                         worker="1-2", slot=1, pid=11, ms=1100, ts=2.0)
        paginas = db.paginas_job(self.con, "job-pag")
        self.assertEqual([p["pag"] for p in paginas], [1, 2])
        self.assertEqual(paginas[0]["ocr"], 1)
        self.assertEqual(paginas[1]["ocr"], 0)
        self.assertEqual(db.resumen_paginas(self.con, "job-pag")["por_estado"],
                         {"OK": 1, "POR_VERIFICAR": 1})
        self.assertEqual(len(db.trabajadores_job(self.con, "job-pag")), 2)

    def test_eta_cuenta_paginas_por_delante(self) -> None:
        self.crear(int(self.ana["id"]), self.ana["email"], "corriendo", 10)
        self.crear(int(self.beto["id"]), self.beto["email"], "esperando", 5)
        self.con.execute(
            "UPDATE jobs SET status='running', pages_done=2, started_at=created_at"
            " WHERE id='corriendo'"
        )
        self.con.commit()

        job = db.job_por_id(self.con, "esperando", int(self.beto["id"]))
        stats = db.estadisticas_cola(self.con, job, 10.0)
        # 8 páginas restantes del job en curso + 5 del job en espera.
        self.assertEqual(stats["pages_ahead"], 8)
        self.assertEqual(stats["posicion"], 1)
        self.assertEqual(stats["eta_seconds"], 130)


class CoberturaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "panel.db"
        db.inicializar(self.ruta)
        self.con = db.conectar(self.ruta)
        self.user = db.upsert_usuario(
            self.con, "oid-cov", "cov@empresa.com", "Cobertura", "user"
        )
        self.uid = int(self.user["id"])
        db.asignar_sedes(self.con, self.uid, ["Reynosa"])

    def tearDown(self) -> None:
        self.con.close()
        self.tmp.cleanup()

    def job(self, job_id: str, serial: str, estado: str = "OK") -> None:
        db.crear_job(
            self.con, job_id=job_id, owner_id=self.uid,
            owner_email=self.user["email"], filename=f"{job_id}.pdf",
            stored_name=f"{job_id}.pdf", size=100, pages=1,
            client_ip="127.0.0.1",
        )
        db.upsert_pagina(self.con, job_id, 1, sn=serial, estado=estado,
                         texto="x", ts=1)
        db.finalizar_job(self.con, job_id, f"/tmp/{job_id}.zip", 1)
        db.registrar_cobertura_job(self.con, job_id)

    def test_ubicaciones_no_se_fusionan_solas(self) -> None:
        db.upsert_equipo(self.con, "AAA111", sede="Reynosa",
                         ubicacion="Segundos Penal")
        db.upsert_equipo(self.con, "BBB222", sede="Reynosa",
                         ubicacion="2 penal")
        self.assertEqual(db.contar_equipos(self.con), 2)
        ubicaciones = {f["ubicacion"] for f in db.listar_equipos(self.con)}
        self.assertEqual(ubicaciones, {"Segundos Penal", "2 penal"})
        self.assertIn("2 penal", db.ubicaciones_similares(self.con, "Segundos Penal"))

    def test_serial_variantes_si_se_consolidan(self) -> None:
        db.upsert_equipo(self.con, "Mx3cv6Q0DZ", sede="Reynosa",
                         ubicacion="Segundos Penal")
        db.upsert_equipo(self.con, "MX3CV6QODZ", sede="Reynosa",
                         ubicacion="Juzgado Segundo en Materia Penal")
        self.assertEqual(db.contar_equipos(self.con), 1)
        fila = db.listar_equipos(self.con)[0]
        self.assertEqual(fila["ubicacion"], "Juzgado Segundo en Materia Penal")

    def test_sugerencias_y_exactas_solo_de_la_sede(self) -> None:
        db.upsert_equipo(self.con, "AAA111", sede="Reynosa",
                         ubicacion="JUZGADO CUARTO ADMINISTRATIVO")
        db.upsert_equipo(self.con, "BBB222", sede="Matamoros",
                         ubicacion="Juzgado Cuarto Administrativo Matamoros")
        db.upsert_equipo(self.con, "CCC333", sede="Reynosa",
                         ubicacion="Juzgado Quinto Administrativo")
        propias = db.sugerencias_ubicacion(
            self.con, "JUZGADO CUARTO", "Reynosa"
        )
        self.assertIn("JUZGADO CUARTO ADMINISTRATIVO", propias)
        self.assertNotIn("Juzgado Cuarto Administrativo Matamoros", propias)
        self.assertTrue(db.ubicacion_exacta(
            self.con, "juzgado cuarto administrativo", "Reynosa"
        ))
        self.assertFalse(db.ubicacion_exacta(
            self.con, "juzgado cuarto administrativo", "Matamoros"
        ))

    def test_faltantes_y_cobertura(self) -> None:
        db.upsert_equipo(self.con, "AAA111", sede="Reynosa", marca="HP")
        db.upsert_equipo(self.con, "BBB222", sede="Reynosa", marca="HP")
        self.job("j1", "AAA111")
        datos = db.cobertura_para_usuario(self.con, self.uid, db.periodo_de())
        self.assertEqual(datos["esperados"], 2)
        self.assertEqual(datos["escaneados"], 1)
        self.assertEqual([f["sn"] for f in datos["sedes"][0]["faltantes"]], ["BBB222"])

        # El mismo serial en otro job se marca duplicado y no aumenta cobertura.
        self.job("j2", "AAA111")
        datos = db.cobertura_para_usuario(self.con, self.uid, db.periodo_de())
        self.assertEqual(datos["escaneados"], 1)
        self.assertEqual(datos["duplicados"], 1)

    def test_nombre_personalizado_sobrevive_al_login(self) -> None:
        db.actualizar_display_name(self.con, self.uid, "Nombre propio")
        db.upsert_usuario(self.con, "oid-cov", self.user["email"], "Nombre Entra", "user")
        self.assertEqual(db.usuario_por_id(self.con, self.uid)["display_name"], "Nombre propio")

    def test_verificacion_alterna_o_cero(self) -> None:
        db.upsert_equipo(self.con, "VNB0B04743", sede="Reynosa")
        self.assertEqual(
            db.buscar_equipo(self.con, "VNBOB04743")["sn"],
            "VNB0B04743",
        )
        self.job("j-o-cero", "VNBOB04743")
        datos = db.cobertura_para_usuario(self.con, self.uid, db.periodo_de())
        self.assertEqual(datos["escaneados"], 1)
        self.assertEqual(datos["sedes"][0]["detalle_escaneos"][0]["sn"], "VNB0B04743")

    def test_reset_no_reinyecta_jobs_viejos(self) -> None:
        db.upsert_equipo(self.con, "AAA111", sede="Reynosa")
        db.upsert_equipo(self.con, "BBB222", sede="Reynosa")
        self.job("j1", "AAA111")
        periodo = db.periodo_de()
        self.job("j2", "BBB222")
        datos = db.cobertura_para_usuario(self.con, self.uid, periodo)
        self.assertEqual(datos["escaneados"], 2)
        db.reiniciar_cobertura(self.con, periodo, "Reynosa")
        datos = db.cobertura_para_usuario(self.con, self.uid, periodo)
        self.assertEqual(datos["escaneados"], 0)
        self.job("j3", "AAA111")
        datos = db.cobertura_para_usuario(self.con, self.uid, periodo)
        self.assertEqual(datos["escaneados"], 1)


if __name__ == "__main__":
    unittest.main()
