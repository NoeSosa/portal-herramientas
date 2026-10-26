#!/home/node/.openclaw/workspace/venv-pdfseparador/bin/python
"""
separador_pdf.py — Separador topológico multimarca (adaptado a servidor ARM64)
================================================================================
Adaptación del script original de Noe para correr en el servidor (aarch64),
con OCR PARALELO multi-proceso (2 instancias, 1 núcleo c/u).

Cambios respecto al original:
  1. NO interactivo: acepta rutas por CLI, sin menú de sede (opcional --sede).
  2. Parche numpy (np.sctypes) incluido para imgaug en numpy 2.x.
  3. Dashboard de consola simplificado: logs limpios (sin limpiar pantalla).
  4. Inventario Excel opcional: sin él, seriales se toman del OCR.
  5. --workers N: parte el PDF en N rangos y corre N procesos OCR en paralelo
     (cada uno con cpu_threads=1). Después une los textos y corre la lógica
     completa en un solo proceso. Acelera ~lineal en servidores multi-núcleo
     donde Paddle no paraleliza por hilos.
  6. Rutas y DPI configurables por CLI.

Uso:
  python separador_pdf.py --pdf <archivo.pdf> [--dir-salida <dir>]
      [--excel <inventario.xlsx>] [--hoja <nombre>] [--sede <texto>]
      [--dpi 200] [--workers 2] [--max-paginas N]

Ejemplos:
  # OCR paralelo (2 núcleos) a 200 dpi con inventario:
  python separador_pdf.py --pdf Escaneados/lote.pdf --dpi 200 --workers 2 \
      --excel CONTADORES_JUNIO_ZONA_09.xlsx --hoja "1. Consumo de Impresión Mono"

✅ VALIDADO: 08/sep/2026 (Ches) — motor PaddleOCR 2.7.0.3 + paddle 2.6.2 ARM64,
OCR paralelo 2 procesos (benchmark: threads no acelera, procesos sí).
"""
import os
import sys
import time
import re
import json
import argparse
import subprocess
import gc

# Parche numpy para imgaug (numpy 2.x eliminó np.sctypes; imgaug 0.4 lo usa)
import numpy as np
if not hasattr(np, 'sctypes'):
    np.sctypes = {
        'int': [np.int8, np.int16, np.int32, np.int64],
        'uint': [np.uint8, np.uint16, np.uint32, np.uint64],
        'float': [np.float16, np.float32, np.float64],
        'complex': [np.complex64, np.complex128],
    }

import warnings
warnings.filterwarnings('ignore')
import logging
logging.disable(logging.WARNING)

try:
    import fitz  # PyMuPDF
    import cv2
    from paddleocr import PaddleOCR
except ImportError as e:
    print(f"\n[ERROR] Falta dependencia: {e}")
    sys.exit(1)

# ==========================================
# LÓGICA DE CORRECCIÓN OCR (igual que original)
# ==========================================
def normalizar_sn(sn):
    sn = sn.upper().strip()
    res = []
    has_num = False
    for c in sn:
        if c.isdigit(): has_num = True
        if has_num and c == 'O': res.append('0')
        elif has_num and c == 'I': res.append('1')
        elif has_num and c == 'L': res.append('1')
        else: res.append(c)
    return "".join(res)

def generar_variantes_ocr(sn):
    variantes = set([sn])
    reemplazos = [
        ('O', '0'), ('0', 'O'),
        ('I', '1'), ('1', 'I'),
        ('L', '1'), ('1', 'L'),
        ('B', '8'), ('8', 'B'),
        ('S', '5'), ('5', 'S'),
        ('Z', '2'), ('2', 'Z')
    ]
    for orig, remp in reemplazos:
        if orig in sn: variantes.add(sn.replace(orig, remp))
    return variantes

def obtener_sns_esperados(df, sede_elegida=None):
    """Construye dict {SN_real: SN_normalizado} desde el df, opcional filtro sede."""
    sns_validos = {}
    try:
        col_sn = next((c for c in df.columns if 'serie' in str(c).lower()), None)
        col_dom = next((c for c in df.columns if 'domicilio' in str(c).lower() or 'ubicación' in str(c).lower()), None)
        if col_sn is None:
            print("  [aviso] No se encontró columna de serie; sin validación.")
            return sns_validos
        df_f = df
        if sede_elegida and col_dom:
            df_f = df[df[col_dom].astype(str).str.contains(sede_elegida, regex=False, na=False)]
        for idx, row in df_f.iterrows():
            sn = str(row[col_sn]).strip().upper()
            if sn and sn not in ['NAN', 'NONE', '0']:
                sns_validos[sn] = normalizar_sn(sn)
    except Exception as e:
        print(f"  [aviso] Error leyendo inventario: {e}")
    return sns_validos

