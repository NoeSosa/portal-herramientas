#!/usr/bin/env python3
"""Rellena el TEXTO (y las cajas, si las hay) de las hojas ya registradas.

Los JSONL de `auto/progress/<job_id>.ocr.jsonl` conservan la lectura completa de
cada hoja. Cuando una fila de `eventos` se creó desde el log (sin texto), este
script la completa sin volver a pasar OCR.

✅ VALIDADO: 18/oct/2026 — recuperó el texto de las corridas 15-18 de Noe.
📘 EJEMPLO:
    ./venv/bin/python separador_pdfs/panel/backfill_ocr_texto.py --db separador_pdfs/panel/panel.db \
        --progress separador_pdfs/auto/progress --dry
    (quitar --dry para escribir)
"""
import argparse
import json
import os
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--dry", action="store_true", help="solo contar, no escribir")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        print(f"no existe la base: {args.db}")
        return 2
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    total = rellenas = 0
    for nombre in sorted(os.listdir(args.progress)):
        if not nombre.endswith(".ocr.jsonl"):
            continue
        job_id = nombre[:-len(".ocr.jsonl")]
        ruta = os.path.join(args.progress, nombre)
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for linea in f:
                try:
                    ev = json.loads(linea)
                except ValueError:
                    continue
                if (ev.get("tipo") or "ocr_pagina") != "ocr_pagina":
                    continue
                pdf, pag = ev.get("pdf"), ev.get("pag")
                if not pdf or not pag:
                    continue
                total += 1
                fila = con.execute(
                    "SELECT id FROM eventos WHERE job_id=? AND tipo='ocr_pagina'"
                    " AND pdf=? AND pag=? AND (texto IS NULL OR texto='')",
                    (job_id, pdf, int(pag))).fetchone()
                if not fila:
                    continue
                if not args.dry:
                    con.execute(
                        "UPDATE eventos SET texto=?, cajas=COALESCE(cajas, ?),"
                        " ms_pagina=COALESCE(ms_pagina, ?),"
                        " sn=CASE WHEN COALESCE(sn,'')='' THEN ? ELSE sn END"
                        " WHERE id=?",
                        ((ev.get("texto") or "")[:900],
                         json.dumps(ev.get("cajas") or [], ensure_ascii=False,
                                    separators=(",", ":")) if ev.get("cajas") else None,
                         ev.get("ms"), ev.get("sn") or "", fila["id"]))
                rellenas += 1
    if not args.dry:
        con.commit()
    print(f"{'[dry] ' if args.dry else ''}hojas en los JSONL: {total} · "
          f"filas completadas: {rellenas}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
