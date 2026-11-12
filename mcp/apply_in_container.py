#!/usr/bin/env python3
"""Aplica la actualización Constitución DENTRO del contenedor pdfsep-web.

Uso:  python3 apply_in_container.py '<json-config>'
Config: {"seed": ruta_csv, "pares": ruta_csv, "held": [sn...],
         "exp_insertados": 47, "exp_actualizados": 145,
         "fecha_baja": "2026-11-12", "registrar_ubicaciones": false}

Corre con el código del propio panel (db/inventario/normalizacion importados
de /panel_web): el núcleo se aplica con inventario.importar_xlsx (misma
semántica que una importación del panel, incluida la reconciliación de
cobertura del mes abierto) y las extensiones (perfil/tipo/IP/fecha_alta) y
los retiros por sustitución se aplican con SQL acotado. Imprime una sola
línea JSON con el resultado. Si el conteo del import no coincide con lo
esperado, sale con código 3 ANTES de tocar extensiones/retiros.
"""
import csv
import ipaddress
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/panel_web")
os.chdir("/panel_web")

from openpyxl import Workbook  # noqa: E402

from app import db, inventario  # noqa: E402
from app.normalizacion import normalizar_ubicacion  # noqa: E402

SEDE_DB = "Avenida Constitución Poniente 241"
ESTATUS_MAP = {
    "En Operacion": "En Operación",
    "En Operación": "En Operación",
    "Dañada": "Dañada",
    "Almacenada": "Almacenada",
}


def ip_limpia(valor: str) -> str | None:
    texto = (valor or "").replace(" ", "").strip()
    if texto in ("", "0"):
        return None
    try:
        return str(ipaddress.ip_address(texto))
    except ValueError:
        return None


def fecha_limpia(valor: str) -> str | None:
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto).date().isoformat()
    except ValueError:
        return None


def main() -> None:
    cfg = json.loads(sys.argv[1])
    seed_path, pares_path = Path(cfg["seed"]), Path(cfg["pares"])
    held = set(cfg.get("held") or [])
    fecha_baja = cfg.get("fecha_baja") or "2026-11-12"
    data_dir = Path(os.environ.get("DATA_DIR") or "/data")
    tmp_dir = data_dir / "mcp_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    seed = [s for s in csv.DictReader(open(seed_path, newline="", encoding="utf-8"))
            if s["serie"] not in held]
    pares = [p for p in csv.DictReader(open(pares_path, newline="", encoding="utf-8"))
             if p["nueva"] not in held]

    # 1) XLSX de importación en el formato exacto del panel (10 columnas).
    xlsx_tmp = tmp_dir / "import_mcp_constitucion.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Constitucion MCP"
    ws.append(inventario.COLUMNAS_EXCEL)
    for s in seed:
        ws.append([s["serie"], s["marca"], s["modelo"], SEDE_DB, "9", SEDE_DB,
                   s["juzgado"], s["piso"], ESTATUS_MAP[s["estatus"]], s["contador"]])
    wb.save(xlsx_tmp)
    wb.close()

    con = db.conectar(data_dir / "panel_web.db")

    # 2) Importación con el código del panel (upserts + cobertura mes abierto).
    res = inventario.importar_xlsx(con, xlsx_tmp)
    esperado = {"leidos": len(seed),
                "insertados": cfg["exp_insertados"],
                "actualizados": cfg["exp_actualizados"]}
    if any(res[k] != v for k, v in esperado.items()):
        print(json.dumps({"ok": False, "error": "conteo_import_no_esperado",
                          "import": res, "esperado": esperado}, ensure_ascii=False))
        raise SystemExit(3)

    # 3) Extensiones: perfil/tipo/IP/fecha_alta (solo llena; nunca blanquea).
    ext = {"perfil": 0, "tipo": 0, "ip_fisica": 0, "ip_hp": 0, "ip_ekm": 0, "fecha_alta": 0}
    for s in seed:
        sets, vals = [], []
        if s["perfil"]:
            sets.append("perfil=?"); vals.append(s["perfil"])
        if s["equipo"]:
            sets.append("tipo=?"); vals.append(s["equipo"])
        for col in ("ip_fisica", "ip_hp", "ip_ekm"):
            limpia = ip_limpia(s[col])
            if limpia:
                sets.append(f"{col}=?"); vals.append(limpia)
                ext[col] += 1
        fa = fecha_limpia(s["fecha_impl"])
        if fa:
            sets.append("fecha_alta=?"); vals.append(fa)
        if not sets:
            continue
        vals.append(s["serie"])
        cur = con.execute(
            f"UPDATE equipos SET {', '.join(sets)}, actualizado_en=datetime('now') WHERE sn=?",
            vals,
        )
        if cur.rowcount:
            if s["perfil"]:
                ext["perfil"] += 1
            if s["equipo"]:
                ext["tipo"] += 1
            if fa:
                ext["fecha_alta"] += 1
    con.commit()

    # 4) Retiros por sustitución (nunca borra la fila).
    retires = []
    for p in pares:
        cur = con.execute(
            "UPDATE equipos SET estatus='Retirada', fecha_baja=?, sustituto_sn=?,"
            " actualizado_en=datetime('now') WHERE sn=? AND sede=?",
            (fecha_baja, p["nueva"], p["vieja"], SEDE_DB),
        )
        retires.append({"vieja": p["vieja"], "nueva": p["nueva"], "filas": cur.rowcount})
    con.commit()

    # 5) Registro de ubicaciones nuevas (solo si el panel lo hace en import).
    ubi_nuevas = 0
    if cfg.get("registrar_ubicaciones"):
        existentes = {r[0] for r in con.execute("SELECT nombre FROM ubicaciones")}
        for juzgado in sorted({s["juzgado"] for s in seed}):
            if juzgado not in existentes:
                con.execute(
                    "INSERT OR IGNORE INTO ubicaciones(normalizado, nombre, actualizado_en)"
                    " VALUES(?,?,datetime('now'))",
                    (normalizar_ubicacion(juzgado), juzgado),
                )
                ubi_nuevas += 1
        con.commit()

    # 6) Regenerar el Excel que lee el pipeline + auditoría.
    filas_export = inventario.exportar_xlsx(con, data_dir / "inventarios" / "inventario_panel.xlsx")
    detalle = json.dumps({"import": res, "ext": ext,
                          "retires": sum(1 for r in retires if r["filas"] == 1),
                          "holds": len(held), "export_filas": filas_export},
                         ensure_ascii=False)
    db.auditar(con, user_id=None, email="miku-mcp",
               action="miku_mcp_actualizacion_constitucion", detail=detalle)
    print(json.dumps({"ok": True, "import": res, "extensiones": ext,
                      "retiros": retires, "ubicaciones_nuevas": ubi_nuevas,
                      "export_filas": filas_export}, ensure_ascii=False))


if __name__ == "__main__":
    main()