def validar_sn_contra_inventario(texto, sns_esperados):
    if not sns_esperados:
        return None
    palabras = re.findall(r'[A-Z0-9]{5,15}', texto.upper())
    for palabra in palabras:
        if palabra in sns_esperados: return palabra
        for var in generar_variantes_ocr(palabra):
            if var in sns_esperados: return var
        pal_norm = normalizar_sn(palabra)
        for sn_real, sn_norm_esperado in sns_esperados.items():
            if pal_norm == sn_norm_esperado: return sn_real
    return None

def modelo_hp_nuevo(texto):
    """Sólo los dos modelos nuevos; no inferirlos por el prefijo del serial."""
    if re.search(r"(?i)\be\s*4[0o][0o]4[0o]\b", texto):
        return "hp_e40040"
    if re.search(r"(?i)\bx\s*53[0o]5[2z]\b", texto):
        return "hp_x53052"
    return None


# El OCR de estas páginas a veces une palabra y separador: "de_serie",
# "dei producto", "producto:_". El patrón acepta esas variantes.
PATRON_SERIAL_ETIQUETA = re.compile(
    r"(?i)(?:n[uú]mero[\s_\-]*(?:d[eai][\s_\-]*)?serie"
    r"|(?:product[\s_\-]+)?serial[\s_\-]*(?:no|number|num)?)"
    r"(?:[\s_\-]*(?:d[eai][il1]?[\s_\-]*)?(?:producto|equipo))?"
    r"[\s:=_\-]*([A-Z0-9]{6,16})\b"
)


def extraer_serial_etiqueta(texto):
    """Extrae el serial de una etiqueta, aunque el OCR meta guiones/underscores.

    Acepta "Número de serie del producto", "Numero de_serie del producto",
    "Numero de serie dei producto" y "Product Serial Number". No exige que el
    serial exista en el inventario ni que el modelo sea conocido.
    """
    m = PATRON_SERIAL_ETIQUETA.search(texto or "")
    if not m:
        return None
    cand = m.group(1).upper()
    # Un serial real de estos equipos tiene al menos dos dígitos.
    return normalizar_sn(cand) if sum(ch.isdigit() for ch in cand) >= 2 else None


def _serial_valido(cand):
    """Un token de serial: largo 8-16, con letras y al menos 2 dígitos."""
    if not (8 <= len(cand) <= 16):
        return False
    if not any(ch.isalpha() for ch in cand):
        return False
    if sum(ch.isdigit() for ch in cand) < 2:
        return False
    return True


def extraer_serial_generico(texto):
    """Último recurso para series con prefijos como MX3, VNG, etc.

    Primero busca cerca de la palabra serie/serial, para no confundir fechas
    u otros códigos de la hoja. Acepta minúsculas y mezclas como MX3CV6Q0DZ.
    """
    limpio = (texto or "").upper()
    for m in re.finditer(r"(?:N[UÚ]MERO|SERIAL|SERIE)(.{0,120})", limpio, re.DOTALL):
        for cand in re.findall(r"\b[A-Z0-9]{8,16}\b", m.group(1)):
            if _serial_valido(cand):
                return normalizar_sn(cand)
    # Sin etiqueta, sólo aceptar formas de serial (2-3 letras + dígito) para
    # no confundir fechas como 23OCT2026 ni palabras largas.
    for cand in re.findall(
        r"\b(?:[A-Z]{2,3}\d[A-Z0-9]{4,12}|[A-Z]{3}[A-Z0-9]{5,13})\b", limpio
    ):
        if _serial_valido(cand):
            return normalizar_sn(cand)
    return None


def datos_uso_hp(texto):
    """Clasificación conservadora de hojas E40040/X53052 leídas por OCR."""
    modelo = modelo_hp_nuevo(texto)
    if not modelo:
        return None
    low = texto.lower().translate(str.maketrans("áéíóú", "aeiou"))
    uso = bool(re.search(r"\b(?:pagina\s+de\s+uso|usage\s+page)\b", low))
    config = bool(re.search(r"\b(?:configuraci[o0]n|configuration|jetdirect)\b", low))
    numero = re.search(r"\b(?:pagina|page)\s*[:#]?\s*([12ilz])\b", low)
    pagina = {"1": 1, "i": 1, "l": 1, "2": 2, "z": 2}.get(numero.group(1)) if numero else None
    sn = extraer_serial_etiqueta(texto)
    return {"modelo": modelo, "tipo": "uso" if uso else "config" if config else "desconocido",
            "pagina": pagina, "sn": sn}


