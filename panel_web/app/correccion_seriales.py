"""Corrige seriales OCR de un job ya procesado sin repetir el OCR.

La lectura original se conserva en el texto OCR; lo que se corrige es la
identidad usada para `job_pages` y para los nombres dentro del ZIP. Solo se
renombra cuando la verificación alterna del inventario produce una coincidencia
única.
"""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import db

logger = logging.getLogger(__name__)


def _mapa_canonicos(con, job_id: str) -> dict[str, str]:
    """Devuelve {serial_ocr: serial_inventario} solo para coincidencias únicas."""
    filas = con.execute(
        "SELECT DISTINCT sn FROM job_pages"
        " WHERE job_id=? AND COALESCE(sn,'')<>''",
        (job_id,),
    ).fetchall()
    mapa: dict[str, str] = {}
    for fila in filas:
        sn = str(fila["sn"]).strip()
        if not sn:
            continue
        try:
            equipo = db.buscar_equipo(con, sn)
        except db.SerialAmbiguo:
            continue
        if equipo and equipo["sn"] != sn:
            mapa[sn] = equipo["sn"]
    return mapa


def _nombre_corregido(nombre: str, mapa: dict[str, str]) -> str:
    """Renombra el prefijo del serial conservando __marca__ y _pagN."""
    for viejo in sorted(mapa, key=len, reverse=True):
        if not nombre.startswith(viejo):
            continue
        resto = nombre[len(viejo):]
        # Evita que "ABC" renombre "ABCD...".
        if resto and resto[0] not in "._-":
            continue
        return mapa[viejo] + resto
    return nombre


def _reescribir_zip(ruta: Path, mapa: dict[str, str]) -> bool:
    """Reescribe el ZIP de forma atómica. Devuelve True si cambió algo."""
    if not mapa or not ruta.is_file():
        return False
    cambios = False
    fd, ruta_temp = tempfile.mkstemp(
        prefix=".resultado-", suffix=".zip", dir=str(ruta.parent),
    )
    os.close(fd)
    temp = Path(ruta_temp)
    usados: set[str] = set()
    try:
        with zipfile.ZipFile(ruta) as origen, zipfile.ZipFile(
            temp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True,
        ) as destino:
            for info in origen.infolist():
                nuevo = info.filename
                if not info.is_dir():
                    padre = PurePosixPath(info.filename).parent
                    original = PurePosixPath(info.filename).name
                    cambiado = _nombre_corregido(original, mapa)
                    if cambiado != original:
                        cambios = True
                    nombre = cambiado
                    if nombre in usados:
                        base = PurePosixPath(nombre)
                        contador = 2
                        while True:
                            candidato = str(
                                base.with_name(
                                    f"{base.stem}__dup{contador}{base.suffix}"
                                )
                            )
                            if candidato not in usados:
                                nombre = candidato
                                break
                            contador += 1
                    usados.add(nombre)
                    nuevo = str(padre / nombre) if str(padre) != "." else nombre
                # Los PDFs escaneados ya vienen comprimidos; igual que el worker.
                compresion = (
                    zipfile.ZIP_STORED if nuevo.lower().endswith(".pdf")
                    else zipfile.ZIP_DEFLATED
                )
                datos = origen.read(info)
                copia = zipfile.ZipInfo(nuevo, date_time=info.date_time)
                copia.compress_type = compresion
                copia.external_attr = info.external_attr
                destino.writestr(copia, datos)
        if cambios:
            os.chmod(temp, 0o600)
            os.replace(temp, ruta)
        return cambios
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def reparar_seriales_job(con, job_id: str, ruta_zip: str | Path) -> dict[str, Any]:
    """Corrige job_pages y el ZIP de un job. Es idempotente y seguro de repetir."""
    job = db.job_por_id(con, job_id)
    if not job:
        return {"cambiados": {}, "zip": False}
    mapa = _mapa_canonicos(con, job_id)
    if not mapa:
        return {"cambiados": {}, "zip": False}
    ruta = Path(ruta_zip)
    cambio_zip = _reescribir_zip(ruta, mapa)
    if not cambio_zip:
        # Si el ZIP ya estaba corregido, aún hay que reparar job_pages.
        logger.info("ZIP de %s sin cambios de nombre; se revisa job_pages", job_id)
    for viejo, nuevo in mapa.items():
        con.execute(
            "UPDATE job_pages SET sn=? WHERE job_id=? AND sn=?",
            (nuevo, job_id, viejo),
        )
        # Evita dejar dos lecturas del mismo job con el serial viejo y el nuevo.
        existe_lectura = con.execute(
            "SELECT 1 FROM cobertura_lecturas WHERE job_id=? AND sn=?",
            (job_id, nuevo),
        ).fetchone()
        if existe_lectura:
            con.execute(
                "DELETE FROM cobertura_lecturas WHERE job_id=? AND sn=?",
                (job_id, viejo),
            )
        else:
            con.execute(
                "UPDATE cobertura_lecturas SET sn=? WHERE job_id=? AND sn=?",
                (nuevo, job_id, viejo),
            )
        # Si ya existe la fila canónica de cobertura, se descarta la vieja.
        con.execute(
            "UPDATE OR IGNORE cobertura_escaneos SET sn=?"
            " WHERE job_id=? AND sn=?",
            (nuevo, job_id, viejo),
        )
        con.execute(
            "DELETE FROM cobertura_escaneos WHERE job_id=? AND sn=?",
            (job_id, viejo),
        )
    db.recalcular_duplicados(con)
    con.commit()
    return {"cambiados": mapa, "zip": cambio_zip}
