"""Pruebas del mapeo del Excel real: LOCALIDAD→sede y Ubicación de Equipo."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from panel_web.app import db, inventario  # noqa: E402


class InventarioExcelTest(unittest.TestCase):
    def test_columnas_zona_09(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            origen = tmp / "zona09.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "1. Consumo de Impresión Mono"
            ws.append([
                "ZONA", "Descripción del Equipo", "Dirección IP del Equipo",
                "Modelo", "N° de Serie", "Marca", "Estatus", "ESTADO",
                "LOCALIDAD", "Domicilio", "Ubicación de Equipo",
            ])
            ws.append([
                9, "Multifuncional", "10.70.142.11", "IM 430F",
                "3356P351626", "Ricoh", "En Operación", "Tamaulipas",
                "Reynosa", "Avenida de los Encinos 800", "Centro de Justicia",
            ])
            wb.save(origen)

            con = db.conectar(tmp / "panel.db")
            try:
                db.inicializar(tmp / "panel.db")
                resultado = inventario.importar_xlsx(con, origen)
                self.assertEqual(resultado["insertados"], 1)
                fila = db.listar_equipos(con)[0]
                self.assertEqual(fila["sn"], "3356P351626")
                self.assertEqual(fila["sede"], "Avenida de los Encinos 800")
                self.assertEqual(fila["domicilio"], "Avenida de los Encinos 800")
                self.assertEqual(fila["ubicacion"], "Centro de Justicia")
                self.assertEqual(fila["estatus"], "En Operación")
                self.assertEqual(fila["piso"], "")

                destino = tmp / "inventario_panel.xlsx"
                inventario.exportar_xlsx(con, destino)
                ws2 = load_workbook(destino).active
                headers = [c.value for c in ws2[1]]
                self.assertIn("Ubicación", headers)
                self.assertIn("Piso", headers)
                self.assertEqual(ws2.cell(2, headers.index("SEDE") + 1).value, "Avenida de los Encinos 800")
                self.assertEqual(ws2.cell(2, headers.index("Ubicación") + 1).value, "Centro de Justicia")
            finally:
                con.close()

    def test_formula_no_se_exporta_como_formula(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            con = db.conectar(tmp / "panel.db")
            try:
                db.inicializar(tmp / "panel.db")
                db.upsert_equipo(con, "ABC1", sede='=HYPERLINK("http://x")')
                destino = tmp / "inventario_panel.xlsx"
                inventario.exportar_xlsx(con, destino)
                ws = load_workbook(destino).active
                valor = ws.cell(2, 4).value
                self.assertTrue(str(valor).startswith("'="), valor)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