def preparar_uso_hp(paginas):
    """Filtra configuración y hereda serie únicamente entre P1/P2 contiguas.

    Conserva hojas ambiguas para revisión. Admite el esquema del pipeline
    automático (info/sn_ocr) y el del separador directo (sn).
    """
    def serial(p):
        return (p.get("info") or {}).get("sn") or p.get("sn") or p.get("sn_ocr")

    descartadas = []
    por_num = {p["num"]: p for p in paginas}
    for p in paginas:
        datos = datos_uso_hp(p.get("texto", ""))
        if not datos:
            continue
        p["hp_uso"] = datos
        p["modelo"] = datos["modelo"]
        p["hp_revision"] = datos["tipo"] != "uso" or datos["pagina"] != 1
        if datos["tipo"] == "config":
            descartadas.append({"num": p["num"], "sn": serial(p) or datos["sn"] or "SIN_ID",
                                "modelo": p["modelo"], "razon": "configuración/red HP E40040/X53052; no es página de uso"})

    for p2 in paginas:
        datos = p2.get("hp_uso", {})
        if datos.get("tipo") != "uso" or datos.get("pagina") != 2:
            continue
        p1 = por_num.get(p2["num"] - 1)
        anterior = (p1 or {}).get("hp_uso", {})
        if (anterior.get("tipo") != "uso" or anterior.get("pagina") != 1
                or anterior.get("modelo") != datos["modelo"] or not serial(p1)):
            continue
        if serial(p2) and normalizar_sn(serial(p2)) != normalizar_sn(serial(p1)):
            p1["hp_revision"] = True
            continue
        for key in ("sn", "info", "sn_detect", "sn_ocr"):
            if key in p1:
                p2[key] = p1[key]
        p2["hp_revision"] = False
        p2["hp_pareja"] = p1["num"]

    excluir = {p["num"] for p in descartadas}
    return [p for p in paginas if p["num"] not in excluir], descartadas


def revisar_grupo_hp(paginas):
    """Una P1 sola o una P1 seguida de su P2; duplicados no se validan."""
    nuevas = [p for p in paginas if p.get("hp_uso")]
    if not nuevas:
        return False
    if len(nuevas) != len(paginas) or any(p.get("hp_revision") for p in nuevas):
        return True
    if len(nuevas) == 1:
        return nuevas[0]["hp_uso"]["pagina"] != 1
    if len(nuevas) != 2:
        return True
    p1, p2 = sorted(nuevas, key=lambda p: p["num"])
    return p2.get("hp_pareja") != p1["num"]


def identificar_modelo(texto, sn):
    nuevo = modelo_hp_nuevo(texto)
    if nuevo:
        return nuevo
    texto_low = texto.lower()
    sn_low = str(sn).lower() if sn else ""
    if "phbtw" in sn_low or "vnb" in sn_low: return "hp_501"
    if "cnb" in sn_low: return "hp_mfp"
    if "ecosys" in texto_low or "kyocera" in texto_low: return "kyocera"
    if "pantum" in texto_low or "bm5100" in texto_low: return "pantum"
    if "ricoh" in texto_low or "page counter:printing" in texto_low: return "ricoh"
    return "desconocido"

# ==========================================
# FASE 1: OCR de páginas (paralelizable por rango)
# ==========================================
# ==========================================
# Progreso en vivo (solo para el panel): candidato de serial por PATRÓN
# ==========================================
# NO valida contra el inventario (eso es la clasificación, después): aquí solo
# interesa VER en pantalla qué está leyendo el OCR hoja por hoja.
_patrones_candidato = (
    re.compile(r'(?i)(?:numero\s*serie\s*equipo|n[uú]mero\s*de\s*serie|serial\s*(?:no|number|num)?)\s*[:\-]?\s*([A-Z0-9]{6,16})'),
    re.compile(r'(?i)\b((?:VNB0B0|PHBTW|CN[BS][A-Z0-9]{6,10}|P[A-Z0-9]{2}BTW|\d{4}P(?:C)?[A-Z]?\d{4,6})[A-Z0-9]{0,8})\b'),
)


def candidato_serial(texto):
    """Primer serial con pinta válida en el texto de una hoja ('' si no hay).
    Solo para mostrar en vivo; la validación real la hace la clasificación."""
    for patron in _patrones_candidato:
        m = patron.search(texto or "")
        if m:
            cand = m.group(1).upper()
            if sum(ch.isdigit() for ch in cand) >= 2:
                return cand
    return ""


# --- Cajas que detecta el OCR (SOLO para el panel: animación sobre la hoja) ---
# PaddleOCR devuelve, por cada línea, su polígono envolvente. Aquí se normaliza a
# 0..1 respecto de la hoja y se marca qué línea es "dato relevante". Es puramente
# visual: nada de esto entra en la clasificación ni en la validación del serial.
_RE_SN_CAJA = re.compile(r'(?i)\b(?:CN[BS][A-Z0-9]{6,10}|VNB0B0[A-Z0-9]{4,8}|PHBTW[A-Z0-9]{4,8})\b')
_RE_RELEVANTE = (
    ("serial", re.compile(r'(?i)(numero\s*serie|n[uú]mero\s*de\s*serie|serial\s*(?:no|number|num)?)\s*[:\-]?\s*[A-Z0-9]{5,}')),
    ("contador", re.compile(r'(?i)\b(counter|contador|copias|copies|total\s*pages|impresiones|p[aá]ginas)\b')),
    ("modelo", re.compile(r'(?i)\b(hp\s*l?aserjet|laserjet|pantum|ricoh|ecosys|kyocera|mfp|bm5100)\b')),
    ("fecha", re.compile(r'\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b')),
)


