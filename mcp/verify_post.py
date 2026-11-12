#!/usr/bin/env python3
"""Verificación post-aplicación (solo lectura) de panel_web.db."""
import json
import sqlite3
import sys
from collections import Counter

DB = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/docker/volumes/pdf-separator_panel_data/_data/panel_web.db"
SEDE = "Avenida Constitución Poniente 241"
HELD_VIEJAS = ["CNB1Q5J0P9", "3356P351749", "PHBTW26209"]
HELD_NUEVAS = ["MX3CV8N04Z", "MX3CV7R0KT", "VNG3H10259"]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
out = {}
out["total_equipos"] = con.execute("SELECT COUNT(*) FROM equipos").fetchone()[0]
const = [dict(r) for r in con.execute("SELECT * FROM equipos WHERE sede=?", (SEDE,))]
out["const_total"] = len(const)
out["const_estatus"] = dict(Counter(r["estatus"] for r in const))
for col in ("perfil", "tipo", "ip_fisica", "ip_hp", "ip_ekm", "fecha_alta", "sustituto_sn"):
    out[f"const_{col}_pobladas"] = sum(1 for r in const if r[col] not in (None, ""))
out["retiradas"] = [
    {"sn": r["sn"], "sustituto_sn": r["sustituto_sn"], "fecha_baja": r["fecha_baja"]}
    for r in const if r["estatus"] == "Retirada"
]
out["held_viejas"] = [
    {"sn": r["sn"], "estatus": r["estatus"]}
    for r in const if r["sn"] in HELD_VIEJAS
]
out["held_nuevas_presentes"] = [
    r["sn"] for r in con.execute("SELECT sn FROM equipos WHERE sn IN (?,?,?)", HELD_NUEVAS)
]
out["cobertura_periodos"] = [r[0] for r in con.execute("SELECT periodo FROM cobertura_periodos ORDER BY periodo")]
out["cobertura_inv_2026_11"] = con.execute(
    "SELECT COUNT(*) FROM cobertura_inventario WHERE periodo='2026-11'").fetchone()[0]
out["ubicaciones"] = con.execute("SELECT COUNT(*) FROM ubicaciones").fetchone()[0]
aud = con.execute(
    "SELECT ts, action, detail FROM audit WHERE email='miku-mcp' ORDER BY id DESC LIMIT 1").fetchone()
out["auditoria_mcp"] = dict(aud) if aud else None
print(json.dumps(out, ensure_ascii=False, indent=1))
