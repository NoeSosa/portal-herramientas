"""Importación/exportación del inventario que usa el pipeline.

El pipeline lee `/data/inventarios/inventario_panel.xlsx` (columnas Serie,
Marca, Modelo, SEDE, ZONA, Domicilio, Estatus, Contador inicial). Este módulo
importa Excel de cualquier formato razonable y regenera ese archivo después de
cada alta/edición/baja desde Administración.
"""

from __future__ import annotations

import os
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from . import db

COLUMNAS_EXCEL = ["Serie", "Marca", "Modelo", "SEDE", "ZONA", "Domicilio",
                  "Ubicación", "Piso", "Estatus", "Contador inicial"]


def _norm(valor: Any) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().replace("_", " ").split())


def _mapa_columnas(cabeceras: list[Any]) -> dict[str, int]:
    mapa: dict[str, int] = {}
    for i, cab in enumerate(cabeceras):
        n = _norm(cab)
        if not n:
            continue
        if "serie" in n or n in {"sn", "serial", "numero de serie", "no de serie"}:
            mapa.setdefault("sn", i)
        elif "marca" in n:
            mapa.setdefault("marca", i)
        elif "modelo" in n:
            mapa.setdefault("modelo", i)
        elif "sede" in n:
            mapa.setdefault("sede", i)
        elif n in {"localidad", "ciudad", "municipio"}:
            mapa.setdefault("localidad", i)
        elif "zona" in n:
            mapa.setdefault("zona", i)
        elif "ubicacion" in n or n in {"area", "área"}:
            mapa.setdefault("ubicacion", i)
        elif n == "piso" or "nivel" in n or "planta" in n:
            mapa.setdefault("piso", i)
        elif "ip" in n and ("direccion" in n or n == "ip"):
            mapa.setdefault("ip_fisica", i)
        elif "domicilio" in n or "direccion" in n:
            mapa.setdefault("domicilio", i)
        elif n in {"estatus", "status"}:
            mapa.setdefault("estatus", i)
        elif "contador" in n:
            mapa.setdefault("contador_inicial", i)
    return mapa


def _texto(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip()
    return "" if texto.lower() in {"nan", "none"} else texto


def importar_xlsx(con: sqlite3.Connection, ruta: Path) -> dict[str, int]:
    """Importa todas las hojas con cabecera de Serie/SN. No borra lo existente."""
    wb = load_workbook(ruta, read_only=True, data_only=True)
    leidos = insertados = actualizados = hojas = 0
    try:
        for ws in wb.worksheets:
            filas = ws.iter_rows(values_only=True)
            cabecera = None
            for _ in range(15):
                try:
                    posible = list(next(filas))
                except StopIteration:
                    break
                mapa = _mapa_columnas(posible)
                if "sn" in mapa:
                    cabecera = mapa
                    break
            if not cabecera:
                continue
            hojas += 1
            for fila in filas:
                if leidos >= 50000:
                    break
                sn = _texto(fila[cabecera["sn"]]).upper() if cabecera["sn"] < len(fila) else ""
                if not sn or sn in {"NAN", "NONE", "0"}:
                    continue
                existia = con.execute(
                    "SELECT 1 FROM equipos WHERE sn=?", (sn,)
                ).fetchone() is not None
                def valor(campo: str) -> str:
                    idx = cabecera.get(campo)
                    if idx is None or idx >= len(fila):
                        return ""
                    return _texto(fila[idx])
                sede = valor("sede") or valor("localidad")
                domicilio = valor("domicilio")
                if not sede and domicilio:
                    sede = domicilio
                datos = {
                    "marca": valor("marca"), "modelo": valor("modelo"),
                    "sede": sede, "zona": valor("zona"), "domicilio": domicilio,
                    "ubicacion": valor("ubicacion"), "piso": valor("piso"),
                    "estatus": valor("estatus"),
                    "contador_inicial": valor("contador_inicial"),
                    "origen_hoja": f"{ruta.name}::{ws.title}",
                }
                db.upsert_equipo(con, sn, **datos)
                leidos += 1
                if existia:
                    actualizados += 1
                else:
                    insertados += 1
    finally:
        wb.close()
    return {"leidos": leidos, "insertados": insertados,
            "actualizados": actualizados, "hojas": hojas}


def exportar_xlsx(con: sqlite3.Connection, destino: Path) -> int:
    """Regenera el Excel consolidado que lee el pipeline. Devuelve filas."""
    filas = con.execute(
        "SELECT sn, marca, modelo, sede, zona, domicilio, ubicacion, piso,"
        " estatus, contador_inicial FROM equipos ORDER BY sn"
    ).fetchall()
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario (panel)"
    ws.append(COLUMNAS_EXCEL)
    for f in filas:
        ws.append([f["sn"], f["marca"] or "", f["modelo"] or "", f["sede"] or "",
                   f["zona"] or "", f["domicilio"] or "", f["ubicacion"] or "",
                   f["piso"] or "", f["estatus"] or "",
                   f["contador_inicial"] or ""])
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".xlsx.tmp")
    wb.save(tmp)
    wb.close()
    os.replace(tmp, destino)
    return len(filas)


def info_exportado(destino: Path) -> dict[str, Any] | None:
    if not destino.is_file():
        return None
    st = destino.stat()
    return {"nombre": destino.name, "bytes": st.st_size,
            "mtime": st.st_mtime}