def _tipo_caja(texto):
    """Etiqueta de un renglón leído: serial / contador / modelo / fecha / ""."""
    t = texto or ""
    if _RE_SN_CAJA.search(t):
        return "serial"
    for nombre, patron in _RE_RELEVANTE:
        if patron.search(t):
            return nombre
    return ""


def cajas_lectura(res, ancho, alto, y_offset=0.0, alto_frac=1.0, maximo=80):
    """Cajas normalizadas (0..1) de todo lo que el OCR detectó en una imagen.

    `y_offset` y `alto_frac` recolocan las cajas de una FRANJA sobre la hoja
    completa, para que la animación caiga en el sitio correcto.
    """
    if not res or not res[0] or not ancho or not alto:
        return []
    cajas = []
    for item in res[0][:maximo]:
        try:
            poli, (txt, _conf) = item[0], item[1]
        except (IndexError, TypeError, ValueError):
            continue
        txt = (txt or "").strip()
        if not txt:
            continue
        xs = [p[0] for p in poli]
        ys = [p[1] for p in poli]
        caja = {
            "x": round(min(max(min(xs) / ancho, 0.0), 1.0), 4),
            "y": round(min(max((min(ys) / alto) * alto_frac + y_offset, 0.0), 1.0), 4),
            "w": round(min(max((max(xs) - min(xs)) / ancho, 0.0), 1.0), 4),
            "h": round(min(max(((max(ys) - min(ys)) / alto) * alto_frac, 0.002), 1.0), 4),
            "t": txt[:72],
        }
        tipo = _tipo_caja(txt)
        if tipo:
            caja["r"] = tipo
        cajas.append(caja)
    return cajas


def publicar_lectura(pdf, pag, pag_total, ini, fin, ms, texto, **extra):
    """Publica la lectura de UNA hoja (o de una franja) para el panel.

    Si PANEL_OCR_JSONL está puesto escribe una línea JSON que el worker del panel
    lee y manda por SSE. Con `banda`/`lineas` se publica una FRANJA (lectura
    secuencial: el panel va viendo las líneas conforme se leen).
    """
    ruta = os.environ.get("PANEL_OCR_JSONL")
    if not ruta:
        return
    try:
        linea = {
            "tipo": extra.pop("tipo", "ocr_banda" if extra.get("banda") else "ocr_pagina"),
            "pdf": os.path.basename(pdf), "pag": pag, "pag_total": pag_total,
            "worker": f"{ini}-{fin}", "pid": os.getpid(), "ms": round(ms, 1),
            "slot": int(os.environ.get("PANEL_WORKER_SLOT", "-1") or -1),
            "sn": candidato_serial(texto),
            "texto": " ".join((texto or "").split())[:20000],
            "ts": time.time(),
        }
        linea.update(extra)
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"    [panel] aviso: no pude publicar la lectura: {e}", flush=True)


def _es_sn_solo(cand):
    """¿Con el número de serie basta para identificar este equipo?

    Sí para HP MFP 432 (CNB…) y HP 501 (VNB0B0…/PHBTW…): el modelo se deduce del
    propio serial, se espera 1 hoja y la sede viene del inventario.
    Ricoh y Pantum NO: necesitan texto de la hoja (contador vs configuración,
    page 1/2…), así que su lectura debe completarse.
    """
    c = (cand or "").upper()
    return c.startswith(("CNB", "VNB0B0", "PHBTW"))


