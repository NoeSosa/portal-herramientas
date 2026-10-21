#!/usr/bin/env python3
"""
inventario.py — Importa/exporta el inventario (números de serie) entre los Excel
locales y la tabla `equipos` de la BD del panel.
===============================================================================
Motivo: el pipeline valida los seriales contra los Excel de `inventarios_local/`.
Para poder EDITAR/AÑADIR/BORRAR seriales desde el panel, el panel es dueño de la
tabla `equipos` y **exporta** un único Excel (`inventario_panel.xlsx`) que el
pipeline usa de forma preferente. Así las ediciones del panel son las que mandan
y el pipeline no tiene que saber nada de SQLite.

Se ejecuta con el intérprete que tiene pandas/openpyxl (`PY_VENV_PDF`).

Uso:
    python inventario.py importar <db> <xlsx> [<xlsx> …]   # Excel → tabla equipos
    python inventario.py exportar <db> <salida.xlsx>       # tabla equipos → Excel
    python inventario.py contar <db>

Columnas del Excel exportado: las que espera `construir_indice()` de
`separador_auto.py` (Serie, Marca, Modelo, SEDE, ZONA, Domicilio, Estatus,
Contador inicial).
"""
import os
import sqlite3
import sys

COLUMNAS_EXCEL = ["Serie", "Marca", "Modelo", "SEDE", "ZONA", "Domicilio",
                  "Estatus", "Contador inicial"]


def _con(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def _columna(cols, *claves):
    """Primera columna cuyo nombre (minúsculas) coincide con alguna clave."""
    for c in cols:
        n = str(c).strip().lower()
        if n in claves:
            return c
    for c in cols:
        n = str(c).strip().lower()
        if any(k in n for k in claves):
            return c
    return None


def _texto(v):
    if v is None:
        return ""
    try:
        if v != v:  # NaN
            return ""
    except Exception:
        pass
    return str(v).strip()


def _celda_segura(v):
    """Evita que un serial o sede se convierta en fórmula al abrir el Excel."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def importar(db, xlsx_rutas):
    """Lee los Excel y hace upsert en `equipos`. Devuelve (leidos, insertados,
    actualizados, archivos)."""
    import pandas as pd
    con = _con(db)
    leidos = insertados = actualizados = 0
    for ruta in xlsx_rutas:
        if not os.path.isfile(ruta):
            continue
        try:
            xl = pd.ExcelFile(ruta)
        except Exception as e:
            print(f"[inventario] aviso: no pude abrir {os.path.basename(ruta)}: {e}")
            continue
        for hoja in xl.sheet_names:
            try:
                df = xl.parse(hoja)
            except Exception:
                continue
            cols = list(df.columns)
            c_sn = _columna(cols, "serie")
            if not c_sn:
                continue
            c_marca = _columna(cols, "marca")
            c_modelo = _columna(cols, "modelo")
            c_sede = _columna(cols, "sede")
            c_zona = _columna(cols, "zona")
            c_dom = _columna(cols, "domicilio")
            c_est = _columna(cols, "estatus")
            c_cont = _columna(cols, "contador inicial", "contador")
            for _, fila in df.iterrows():
                sn = _texto(fila[c_sn]).upper()
                if not sn or sn in ("NAN", "NONE", "0"):
                    continue
                leidos += 1
                datos = dict(
                    sn=sn,
                    marca=_texto(fila[c_marca]) if c_marca else "",
                    modelo=_texto(fila[c_modelo]) if c_modelo else "",
                    sede=_texto(fila[c_sede]) if c_sede else "",
                    zona=_texto(fila[c_zona]) if c_zona else "",
                    domicilio=_texto(fila[c_dom]) if c_dom else "",
                    estatus=_texto(fila[c_est]) if c_est else "",
                    contador_inicial=_texto(fila[c_cont]) if c_cont else "",
                    origen_hoja=f"{os.path.basename(ruta)}::{hoja}",
                )
                # "SEDE" del Excel puede venir vacía y usar Domicilio (caso Z09)
                if not datos["sede"] and datos["domicilio"]:
                    datos["sede"] = datos["domicilio"]
                existia = con.execute("SELECT 1 FROM equipos WHERE sn=?", (sn,)).fetchone()
                _upsert(con, datos)
                if existia:
                    actualizados += 1
                else:
                    insertados += 1
        con.commit()
    con.close()
    return leidos, insertados, actualizados, len(xlsx_rutas)


def _upsert(con, datos):
    campos = ["sn", "marca", "modelo", "sede", "zona", "domicilio", "estatus",
              "contador_inicial", "origen_hoja"]
    valores = [datos.get(c, "") for c in campos]
    sets = ", ".join(f"{c}=excluded.{c}" for c in campos if c != "sn")
    con.execute(
        f"INSERT INTO equipos(sn, marca, modelo, sede, zona, domicilio, estatus,"
        f" contador_inicial, origen_hoja, actualizado_en)"
        f" VALUES({','.join('?' * len(campos))}, datetime('now'))"
        f" ON CONFLICT(sn) DO UPDATE SET {sets}, actualizado_en=datetime('now')",
        valores)


def exportar(db, destino):
    """Escribe el Excel consolidado que usa el pipeline. Devuelve nº de filas."""
    import openpyxl
    con = _con(db)
    filas = con.execute(
        "SELECT sn, marca, modelo, sede, zona, domicilio, estatus, contador_inicial"
        " FROM equipos ORDER BY sn").fetchall()
    con.close()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inventario (panel)"
    ws.append(COLUMNAS_EXCEL)
    for f in filas:
        ws.append([_celda_segura(v) for v in (
            f["sn"], f["marca"], f["modelo"], f["sede"], f["zona"],
            f["domicilio"], f["estatus"], f["contador_inicial"],
        )])
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    wb.save(destino)
    return len(filas)


def contar(db):
    con = _con(db)
    n = con.execute("SELECT COUNT(*) FROM equipos").fetchone()[0]
    con.close()
    return n


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    accion, db = sys.argv[1], sys.argv[2]
    if accion == "importar":
        print(importar(db, sys.argv[3:]))
        return 0
    if accion == "exportar":
        print(exportar(db, sys.argv[3]))
        return 0
    if accion == "contar":
        print(contar(db))
        return 0
    print(f"acción desconocida: {accion}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
