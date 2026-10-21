#!/home/node/.openclaw/workspace/venv/bin/python
"""
panel_local.py — Gestor de ARCHIVOS LOCALES del panel del separador (modo local).
================================================================================
Con `modo: "local"` (por defecto) el panel no habla con Drive: todo pasa por disco.

  separador_pdfs/entrada_local/            ← PDFs por procesar (subida desde la UI)
  separador_pdfs/entrada_local/procesados/ ← PDFs ya procesados (el pipeline los mueve)
  separador_pdfs/inventarios_local/*.xlsx  ← inventarios que usa el pipeline en local
  separador_pdfs/auto/salida/Separador_<fecha>/<Sede>/{VERIFICADOS,POR_VERIFICAR}/

Funciones: listar entrada (con nº de páginas), subir/borrar, explorador de
resultados, ZIP por sede (sustituye al link de Drive), espacio en disco y limpieza
de corridas viejas. Nada de esto llama a rclone.

✅ VALIDADO: 16/oct/2026 (Ches) — entrada listada con páginas, borrado, ZIP por sede,
   descarga individual, espacio y limpieza, todo sin rclone (ver docs/panel-separador.md).
"""
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime

import panel_config as pc

CARPETAS_SEDE = ("VERIFICADOS", "POR_VERIFICAR")
# Archivos informativos de la corrida que también se pueden descargar
ARCHIVOS_RUN = ("00_LEEME.txt", "01_RESUMEN_GENERAL.txt")


# ------------------------------------------------------------------ utilidades
def _seguro(nombre):
    """Nombre de archivo/carpeta sin rutas ni '..' (anti path-traversal)."""
    n = os.path.basename(str(nombre or "").strip())
    return n if n and n not in (".", "..") and "/" not in n and "\\" not in n else ""


def _dentro(base, *partes):
    """Ruta absoluta dentro de `base` o None si se sale (anti path-traversal)."""
    base_abs = os.path.abspath(base)
    ruta = os.path.abspath(os.path.join(base_abs, *[p for p in partes if p]))
    return ruta if ruta == base_abs or ruta.startswith(base_abs + os.sep) else None


def paginas_de(rutas, timeout=180):
    """Nº de páginas de cada PDF, usando el venv que tiene PyMuPDF
    (venv-pdfseparador). Devuelve {ruta: n|None}."""
    rutas = [r for r in rutas if r]
    if not rutas:
        return {}
    codigo = ("import fitz,json,sys\n"
              "out={}\n"
              "for r in sys.argv[1:]:\n"
              "    try:\n"
              "        d=fitz.open(r); out[r]=len(d); d.close()\n"
              "    except Exception:\n"
              "        out[r]=None\n"
              "print(json.dumps(out))\n")
    try:
        r = subprocess.run([pc.PY_VENV_PDF, "-c", codigo] + rutas, capture_output=True,
                           text=True, timeout=timeout)
        import json
        for linea in reversed((r.stdout or "").splitlines()):
            if linea.startswith("{"):
                return json.loads(linea)
    except Exception:
        pass
    return {r: None for r in rutas}


def _tam_dir(ruta, limite=200000):
    """Tamaño de un árbol de directorios (sin seguir enlaces), con tope de archivos."""
    total, n = 0, 0
    for raiz, _dirs, archivos in os.walk(ruta):
        for a in archivos:
            n += 1
            if n > limite:
                return total
            try:
                total += os.path.getsize(os.path.join(raiz, a))
            except OSError:
                pass
    return total


# ------------------------------------------------------------------ entrada local
def listar_entrada(cfg):
    """PDFs pendientes en la entrada local, con tamaño, fecha y nº de páginas.
    En modo drive delega en la versión de Drive (código intacto)."""
    if cfg.get("modo", "local") == "drive":
        return pc.listar_entrada_drive(cfg)
    dir_entrada = pc.entrada_local(cfg)
    os.makedirs(dir_entrada, exist_ok=True)
    nombres = sorted(n for n in os.listdir(dir_entrada)
                     if os.path.isfile(os.path.join(dir_entrada, n)))
    rutas = [os.path.join(dir_entrada, n) for n in nombres]
    pags = paginas_de(rutas)
    archivos = []
    for n in nombres:
        ruta = os.path.join(dir_entrada, n)
        try:
            st = os.stat(ruta)
        except OSError:
            continue
        archivos.append({
            "Name": n,
            "Size": st.st_size,
            "ModTime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "paginas": pags.get(ruta),
            "es_pdf": n.lower().endswith(".pdf"),
            "encolado": False,
        })
    return archivos, ""