def ocr_rango_secuencial(ruta_pdf, pag_inicio, pag_fin, dpi, ocr, bandas=1):
    """Lectura ADAPTATIVA (secuencial) con corte temprano.

    Para cada hoja:
      1. Lee la **franja del encabezado** (35 % superior) y la publica en vivo.
      2. Si ahí está el serial de un modelo que **solo necesita el SN** (HP 432/501),
         termina la hoja con esa lectura parcial → ahorra el resto de la hoja.
      3. Si no, lee la **hoja completa** como siempre (mismo coste que antes + la
         franja) y publica el texto completo.

    Así el panel enseña la lectura conforme entra (secuencial) y solo se paga el
    coste extra de la franja en las hojas que NO se pueden cortar.
    """
    doc = fitz.open(ruta_pdf)
    total_doc = len(doc)
    solo_sn = os.environ.get("PANEL_OCR_SOLO_SN", "1") != "0"
    frac = float(os.environ.get("PANEL_OCR_FRANJA", "0.35") or 0.35)
    resultados = []
    for num_pagina in range(pag_inicio, pag_fin):
        pag = doc.load_page(num_pagina)
        r = pag.rect
        t_pag = time.time()

        # --- 1) franja del encabezado (donde vive el serial en las hojas reales)
        clip = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * frac)
        pix = pag.get_pixmap(dpi=dpi, clip=clip)
        img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
        res = ocr.ocr(img, cls=False)
        lineas_cab = [l[1][0] for l in res[0]] if res and res[0] else []
        cajas_cab = cajas_lectura(res, img.shape[1], img.shape[0],
                                  y_offset=0.0, alto_frac=frac)
        del pix, img, res
        texto_cab = " ".join(lineas_cab)
        cand = candidato_serial(texto_cab)
        publicar_lectura(ruta_pdf, num_pagina + 1, total_doc, pag_inicio, pag_fin,
                         (time.time() - t_pag) * 1000.0, texto_cab,
                         banda=1, bandas=2, y0=0.0, y1=round(frac, 4),
                         lineas=lineas_cab[:40], cajas=cajas_cab)

        # --- 2) ¿con el serial del encabezado basta? → corte temprano
        if solo_sn and cand and _es_sn_solo(cand) and not modelo_hp_nuevo(texto_cab):
            texto = re.sub(r'VNB08[0O]', 'VNB0B0', texto_cab, flags=re.IGNORECASE)
            resultados.append({'num': num_pagina, 'texto': texto})
            ms = (time.time() - t_pag) * 1000.0
            print(f"    [worker {pag_inicio}-{pag_fin}] pág {num_pagina + 1}/{pag_fin}"
                  f" · {ms:.0f}ms · sn~{cand} · parcial(en cabecera)", flush=True)
            publicar_lectura(ruta_pdf, num_pagina + 1, total_doc, pag_inicio, pag_fin,
                             ms, texto, tipo="ocr_pagina", parcial=True, cajas=cajas_cab)
            del pag
            continue

        # --- 3) hoja completa (camino normal) + publicación en vivo del resto
        pix = pag.get_pixmap(dpi=dpi)
        img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
        res = ocr.ocr(img, cls=False)
        lineas = [l[1][0] for l in res[0]] if res and res[0] else []
        cajas = cajas_lectura(res, img.shape[1], img.shape[0])
        del pix, img, res
        texto = " ".join(lineas)
        texto = re.sub(r'VNB08[0O]', 'VNB0B0', texto, flags=re.IGNORECASE)
        resultados.append({'num': num_pagina, 'texto': texto})
        ms = (time.time() - t_pag) * 1000.0
        cand_full = candidato_serial(texto)
        print(f"    [worker {pag_inicio}-{pag_fin}] pág {num_pagina + 1}/{pag_fin}"
              f" · {ms:.0f}ms" + (f" · sn~{cand_full}" if cand_full else "") + " · hoja completa",
              flush=True)
        publicar_lectura(ruta_pdf, num_pagina + 1, total_doc, pag_inicio, pag_fin, ms, texto,
                         tipo="ocr_pagina", parcial=False, cajas=cajas)
        del pag
    doc.close()
    return resultados


def ocr_rango(ruta_pdf, pag_inicio, pag_fin, dpi, cpu_threads=1, ocr=None):
    """Devuelve lista [{num, texto}] para páginas [pag_inicio, pag_fin).

    PANEL (16/oct/2026): imprime UNA LÍNEA POR PÁGINA con su tiempo real y el
    serial candidato, y además publica la LECTURA completa (texto) en el archivo
    de progreso del panel (`PANEL_OCR_JSONL`) para verla en vivo. La lógica de OCR
    no cambia.
    """
    if ocr is None:
        ocr = PaddleOCR(lang='en', use_angle_cls=False, show_log=False, cpu_threads=cpu_threads)
    # Lectura SECUENCIAL por franjas (opt-in): ver lo que lee el OCR conforme avanza
    # y poder cortar la hoja al encontrar el serial (modelos que solo necesitan SN).
    if os.environ.get("PANEL_OCR_SECUENCIAL", "0") == "1":
        return ocr_rango_secuencial(ruta_pdf, pag_inicio, pag_fin, dpi, ocr,
                                    os.environ.get("PANEL_OCR_BANDAS", "4"))
    doc = fitz.open(ruta_pdf)
    total_doc = len(doc)
    resultados = []
    for num_pagina in range(pag_inicio, pag_fin):
        t_pag = time.time()
        pagina = doc.load_page(num_pagina)
        pix = pagina.get_pixmap(dpi=dpi)
        img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
        res = ocr.ocr(img, cls=False)
        lineas = res[0] if res and res[0] else []
        texto = " ".join([linea[1][0] for linea in lineas])
        cajas = cajas_lectura(res, img.shape[1], img.shape[0])
        # Parche quirúrgico HP 501
        texto = re.sub(r'VNB08[0O]', 'VNB0B0', texto, flags=re.IGNORECASE)
        resultados.append({'num': num_pagina, 'texto': texto})
        del pagina, pix, img, res
        ms = (time.time() - t_pag) * 1000.0
        cand = candidato_serial(texto)
        # 1) línea para el log del job (avance visible en el log)
        print(f"    [worker {pag_inicio}-{pag_fin}] pág {num_pagina + 1}/{pag_fin}"
              f" · {ms:.0f}ms" + (f" · sn~{cand}" if cand else ""), flush=True)
        # 2) la lectura completa, para el panel (texto que vio el OCR en la hoja)
        publicar_lectura(ruta_pdf, num_pagina + 1, total_doc, pag_inicio, pag_fin, ms, texto,
                         cajas=cajas)
    doc.close()
    return resultados

