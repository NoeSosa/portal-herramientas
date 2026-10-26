"""Regresiones de conservación histórica, identidad y reconciliación."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from panel_web.app import db


class IntegridadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'panel.db'
        db.inicializar(self.path)
        self.con = db.conectar(self.path)
        self.addCleanup(self.con.close)
        self.user = db.upsert_usuario(self.con, 'u', 'u@example.invalid', 'U', 'user')

    def job(self, jid, sn='ABC-123'):
        db.crear_job(self.con, job_id=jid, owner_id=self.user['id'],
                     owner_email=self.user['email'], filename=jid+'.pdf',
                     stored_name=jid+'.pdf', size=10, pages=1, client_ip=None)
        db.upsert_pagina(self.con, jid, 1, sn=sn, estado='OK')
        db.finalizar_job(self.con, jid, 'resultado.zip', 1)
        db.registrar_cobertura_job(self.con, jid)

    def coverage(self):
        return db.cobertura_sede(self.con, 'A', db.periodo_de())

    def test_migracion_repara_claves_sin_perder_serial_original(self):
        db.upsert_equipo(self.con, 'ABC-123', sede='A')
        self.con.execute('UPDATE equipos SET sn_norm=NULL')
        self.con.commit()
        db.inicializar(self.path)
        self.assertEqual(db.buscar_equipo(self.con, 'ABC123')['sn'], 'ABC-123')
        creado, sn = db.upsert_equipo(self.con, 'ABC123', marca='HP')
        self.assertFalse(creado)
        self.assertEqual(sn, 'ABC-123')
        self.assertEqual(db.contar_equipos(self.con), 1)

    def test_retencion_conserva_cobertura_y_nombre(self):
        db.upsert_equipo(self.con, 'ABC-123', sede='A')
        self.job('j1')
        db.borrar_job_por_id(self.con, 'j1')
        c = self.coverage()
        self.assertEqual(c['escaneados'], 1)
        self.assertEqual(c['detalle_escaneos'][0]['documento'], 'j1.pdf')
        self.assertTrue(c['detalle_escaneos'][0]['archivo_eliminado'])

    def test_alta_reconcilia_lectura_aun_despues_de_retencion(self):
        self.job('j1')
        db.borrar_job_por_id(self.con, 'j1')
        db.upsert_equipo(self.con, 'ABC-123', sede='A')
        self.assertEqual(self.coverage()['escaneados'], 1)
        db.actualizar_cobertura_inventario(self.con)
        self.assertEqual(len(self.coverage()['detalle_escaneos']), 1)

    def test_exclusion_y_borrado_recalculan_duplicados(self):
        db.upsert_equipo(self.con, 'ABC-123', sede='A')
        self.job('j1')
        self.job('j2')
        self.assertEqual(len(self.coverage()['duplicados']), 1)
        cid = self.con.execute("SELECT id FROM cobertura_escaneos WHERE job_id='j1'").fetchone()[0]
        db.excluir_cobertura(self.con, cid, 'correccion')
        db.actualizar_cobertura_inventario(self.con)
        self.assertEqual(len(self.coverage()['duplicados']), 0)
        self.assertEqual(self.coverage()['escaneados'], 1)
        self.assertFalse(db.borrar_job(self.con, 'j2', self.user['id']+1))
        self.assertEqual(self.coverage()['escaneados'], 1)
        self.assertTrue(db.borrar_job(self.con, 'j2', self.user['id']))
        self.assertEqual(self.coverage()['escaneados'], 0)

    def test_mes_cerrado_conserva_inventario_y_sede(self):
        with patch.object(db, 'periodo_de', return_value='2026-09'):
            db.upsert_equipo(self.con, 'ABC-123', sede='A')
        with patch.object(db, 'periodo_de', return_value='2026-10'):
            db.upsert_equipo(self.con, 'ABC-123', sede='B')
            db.upsert_equipo(self.con, 'XYZ999', sede='A')
            c = db.cobertura_sede(self.con, 'A', '2026-09')
            self.assertTrue(c['cerrado'])
            self.assertEqual([e['sn'] for e in c['equipos']], ['ABC-123'])
            self.assertEqual(c['inventario_origen'], 'registrado')

    def test_reset_no_reintroduce_lecturas_al_actualizar_inventario(self):
        db.upsert_equipo(self.con, 'ABC-123', sede='A')
        self.job('j1')
        db.reiniciar_cobertura(self.con, db.periodo_de(), 'A')
        db.upsert_equipo(self.con, 'XYZ999', sede='A')
        self.assertEqual(self.coverage()['escaneados'], 0)
