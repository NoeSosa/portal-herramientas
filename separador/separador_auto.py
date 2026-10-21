#!/home/node/.openclaw/workspace/venv-pdfseparador/bin/python
"""
separador_auto.py — Pipeline AUTOMÁTICO completo (separador PDFs → Drive → correo)
===================================================================================
Ejecución 100% headless (sin modelo/LLM): consume 0 tokens en runtime.
Disparo recomendado: vigilante cada 7 min (cron) — este script sale inmediato
si no hay PDFs nuevos.

Flujo:
  1. Descarga PDFs de gdrive:Entrada_Separador/ (solo raíz, no Procesados/)
  2. Descarga inventarios de gdrive:Inventarios_Separador/*.xlsx (multi-zona)
  3. OCR paralelo 2 workers a 200 dpi por PDF (reusa separador_pdf.py)
  4. Detecta seriales y los valida contra el índice global (todas las zonas)
  5. Asigna SEDE (columna SEDE, si no Domicilio, si no ZONA) a cada serial
  6. Organiza por sede; los no identificados → Verificar/ con la sede del
     documento de origen (sede de los seriales OK del mismo PDF)
  7. Sube a gdrive:Separador_<fecha>/ y genera link público de la carpeta
  8. Envía correo a GMAIL_USER con resumen + link (link al final). Además, si
     algún equipo pertenece a una sede con correo configurado en CORREOS_SEDE,
     envía un aviso a esos destinatarios con copia (CC) a GMAIL_USER.
  9. Mueve PDFs procesados a gdrive:Entrada_Separador/Procesados/

Config: rutas y credenciales se leen de este archivo + .env del workspace.

Uso:
  python separador_auto.py          # corrida completa
  python separador_auto.py --dry    # sin subir a Drive ni enviar correo

✅ VALIDADO: 08/sep/2026 (Ches)
"""
import os
import sys
import re
import json
import time
import html
import shutil
import smtplib
import subprocess
import argparse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import Counter

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
WS = os.path.dirname(os.path.abspath(__file__))
SEP_DIR = WS                          # separador_pdfs/
PY = os.path.join(WS, "..", "venv-pdfseparador", "bin", "python")
SEPARADOR = os.path.join(SEP_DIR, "separador_pdf.py")
ENV_FILE = os.path.join(WS, "..", ".env")
RCLONE = "/home/node/.local/bin/rclone"

LOCAL = os.path.join(SEP_DIR, "auto")
LOCAL_ENTRADA = os.path.join(LOCAL, "entrada")
LOCAL_INV = os.path.join(LOCAL, "inventarios")
LOCAL_SALIDA = os.path.join(LOCAL, "salida")
LOCK = os.path.join(LOCAL, "run.lock")

GDRIVE = "gdrive:"
CARPETA_ENTRADA = "Entrada_Separador"
CARPETA_INV_REMOTO = "Inventarios_Separador"
CARPETA_PROCESADOS = "Entrada_Separador/Procesados"

# ---------------------------------------------------------------------------
# CORREOS POR SEDE (25/sep/2026)
# Si un equipo cae en una sede de este diccionario, además del correo habitual a
# GMAIL_USER se avisa a los destinatarios de esa sede (CC a GMAIL_USER).
#   · La clave se compara en minúsculas y por contención: "san jeronimo"
#     coincide con "OAJ SAN JERÓNIMO" y con "SAN JERONIMO, AVENIDA ...".
#   · Los destinatarios del correo por sede reciben ÚNICAMENTE los equipos de
#     su sede (el resumen completo sigue llegando solo a GMAIL_USER).
#   · para añadir una sede, agrega una línea aquí y nada más.
# ---------------------------------------------------------------------------
CORREOS_SEDE = {
    "san jeronimo": ["rolando4jun@gmail.com"],
    "san jerónimo": ["rolando4jun@gmail.com"],
}

# ---------------------------------------------------------------------------
# SEPARACIÓN DE UBICACIONES QUE COMPARTEN SEDE EN EL INVENTARIO (25/sep/2026)
# La hoja de inventario trae DOS ubicaciones distintas dentro de la misma sede
# ("OAJ CONSTITUCION NL" para 156 equipos y "OAJ AVENIDA DE LOS ENCINOS TAMPS"
# para 1). Si el lote trae las dos, todo caería bajo el mismo nombre de carpeta.
# Este mapa corrige la sede de la página SOLO cuando aparece la huella textual
# de la ubicación en la hoja escaneada. El orden importa: la primera coincidencia
# gana, por eso las huellas específicas van antes que las genéricas.
# ---------------------------------------------------------------------------
SEDE_HUELLA = {
    "avenida de los encinos": "OAJ AVENIDA DE LOS ENCINOS TAMPS",
    "encinos": "OAJ AVENIDA DE LOS ENCINOS TAMPS",
    "tamps": "OAJ AVENIDA DE LOS ENCINOS TAMPS",
    "tamaulipas": "OAJ AVENIDA DE LOS ENCINOS TAMPS",
}
ORDEN_SEDE_HUELLA = ["avenida de los encinos", "encinos", "tamps", "tamaulipas"]