# ==========================================
# FASE 2: lógica topológica + generación de PDFs
# ==========================================
def es_pagina_blanca(ruta_pdf, num_pagina):
    """Comprueba la imagen; un fallo OCR nunca basta para descartar una hoja."""
    try:
        with fitz.open(ruta_pdf) as doc:
            pix = doc.load_page(num_pagina).get_pixmap(dpi=72, colorspace=fitz.csGRAY, alpha=False)
            muestras = pix.samples
            # Umbral conservador: cualquier trazo apreciable obliga a revisar.
            return bool(muestras) and sum(v < 235 for v in muestras) / len(muestras) < 0.00002
    except Exception:
        return False


def fase_logica(ruta_pdf, textos_paginas, carpeta_salida, sns_esperados):
    """textos_paginas: dict {num_pagina: texto}. Corre toda la lógica del original."""
    total_pag = max(textos_paginas.keys()) + 1 if textos_paginas else 0
    paginas_info = []

    patrones_respaldo = [
        r'(?:Serial\s*(?:No|Number|#|Num)\.?|ID\s*m[áa]quina)[\s:.\-]*([A-Z0-9]{6,15})',
        r'\b(\d{4}[A-Z]\d{6})\b',
        r'\b((?:PHBTW|VNB\d?[B8O0]|CNB\d?[A-Z])[A-Z0-9]{4,10})\b',
        r'\b(CR2[A-Z0-9]+)\b'
    ]

    for num_pagina in sorted(textos_paginas.keys()):
        texto_pagina = textos_paginas[num_pagina]
        # Hojas en blanco/reverso: no generan un PDF "Sin_Identificar".
        if not (texto_pagina or "").strip() and es_pagina_blanca(ruta_pdf, num_pagina):
            continue

        sn_encontrado = validar_sn_contra_inventario(texto_pagina, sns_esperados)

        if not sn_encontrado:
            sn_encontrado = extraer_serial_etiqueta(texto_pagina)

        if not sn_encontrado:
            for patron in patrones_respaldo:
                coincidencia = re.search(patron, texto_pagina, re.IGNORECASE)
                if coincidencia:
                    sn_encontrado = coincidencia.group(1).strip().upper()
                    if sn_encontrado.startswith("VNB080") or sn_encontrado.startswith("VNB08O"):
                        sn_encontrado = "VNB0B0" + sn_encontrado[6:]
                    break

        if not sn_encontrado:
            sn_encontrado = extraer_serial_generico(texto_pagina)

        datos_hp = datos_uso_hp(texto_pagina)
        if not sn_encontrado and datos_hp:
            sn_encontrado = datos_hp['sn']

        texto_low = texto_pagina.lower()
        es_doc_pantum = "pantum" in texto_low or "bm5100" in texto_low
        is_pantum_p1 = False
        is_pantum_p2 = False
        if es_doc_pantum:
            if re.search(r'page\s*[1il|]', texto_low):
                is_pantum_p1 = True
            elif re.search(r'page\s*[2z]', texto_low):
                is_pantum_p2 = True
            else:
                if ("product information" in texto_low or "system settings" in texto_low
                        or "consumables information" in texto_low):
                    is_pantum_p1 = True
                elif ("copy information" in texto_low or "faxer information" in texto_low
                        or "ordinary copy setting" in texto_low):
                    is_pantum_p2 = True

        if is_pantum_p1 or is_pantum_p2:
            modelo = 'pantum'
        else:
            modelo = identificar_modelo(texto_pagina, sn_encontrado)

        if not sn_encontrado:
            m = re.search(r'\b((?:VNB0B0|PHBTW|CNB|CR2)[A-Z0-9]{4,12})\b', texto_pagina.upper())
            if m:
                sn_encontrado = m.group(1)

        paginas_info.append({'num': num_pagina, 'texto': texto_pagina, 'sn': sn_encontrado, 'modelo': modelo,
                             'is_p1': is_pantum_p1, 'is_p2': is_pantum_p2})

    paginas_info, descartadas_hp = preparar_uso_hp(paginas_info)
    for p in paginas_info:
        if p.get("hp_uso") and not p["sn"]:
            p["sn"] = f"Sin_Identificar_pag{p['num'] + 1}"

    # --- Emparejamiento topológico P1-P2 (Pantum) ---
    pantum_unpaired = [p for p in paginas_info if p['is_p1'] or p['is_p2']]
    while True:
        emparejo = False
        for i in range(len(pantum_unpaired) - 1):
            p_a, p_b = pantum_unpaired[i], pantum_unpaired[i + 1]
            if (p_a['is_p1'] and p_b['is_p2']) or (p_a['is_p2'] and p_b['is_p1']):
                p1_node = p_a if p_a['is_p1'] else p_b
                p2_node = p_a if p_a['is_p2'] else p_b
                p2_node['sn'] = p1_node['sn']
                p2_node['modelo'] = 'pantum'
                pantum_unpaired.pop(i + 1)
                pantum_unpaired.pop(i)
                emparejo = True
                break
        if not emparejo:
            break
    for p in pantum_unpaired:
        if p['is_p2']:
            p['sn'] = f"Pantum_Huerfana_P2_pag{p['num'] + 1}"
        elif p['is_p1'] and not p['sn']:
            p['sn'] = f"Pantum_Huerfana_P1_pag{p['num'] + 1}"

    # --- Herencia Ricoh / HP MFP ---
    for i, info in enumerate(paginas_info):
        if not info['sn'] and info['modelo'] not in ['pantum', 'kyocera', 'hp_501']:
            prev_p = paginas_info[i - 1] if i > 0 else None
            next_p = paginas_info[i + 1] if i < len(paginas_info) - 1 else None
            if prev_p and prev_p['sn'] and prev_p['modelo'] in ['ricoh', 'hp_mfp']:
                info['sn'] = prev_p['sn']; info['modelo'] = prev_p['modelo']
            elif next_p and next_p['sn'] and next_p['modelo'] in ['ricoh', 'hp_mfp']:
                info['sn'] = next_p['sn']; info['modelo'] = next_p['modelo']
            else:
                info['sn'] = f"Sin_Identificar_pag{i + 1}"

    # --- Agrupar por SN ---
    paginas_por_sn = {}
    for info in paginas_info:
        paginas_por_sn.setdefault(info['sn'], []).append(info)

    # --- Generar PDFs por grupo ---
    carpeta_verificar = os.path.join(carpeta_salida, "Verificar")
    os.makedirs(carpeta_verificar, exist_ok=True)
    equipos_ok = 0
    equipos_revisar = 0
    grupos = []
    doc = fitz.open(ruta_pdf)

    for sn, lista_paginas in paginas_por_sn.items():
        en_inventario = sn in sns_esperados
        modelo = lista_paginas[0]['modelo']
        cant = len(lista_paginas)

        if modelo == 'pantum':
            lista_paginas.sort(key=lambda x: not x['is_p1'])

        requiere_revision = not en_inventario or str(sn).startswith(('Sin_', 'Pantum_Huerfana'))
        if revisar_grupo_hp(lista_paginas): requiere_revision = True
        if modelo == 'hp_501' and cant != 1: requiere_revision = True
        if modelo == 'kyocera' and cant != 1: requiere_revision = True
        if modelo == 'pantum' and cant != 2: requiere_revision = True
        if modelo == 'ricoh' and cant != 5: requiere_revision = True

        if requiere_revision:
            carpeta_destino = carpeta_verificar
            nombre_salida = f"{sn}-revisar.pdf"
            equipos_revisar += 1
        else:
            carpeta_destino = carpeta_salida
            nombre_salida = f"{sn}.pdf"
            equipos_ok += 1

        nuevo_pdf = fitz.open()
        for p_info in lista_paginas:
            nuevo_pdf.insert_pdf(doc, from_page=p_info['num'], to_page=p_info['num'])
        archivo_salida = os.path.join(carpeta_destino, nombre_salida)
        nuevo_pdf.save(archivo_salida)
        nuevo_pdf.close()
        grupos.append({'sn': sn, 'modelo': modelo, 'paginas': cant,
                       'archivo': archivo_salida, 'revisar': requiere_revision,
                       'inventario': en_inventario})
    doc.close()
    return equipos_ok, equipos_revisar, grupos


