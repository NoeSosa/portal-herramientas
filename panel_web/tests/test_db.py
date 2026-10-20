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


if __name__ == "__main__":
    unittest.main()