def correos_de_sede(sede):
    """Devuelve la lista de correos configurados para una sede (o [])."""
    if not sede:
        return []
    s = re.sub(r'\s+', ' ', str(sede)).strip().lower()
    s_ascii = (s.replace("á", "a").replace("é", "e").replace("í", "i")
                 .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
    vistos, out = set(), []
    for clave, correos in CORREOS_SEDE.items():
        c = str(clave).strip().lower()
        c_ascii = (c.replace("á", "a").replace("é", "e").replace("í", "i")
                     .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
        if c_ascii in s_ascii:
            for e in correos:
                if e and e.lower() not in vistos:
                    vistos.add(e.lower())
                    out.append(e)
    return out

DPI = 200

# Patrón de serial por marca (nivel de módulo: lo usan varias funciones).
# FIX 2026-09-18: antes era `CNB[A-Z0-9]?`, que solo capturaba 1-2 chars tras
# "CNB" (devolvía 'CNBI'/'CNB1') y tiraba el resto del serial; con varias hojas eso
# colapsaba equipos distintos bajo el mismo nombre y los PDFs se sobrescribían.
# Ahora exige el serial completo y tolera la confusión OCR B<->S (`CN[BS]`).
patron_serial_ocr = re.compile(r'(?i)\b((?:VNB0B0|PHBTW|CN[BS][A-Z0-9]{6,10}|P[A-Z0-9]{2}BTW|\d{4}P(?:C)?[A-Z]?\d{4,6}))[A-Z0-9]{0,8}\b')
WORKERS = 3

# Importar helpers del separador
sys.path.insert(0, SEP_DIR)
from separador_pdf import (normalizar_sn, generar_variantes_ocr,
                           identificar_modelo, ocr_rango)  # noqa: E402


def load_creds():
    creds = {}
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    creds[k] = v
    return creds


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


def rc(*args, timeout=300):
    """Ejecuta rclone; devuelve (exit, stdout)."""
    r = subprocess.run([RCLONE] + list(args), capture_output=True, text=True,
                       timeout=timeout)
    out = "\n".join(l for l in r.stdout.splitlines()
                    if "retired" not in l and "NOTICE" not in l and l.strip())
    return r.returncode, out


# ----------------------------------------------------------------------------
# VALIDACIÓN DE SEGURIDAD DE ARCHIVOS
# ----------------------------------------------------------------------------
CARPETA_RECHAZADOS = "Entrada_Separador/Rechazados"
CARPETA_VIRUS = "Entrada_Separador/Virus"
# Patrones de contenido que SÍ delatan ejecución embebida (no aparecen en un
# PDF de escaneo limpio). El escaneo no es antivirus: es una segunda barrera
# contra scripts/ejecutables camuflados como .pdf.
BIN_BLACKLIST = (
    b"\x7fELF",          # binario ELF (Linux) — solo se evalúa en la CABECERA
    # ⛔ NO incluir b"\xff\xd8\xff" (JPEG): un PDF ESCANEADO guarda cada página como
    # imagen JPEG (DCTDecode) y esos streams empiezan con FF D8 FF. Tenerlo aquí
    # marcaba como virus CUALQUIER escaneo cuyo stream cayera en una ventana de
    # muestreo (falso positivo 25/sep/2026).
    # ⛔ NO incluir b"pk\x03\x04" ni b"\x89png": un PDF legítimo puede llevar ZIP o PNG
    # dentro (/EmbeddedFile, imágenes).
    b"powershell", b"powershell.exe", b"cmd.exe", b"rundll32",
    b"wscript", b"cscript", b"certutil", b"bitsadmin",
    b"nc -e", b"/bin/sh -c", b"/bin/bash -c", b"python -c",
    b"#!/bin", b"#!/usr/bin/",
)
# NOTA: ya no se incluye b"mz"/b"MZ" suelto: es falso positivo (aparece por azar
# ~1 vez cada 64 KB en datos comprimidos: 32 veces en un escaneo de 2 MB, medido
# el 25/sep/2026). La cabecera MZ de un .exe real NO pasa la validación de
# cabecera mágica %PDF, así que un ejecutable camuflado ya es rechazado por
# _es_pdf_valido antes de llegar aquí.
# Heurística estructural: acciones de código activo en los OBJETOS del PDF
# (se buscan con PyMuPDF en el diccionario de cada objeto, no en bytes crudos).
CODE_HINTS = ("javascript", "java.net.url", "openaction", "launch",
              "embeddedfile", "filespec", "fdf", "submitform",
              "importdata", "app.launchurl")

# Esquemas de URI peligrosos en anotaciones /URI
URI_PELIGROSOS = ("file:", "smb:", "ms-msdt:", "search-ms:", "javascript:",
                  "vbscript:", "cmd:", "command:", "mshta:", "runscript:")

MAX_PDF_MB = 200  # tamaño máximo razonable de un PDF escaneado
def _es_pdf_valido(ruta):
    """Valida cabecera mágica + extensión. NO confía solo en la extensión."""
    if not os.path.exists(ruta) or os.path.getsize(ruta) == 0:
        return False, "archivo vacío"
    if os.path.getsize(ruta) > MAX_PDF_MB * 1024 * 1024:
        return False, f"supera {MAX_PDF_MB}MB"
    with open(ruta, "rb") as f:
        head = f.read(2048)
    if not head.startswith(b"%PDF") and not head.startswith(b"%FDF"):
        return False, "no es PDF (cabecera mágica ausente)"
    ext = os.path.splitext(ruta)[1].lower()
    if ext != ".pdf":
        return False, f"extensión no permitida: '{ext}'"
    # Validación estructural: PyMuPDF debe poder abrirlo y leer al menos la página 0
    # (rechaza PDF corruptos, cifrados sin password o trampas que romperían el OCR)
    try:
        import fitz
        doc = fitz.open(ruta)
        n = len(doc)
        if n == 0:
            doc.close()
            return False, "PDF sin páginas"
        if doc.needs_pass:
            doc.close()
            return False, "PDF cifrado/sin lectura permitida"
        doc.load_page(0)  # fuerza parseo de la primera página
        doc.close()
    except Exception as e:
        return False, f"no se pudo procesar: {e}"
    return True, "ok"

def _scaneo_malicioso(ruta):
    """Detecta ejecución/acciones peligrosas en un PDF por ESTRUCTURA (no por bytes).

    Lección 25/sep/2026: escanear bytes crudos del interior da falsos positivos —
    un PDF escaneado lleva las páginas como JPEG (FF D8 FF) y sus streams vienen
    comprimidos, así que cualquier pareja de bytes aparece por azar. Por eso:
      1) La lista negra binaria solo mira la CABECERA real del archivo.
      2) Las señales de código se buscan en los OBJETOS del PDF (PyMuPDF), no en
         bytes comprimidos, y solo si el objeto NO declara filtros de imagen.
      3) Se renderizan las 3 primeras páginas: estructura rota o contenido que no
         es PDF no puede pasar.
    """
    size = os.path.getsize(ruta)

    # 1) Barrera binaria SOLO en la cabecera real (no en el interior del archivo).
    with open(ruta, "rb") as f:
        cabeza = f.read(1024).lower()
    for pat in BIN_BLACKLIST:
        if pat in cabeza:
            return True, (pat.decode("latin-1", "ignore") or "binario")[:40]

    # 2) Barrera estructural sobre los objetos del PDF.
    import fitz
    try:
        doc = fitz.open(ruta)
    except Exception as e:
        return False, f"no abrible ({e})"

    try:
        # 2a) Catálogo y metadatos del documento: acciones de apertura / JS / launch.
        try:
            xref = doc.pdf_catalog()
            if xref:
                cat = doc.xref_object(xref, compressed=False).lower()
                for k in ("javascript", "openaction", "/aa", "launch",
                          "embeddedfile", "/filespec"):
                    if k in cat:
                        return True, f"catálogo: {k}"
        except Exception:
            pass

        # 2b) Recorrer TODOS los objetos. Solo se inspecciona el contenido si el
        # objeto NO declara filtros de imagen/compresión (si no, es un stream
        # binario de imagen y buscar texto ahí es basura).
        filtros_imagen = ("/dctdecode", "/jpxdecode", "/ccittfaxdecode",
                          "/jbIG2decode", "/flatedecode", "/lzwdecode",
                          "/ascii85decode", "/asciihexdecode", "/runlengthdecode")
        try:
            n_xref = doc.xref_length()
        except Exception:
            n_xref = 0
        for x in range(1, n_xref):
            try:
                obj = doc.xref_object(x, compressed=True).lower()
            except Exception:
                continue
            if not obj:
                continue
            # No inspeccionar streams de imagen/compresión (falso positivo seguro)
            if "/filter" in obj:
                continue
            for k in ("javascript", "/js", "openaction", "/launch",
                      "launchurl", "embeddedfile", "/filespec",
                      "submitform", "importdata"):
                if k in obj:
                    return True, f"objeto {x}: {k}"
            # Anotaciones con URI peligrosa (abrir fichero local, smb, etc.)
            if "/uri" in obj:
                for esq in URI_PELIGROSOS:
                    if esq in obj:
                        return True, f"URI peligrosa: {esq}"

        # 2c) Render de las 3 primeras páginas: estructura real, no lo que dice ser.
        for pageno in range(min(len(doc), 3)):
            try:
                doc.load_page(pageno).get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
            except Exception as e:
                return True, f"página {pageno+1} no renderizable ({e})"
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return False, ""

def _mover_remoto(remoto_src, remoto_dst, nombre):
    """Mueve un archivo remoto entre carpetas de Drive (solo si el dst no existe)."""
    rc("moveto", f"{GDRIVE}{remoto_src}/{nombre}", f"{GDRIVE}{remoto_dst}/{nombre}")

# ⚠️ 25/sep/2026: barrera DESACTIVADA a petición de Noe — el archivo pasa DIRECTO.
# El escaneo _scaneo_malicioso sigue existiendo pero no se invoca; se conserva el
# check estructural mínimo de _es_pdf_valido (que un PDF abrible y no-cifrado sí
# cumple). Re-activarla = descomentar la llamada en descargar_entrada().
FILTRO_VIRUS_ACTIVO = False

def descargar_entrada():
    """Descarga SOLO PDFs válidos. Rechaza (mueve) archivos no-PDF o sospechosos.
    Devuelve lista de rutas locales de PDFs válidos pendientes de procesar."""
    os.makedirs(LOCAL_ENTRADA, exist_ok=True)
    # Listar TODO tipo de archivo (no filtrar por .pdf acá: si hay otro, se rechaza)
    code, out = rc("lsf", f"{GDRIVE}{CARPETA_ENTRADA}", "--files-only")
    if code != 0:
        log("[drive] ERROR listando entrada:", out)
        return []
    # Evitar subcarpetas (Procesados/, Rechazados/, Virus/) — lsf de raíz las ignora
    archivos = [f for f in out.splitlines()
                if f.strip() and "/" not in f and f != "Procesados/"]
    rechazados_local = []
    validos = []
    for f in archivos:
        nombre = os.path.basename(f)
        dest = os.path.join(LOCAL_ENTRADA, nombre)
        # Copia el archivo tal cual para inspeccionarlo
        code2, _ = rc("copyto", f"{GDRIVE}{CARPETA_ENTRADA}/{f}", dest)
        if code2 != 0 or not os.path.exists(dest):
            log(f"[validar] no se pudo descargar '{nombre}'")
            continue
        size = os.path.getsize(dest)
        if size == 0:
            log(f"[validar] '{nombre}': archivo vacío → Rechazados")
            rechazados_local.append((dest, "vacío", nombre, False))
            continue
        pdf_ok, razon_hdr = _es_pdf_valido(dest)
        if not pdf_ok:
            log(f"[validar] '{nombre}': {razon_hdr} → Rechazados")
            rechazados_local.append((dest, razon_hdr, nombre, False))
            continue
        if FILTRO_VIRUS_ACTIVO:
            malo, patron = _scaneo_malicioso(dest)
            if malo:
                log(f"[validar] '{nombre}': posible contenido ejecutable ('{patron}') → Virus")
                rechazados_local.append((dest, f"contenido ejecutable: {patron}", nombre, True))
                continue
        else:
            log(f"[validar] '{nombre}': filtro de virus DESACTIVADO → pasa directo")
        validos.append(dest)
    # Mover rechazados a carpeta adecuada y quitar copia local
    for dest_local, razon, nombre, virus in rechazados_local:
        carpeta = CARPETA_VIRUS if virus else CARPETA_RECHAZADOS
        _mover_remoto(CARPETA_ENTRADA, carpeta, nombre)
        try:
            os.remove(dest_local)
        except OSError:
            pass
    return validos


def descargar_inventarios():
    os.makedirs(LOCAL_INV, exist_ok=True)
    for old in os.listdir(LOCAL_INV):
        if old.endswith(".xlsx"):
            os.remove(os.path.join(LOCAL_INV, old))
    code, out = rc("lsf", f"{GDRIVE}{CARPETA_INV_REMOTO}", "--include", "*.xlsx",
                   "--files-only")
    if code != 0:
        log("[drive] ERROR listando inventarios:", out)
        return []
    invs = []
    for f in out.splitlines():
        if not f.strip().lower().endswith(".xlsx"):
            continue
        dest = os.path.join(LOCAL_INV, os.path.basename(f))
        code2, _ = rc("copyto", f"{GDRIVE}{CARPETA_INV_REMOTO}/{f}", dest)
        if code2 == 0 and os.path.exists(dest):
            invs.append(dest)
    return invs


# ----------------------------------------------------------------------------
# PASO 4: índice global de inventarios → serial → (sede, marca, modelo, zona)
# ----------------------------------------------------------------------------
def construir_indice(archivos_inv):
    """Devuelve {sn_upper: info} y {sn_norm: info} con todas las zonas."""
    import pandas as pd
    idx = {}
    idx_norm = {}
    for xlsx in archivos_inv:
        try:
            xl = pd.ExcelFile(xlsx)
            for hoja in xl.sheet_names:
                df = xl.parse(hoja)
                cols = {str(c).strip().lower(): c for c in df.columns}
                col_sn = next((cols[c] for c in cols if "serie" in c), None)
                if col_sn is None:
                    continue
                col_sede = next((cols[c] for c in cols
                                 if c.strip() == "sede"), None)
                col_dom = next((cols[c] for c in cols
                                if "domicilio" in c), None)  # sin 'ubicación': esa es el juzgado
                col_zona = next((cols[c] for c in cols if c.strip() == "zona"), None)
                col_marca = next((cols[c] for c in cols if c.strip() == "marca"), None)
                col_modelo = next((cols[c] for c in cols if c.strip() == "modelo"), None)
                for _, row in df.iterrows():
                    sn = str(row[col_sn]).strip().upper() if pd.notna(row[col_sn]) else ""
                    if not sn or sn in ("NAN", "NONE", "0"):
                        continue
                    # sede = columna SEDE si existe; si no, Domicilio
                    sede = ""
                    if col_sede is not None and pd.notna(row[col_sede]):
                        sede = str(row[col_sede]).strip()
                    if not sede and col_dom is not None and pd.notna(row[col_dom]):
                        sede = str(row[col_dom]).strip()
                    # zona = columna ZONA (numérica, ej. "9") o textual; si no hay, derivar
                    zona = ""
                    if col_zona is not None and pd.notna(row[col_zona]):
                        zona = str(row[col_zona]).strip()
                    # Normalizaciones: Z09 usa Domicilio como sede (no hay col SEDE);
                    # si ambas vienen (SEDE textual + domicilio) la zona puede derivarse.
                    if not zona:
                        # si hay SEDE textual tipo 'OAJ xxx' lo usamos como zona amplia
                        zona = str(row[col_sede]).strip() if col_sede is not None and pd.notna(row[col_sede]) else ""
                    marca = str(row[col_marca]).strip() if col_marca is not None and pd.notna(row[col_marca]) else ""
                    modelo = str(row[col_modelo]).strip() if col_modelo is not None and pd.notna(row[col_modelo]) else ""
                    # zona limpia: prefijo legible para carpeta
                    zona_dir = f"ZONA_{zona}" if zona and zona.isdigit() else (zona or "SIN_ZONA")
                    info = {"sn": sn, "sede": sede, "zona": zona, "zona_dir": zona_dir,
                            "marca": marca, "modelo": modelo, "origen": os.path.basename(xlsx)}
                    idx[sn] = info
                    idx_norm.setdefault(normalizar_sn(sn), info)
        except Exception as e:
            log(f"[inv] aviso {os.path.basename(xlsx)}: {e}")
    return idx, idx_norm


def buscar_serial(texto, idx, idx_norm):
    """Devuelve info del inventario si el texto contiene un serial conocido."""
    palabras = re.findall(r'[A-Z0-9]{5,15}', texto.upper())
    for p in palabras:
        if p in idx:
            return idx[p]
        for var in generar_variantes_ocr(p):
            if var in idx:
                return idx[var]
        pn = normalizar_sn(p)
        if pn in idx_norm:
            return idx_norm[pn]
    return None


# ----------------------------------------------------------------------------
# PASO 3-6: procesar un PDF
# ----------------------------------------------------------------------------
def sanitizar_nombre(nombre, maxlen=60):
    nombre = re.sub(r'[\\/:*?"<>|]+', "_", str(nombre)).strip(" ._")
    return nombre[:maxlen] if nombre else "SIN_SEDE"


def pdf_nombre_local(ruta):
    """Retorna el nombre (sin extensión) de un PDF local (trazabilidad)."""
    return sanitizar_nombre(os.path.splitext(os.path.basename(ruta))[0], 60)


def sedes_por_serial(res_ok):
    """Mapa serie -> (zona_dir, sede) para cada OK del procesamiento."""
    return {eq["sn"]: (eq.get("zona_dir") or "SIN_ZONA", eq["sede"] or "SIN_SEDE")
            for eq in res_ok}



def reocr_serial_encabezado(ruta_pdf, num_pagina, dpi_alto=400):
    """Re-OCR del encabezado de UNA página a mayor dpi para rescatar el serial.

    FIX 2026-09-18: a 200 dpi algunas hojas de contador HP quedan ilegibles y la
    página se perdía como 'Sin_Identificar'. A 400 dpi el encabezado ('Numero
    serie equipo : XXXXX') sí se lee. Devuelve el serial o None.
    """
    patron = re.compile(
        r'(?i)(?:numero\s*serie\s*equipo|n[uú]mero\s*de\s*serie|serial\s*(?:no|number|num)?)\s*[:\-]?\s*([A-Z0-9]{6,16})')
    patron_laxo = re.compile(r'(?i)\b(CN[BS][A-Z0-9]{6,10}|VNB[A-Z0-9]{4,10}|PHBTW[A-Z0-9]{4,10})\b')
    import fitz as _fitz
    # Parche numpy/imgaug (numpy 2.x eliminó np.sctypes; imgaug 0.4 lo usa).
    # Necesario aquí porque este import de PaddleOCR ocurre aparte del módulo base.
    import numpy as _np
    if not hasattr(_np, "sctypes"):
        _np.sctypes = {"int": [_np.int8, _np.int16, _np.int32, _np.int64],
                       "uint": [_np.uint8, _np.uint16, _np.uint32, _np.uint64],
                       "float": [_np.float16, _np.float32, _np.float64],
                       "complex": [_np.complex64, _np.complex128]}
    from paddleocr import PaddleOCR
    import cv2 as _cv2
    doc = _fitz.open(ruta_pdf)
    pag = doc.load_page(num_pagina)
    r = pag.rect
    # Solo la franja superior (donde vive el encabezado con el serial)
    clip = _fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * 0.35)
    pix = pag.get_pixmap(dpi=dpi_alto, clip=clip)
    img = _cv2.imdecode(_np.frombuffer(pix.tobytes("png"), _np.uint8), _cv2.IMREAD_COLOR)
    ocr = PaddleOCR(lang="en", use_angle_cls=False, show_log=False, cpu_threads=1)
    res = ocr.ocr(img, cls=False)
    texto = " ".join([l[1][0] for l in res[0]]) if res and res[0] else ""
    doc.close()
    m = patron.search(texto)
    if m:
        cand = m.group(1).upper()
        if sum(ch.isdigit() for ch in cand) >= 2:
            return cand
    m = patron_laxo.search(texto)
    if m:
        return m.group(1).upper()
    return None


def reocr_pagina_rotada(ruta_pdf, num_pagina, dpi=None):
    """Re-OCR de UNA página rotándola 180° (hoja escaneada de cabeza).

    FIX 2026-09-18: si una hoja entra girada 180°, el OCR devuelve basura y la
    página se pierde. Solo se reintenta cuando el primer intento NO produjo nada
    útil (ni serial, ni marca, ni p1/p2), así no se agrega costo al caso normal.
    Devuelve el texto rotado (o '' si falla).
    """
    import numpy as _np
    if not hasattr(_np, "sctypes"):
        _np.sctypes = {"int": [_np.int8, _np.int16, _np.int32, _np.int64],
                       "uint": [_np.uint8, _np.uint16, _np.uint32, _np.uint64],
                       "float": [_np.float16, _np.float32, _np.float64],
                       "complex": [_np.complex64, _np.complex128]}
    import fitz as _fitz, cv2 as _cv2
    from paddleocr import PaddleOCR
    d = dpi or DPI
    doc = _fitz.open(ruta_pdf)
    pag = doc.load_page(num_pagina)
    pix = pag.get_pixmap(dpi=d)
    img = _cv2.imdecode(_np.frombuffer(pix.tobytes("png"), _np.uint8), _cv2.IMREAD_COLOR)
    img180 = _cv2.rotate(img, _cv2.ROTATE_180)
    ocr = PaddleOCR(lang="en", use_angle_cls=False, show_log=False, cpu_threads=1)
    res = ocr.ocr(img180, cls=False)
    texto = " ".join([l[1][0] for l in res[0]]) if res and res[0] else ""
    doc.close()
    return texto


def texto_es_util(txt):
    """¿El OCR de esta página produjo algo aprovechable? (para decidir re-escaneo)."""
    if not txt or len(txt.strip()) < 40:
        return False
    low = txt.lower()
    # Palabras-ancla de las hojas de contador (cualquier marca conocida)
    anclas = ("contador", "page counter", "counter", "pantum", "bm5100",
              "ricoh", "kyocera", "ecosys", "hp laser", "laser mfp",
              "numero serie", "n[uú]mero de serie", "serial", "page 1", "page 2",
              "uso total", "total", "copier", "printer")
    import re as _re
    if any(_re.search(a, low) for a in anclas):
        return True
    # Si trae un serial con pinta válida, también sirve
    if patron_serial_ocr.search(txt):
        return True
    return False


def procesar_pdf(ruta_pdf, idx, idx_norm):
    """Devuelve estructura: {ok: [...], verificar: [...], sede_doc}."""
    # OCR con workers (subprocesos) — mismo patrón que separador_pdf.py
    doc_t = __import__("fitz").open(ruta_pdf)
    total = len(doc_t)
    doc_t.close()
    textos = {}
    n_workers = max(1, min(WORKERS, total))
    if n_workers == 1:
        import numpy as np
        if not hasattr(np, "sctypes"):
            np.sctypes = {"int": [np.int8, np.int16, np.int32, np.int64],
                          "uint": [np.uint8, np.uint16, np.uint32, np.uint64],
                          "float": [np.float16, np.float32, np.float64],
                          "complex": [np.complex64, np.complex128]}
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(lang="en", use_angle_cls=False, show_log=False, cpu_threads=1)
        for r in ocr_rango(ruta_pdf, 0, total, DPI, 1, ocr):
            textos[r["num"]] = r["texto"]
    else:
        procs, tmps = [], []
        for w in range(n_workers):
            ini = total * w // n_workers
            fin = total * (w + 1) // n_workers
            if fin <= ini:
                continue
            jf = os.path.join(LOCAL, f"_ocr_{os.path.basename(ruta_pdf)}_{w}.json")
            tmps.append(jf)
            procs.append(subprocess.Popen(
                [sys.executable, SEPARADOR, "--modo-ocr-worker", "--pdf", ruta_pdf,
                 "--rango", f"{ini}:{fin}", "--dpi", str(DPI), "--json-out", jf],
                env=dict(os.environ, LD_LIBRARY_PATH=os.environ.get("LD_LIBRARY_PATH", "")
                         + os.pathsep + "/home/node/.local/lib")))
        for p in procs:
            p.wait()
        for jf in tmps:
            if os.path.exists(jf):
                with open(jf, encoding="utf-8") as f:
                    for r in json.load(f):
                        textos[r["num"]] = r["texto"]
                os.remove(jf)

    # Detección por página
    paginas = []
    # FIX 2026-09-18: el patrón anterior `CNB[A-Z0-9]?` solo capturaba 1-2 chars tras
    # Serial etiquetado explícitamente en la hoja (fuente más fiable que el patrón).
    # En las HP MFP 432 la etiqueta viene como "Numero serie equipo : XXXXX".
    patron_serie_etiqueta = re.compile(
        r'(?i)(?:numero\s*serie\s*equipo|n[uú]mero\s*de\s*serie|serial\s*(?:no|number|num)?)\s*[:\-]?\s*([A-Z0-9]{6,16})')
    for num in sorted(textos):
        txt = textos[num]
        # FIX 2026-09-18: si el OCR no sacó nada aprovechable (p.ej. hoja girada 180º),
        # reintentar UNA vez girando la página. Solo aplica al caso malo: en hojas
        # normales esto nunca se ejecuta, así que no agrega costo al flujo habitual.
        if not texto_es_util(txt):
            try:
                txt_180 = reocr_pagina_rotada(ruta_pdf, num)
                if texto_es_util(txt_180) and len(txt_180) > len(txt or ""):
                    log(f"[rotado] pág {num+1}: OCR ilegible, se reintentó girada 180º")
                    txt = txt_180
                    textos[num] = txt_180
            except Exception as e:
                log(f"[aviso] re-OCR rotado pág {num+1} falló: {e}")
        info = buscar_serial(txt, idx, idx_norm)
        modelo = identificar_modelo(txt, info["sn"] if info else None)
        # Serial detectado por patrón (aunque no esté en el inventario) para nombrar el archivo
        sn_ocr = info["sn"] if info else None
        if not sn_ocr:
            # 1º intento: etiqueta explícita "Numero serie equipo : XXXX"
            m_etq = patron_serie_etiqueta.search(txt)
            if m_etq:
                cand = m_etq.group(1).upper()
                # Descartar capturas basura (solo letras sueltas o muy cortas)
                if sum(ch.isdigit() for ch in cand) >= 2:
                    sn_ocr = cand
            # 2º intento: patrón por marca (serial completo)
            if not sn_ocr:
                m = patron_serial_ocr.search(txt)
                if m:
                    sn_ocr = m.group(1).upper()
            # FIX 2026-09-18: 3er intento — re-OCR SOLO del encabezado a mayor dpi.
            # La hoja de contador HP trae "Numero serie equipo" en la parte superior;
            # a 200 dpi a veces queda ilegible (p.ej. la pág 7 de este lote).
            if not sn_ocr:
                try:
                    sn_ocr = reocr_serial_encabezado(ruta_pdf, num)
                except Exception as e:
                    log(f"[aviso] re-OCR encabezado pág {num+1} falló: {e}")
        # Pantum p1/p2
        low = txt.lower()
        is_p1 = is_p2 = False
        if "pantum" in low or "bm5100" in low:
            if re.search(r"page\s*[1il|]", low):
                is_p1 = True
            elif re.search(r"page\s*[2z]", low):
                is_p2 = True
            elif any(k in low for k in ("product information", "system settings", "consumables information")):
                is_p1 = True
            elif any(k in low for k in ("copy information", "faxer information", "ordinary copy setting")):
                is_p2 = True
            if is_p1 or is_p2:
                modelo = "pantum"
        # --- RICOH: detectar si es hoja de CONFIGURACIÓN (descartable) o CONTADOR ---
        # Anchors.confirmadas en PDF real (RICOH IM 430): la hoja de configuración
        # trae 'pagina de configuracion'/'referencia del sistema'/'id maquina'/
        # 'memoria total'/'firmware'/'lenguaje impresora'; las de contador traen
        # 'page counter' + (printing/transmission/coverage).
        es_config_ricoh = False
        # FIX 2026-09-18: antes era `modelo == "ricoh" or re.search(r"ricoh im 430|ricoh", low)`.
        # El segundo término hacía que CUALQUIER hoja que solo MENCIONARA "ricoh"
        # (p.ej. hojas HP 432 con lista de equipos compatibles) entrara aquí y sus
        # hojas de configuración fueran descartadas. Ahora solo aplica a Ricoh real
        # y nunca si la hoja muestra señales de HP.
        marca_hp = any(k in low for k in ("hp ", "hp_", "laserjet", "mfp m4", "hewlett"))
        # FIX 2026-09-25 (2): `palabras_sede` se usa MÁS ABAJO (huella de sede),
        # fuera de este `if`. Estaba definida dentro del if de Ricoh → en cualquier
        # página NO-Ricoh (o de HP) lanzaba UnboundLocalError y abortaba la corrida.
        # Era intermitente: si una página Ricoh se procesaba antes, la variable ya
        # quedaba ligada y las siguientes pasaban. Definida aquí, aplica a todas.
        palabras_sede = ("oaj", "juzgado", "defensoria", "defensoría",
                         "avenida de los encinos", "encinos", "tamps", "tamaulipas")
        if modelo == "ricoh" and not marca_hp:
            palabras_config = ("configuracion", "configuración", "referencia del sistema",
                               "id maquina", "id máquina", "memoria total", "firmware",
                               "version firmware", "lenguaje impresora", "entrada papel",
                               "conexion dispositivo",
                               # 2ª hoja de config (Informacion Interface):
                               "informacion interface", "información interface",
                               "interfaz de red", "direccion mac", "dirección mac",
                               "nombre host", "impresora virtual", "red (netbeui)")
            palabras_contador = ("page counter", "total counter", "copier counter",
                                 "printer counter", "fax counter", "duplex counter",
                                 "send counter", "scan counter", "coverage")
            if any(k in low for k in palabras_config):
                es_config_ricoh = True
            # Si además hay contador, no la marcamos config (una hoja mixta rara)
            if any(k in low for k in palabras_contador):
                es_config_ricoh = False
        # FIX 2026-09-25: huella textual de la sede en la hoja, para separar lotes
        # que caen en la misma carpeta del inventario (p.ej. la impresora de
        # Encinos, Tamps, que sin esto se mezclaba con OAJ Constitución).
        grupos_sede = []
        # FIX 2026-09-25 (2): filtrar None. Cuando la hoja no tiene serial
        # (sn_ocr=None y/o info=None) la lista traía None y re.escape(None)
        # abortaba la corrida con TypeError. Comparación literal (no regex):
        # el serial es texto fijo y `in` basta.
        for _sn in [x for x in ([sn_ocr] + ([info["sn"]] if info else [])) if x]:
            _i = txt.find(_sn)
            if _i >= 0:
                grupos_sede.append(txt[max(0, _i - 400):_i + len(_sn) + 400])
        low_page = txt.lower()
        sede_huella = ""
        if any(k in low_page for k in palabras_sede):
            sede_huella = "\n".join(grupos_sede) if grupos_sede else txt
        paginas.append({"num": num, "texto": txt, "info": info,
                        "modelo": modelo, "is_p1": is_p1, "is_p2": is_p2,
                        "sn_detect": info["sn"] if info else None,
                        "sn_ocr": sn_ocr, "es_config_ricoh": es_config_ricoh,
                        "sede_huella": sede_huella})

    # Emparejar Pantum P1-P2 adyacentes
    huérf = [p for p in paginas if p["is_p1"] or p["is_p2"]]
    while True:
        joined = False
        for i in range(len(huérf) - 1):
            a, b = huérf[i], huérf[i + 1]
            if (a["is_p1"] and b["is_p2"]) or (a["is_p2"] and b["is_p1"]):
                p1 = a if a["is_p1"] else b
                p2 = a if a["is_p2"] else b
                if p2["info"] is None and p1["info"]:
                    p2["info"] = p1["info"]
                    p2["sn_detect"] = p1["sn_detect"]
                huérf.pop(i + 1)
                huérf.pop(i)
                joined = True
                break
        if not joined:
            break

    # --- RICOH: descartar hojas de CONFIGURACIÓN dentro de un set de 4-5 hojas ---
    # (el usuario confirmó: para las Ricoh de 4 o 5 hojas, la(s) hoja(s) de
    # configuración se elimina; se conservan solo las de contador 'page counter').
    paginas_filtradas = []
    for p in paginas:
        if p.get("es_config_ricoh"):
            # contabilizamos cuántas hojas del mismo serial hay para decidir luego
            paginas_filtradas.append((p, True))  # (pagina, es_config)
        else:
            paginas_filtradas.append((p, False))
    # Agrupar por serial para saber el total de hojas de cada Ricoh antes de recortar
    de_paginas = {}
    for p, _es_cfg in paginas_filtradas:
        cl = p["info"]["sn"] if p["info"] else None
        if cl:
            de_paginas.setdefault(cl, []).append(_es_cfg)
    # Si una Ricoh trae 4-5 hojas y tiene config, la quitamos (mantener solo contador)
    quitar_nums = set()
    for cl, lista_cfg in de_paginas.items():
        if len(lista_cfg) in (4, 5) and any(lista_cfg):
            for p, es_cfg in paginas_filtradas:
                if p["info"] and p["info"]["sn"] == cl and es_cfg:
                    quitar_nums.add(p["num"])
    # FIX 2026-09-18: registrar las hojas descartadas para que la cuenta cuadre
    # (hojas origen = OK + POR_VERIFICAR + DESCARTADAS). Antes desaparecían sin rastro.
    descartadas = []
    for p, _es_cfg in paginas_filtradas:
        if p["num"] in quitar_nums:
            descartadas.append({
                "num": p["num"],
                "sn": (p["info"]["sn"] if p["info"] else None) or p.get("sn_ocr") or "SIN_ID",
                "modelo": p.get("modelo") or "?",
                "razon": "hoja de configuración Ricoh en set de 4-5 hojas",
            })
    paginas = [p for p in paginas if p["num"] not in quitar_nums]

    # Sedes de los seriales OK de este documento (para heredar a los huérfanos)
    sedes_doc = Counter(p["info"]["sede"] for p in paginas if p["info"] and p["info"]["sede"])
    sede_doc = sedes_doc.most_common(1)[0][0] if sedes_doc else ""

    # FIX 2026-09-25: separar por huella textual cuando el inventario asigna la
    # misma sede a varias ubicaciones distintas (Encinos mezclada con Constitución).
    sedes_disponibles = sorted({p["info"]["sede"] for p in paginas
                                if p["info"] and p["info"]["sede"]})
    for p in paginas:
        if not (p["info"] and p["info"]["sede"]) or not p.get("sede_huella"):
            continue
        elegida = None
        for clave in ORDEN_SEDE_HUELLA:
            if clave in p["sede_huella"].lower():
                elegida = SEDE_HUELLA.get(clave)
                break
        if elegida:
            elegida = re.sub(r'\s+', ' ', elegida).strip()
            otras = [s for s in sedes_disponibles if s != p["info"]["sede"]]
            if otras and p["info"]["sede"].lower() in elegida.lower():
                p["info"] = dict(p["info"], sede=elegida)
                log(f"[sede] pág {p['num']+1}: huella de ubicación -> {elegida}")

    # Recalcular sedes y huérfanos con la corrección aplicada
    sedes_doc = Counter(p["info"]["sede"] for p in paginas if p["info"] and p["info"]["sede"])
    sede_doc = sedes_doc.most_common(1)[0][0] if sedes_doc else ""
    for p in paginas:
        if p.get("sede_final"):
            continue
        p["sede_final"] = (p["info"]["sede"] if p["info"] and p["info"]["sede"] else sede_doc)

    # Clasificar
    ok = []        # {sn, sede, modelo, paginas:[nums]}
    verificar = []  # {nombre, sede_sugerida, paginas:[nums], razon}
    grupos = {}
    for p in paginas:
        if p["is_p1"] or p["is_p2"]:
            if p["info"]:
                grupos.setdefault(p["info"]["sn"], {"info": p["info"], "pags": [], "modelo": "pantum"})["pags"].append(p["num"])
            else:
                verificar.append({"nombre": f"Pantum_{'P1' if p['is_p1'] else 'P2'}_pag{p['num']+1}",
                                  "sede": p.get("sede_final") or sede_doc, "paginas": [p["num"]], "razon": "pantum sin serial"})
            continue
        if p["info"]:
            grupos.setdefault(p["info"]["sn"], {"info": p["info"], "pags": [], "modelo": p["modelo"], "sn_ocr": p["sn_ocr"] or p["info"]["sn"]})["pags"].append(p["num"])
        else:
            # Sin match de inventario: si el OCR detectó un serial (PHBTW/CNB/VNB/335x)
            # de una marca conocida, usarlo como nombre para poder identificar la máquina.
            if p.get("sn_ocr"):
                verificar.append({"nombre": p["sn_ocr"], "sn": p["sn_ocr"],
                                  "sede": p.get("sede_final") or sede_doc, "paginas": [p["num"]],
                                  "razon": f"{p['modelo']} sin match en inventario"})
            else:
                verificar.append({"nombre": f"Sin_Identificar_pag{p['num']+1}", "sn": None,
                                  "sede": p.get("sede_final") or sede_doc, "paginas": [p["num"]],
                                  "razon": "sin serial en inventario"})

    # Reglas por modelo (páginas esperadas)
    for sn, g in grupos.items():
        cant = len(g["pags"])
        modelo = g["modelo"]
        mal = (modelo == "hp_501" and cant != 1) or (modelo == "kyocera" and cant != 1) \
            or (modelo == "pantum" and cant != 2) \
            or (modelo == "ricoh" and cant not in (2, 3, 5))
        if mal or not g["info"]["sede"]:
            verificar.append({"nombre": g["info"]["sn"], "sn": g["info"]["sn"],
                              "marca": g["info"].get("marca"),
                              "modelo": modelo,
                              "sede": g["info"]["sede"] or (g.get("sede_final") or sede_doc),
                              "paginas": g["pags"],
                              "razon": f"{modelo} con {cant} pág (esperaba 2/3/5 para Ricoh/1 HP/2 Pantum)"})
        else:
            ok.append({"sn": g["info"]["sn"], "sede": g["info"]["sede"],
                       "modelo": modelo, "marca": g["info"]["marca"],
                       "zona": g["info"]["zona"], "zona_dir": g["info"].get("zona_dir") or "SIN_ZONA",
                       "origen": g["info"]["origen"],
                       "paginas": sorted(g["pags"])})

    ok.sort(key=lambda x: x["sn"])
    return {"ok": ok, "verificar": verificar, "sede_doc": sede_doc,
            "descartadas": descartadas}


# ----------------------------------------------------------------------------
# PASO 7: subida a Drive
# ----------------------------------------------------------------------------
def subir_y_linkear(carpeta_local, nombre_remoto):
    """Sube carpeta local a gdrive:<nombre_remoto> y devuelve URL.

    FIX 2026-09-25: `rc()` tiene timeout fijo de 300 s. Una corrida mediana
    (19 PDFs) genera ~226 archivos / 150 MB y la subida no cabía →
    subprocess.TimeoutExpired tumbaba TODO el pipeline DESPUÉS del OCR
    (perdiendo el correo y dejando la corrida sin link). La subida se pasa
    de 300 s, así que aquí se usa timeout amplio y, si rclone ya subió
    parcialmente, hay que reanudar: `rclone copy` es reanudable y omite lo
    que ya está, así que un reintento completa lo que faltó.
    """
    code, out = rc("mkdir", f"{GDRIVE}{nombre_remoto}")
    try:
        code2, out2 = rc("copy", carpeta_local, f"{GDRIVE}{nombre_remoto}",
                         "--transfers", "8", timeout=3600)
    except subprocess.TimeoutExpired:
        # Reanudar: rclone copy no re-sube lo que ya está en el destino.
        log("[7] Subida lenta (>1 h); reintentando lo que falte...")
        code2, out2 = rc("copy", carpeta_local, f"{GDRIVE}{nombre_remoto}",
                         "--transfers", "8", timeout=3600)
    if code2 != 0:
        return None, out2
    code3, url = rc("link", f"{GDRIVE}{nombre_remoto}")
    if code3 != 0 or not url.strip():
        # fallback: link a archivo no disponible; usar búsqueda por id
        code4, idout = rc("lsjson", f"{GDRIVE}{nombre_remoto}", "--files-only")
        url = f"https://drive.google.com/drive/folders/VER_ID"
    return (url.strip() if url.strip() else None), None


# ----------------------------------------------------------------------------
# PASO 8: correo
# ----------------------------------------------------------------------------
def enviar_correo(asunto, cuerpo_html, cuerpo_txt, destinatarios=None):
    """Envía el correo. Sin destinatarios va a GMAIL_USER (comportamiento histórico).
    Con destinatarios: para = esos correos, con CC a GMAIL_USER (para que Noe vea una copia)."""
    creds = load_creds()
    user = creds.get("GMAIL_USER", "")
    app_pass = creds.get("GMAIL_APP_PASSWORD", "")
    if not user or not app_pass:
        log("[mail] ERROR: faltan credenciales GMAIL en .env")
        return False
    dest = []
    for d in (destinatarios or []):
        d = (d or "").strip()
        if d and d.lower() not in [x.lower() for x in dest] and d.lower() != user.lower():
            dest.append(d)
    para = dest if dest else [user]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = user
    msg["To"] = ", ".join(para)
    if dest:
        msg["Cc"] = user
    msg.attach(MIMEText(cuerpo_txt, "plain", "utf-8"))
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
            s.login(user, app_pass)
            s.sendmail(user, para + ([user] if dest else []), msg.as_string())
        log("[mail] enviado OK a", ", ".join(para) + (" (CC " + user + ")" if dest else ""))
        return True
    except Exception as e:
        log("[mail] ERROR:", e)
        return False


def enviar_correo_inicio(lista_nombres, n_archivos):
    """Aviso de inicio: qué documentos se van a procesar."""
    lista = "\n".join(f"  • {html.escape(n)}" for n in lista_nombres)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    asunto = f"🔄 Separador PDF {stamp} — iniciando {n_archivos} documento(s)"
    txt = f"Se recibieron {n_archivos} PDF(s) en Entrada_Separador. Iniciando procesamiento ahora.\n\nDocumentos:\n{lista}\n\nTe avisaré al terminar con el link de resultados."
    cuerpo_html = (f"<p>Se recibieron <b>{n_archivos}</b> PDF(s). Iniciando procesamiento ahora.</p>"
                   f"<p><b>Documentos:</b></p><pre>{lista}</pre>"
                   f"<p>Te avisaré al terminar con el link de resultados.</p>")
    enviar_correo(asunto, cuerpo_html, txt)


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="No sube a Drive ni envía correo")
    args = ap.parse_args()

    try:
        _ejecutar(args)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log("[ERROR] Falló el pipeline:", e)
        log(tb)
        if not args.dry:
            try:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                asunto = f"❌ Separador PDF {stamp} — ERROR de ejecución"
                txt = f"El procesamiento falló:\n\n{e}\n\n{tb}\n"
                htm = f"<p>El procesamiento falló:</p><pre>{html.escape(str(e))}\n\n{html.escape(tb)}</pre>"
                enviar_correo(asunto, htm, txt)
            except Exception as e2:
                log("[mail] aviso de error falló:", e2)
        sys.exit(1)