def main():
    ap = argparse.ArgumentParser(description="Separador topológico multimarca (servidor, OCR paralelo)")
    ap.add_argument("--pdf", required=True, help="Ruta del PDF escaneado a procesar")
    ap.add_argument("--dir-salida", default="Resultados_Organizados", help="Carpeta de salida")
    ap.add_argument("--excel", default=None, help="Inventario Excel (opcional)")
    ap.add_argument("--hoja", default="1. Consumo de Impresión Mono", help="Hoja del Excel")
    ap.add_argument("--sede", default=None, help="Filtro de sede/domicilio (opcional)")
    ap.add_argument("--dpi", type=int, default=200, help="Resolución OCR (default 200)")
    ap.add_argument("--workers", type=int, default=2, help="Procesos OCR en paralelo (default 2)")
    ap.add_argument("--max-paginas", type=int, default=None, help="Limitar páginas (pruebas)")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        print(f"[ERROR] No existe el PDF: {args.pdf}")
        sys.exit(1)
    os.makedirs(args.dir_salida, exist_ok=True)

    # Inventario
    sns_esperados = {}
    if args.excel and os.path.exists(args.excel):
        import pandas as pd
        print(f"[inventario] Leyendo {args.excel} (hoja '{args.hoja}')...")
        df = pd.read_excel(args.excel, sheet_name=args.hoja, header=0)
        sns_esperados = obtener_sns_esperados(df, args.sede)
        print(f"[inventario] {len(sns_esperados)} seriales" + (f" de sede '{args.sede}'" if args.sede else ""))
    else:
        print("[inventario] Sin Excel: seriales se toman del OCR (sin validación).")

    doc = fitz.open(args.pdf)
    total = len(doc)
    if args.max_paginas and total > args.max_paginas:
        total = args.max_paginas
    doc.close()

    # --- FASE 1: OCR paralelo por rangos ---
    n_workers = max(1, min(args.workers, total))
    t0 = time.time()
    print(f"[ocr] {total} páginas, {n_workers} worker(s), {args.dpi} dpi, cpu_threads=1 c/u")
    print(f"[ocr] Inicializando PaddleOCR (primera vez descarga modelos)...")

    # Si 1 solo worker: OCR directo en este proceso
    if n_workers == 1:
        ocr = PaddleOCR(lang='en', use_angle_cls=False, show_log=False, cpu_threads=1)
        res = ocr_rango(args.pdf, 0, total, args.dpi, 1, ocr)
        textos = {r['num']: r['texto'] for r in res}
    else:
        # Repartir páginas en rangos equitativos
        rangos = []
        for w in range(n_workers):
            ini = (total * w) // n_workers
            fin = (total * (w + 1)) // n_workers
            if fin > ini:
                rangos.append((ini, fin))
        procs = []
        tmp_json = []
        for w, (ini, fin) in enumerate(rangos):
            jf = os.path.join(args.dir_salida, f"_ocr_worker_{w}.json")
            tmp_json.append(jf)
            cmd = [sys.executable, os.path.abspath(__file__), "--modo-ocr-worker",
                   "--pdf", os.path.abspath(args.pdf),
                   "--rango", f"{ini}:{fin}",
                   "--dpi", str(args.dpi),
                   "--json-out", jf]
            env = dict(os.environ)
            env['LD_LIBRARY_PATH'] = os.environ.get('LD_LIBRARY_PATH', '') + os.pathsep + '/home/node/.local/lib'
            procs.append(subprocess.Popen(cmd, env=env))

        textos = {}
        for p in procs:
            p.wait()
        for jf in tmp_json:
            if os.path.exists(jf):
                with open(jf, 'r', encoding='utf-8') as f:
                    for r in json.load(f):
                        textos[r['num']] = r['texto']
                os.remove(jf)
        print("[ocr] Workers terminados, uniendo textos...")

    t_ocr = time.time() - t0
    print(f"[ocr] Fase OCR completa en {t_ocr:.0f}s")

    # --- FASE 2: lógica topológica ---
    t1 = time.time()
    ok, rev, grupos = fase_logica(args.pdf, textos, args.dir_salida, sns_esperados)
    t_log = time.time() - t1

    print("\n=== RESULTADO ===")
    for g in sorted(grupos, key=lambda x: (x['revisar'], x['sn'])):
        marca = "REVISAR" if g['revisar'] else "OK     "
        inv = " [inventario]" if g['inventario'] else ""
        print(f"  [{marca}] {g['sn']:28s} {g['modelo']:12s} {g['paginas']} pág -> {os.path.basename(g['archivo'])}{inv}")
    print(f"\nEquipos OK: {ok} | A revisar: {rev}")
    print(f"Tiempo: OCR {t_ocr:.0f}s + lógica {t_log:.0f}s = {t_ocr + t_log:.0f}s total")
    print(f"Salida: {os.path.abspath(args.dir_salida)}")


def modo_ocr_worker():
    """Modo interno: hace OCR de un rango y guarda JSON {num, texto}."""
    # Quitar el flag --modo-ocr-worker que ya consumió el dispatcher
    argv = [a for a in sys.argv if a != "--modo-ocr-worker"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--rango", required=True)  # "ini:fin"
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--json-out", required=True)
    args = ap.parse_args(argv[1:])
    ini, fin = [int(x) for x in args.rango.split(":")]
    res = ocr_rango(args.pdf, ini, fin, args.dpi, cpu_threads=1)
    with open(args.json_out, 'w', encoding='utf-8') as f:
        json.dump(res, f)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--modo-ocr-worker":
        modo_ocr_worker()
    else:
        main()