def ruta_pdf_proceso(cfg, nombre):
    """Localiza un PDF en la entrada local o en procesados/ (para la miniatura).
    Solo acepta un nombre de archivo, nunca una ruta (anti path-traversal)."""
    n = _seguro(nombre)
    if not n:
        return None
    for base in (pc.entrada_local(cfg), pc.procesados_local(cfg)):
        ruta = _dentro(base, n)
        if ruta and os.path.isfile(ruta):
            return ruta
    return None


def ruta_entrada(cfg, nombre):
    """Ruta absoluta de un archivo dentro de la entrada local (o None)."""
    n = _seguro(nombre)
    return _dentro(pc.entrada_local(cfg), n) if n else None


def nombre_libre(directorio, nombre):
    """Evita pisar un archivo existente: agrega _2, _3… antes de la extensión."""
    n = _seguro(nombre)
    if not n:
        return None
    destino = os.path.join(directorio, n)
    if not os.path.exists(destino):
        return n
    tallo, ext = os.path.splitext(n)
    for i in range(2, 1000):
        cand = f"{tallo}_{i}{ext}"
        if not os.path.exists(os.path.join(directorio, cand)):
            return cand
    return f"{tallo}_{int(time.time())}{ext}"


def borrar_entrada(cfg, nombre):
    """Borra un PDF de la entrada local (solo dentro de entrada_local/)."""
    ruta = ruta_entrada(cfg, nombre)
    if not ruta or not os.path.isfile(ruta):
        return False, "no existe"
    try:
        os.remove(ruta)
        return True, ""
    except OSError as e:
        return False, str(e)