def _garantizar_carpetas_cuarentena():
    """Crea Rechazados/ y Virus/ en Drive si no existen."""
    for carpeta in (CARPETA_RECHAZADOS, CARPETA_VIRUS):
        rc("mkdir", f"{GDRIVE}{carpeta}")


def _ejecutar(args):
    # Lock simple anti-solapamiento
    try:
        for d in (LOCAL_ENTRADA, LOCAL_INV, LOCAL_SALIDA, LOCAL):
            os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    _garantizar_carpetas_cuarentena()
    if os.path.exists(LOCK):
        age = time.time() - os.path.getmtime(LOCK)
        if age < 3600:
            log("Corrida previa en curso; saliendo.")
            return
    open(LOCK, "w").close()
    try:
        for d in (LOCAL_ENTRADA, LOCAL_INV, LOCAL_SALIDA):
            os.makedirs(d, exist_ok=True)

        log("[1] Buscando PDFs en Drive:", CARPETA_ENTRADA)
        pdfs = descargar_entrada()
        if not pdfs:
            log("Sin PDFs nuevos. Fin.")
            return
        log(f"[1] {len(pdfs)} PDF(s) nuevo(s): {[os.path.basename(p) for p in pdfs]}")

        # Aviso de inicio por correo (feedback inmediato)
        nombres = sorted(os.path.basename(p) for p in pdfs)
        if not args.dry:
            try:
                enviar_correo_inicio(nombres, len(nombres))
            except Exception as e:
                log("[mail] aviso inicio falló:", e)
        else:
            log(f"[dry] (sin correo de inicio) {len(nombres)} docs: {nombres}")

        log("[2] Descargando inventarios...")
        invs = descargar_inventarios()
        log(f"[2] Inventarios: {[os.path.basename(i) for i in invs]}")
        idx, idx_norm = construir_indice(invs) if invs else ({}, {})
        log(f"[4] Índice global: {len(idx)} seriales")

        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        carpeta_local = os.path.join(LOCAL_SALIDA, f"Separador_{stamp}")
        os.makedirs(carpeta_local, exist_ok=True)

        resumen = []          # lista global para el reporte resumido por archivo
        descartadas_doc = []  # FIX 2026-09-18: hojas omitidas a propósito (para trazabilidad)
        trazabilidad = []     # 1 fila POR ARCHIVO generado (no por hoja):
                              # zona, sede, serial, modelo, n_pag, pdf_origen, estado, archivo

        for pdf in pdfs:
            log(f"[3] Procesando {os.path.basename(pdf)} ({DPI}dpi, {WORKERS}w)...")
            t0 = time.time()
            res = procesar_pdf(pdf, idx, idx_norm)
            doc_stem = sanitizar_nombre(os.path.splitext(os.path.basename(pdf))[0], 60)
            sede_doc = sanitizar_nombre(res["sede_doc"]) if res["sede_doc"] else "SIN_SEDE"
            # FIX 2026-09-18: acumular hojas descartadas con la sede del documento
            for d in res.get("descartadas", []):
                descartadas_doc.append(dict(d, sede=sede_doc))

            for eq in res["ok"]:
                zona_dir = sanitizar_nombre(eq.get("zona_dir") or eq["sede"][:40] or "SIN_ZONA")
                sede_dir = sanitizar_nombre(eq["sede"]) if eq["sede"] else "SIN_SEDE"
                # ruta: <corrida>/<SEDE>/VERIFICADOS/<serial>.pdf  (la zona va en el txt/reporte)
                carpeta_sede = os.path.join(carpeta_local, sede_dir)
                ok_dir = os.path.join(carpeta_sede, "VERIFICADOS")
                os.makedirs(ok_dir, exist_ok=True)
                sn = eq['sn']
                sal = os.path.join(ok_dir, f"{sn}.pdf")
                # FIX 2026-09-18: si el nombre se repite, NO sobrescribir (se perdían hojas).
                # Se añade la página para desambiguar.
                if os.path.exists(sal):
                    pgs = "_".join(str(x + 1) for x in eq["paginas"])
                    sal = os.path.join(ok_dir, f"{sn}_pag{pgs}.pdf")
                    log(f"[aviso] nombre duplicado para {sn}; se guarda como {os.path.basename(sal)}")
                merge_paginas(pdf, eq["paginas"], sal)
                resumen.append(f"OK|{sn}|{sede_dir}|{len(eq['paginas'])}pág")
                trazabilidad.append(
                    f"{sede_dir}|{sn}|{eq['modelo']}|{len(eq['paginas'])}|{os.path.basename(pdf)}|OK|{sede_dir}/VERIFICADOS/{os.path.basename(sal)}")

            for v in res["verificar"]:
                sede_sug = sanitizar_nombre(v["sede"]) if v["sede"] else "SIN_SEDE"
                # si el ítem a revisar no tiene sede, cae en la sede del documento (heredada)
                if sede_sug == "SIN_SEDE" and res.get("sede_doc"):
                    sede_sug = sanitizar_nombre(res["sede_doc"])
                # POR_VERIFICAR dentro de la carpeta de la sede de origen
                carpeta_sede_v = os.path.join(carpeta_local, sede_sug)
                rev_dir = os.path.join(carpeta_sede_v, "POR_VERIFICAR")
                os.makedirs(rev_dir, exist_ok=True)
                snn = (v.get("sn") or v.get("nombre") or "SINID")
                # Marca/modelo legible (mayúscula corta) para incluir en el nombre
                marca = (v.get('marca') or '').strip().upper()
                if not marca or marca in ('NAN','NONE'):
                    marca = 'DESCON' if not v.get('modelo') else str(v.get('modelo')).upper()[:6]
                if not marca or marca in ('NAN','NONE'):
                    marca = 'MYS'
                nombre = f"{snn}__{marca}__de_{doc_stem}.pdf"
                sal = os.path.join(rev_dir, nombre)
                # FIX 2026-09-18: evitar sobrescritura cuando varios equipos sin
                # identificar caen en el mismo nombre (se perdían hojas del lote).
                if os.path.exists(sal):
                    pgs = "_".join(str(x + 1) for x in v["paginas"])
                    nombre = f"{snn}__{marca}__pag{pgs}__de_{doc_stem}.pdf"
                    sal = os.path.join(rev_dir, nombre)
                    log(f"[aviso] nombre duplicado en POR_VERIFICAR para {snn}; se guarda como {nombre}")
                merge_paginas(pdf, v["paginas"], sal)
                resumen.append(f"REVISAR|{snn}|{sede_sug}|{v.get('razon','')}")
                trazabilidad.append(
                    f"{sede_sug}|{snn}|{v.get('modelo','?')}|{len(v['paginas'])}|{os.path.basename(pdf)}|POR_VERIFICAR|{sede_sug}/POR_VERIFICAR/{nombre}")

            log(f"[3] {os.path.basename(pdf)}: {len(res['ok'])} OK, "
                f"{len(res['verificar'])} verificar en {time.time()-t0:.0f}s")

        # --- 01_LEEME.txt: instrucción para cualquier persona que abra la carpeta ---
        n_ok = sum(1 for r in resumen if r.startswith("OK|"))
        n_rev = sum(1 for r in resumen if r.startswith("REVISAR|"))
        leeme = (
            f"SEPARADOR DE CONTADORES — CORRIDA {stamp}\n"
            f"=" * 60 + "\n"
            f"Resumen: {n_ok} USUARIOS listos y {n_rev} a revisar.\n\n"
            f"Procesados de {len(pdfs)} PDF(s) escaneados.\n\n"
            f"CÓMO LEER ESTA CARPETA:\n"
            f"  • VERIFICADOS/  → contadores correctos, ya separados por usuario.\n"
            f"  • POR_VERIFICAR/   → documentos con dudas (hojas sueltas, número de páginas\n"
            f"                    inusual o serial no registrado). Revisar antes de usar.\n"
            f"  • 02_REGISTRO.txt → de qué PDF salió cada hoja (trazabilidad).\n"
            f"\nCarpeta creada automaticamente."
        )
        with open(os.path.join(carpeta_local, "00_LEEME.txt"), "w", encoding="utf-8") as f:
            f.write(leeme)

        # --- Agrupar trazabilidad por sede (formato: sede|serial|modelo|n|pdf|estado|archivo) ---
        sedes = {}
        for linea in trazabilidad:
            partes = linea.split('|')
            if len(partes) >= 7:
                sedes.setdefault(partes[0], []).append(partes)

        # --- 02_REGISTRO por sede (trazabilidad legible) dentro de cada carpeta de sede ---
        for sede, filas in sedes.items():
            filas_ok = [f for f in filas if f[5] == 'OK']
            filas_rev = [f for f in filas if f[5] != 'OK']
            ruta_ok = os.path.join(carpeta_local, sede, "VERIFICADOS")
            ruta_rev = os.path.join(carpeta_local, sede, "POR_VERIFICAR")
            os.makedirs(ruta_ok, exist_ok=True)
            os.makedirs(ruta_rev, exist_ok=True)
            txt = list()
            txt.append(f"TRAZABILIDAD — {sede}")
            txt.append(f"Usuario/modelo\tPDF origen\thojas\testado\tarchivo")
            txt.append("-" * 90)
            for f in filas:
                # f = [sede, serial, modelo, n_pag, pdf_origen, estado, archivo]
                txt.append(f"{f[1]}\t{f[4]}\t{f[3]}\t{f[5]}\t{os.path.basename(f[6])}")
            # FIX 2026-09-18: hojas descartadas (para que cuadre: origen = OK + POR_VERIFICAR + descartadas)
            desc_sede = [d for d in descartadas_doc if (d.get("sede") or sede) == sede]
            if desc_sede:
                txt.append("")
                txt.append("DESCARTADAS (no se generó archivo — hojas omitidas a propósito)")
                txt.append("Página\tSerial\tModelo\tMotivo")
                txt.append("-" * 90)
                for d in sorted(desc_sede, key=lambda x: x["num"]):
                    txt.append(f"pag{d['num']+1}\t{d['sn']}\t{d['modelo']}\t{d['razon']}")
            reg = os.path.join(carpeta_local, sede, "02_REGISTRO.txt")
            with open(reg, "w", encoding="utf-8") as fw:
                fw.write("\n".join(txt))

        # --- 01_RESUMEN_GENERAL.txt (raíz) ---
        lineas_gen = [f"Resumen corrida {stamp}: {n_ok} usuarios OK · {n_rev} a revisar", ""]
        for sede in sorted(sedes):
            filas_ok = [f for f in sedes[sede] if f[5] == 'OK']
            filas_rev = [f for f in sedes[sede] if f[5] != 'OK']
            lineas_gen.append(f"[SEDE] {sede} → {len(filas_ok)} OK · {len(filas_rev)} a revisar")
        lineas_gen.append("")
        for linea in resumen:
            lineas_gen.append(linea)
        # FIX 2026-09-18: desglose de hojas descartadas (cuadre de la cuenta)
        if descartadas_doc:
            lineas_gen.append("")
            lineas_gen.append(f"DESCARTADAS: {len(descartadas_doc)} hoja(s) omitida(s)")
            for d in sorted(descartadas_doc, key=lambda x: x["num"]):
                lineas_gen.append(f"DESCARTADA|pag{d['num']+1}|{d['sn']}|{d['modelo']}|{d['razon']}")
        with open(os.path.join(carpeta_local, "01_RESUMEN_GENERAL.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lineas_gen))

        # --- Gmail alias: archivo con la dirección destino de esa sede ---
        # (si la sede tiene correo configurado, queda un .gmail/ con el alias
        #  legible por humanos y utilizable con rclone gdrive --drive-export-formats)
        for sede in sorted(sedes):
            correos = correos_de_sede(sede)
            if not correos:
                continue
            ruta_gmail = os.path.join(carpeta_local, sede, ".gmail")
            os.makedirs(ruta_gmail, exist_ok=True)
            with open(os.path.join(ruta_gmail, "TO.txt"), "w", encoding="utf-8") as fg:
                fg.write("Sede: " + sede + "\n")
                fg.write("Destinatarios: " + ", ".join(correos) + "\n")
            log(f"[correo-sede] {sede} -> {', '.join(correos)}")

        # Subir a Drive
        url = None
        if not args.dry:
            nombre_remoto = f"Separador_{stamp}"
            log("[7] Subiendo a Drive...")
            url, err = subir_y_linkear(carpeta_local, nombre_remoto)
            if url:
                log("[7] Link:", url)
            else:
                log("[7] Error subiendo:", err)

            # Mover procesados (archivo REMOTO de Entrada → Procesados)
            log("[9] Moviendo PDFs a Procesados/")
            for pdf in pdfs:
                nombre = os.path.basename(pdf)
                rc("moveto", f"{GDRIVE}{CARPETA_ENTRADA}/{nombre}",
                   f"{GDRIVE}{CARPETA_PROCESADOS}/{nombre}")

        # Resumen + correo
        n_ok = sum(1 for r in resumen if r.startswith("OK|"))
        n_ver = sum(1 for r in resumen if r.startswith("VER|"))
        if args.dry:
            log(f"[dry] {n_ok} OK, {n_ver} verificar — sin subir/enviar")
        else:
            asunto = f"📄 Separador PDF {stamp} — {n_ok} OK · {n_ver} a verificar"
            link_html = f'<p><b>Carpeta:</b> <a href="{html.escape(url)}">{html.escape(url)}</a></p>' if url else "<p>(no se pudo generar link)</p>"
            cuerpo_txt = "\n".join(resumen) + "\n\nLink: " + (url or "sin link") + "\n"
            cuerpo_html = ("<pre>" + html.escape("\n".join(resumen)) + "</pre>" + link_html)
            enviar_correo(asunto, cuerpo_html, cuerpo_txt)

            # --- Aviso por sede: solo a los equipos de esa sede (CC a GMAIL_USER) ---
            for sede in sorted(sedes):
                correos = correos_de_sede(sede)
                if not correos:
                    continue
                filas_sede = [f for f in sedes[sede]]
                n_ok_s = sum(1 for f in filas_sede if f[5] == 'OK')
                n_rev_s = len(filas_sede) - n_ok_s
                detalle = []
                for f in filas_sede:
                    estado = "OK" if f[5] == 'OK' else "REVISAR"
                    detalle.append(f"{estado}|{f[1]}|{f[3]} pág.|{os.path.basename(f[6])}")
                asunto_s = (f"📄 Contadores {stamp} — {sede} · "
                            f"{n_ok_s} listo(s), {n_rev_s} a revisar")
                texto_s = (f"Se separaron los contadores de {sede}.\n\n"
                           f"Listos: {n_ok_s} · A revisar: {n_rev_s}\n\n"
                           + "\n".join(detalle)
                           + f"\n\nCarpeta con los archivos:\n{url or 'sin link'}\n")
                html_s = (f"<p>Se separaron los contadores de <b>{html.escape(sede)}</b>.</p>"
                          f"<p><b>{n_ok_s}</b> listo(s) · <b>{n_rev_s}</b> a revisar</p>"
                          f"<pre>{html.escape(chr(10).join(detalle))}</pre>"
                          + (link_html if url else ""))
                enviar_correo(asunto_s, html_s, texto_s, destinatarios=correos)
        log("FIN")
    finally:
        os.remove(LOCK) if os.path.exists(LOCK) else None


def merge_paginas(ruta_pdf, paginas, salida):
    import fitz
    src = fitz.open(ruta_pdf)
    dst = fitz.open()
    for n in paginas:
        dst.insert_pdf(src, from_page=n, to_page=n)
    dst.save(salida)
    dst.close()
    src.close()


if __name__ == "__main__":
    main()
