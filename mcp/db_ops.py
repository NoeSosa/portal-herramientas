#!/usr/bin/env python3
"""Helper del MCP pdf-separator: corre en el servidor (vía sudo) y opera
panel_web.db. Uso: sudo python3 db_ops.py '<json-op>'
Ops: consultar, buscar, resumen, plan, aplicar, exportar, selftest_norm.
Imprime una sola línea JSON al final (stdout). La BD nunca sale del servidor.
"""
import json
import os
import pwd
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "app_ref"))
from normalizacion import (  # noqa: E402
    normalizar_serial,
    normalizar_serial_alterno,
    normalizar_ubicacion,
)

DB_PATH = "/var/lib/docker/volumes/pdf-separator_panel_data/_data/panel_web.db"
VOL = Path(DB_PATH).parent
MCP_DIR = Path(__file__).resolve().parent
SEDE_CONST = "Avenida Constitución Poniente 241"
TZ_MX = ZoneInfo("America/Mexico_City")
COLUMNAS_BUSQUEDA = ("sn", "marca", "modelo", "tipo", "sede", "zona", "domicilio",
                     "ubicacion", "piso", "estatus", "perfil", "ip_fisica",
                     "ip_hp", "ip_ekm", "sustituto_sn", "fecha_alta", "fecha_baja",
                     "actualizado_en")


def conectar_ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    return con


def op_consultar(op: dict) -> dict:
    sn = (op.get("sn") or "").strip().upper()
    if not sn:
        return {"encontrado": False, "error": "sn vacía"}
    con = conectar_ro()
    fila = con.execute("SELECT * FROM equipos WHERE sn=?", (sn,)).fetchone()
    via = "exacta"
    if fila is None:
        filas = con.execute("SELECT * FROM equipos WHERE sn_norm=?",
                            (normalizar_serial(sn),)).fetchall()
        if len(filas) == 1:
            fila, via = filas[0], "sn_norm"
        elif len(filas) > 1:
            return {"encontrado": False, "ambiguas": [f["sn"] for f in filas]}
        else:
            alterna = normalizar_serial_alterno(sn)
            filas = [f for f in con.execute("SELECT * FROM equipos").fetchall()
                     if normalizar_serial_alterno(f["sn"]) == alterna]
            if len(filas) == 1:
                fila, via = filas[0], "alterna"
            elif len(filas) > 1:
                return {"encontrado": False, "ambiguas": [f["sn"] for f in filas]}
    if fila is None:
        return {"encontrado": False, "sn": sn}
    return {"encontrado": True, "via": via, "equipo": dict(fila)}


def op_buscar(op: dict) -> dict:
    con = conectar_ro()
    where, params = [], []
    q = (op.get("q") or "").strip()
    if q:
        where.append("(sn LIKE ? OR marca LIKE ? OR modelo LIKE ? OR sede LIKE ?"
                     " OR ubicacion LIKE ? OR piso LIKE ?)")
        params += [f"%{q}%"] * 6
    for campo in ("sede", "marca", "estatus", "zona", "piso"):
        valor = (op.get(campo) or "").strip()
        if valor:
            where.append(f"{campo}=?")
            params.append(valor)
    clausula = (" WHERE " + " AND ".join(where)) if where else ""
    total = con.execute(f"SELECT COUNT(*) FROM equipos{clausula}", params).fetchone()[0]
    limite = max(1, min(int(op.get("limit") or 50), 200))
    offset = max(0, int(op.get("offset") or 0))
    cols = ", ".join(COLUMNAS_BUSQUEDA)
    filas = con.execute(
        f"SELECT {cols} FROM equipos{clausula} ORDER BY sn LIMIT ? OFFSET ?",
        params + [limite, offset]).fetchall()
    return {"total": total, "limit": limite, "offset": offset,
            "equipos": [dict(f) for f in filas]}


def op_resumen(op: dict) -> dict:
    sede = (op.get("sede") or "").strip()
    con = conectar_ro()
    if sede:
        filas = con.execute("SELECT * FROM equipos WHERE sede=?", (sede,)).fetchall()
    else:
        filas = con.execute("SELECT * FROM equipos").fetchall()
    por_ubic = Counter((f["ubicacion"] or "(sin ubicación)") for f in filas)
    cobertura = {}
    if sede:
        cobertura = {r[0]: r[1] for r in con.execute(
            "SELECT periodo, COUNT(*) FROM cobertura_inventario WHERE sede=?"
            " GROUP BY periodo ORDER BY periodo", (sede,))}
    return {
        "sede": sede or "(todas)",
        "total": len(filas),
        "por_estatus": dict(Counter(f["estatus"] for f in filas)),
        "por_ubicacion": por_ubic.most_common(25),
        "cobertura_periodos": [r[0] for r in con.execute(
            "SELECT periodo FROM cobertura_periodos ORDER BY periodo")],
        "cobertura_inventario_por_periodo": cobertura,
    }


def _correr_plan() -> dict:
    """Corre plan.py (dry-run, solo lectura) y devuelve su stats."""
    out = subprocess.run(
        [sys.executable, str(MCP_DIR / "plan.py")],
        capture_output=True, text=True, timeout=180,
    )
    if out.returncode != 0:
        raise RuntimeError(f"plan.py falló ({out.returncode}): {out.stderr.strip()[-400:]}")
    plan = json.loads((MCP_DIR / "plan.json").read_text(encoding="utf-8"))
    stats = plan["stats"]
    return {"stats": stats, "retires": plan["retires"][:20], "holds": plan["holds"]}


def op_plan(op: dict) -> dict:
    return {"ok": True, **_correr_plan()}


