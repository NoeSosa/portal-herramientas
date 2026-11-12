#!/usr/bin/env python3
"""MCP pdf-separator (stdio): inventario del panel pdf-separator vía puente SSH.

La BD (panel_web.db) nunca sale del servidor: cada operación corre en
~/pdf-separator-mcp/db_ops.py del servidor (sudo, solo lectura salvo
`aplicar`, que respalda, audita y usa el código del propio panel).
Uso:  .venv/bin/python server.py            -> servidor MCP (stdio)
      .venv/bin/python server.py --selftest -> prueba sin servidor MCP
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

BASE = Path(__file__).resolve().parent
VM_DIR = BASE.parent
SEED_DEFAULT = VM_DIR / "seed_v2_constitucion.csv"
PARES_DEFAULT = VM_DIR / "pares_sustitucion.csv"
EXPORTS_DIR = VM_DIR / "exports"
REMOTE_DIR = "/home/ubuntu/pdf-separator-mcp"
SEDE_CONST = "Avenida Constitución Poniente 241"
HELD_NUEVAS = ["MX3CV8N04Z", "MX3CV7R0KT", "VNG3H10259"]  # lecturas dudosas

_SSH = [
    "ssh", "-i", str(Path.home() / ".ssh" / "muse_tailscale"),
    "-o", f"ProxyCommand=python3 {Path.home() / 'workspace' / 'ssh_tailscale_proxy.py'} %h %p",
    "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=45", "-o", "IdentitiesOnly=yes",
    "ubuntu@pihole",
]
_SCP = [
    "scp", "-i", str(Path.home() / ".ssh" / "muse_tailscale"),
    "-o", f"ProxyCommand=python3 {Path.home() / 'workspace' / 'ssh_tailscale_proxy.py'} %h %p",
    "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=45", "-o", "IdentitiesOnly=yes",
]
_CODIGO_SYNC = ["db_ops.py", "plan.py", "apply_in_container.py"]


def _sh_single(texto: str) -> str:
    return "'" + texto.replace("'", "'\\''") + "'"


def _db_ops(op: dict, timeout: int = 180) -> dict:
    carga = _sh_single(json.dumps(op, ensure_ascii=False))
    remoto = f"sudo python3 {REMOTE_DIR}/db_ops.py {carga}"
    out = subprocess.run(_SSH + [remoto], capture_output=True, text=True,
                         timeout=timeout + 60)
    lineas = [l for l in out.stdout.strip().splitlines() if l.strip()]
    if not lineas:
        raise RuntimeError(f"db_ops sin salida (rc={out.returncode}): "
                           f"{out.stderr.strip()[-300:]}")
    resultado = json.loads(lineas[-1])
    if "error" in resultado:
        raise RuntimeError(str(resultado["error"]))
    return resultado


def _sync_codigo() -> None:
    origenes = [str(BASE / n) for n in _CODIGO_SYNC]
    out = subprocess.run(_SCP + origenes + [f"ubuntu@pihole:{REMOTE_DIR}/"],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"sync de código falló: {out.stderr.strip()[-300:]}")


def _sync_datos() -> None:
    out = subprocess.run(
        _SCP + [str(SEED_DEFAULT), str(PARES_DEFAULT), f"ubuntu@pihole:{REMOTE_DIR}/"],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"sync de datos falló: {out.stderr.strip()[-300:]}")


mcp = FastMCP("pdf-separator")


@mcp.tool()
def consultar_equipo(sn: str) -> dict:
    """Busca un equipo por serie (exacta, sn_norm o alterna OCR).

    Devuelve la fila completa de `equipos` o {encontrado: false}.
    Solo lectura; nunca sale la BD del servidor.
    """
    return _db_ops({"op": "consultar", "sn": sn}, timeout=60)


@mcp.tool()
def buscar_equipos(q: str = "", sede: str = "", estatus: str = "",
                   limit: int = 50) -> dict:
    """Busca equipos por texto (serie/marca/modelo/sede/ubicación/piso) y
    filtros exactos de sede/estatus. Solo lectura (máx. 200 por llamada)."""
    return _db_ops({"op": "buscar", "q": q, "sede": sede, "estatus": estatus,
                    "limit": limit}, timeout=60)


@mcp.tool()
def resumen_sede(sede: str = "") -> dict:
    """Resumen de una sede (o de todo el inventario si sede=''): total,
    conteo por estatus y por ubicación, y cobertura por periodo."""
    return _db_ops({"op": "resumen", "sede": sede}, timeout=60)


@mcp.tool()
def plan_actualizacion_constitucion() -> dict:
    """Dry-run del plan de actualización de Constitución contra la BD real.

    Solo lectura: calcula actualizaciones (145), altas (47), retiros por
    sustitución (11) y pares en pausa (3 lecturas dudosas), sin escribir.
    """
    _sync_codigo()
    _sync_datos()
    return _db_ops({"op": "plan"}, timeout=180)


@mcp.tool()
def aplicar_actualizacion_constitucion(fecha_baja: str = "") -> dict:
    """APLICA la actualización de Constitución (escritura).

    Requiere autorización explícita del usuario. Respalda la BD con la API
    de backup, aplica con el código del propio panel (import + extensiones
    perfil/tipo/IP/fecha_alta + retiros), regenera inventario_panel.xlsx y
    deja fila en la tabla audit. Los 3 pares de lectura dudosa quedan en
    pausa. `fecha_baja` (YYYY-MM-DD) por defecto: hoy en America/Mexico_City.
    """
    _sync_codigo()
    _sync_datos()
    if not fecha_baja:
        fecha_baja = datetime.now(ZoneInfo("America/Mexico_City")).date().isoformat()
    return _db_ops({"op": "aplicar", "held": HELD_NUEVAS,
                    "fecha_baja": fecha_baja}, timeout=600)


@mcp.tool()
def exportar_inventario() -> dict:
    """Regenera inventario_panel.xlsx con el código del panel y lo trae a la
    VM en ~/workspace/pdf-separator/exports/. Sin cambios en la BD."""
    _sync_codigo()
    resultado = _db_ops({"op": "exportar"}, timeout=180)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    destino = EXPORTS_DIR / f"inventario_panel_{datetime.now(ZoneInfo('America/Mexico_City')):%Y%m%d_%H%M%S}.xlsx"
    out = subprocess.run(
        _SCP + [f"ubuntu@pihole:{resultado['ruta_servidor']}", str(destino)],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"descarga del export falló: {out.stderr.strip()[-300:]}")
    resultado["ruta_vm"] = str(destino)
    return resultado


@mcp.tool()
def verificar_normalizacion() -> dict:
    """Compara las funciones de normalización del panel (copia local en
    app_ref/) contra la BD: 0 diferencias = sin deriva tras rebuilds."""
    return _db_ops({"op": "selftest_norm"}, timeout=120)


def _selftest() -> int:
    print("== consultar 3356P351757 ==")
    eq = consultar_equipo("3356P351757")["equipo"]
    print({k: eq[k] for k in ("sn", "marca", "modelo", "sede", "ubicacion",
                                "estatus", "perfil", "tipo")})
    print("== resumen Constitución ==")
    res = resumen_sede(SEDE_CONST)
    print({"total": res["total"], "por_estatus": res["por_estatus"],
           "periodos": res["cobertura_periodos"]})
    print("== plan (dry-run) ==")
    plan = plan_actualizacion_constitucion()["stats"]
    print({k: plan[k] for k in ("updates_exactos", "updates_via_norm", "inserts",
                                "holds_inserts", "retires", "ubicacion_norm_mismatch_corpus")})
    print("== normalización ==")
    print(verificar_normalizacion())
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    mcp.run()
