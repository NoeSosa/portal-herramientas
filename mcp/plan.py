#!/usr/bin/env python3
"""Dry-run del plan de actualización Constitución -> panel_web.db (solo lectura).

Uso (en el servidor, con sudo por el volumen):
  sudo python3 plan.py [seed_csv] [pares_csv] [db_path] [salida_json]

No escribe nada en la BD. Importa normalizacion.py del panel (copia byte-exacta
en app_ref/) para que sn_norm / ubicacion_norm sean idénticos a los del panel.
"""
import csv
import ipaddress
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app_ref"))
from normalizacion import normalizar_serial, normalizar_serial_alterno, normalizar_ubicacion  # noqa: E402

SEDE_DB = "Avenida Constitución Poniente 241"
RE_SN = re.compile(r"[A-Z0-9][A-Z0-9 _-]{0,255}")
ESTATUS_MAP = {
    "En Operacion": "En Operación",
    "En Operación": "En Operación",
    "Dañada": "Dañada",
    "Almacenada": "Almacenada",
}
HELD_NUEVAS = {"MX3CV8N04Z", "MX3CV7R0KT", "VNG3H10259"}  # lecturas dudosas, en pausa
PERFILES_OK = {"A1", "A2", "C1", "D", ""}


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
    seed_csv = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/pdf-separator-mcp/seed_v2_constitucion.csv"
    pares_csv = sys.argv[2] if len(sys.argv) > 2 else "/home/ubuntu/pdf-separator-mcp/pares_sustitucion.csv"
    db_path = sys.argv[3] if len(sys.argv) > 3 else "/var/lib/docker/volumes/pdf-separator_panel_data/_data/panel_web.db"
    salida = sys.argv[4] if len(sys.argv) > 4 else "/home/ubuntu/pdf-separator-mcp/plan.json"

    seed = list(csv.DictReader(open(seed_csv, newline="", encoding="utf-8")))
    pares = list(csv.DictReader(open(pares_csv, newline="", encoding="utf-8")))
    assert len(seed) == 195 and len(pares) == 14, (len(seed), len(pares))

    errores = []
    for s in seed:
        if not RE_SN.fullmatch(s["serie"]):
            errores.append(f"sn inválida: {s['serie']!r}")
        if s["estatus"] not in ESTATUS_MAP:
            errores.append(f"estatus sin mapeo: {s['serie']} {s['estatus']!r}")
        if s["perfil"] not in PERFILES_OK:
            errores.append(f"perfil raro: {s['serie']} {s['perfil']!r}")
    # colisiones de sn_norm dentro de la semilla (el panel las fusionaría)
    por_norm: dict[str, list[str]] = {}
    for s in seed:
        por_norm.setdefault(normalizar_serial(s["serie"]), []).append(s["serie"])
    colisiones_seed = {k: v for k, v in por_norm.items() if len(v) > 1}

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    estatus_db = {r[0] for r in con.execute("SELECT DISTINCT estatus FROM equipos")}
    assert set(ESTATUS_MAP.values()) <= estatus_db, (set(ESTATUS_MAP.values()) - estatus_db)

    por_sn = {r["sn"]: dict(r) for r in con.execute("SELECT * FROM equipos")}
    por_norm_db: dict[str, list[str]] = {}
    for sn in por_sn:
        por_norm_db.setdefault(normalizar_serial(sn), []).append(sn)
    const = {sn: r for sn, r in por_sn.items() if r["sede"] == SEDE_DB}
    ubic = {r["nombre"]: r["normalizado"] for r in con.execute("SELECT nombre, normalizado FROM ubicaciones")}
    alterna_index: dict[str, list[str]] = {}
    for sn in por_sn:
        alterna_index.setdefault(normalizar_serial_alterno(sn), []).append(sn)
    # auto-test de normalizar_ubicacion contra el corpus del panel (395)
    ubi_mismatch = sum(1 for n, nm in ubic.items() if normalizar_ubicacion(n) != nm)

    plan = {"updates": [], "updates_norm": [], "inserts": [], "holds": [], "retires": [], "stats": {}}
    diffs = Counter()
    ip_stats = Counter()
    fecha_stats = Counter()
    for s in seed:
        sn = s["serie"]
        fila = const.get(sn)
        via_norm = None
        if fila is None:
            cand = [c for c in por_norm_db.get(normalizar_serial(sn), []) if c in const]
            if len(cand) == 1:
                via_norm = cand[0]
                fila = const[via_norm]
        destino = "retener" if sn in HELD_NUEVAS else None
        item = {
            "sn": sn, "sn_db": via_norm, "marca": s["marca"], "modelo": s["modelo"],
            "equipo": s["equipo"], "perfil": s["perfil"] or None,
            "estatus": ESTATUS_MAP[s["estatus"]], "juzgado": s["juzgado"],
            "ubicacion_norm": normalizar_ubicacion(s["juzgado"]),
            "ip_fisica": ip_limpia(s["ip_fisica"]), "ip_hp": ip_limpia(s["ip_hp"]),
            "ip_ekm": ip_limpia(s["ip_ekm"]), "piso": s["piso"], "contador": s["contador"],
            "fecha_alta": fecha_limpia(s["fecha_impl"]),
        }
        for col in ("ip_fisica", "ip_hp", "ip_ekm"):
            crudo = (s[col] or "").replace(" ", "").strip()
            if crudo in ("", "0"):
                ip_stats[f"{col}_vacia"] += 1
            elif item[col]:
                ip_stats[f"{col}_ok"] += 1
            else:
                ip_stats[f"{col}_invalida"] += 1
        fecha_stats["ok" if item["fecha_alta"] else "sin_fecha"] += 1
        if fila is not None:
            viejo = {k: ("" if fila[k] is None else str(fila[k])) for k in
                     ("marca", "modelo", "ubicacion", "estatus", "piso", "contador_inicial")}
            nuevo = {"marca": s["marca"], "modelo": s["modelo"], "ubicacion": s["juzgado"],
                     "estatus": ESTATUS_MAP[s["estatus"]], "piso": s["piso"],
                     "contador_inicial": s["contador"]}
            for k in viejo:
                if viejo[k] != nuevo[k]:
                    diffs[k] += 1
            if normalizar_ubicacion(fila["ubicacion"] or "") != item["ubicacion_norm"]:
                diffs["ubicacion_norm"] += 1
            (plan["updates_norm"] if via_norm else plan["updates"]).append(item)
        elif destino:
            plan["holds"].append(item)
        else:
            plan["inserts"].append(item)

    seed_sns = {s["serie"] for s in seed}
    for p in pares:
        vieja, nueva = p["vieja"], p["nueva"]
        assert vieja not in seed_sns and nueva in seed_sns, p
        dbv = const.get(vieja)
        plan["retires"].append({
            "vieja": vieja, "nueva": nueva, "en_db": dbv is not None,
            "estatus_actual": dbv["estatus"] if dbv else None,
            "held": nueva in HELD_NUEVAS,
        })
    plan["retires"] = [r for r in plan["retires"] if not r["held"]]
    holds_pares = [r for r in
                   [{"vieja": p["vieja"], "nueva": p["nueva"]} for p in pares if p["nueva"] in HELD_NUEVAS]]

    plan["stats"] = {
        "updates_exactos": len(plan["updates"]),
        "updates_via_norm": len(plan["updates_norm"]),
        "inserts": len(plan["inserts"]),
        "holds_inserts": len(plan["holds"]),
        "retires": len(plan["retires"]),
        "holds_pares": holds_pares,
        "diffs_core": dict(diffs),
        "ip": dict(ip_stats),
        "fecha_alta": dict(fecha_stats),
        "perfil_fills": sum(1 for i in plan["updates"] + plan["updates_norm"] + plan["inserts"] if i["perfil"]),
        "tipo_fills": sum(1 for i in plan["updates"] + plan["updates_norm"] + plan["inserts"] if i["equipo"]),
        "juzgados_seed": sorted({s["juzgado"] for s in seed}),
        "juzgado_en_ubicaciones": {j: (j in ubic) for j in sorted({s["juzgado"] for s in seed})},
        "ubicacion_norm_mismatch_corpus": ubi_mismatch,
        "inserts_por_juzgado": dict(Counter(i["juzgado"] for i in plan["inserts"])),
        "errores": errores,
        "colisiones_sn_norm_seed": colisiones_seed,
        "total_equipos_antes": len(por_sn),
        "const_antes": len(const),
    }
    plan["stats"]["alterna_candidatas"] = [
        {"seed": i["sn"], "db": c}
        for i in plan["inserts"]
        for c in alterna_index.get(normalizar_serial_alterno(i["sn"]), [])
        if c in const and c != i["sn"]
    ]
    Path(salida).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(plan["stats"], ensure_ascii=False, indent=1))
    print("updates_norm:", [(i["sn"], i["sn_db"]) for i in plan["updates_norm"]])
    print("holds:", [i["sn"] for i in plan["holds"]])
    print("retires:", [(r["vieja"], r["nueva"], r["estatus_actual"]) for r in plan["retires"]])


if __name__ == "__main__":
    main()