def listar_procesados(cfg):
    """PDFs que el pipeline ya movió a entrada_local/procesados/."""
    d = pc.procesados_local(cfg)
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        ruta = os.path.join(d, n)
        if os.path.isfile(ruta):
            try:
                st = os.stat(ruta)
            except OSError:
                continue
            out.append({"Name": n, "Size": st.st_size,
                        "ModTime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    return out


def inventarios(cfg):
    """Inventarios locales que usará el pipeline (misma lógica que separador_auto)."""
    import glob
    cands = []
    inv_dir = pc.inventarios_local(cfg)
    if os.path.isdir(inv_dir):
        cands += sorted(glob.glob(os.path.join(inv_dir, "*.xlsx")))
    cands += sorted(glob.glob(os.path.join(pc.SEP_DIR, "inventario*.xlsx")))
    vistos, out = set(), []
    for c in cands:
        n = os.path.basename(c)
        if n in vistos:
            continue
        vistos.add(n)
        out.append({"Name": n, "Size": os.path.getsize(c)})
    return out


# ------------------------------------------------------------------ resultados
def listar_resultados(cfg):
    """Árbol de resultados: corrida → sede → {VERIFICADOS, POR_VERIFICAR, otros}."""
    base = pc.SALIDA_LOCAL
    if not os.path.isdir(base):
        return []
    corridas = []
    for nombre in sorted(os.listdir(base), reverse=True):
        if not nombre.startswith("Separador_"):
            continue
        ruta_corrida = os.path.join(base, nombre)
        if not os.path.isdir(ruta_corrida):
            continue
        sedes = []
        for sede in sorted(os.listdir(ruta_corrida)):
            ruta_sede = os.path.join(ruta_corrida, sede)
            if not os.path.isdir(ruta_sede):
                continue
            carpetas = {}
            total = 0
            for carpeta in CARPETAS_SEDE:
                ruta_c = os.path.join(ruta_sede, carpeta)
                if not os.path.isdir(ruta_c):
                    continue
                archivos = []
                for a in sorted(os.listdir(ruta_c)):
                    ra = os.path.join(ruta_c, a)
                    if os.path.isfile(ra):
                        st = os.stat(ra)
                        archivos.append({"Name": a, "Size": st.st_size})
                        total += st.st_size
                carpetas[carpeta] = archivos
            extras = [a for a in sorted(os.listdir(ruta_sede))
                      if a.lower().endswith(".txt") and
                      os.path.isfile(os.path.join(ruta_sede, a))]
            if carpetas or extras:
                sedes.append({"sede": sede, "carpetas": carpetas, "extras": extras,
                              "bytes": total,
                              "n_ok": len(carpetas.get("VERIFICADOS", [])),
                              "n_revisar": len(carpetas.get("POR_VERIFICAR", []))})
        st = os.stat(ruta_corrida)
        corridas.append({
            "corrida": nombre,
            "ModTime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "bytes": sum(s["bytes"] for s in sedes),
            "sedes": sedes,
            "n_ok": sum(s["n_ok"] for s in sedes),
            "n_revisar": sum(s["n_revisar"] for s in sedes),
        })
    return corridas


def ruta_resultado(cfg, corrida, sede, carpeta, archivo):
    """Ruta absoluta validada de un archivo de resultados (o None)."""
    c, s = _seguro(corrida), _seguro(sede)
    if not c.startswith("Separador_") or not s:
        return None
    partes = [c, s]
    if carpeta:
        p = _seguro(carpeta)
        if p not in CARPETAS_SEDE:
            return None
        partes.append(p)
    a = _seguro(archivo)
    if not a:
        return None
    partes.append(a)
    return _dentro(pc.SALIDA_LOCAL, *partes)


def zip_resultados(cfg, corrida, sede, destino):
    """Escribe un ZIP con la sede (VERIFICADOS + POR_VERIFICAR + txt) o con la corrida
    completa si `sede` es vacío. Devuelve (ok, n_archivos|error)."""
    base = _dentro(pc.SALIDA_LOCAL, _seguro(corrida))
    if not base or not os.path.isdir(base):
        return False, "corrida inexistente"
    n = 0
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        if sede:
            s = _seguro(sede)
            raices = [(os.path.join(base, s), s)] if os.path.isdir(os.path.join(base, s)) else []
        else:
            raices = [(base, os.path.basename(base))]
        if not raices:
            return False, "sede inexistente"
        for raiz, arco in raices:
            for r, _dirs, archivos in os.walk(raiz):
                for a in sorted(archivos):
                    comp = os.path.join(r, a)
                    rel = os.path.relpath(comp, raiz)
                    z.write(comp, os.path.join(arco, rel))
                    n += 1
    return True, n


# ------------------------------------------------------------------ disco / limpieza
def espacio(cfg):
    """Espacio del separador por carpeta + espacio libre del disco."""
    carpetas = {
        "entrada_local": pc.entrada_local(cfg),
        "entrada_local/procesados": pc.procesados_local(cfg),
        "resultados (auto/salida)": pc.SALIDA_LOCAL,
        "progreso (auto/progress)": pc.PROGRESS_DIR,
        "legacy auto/entrada (Drive viejo)": os.path.join(pc.AUTO_DIR, "entrada"),
        "inventarios": pc.inventarios_local(cfg),
    }
    detalle = {}
    for k, v in carpetas.items():
        detalle[k] = _tam_dir(v) if os.path.isdir(v) else 0
    uso = shutil.disk_usage(pc.SEP_DIR)
    return {
        "carpetas": detalle,
        "total_separador": sum(detalle.values()),
        "disco": {"total": uso.total, "usado": uso.used, "libre": uso.free,
                  "pct_usado": round(uso.used / uso.total * 100, 1) if uso.total else 0},
    }


def limpiar_corridas(cfg, conservar=2, corrida=None):
    """Borra corridas de resultados. Con `corrida` borra esa; si no, conserva las
    `conservar` más recientes. Nunca borra la más reciente. Devuelve (borradas, bytes)."""
    base = pc.SALIDA_LOCAL
    if not os.path.isdir(base):
        return [], 0
    corridas = sorted((n for n in os.listdir(base) if n.startswith("Separador_")
                       and os.path.isdir(os.path.join(base, n))), reverse=True)
    if not corridas:
        return [], 0
    if corrida:
        c = _seguro(corrida)
        if c not in corridas or c == corridas[0]:
            return [], 0
        a_borrar = [c]
    else:
        a_borrar = corridas[max(0, int(conservar)):]
    borradas, bytes_lib = [], 0
    for c in a_borrar:
        ruta = _dentro(base, c)
        if not ruta or not os.path.isdir(ruta):
            continue
        bytes_lib += _tam_dir(ruta)
        shutil.rmtree(ruta, ignore_errors=True)
        borradas.append(c)
    return borradas, bytes_lib


def borrar_todos_los_resultados(cfg):
    """Borra TODAS las corridas (útil cuando el disco se llena). Marca aparte."""
    base = pc.SALIDA_LOCAL
    if not os.path.isdir(base):
        return [], 0
    corridas = [n for n in sorted(os.listdir(base)) if n.startswith("Separador_")
                and os.path.isdir(os.path.join(base, n))]
    borradas, bytes_lib = [], 0
    for c in corridas:
        ruta = _dentro(base, c)
        if not ruta:
            continue
        bytes_lib += _tam_dir(ruta)
        shutil.rmtree(ruta, ignore_errors=True)
        borradas.append(c)
    return borradas, bytes_lib