def _verificar() -> dict:
    con = conectar_ro()
    const = list(con.execute("SELECT * FROM equipos WHERE sede=?", (SEDE_CONST,)))
    return {
        "total_equipos": con.execute("SELECT COUNT(*) FROM equipos").fetchone()[0],
        "const_total": len(const),
        "const_estatus": dict(Counter(r["estatus"] for r in const)),
        "const_perfil": sum(1 for r in const if r["perfil"] not in (None, "")),
        "const_tipo": sum(1 for r in const if r["tipo"] not in (None, "")),
        "const_sustituto": sum(1 for r in const if r["sustituto_sn"] not in (None, "")),
        "cobertura_periodos": [r[0] for r in con.execute(
            "SELECT periodo FROM cobertura_periodos ORDER BY periodo")],
    }


def op_aplicar(op: dict) -> dict:
    fecha_baja = (op.get("fecha_baja") or "").strip() or \
        datetime.now(TZ_MX).date().isoformat()
    held = op.get("held") or []
    plan = _correr_plan()
    stats = plan["stats"]
    # 1) Respaldo consistente (API de backup, seguro con WAL).
    backups = MCP_DIR / "backups"
    backups.mkdir(exist_ok=True)
    destino = backups / f"panel_web_pre_mcp_{datetime.now(TZ_MX):%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(str(destino))
    src.backup(dst)
    dst.close()
    src.close()
    # 2) Stage en el volumen (el contenedor corre como opc: chown obligatorio).
    stage = VOL / "mcp_tmp"
    stage.mkdir(exist_ok=True)
    for nombre in ("seed_v2_constitucion.csv", "pares_sustitucion.csv",
                   "apply_in_container.py"):
        shutil.copy(MCP_DIR / nombre, stage / nombre)
    opc = pwd.getpwnam("opc")
    for ruta in [stage, *stage.iterdir()]:
        os.chown(ruta, opc.pw_uid, opc.pw_gid)
    stage.chmod(0o755)
    for ruta in stage.iterdir():
        ruta.chmod(0o644)
    # 3) Aplicar dentro del contenedor (código del propio panel).
    cfg = json.dumps({
        "seed": "/data/mcp_tmp/seed_v2_constitucion.csv",
        "pares": "/data/mcp_tmp/pares_sustitucion.csv",
        "held": held,
        "exp_insertados": stats["inserts"],
        "exp_actualizados": stats["updates_exactos"] + stats["updates_via_norm"],
        "fecha_baja": fecha_baja,
        "registrar_ubicaciones": False,
    })
    out = subprocess.run(
        ["docker", "exec", "pdfsep-web", "python3",
         "/data/mcp_tmp/apply_in_container.py", cfg],
        capture_output=True, text=True, timeout=600,
    )
    if out.returncode != 0:
        return {"ok": False, "error": "apply falló",
                "salida": (out.stdout + out.stderr).strip()[-600:],
                "backup": str(destino)}
    try:
        resultado = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        resultado = {"salida_cruda": out.stdout.strip()[-400:]}
    return {"ok": True, "backup": str(destino),
            "respaldo_plan": {k: stats[k] for k in
                              ("updates_exactos", "updates_via_norm", "inserts",
                               "holds_inserts", "retires")},
            "apply": resultado, "verificacion": _verificar()}


def op_exportar(op: dict) -> dict:
    codigo = (
        "import sys, os; sys.path.insert(0, '/panel_web');"
        "from pathlib import Path;"
        "from app import db, inventario;"
        "con = db.conectar(Path(os.environ.get('DATA_DIR') or '/data') / 'panel_web.db');"
        "print(inventario.exportar_xlsx(con, Path('/data/inventarios/inventario_panel.xlsx')))"
    )
    out = subprocess.run(["docker", "exec", "pdfsep-web", "python3", "-c", codigo],
                         capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        return {"ok": False, "error": "exportar falló",
                "salida": (out.stdout + out.stderr).strip()[-400:]}
    ruta = VOL / "inventarios" / "inventario_panel.xlsx"
    # El scp corre como ubuntu y no puede atravesar el volumen (700 opc):
    # se deja una copia legible en el directorio del MCP.
    copia = MCP_DIR / "exports" / "inventario_panel.xlsx"
    copia.parent.mkdir(exist_ok=True)
    shutil.copy(ruta, copia)
    copia.chmod(0o644)
    st = copia.stat()
    return {"ok": True, "filas": int(out.stdout.strip().splitlines()[-1]),
            "bytes": st.st_size, "mtime": st.st_mtime,
            "ruta_servidor": str(copia)}


def op_selftest_norm(op: dict) -> dict:
    con = conectar_ro()
    sn_mismatch = sum(
        1 for r in con.execute("SELECT sn, sn_norm FROM equipos")
        if normalizar_serial(r[0]) != (r[1] or ""))
    ubi_mismatch = sum(
        1 for r in con.execute("SELECT nombre, normalizado FROM ubicaciones")
        if normalizar_ubicacion(r[0]) != r[1])
    return {"sn_mismatch": sn_mismatch, "ubi_mismatch": ubi_mismatch,
            "equipos": con.execute("SELECT COUNT(*) FROM equipos").fetchone()[0],
            "ubicaciones": con.execute("SELECT COUNT(*) FROM ubicaciones").fetchone()[0]}


OPS = {
    "consultar": op_consultar,
    "buscar": op_buscar,
    "resumen": op_resumen,
    "plan": op_plan,
    "aplicar": op_aplicar,
    "exportar": op_exportar,
    "selftest_norm": op_selftest_norm,
}


def main() -> None:
    try:
        op = json.loads(sys.argv[1])
        fn = OPS[op["op"]]
        print(json.dumps(fn(op), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - el MCP necesita el error en JSON
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
