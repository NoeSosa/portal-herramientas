#!/home/node/.openclaw/workspace/venv/bin/python
"""
panel_server.py — Servidor local (FastAPI) del panel del separador de PDFs.
==========================================================================
El servidor es SOLO visor + gestor de archivos + encolador: nunca corre el OCR en
primer plano. El trabajo real lo hace `panel_worker.py` (proceso aparte), así que
cerrar el navegador no afecta al procesamiento.

**MODO LOCAL (por defecto, 16/oct/2026)**: Noe se quedó sin espacio en Drive, así
que el panel NO habla con rclone/Drive: los PDFs se suben a `entrada_local/`, los
resultados se exploran/descargan desde aquí (`auto/salida/Separador_<fecha>/…`) y
el "procesados" es local. `panel_config.rclone()` revienta si alguien lo intenta.
Todo el código de Drive sigue intacto detrás de `modo: "drive"` en config.json.

Endpoints:
  GET  /                     UI (HTML+CSS+JS vanilla, sin CDN)
  GET  /api/estado           snapshot (cola, worker, job, resumen, eventos, modo, disco)
  GET  /api/entrada          PDFs en la entrada (local: tamaño + nº de páginas)
  POST /api/subir            sube PDFs a la entrada (multipart o cuerpo crudo ?nombre=)
  POST /api/borrar           {"nombre": "x.pdf", "que": "entrada|procesados"}
  POST /api/encolar          {"nombres":[…],"iniciar":true,"dry":true,…} → filas en `cola`
  POST /api/disparar         arranca el worker YA (reemplaza al vigilante de 7 min)
  GET  /api/resultados       árbol de resultados (corrida → sede → EQUIPOS_OK/A_REVISAR)
  GET  /api/descargar        ?corrida=&sede=&carpeta=&archivo= → archivo individual
  GET  /api/zip              ?corrida=&sede= → ZIP de la sede (o de la corrida)
  GET  /api/espacio          uso de disco del separador + libre del disco
  POST /api/limpiar          {"que":"corridas","conservar":2} → borra corridas viejas
  GET  /api/progreso         SSE: snapshot al conectar + eventos en vivo
  POST /api/pausar|reanudar|reintentar|cancelar
  GET  /api/health

Arranque:
  venv/bin/python separador_pdfs/panel/panel_server.py            # 127.0.0.1:8791
  PANEL_CONFIG=/ruta/config.test.json … panel_server.py           # pruebas

✅ VALIDADO: 16/oct/2026 (Ches) — /api/entrada local con páginas, subida y borrado,
   job local real (sin correo) con eventos SSE, resultados navegables, descarga
   individual y ZIP por sede, espacio y limpieza. Cero llamadas a rclone (stub).
"""
import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_config as pc          # noqa: E402
import panel_db as db              # noqa: E402
import panel_local as pl           # noqa: E402
import panel_worker as pw          # noqa: E402

PANEL_WORKER = os.path.join(pc.PANEL_DIR, "panel_worker.py")
MINIATURA_PY = os.path.join(pc.PANEL_DIR, "miniatura.py")
INVENTARIO_PY = os.path.join(pc.PANEL_DIR, "inventario.py")
CACHE_MINIATURAS = os.path.join(pc.TMP_DIR, "miniaturas")
INVENTARIO_PANEL = "inventario_panel.xlsx"   # el Excel que el pipeline usa de preferencia
MAX_SUBIDA_MB = 512

app = FastAPI(title="Panel separador de PDFs", version="1.1")
app.mount("/static", StaticFiles(directory=pc.STATIC_DIR), name="static")


# ------------------------------------------------------------------ helpers
def _cfg():
    return pc.cargar_config()


def sse(evento, datos):
    return f"event: {evento}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n"


def estado_worker(con):
    pid = db.control_get(con, "worker_pid") or pw.leer_pidfile() or ""
    return {"pid": pid, "vivo": bool(pw.pid_vivo(pid)),
            "estado": db.control_get(con, "worker_estado", "detenido"),
            "pausa_global": db.pausa_global(con),
            "iniciado_en": db.control_get(con, "worker_iniciado_en")}


def _entrada_descripcion(cfg):
    if pc.modo(cfg) == "local":
        return {"modo": "local", "ruta": pc.entrada_local(cfg),
                "etiqueta": "carpeta local: entrada_local/"}
    return {"modo": "drive", "ruta": pc.remoto(cfg),
            "etiqueta": f"Drive: {pc.remoto(cfg)}"}


def _correr_py(script, args, timeout=300):
    """Ejecuta un helper con el intérprete del pipeline (pandas/PyMuPDF)."""
    r = subprocess.run([pc.PY_VENV_PDF, script] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=timeout,
                       env=pc.entorno_rclone(pc.cargar_config()))
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _exportar_inventario(cfg):
    """Escribe el Excel consolidado que el pipeline leerá (inventario_panel.xlsx)."""
    destino = os.path.join(pc.inventarios_local(cfg), INVENTARIO_PANEL)
    code, out, err = _correr_py(INVENTARIO_PY, ["exportar", cfg["db"], destino])
    return (code == 0), destino, (out or err)


def snapshot(cfg):
    con = db.inicializar(cfg["db"])
    job = db.job_en_proceso(con)
    visible = job or db.job_ultimo(con)
    esp = pl.espacio(cfg)
    return {
        "tipo": "snapshot",
        "ts": time.time(),
        "modo": pc.modo(cfg),
        "dry_default": cfg["dry_default"],
        "correo_activo": cfg["correo_activo"],
        "puerto": cfg["puerto"],
        "db": cfg["db"],
        "entrada": _entrada_descripcion(cfg),
        "rclone_config": cfg["rclone_config"] or None,
        "rclone_bin": cfg["rclone_bin"] or pc.RCLONE,
        "inventarios": pl.inventarios(cfg) if pc.modo(cfg) == "local" else [],
        "espacio": esp,
        "cola": db.cola(con),
        "worker": estado_worker(con),
        "seriales": {"total": db.contar_equipos(con),
                     "sedes": db.valores_equipos(con, "sede"),
                     "marcas": db.valores_equipos(con, "marca"),
                     "estatus": db.valores_equipos(con, "estatus")},
        "ajustes": {"ocr_secuencial": bool(cfg.get("ocr_secuencial")),
                    "ocr_solo_sn": bool(cfg.get("ocr_solo_sn")),
                    "ocr_bandas": int(cfg.get("ocr_bandas") or 4)},
        "actual": job,
        "ultimo_job": visible if not job else None,
        "resumen": db.resumen_job(con, visible["job_id"]) if visible and visible.get("job_id") else {},
        "trabajadores": dict(db.resumen_workers(con, visible["job_id"] if visible else None),
                              programados=int(os.environ.get("SEPARADOR_WORKERS", "0") or 0)),
        "eventos": db.eventos_recientes(con, 80),
    }


def asegurar_worker(cfg):
    """Arranca el worker si no hay uno vivo. Devuelve (pid, arrancado)."""
    pid = pw.worker_activo(cfg)
    if pid:
        return pid, False
    env = pc.entorno_rclone(cfg, base=True)
    env["PANEL_CONFIG"] = cfg["_ruta"]
    env["PYTHONUNBUFFERED"] = "1"
    rutalog = os.path.join(pc.LOGS_DIR, "worker_launch.log")
    with open(rutalog, "a", encoding="utf-8") as lf:
        p = subprocess.Popen([pc.PY_VENV_PANEL, PANEL_WORKER], cwd=pc.SEP_DIR,
                             env=env, stdout=lf, stderr=subprocess.STDOUT,
                             start_new_session=True)
    return p.pid, True


def _md5(ruta, limite_mb=200):
    try:
        if os.path.getsize(ruta) > limite_mb * 1024 * 1024:
            return None
        h = hashlib.md5()
        with open(ruta, "rb") as f:
            for trozo in iter(lambda: f.read(1 << 20), b""):
                h.update(trozo)
        return h.hexdigest()
    except OSError:
        return None


async def _cuerpo(request: Request):
    try:
        return await request.json()
    except Exception:
        return {}


# ------------------------------------------------------------------ UI
@app.get("/")
async def raiz():
    return FileResponse(os.path.join(pc.STATIC_DIR, "index.html"))


@app.get("/api/health")
async def health():
    cfg = _cfg()
    return {"ok": True, "ts": time.time(), "modo": pc.modo(cfg), "db": cfg["db"],
            "puerto": cfg["puerto"]}


@app.get("/api/estado")
async def api_estado():
    cfg = _cfg()
    return await asyncio.to_thread(snapshot, cfg)


# ------------------------------------------------------------------ entrada
@app.get("/api/entrada")
async def api_entrada():
    cfg = _cfg()
    archivos, err = await asyncio.to_thread(pl.listar_entrada, cfg)
    if archivos is None:
        return JSONResponse({"ok": False, "error": err, "archivos": []}, status_code=502)
    con = db.inicializar(cfg["db"])
    encolados = {f["nombre"] for f in db.cola(con) if f["estado"] in db.ESTADOS_ACTIVOS}
    for a in archivos:
        a["encolado"] = a.get("Name") in encolados
        a["Hashes"] = a.get("Hashes") or {}
    return {"ok": True, "modo": pc.modo(cfg), "remoto": _entrada_descripcion(cfg)["ruta"],
            "archivos": archivos, "total": len(archivos)}


@app.post("/api/subir")
async def api_subir(request: Request, nombre: str = "", encolar: bool = True):
    """Sube PDFs a la ENTRADA (local en modo local; Drive en modo drive).
    Acepta multipart/form-data (campo 'archivos' repetible) o cuerpo crudo
    (application/octet-stream) con ?nombre=archivo.pdf"""
    cfg = _cfg()
    os.makedirs(pc.TMP_DIR, exist_ok=True)
    os.makedirs(pc.entrada_local(cfg), exist_ok=True)
    subidos, errores, pendientes = [], [], []

    ctype = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in ctype:
            form = await request.form()
            for _k, v in form.multi_items():
                fname = getattr(v, "filename", None)
                if not fname:
                    continue
                tmp = os.path.join(pc.TMP_DIR, db.limpiar_nombre(fname))
                with open(tmp, "wb") as out:
                    shutil.copyfileobj(v.file, out)
                pendientes.append((os.path.basename(fname), tmp))
        else:
            if not nombre:
                return JSONResponse({"ok": False, "error": "falta ?nombre=archivo.pdf"},
                                    status_code=400)
            tmp = os.path.join(pc.TMP_DIR, db.limpiar_nombre(nombre))
            limite = MAX_SUBIDA_MB * 1024 * 1024
            total = 0
            with open(tmp, "wb") as out:
                async for trozo in request.stream():
                    total += len(trozo)
                    if total > limite:
                        out.close()
                        os.remove(tmp)
                        return JSONResponse(
                            {"ok": False, "error": f"archivo mayor a {MAX_SUBIDA_MB} MB"},
                            status_code=413)
                    out.write(trozo)
            pendientes.append((os.path.basename(nombre), tmp))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"no se pudo recibir: {e}"},
                            status_code=400)

    con = db.inicializar(cfg["db"])
    for fname, tmp in pendientes:
        if not fname.lower().endswith(".pdf"):
            errores.append({"nombre": fname, "error": "no es .pdf"})
            os.remove(tmp)
            continue
        with open(tmp, "rb") as f:
            if f.read(4) != b"%PDF":
                errores.append({"nombre": fname, "error": "cabecera %PDF inválida"})
                os.remove(tmp)
                continue
        if pc.modo(cfg) == "local":
            # Destino: entrada_local/ (nunca sobreescribe: agrega _2, _3…)
            destino_nombre = pl.nombre_libre(pc.entrada_local(cfg), fname)
            destino = pl.ruta_entrada(cfg, destino_nombre)
            if not destino:
                errores.append({"nombre": fname, "error": "nombre inválido"})
                os.remove(tmp)
                continue
            shutil.move(tmp, destino)
            subidos.append(destino_nombre)
            db.evento(con, tipo="log", nivel="ok",
                      detalle=f"subido a entrada_local/{destino_nombre}")
        else:
            code, out = await asyncio.to_thread(pc.rclone, cfg, "copyto", tmp,
                                                pc.remoto(cfg, fname))
            if code == 0:
                subidos.append(fname)
                db.evento(con, tipo="log", nivel="ok",
                          detalle=f"subido a {pc.remoto(cfg, fname)}")
            else:
                errores.append({"nombre": fname, "error": out[:300]})
            try:
                os.remove(tmp)
            except OSError:
                pass

    encolados = []
    if encolar and subidos:
        for n in subidos:
            ruta = pl.ruta_entrada(cfg, n)
            cid, creado = db.encolar(con, n, remoto=(ruta or ""),
                                     tam=(os.path.getsize(ruta) if ruta and os.path.exists(ruta) else None),
                                     md5=_md5(ruta) if ruta and pc.modo(cfg) == "local" else None,
                                     dry=int(cfg["dry_default"]),
                                     sin_correo=0 if cfg["correo_activo"] else 1,
                                     mensaje="Subido desde el panel")
            if creado:
                encolados.append(n)
    return {"ok": not errores, "subidos": subidos, "errores": errores,
            "encolados": encolados, "entrada": _entrada_descripcion(cfg)["ruta"]}


@app.post("/api/borrar")
async def api_borrar(request: Request):
    """Borra un archivo de la entrada local (o de procesados/)."""
    cfg = _cfg()
    datos = await _cuerpo(request)
    nombre = datos.get("nombre")
    que = (datos.get("que") or "entrada").lower()
    if not nombre:
        return JSONResponse({"ok": False, "error": "falta 'nombre'"}, status_code=400)
    if pc.modo(cfg) != "local":
        return JSONResponse({"ok": False, "error": "borrado solo disponible en modo local"},
                            status_code=400)
    base = pc.procesados_local(cfg) if que == "procesados" else pc.entrada_local(cfg)
    ruta = os.path.abspath(os.path.join(base, os.path.basename(str(nombre))))
    if not ruta.startswith(os.path.abspath(base) + os.sep) or not os.path.isfile(ruta):
        return JSONResponse({"ok": False, "error": "no existe"}, status_code=404)
    try:
        tam = os.path.getsize(ruta)
        os.remove(ruta)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    con = db.inicializar(cfg["db"])
    db.evento(con, tipo="log", nivel="aviso",
              detalle=f"borrado {que}/{os.path.basename(str(nombre))} ({tam} B)")
    return {"ok": True, "borrado": os.path.basename(str(nombre)), "que": que, "bytes": tam}


# ------------------------------------------------------------------ encolar / disparar
@app.post("/api/encolar")
async def api_encolar(request: Request):
    cfg = _cfg()
    datos = await _cuerpo(request)
    nombres = [str(n) for n in (datos.get("nombres") or []) if str(n).strip()]
    iniciar = bool(datos.get("iniciar", True))
    dry = datos.get("dry")
    prioridad = int(datos.get("prioridad") or 100)

    archivos, err = await asyncio.to_thread(pl.listar_entrada, cfg)
    meta = {a["Name"]: a for a in (archivos or [])}
    con = db.inicializar(cfg["db"])
    dry_ef = int(cfg["dry_default"] if dry is None else bool(dry))
    sin_correo = 0 if cfg["correo_activo"] else 1
    nuevos, repetidos, desconocidos = [], [], []
    for n in nombres:
        if archivos is not None and n not in meta:
            desconocidos.append(n)
            continue
        a = meta.get(n, {})
        ruta = pl.ruta_entrada(cfg, n) if pc.modo(cfg) == "local" else None
        cid, creado = db.encolar(con, n, remoto=(ruta or pc.remoto(cfg, n)),
                                 tam=a.get("Size"),
                                 md5=(_md5(ruta) if ruta else
                                      (a.get("Hashes") or {}).get("md5")),
                                 prioridad=prioridad, dry=dry_ef,
                                 sin_correo=sin_correo,
                                 mensaje="Encolado desde el panel")
        db.evento(con, cola_id=cid, tipo="estado", nivel="info",
                  detalle=f"{'encolado' if creado else 'ya estaba en cola'}: {n}")
        (nuevos if creado else repetidos).append(n)
    pid = 0
    if iniciar and nuevos:
        pid, _ = asegurar_worker(cfg)
    return {"ok": True, "encolados": nuevos, "repetidos": repetidos,
            "desconocidos": desconocidos, "worker_pid": pid, "dry": dry_ef,
            "sin_correo": sin_correo}


@app.post("/api/disparar")
async def api_disparar(request: Request):
    """Arranca el worker YA (equivale a 'no esperar los 7 min del vigilante')."""
    cfg = _cfg()
    datos = await _cuerpo(request)
    con = db.inicializar(cfg["db"])
    pendientes = [f for f in db.cola(con) if f["estado"] == "pendiente"]
    if not pendientes:
        archivos, _ = await asyncio.to_thread(pl.listar_entrada, cfg)
        dry = int(cfg["dry_default"] if datos.get("dry") is None else bool(datos.get("dry")))
        sin_correo = 0 if cfg["correo_activo"] else 1
        for a in (archivos or []):
            ruta = pl.ruta_entrada(cfg, a["Name"]) if pc.modo(cfg) == "local" else None
            db.encolar(con, a["Name"], remoto=(ruta or pc.remoto(cfg, a["Name"])),
                       tam=a.get("Size"), md5=_md5(ruta) if ruta else None,
                       dry=dry, sin_correo=sin_correo,
                       mensaje="Auto-encolado al disparar")
    db.control_set(con, "pausa_global", "0")
    pid, arrancado = await asyncio.to_thread(asegurar_worker, cfg)
    db.evento(con, tipo="estado", nivel="info",
              detalle=f"disparo manual (modo {pc.modo(cfg)}): worker pid={pid} "
                      f"({'arrancado ahora' if arrancado else 'ya estaba vivo'}), "
                      f"{len(pendientes)} job(s) pendientes")
    return {"ok": True, "worker_pid": pid, "arrancado": arrancado,
            "pendientes": len(pendientes)}


# ------------------------------------------------------------------ resultados
@app.get("/api/procesados")
async def api_procesados():
    """PDFs que el pipeline ya movió a entrada_local/procesados/."""
    cfg = _cfg()
    archivos = await asyncio.to_thread(pl.listar_procesados, cfg)
    return {"ok": True, "archivos": archivos, "total": len(archivos),
            "ruta": pc.procesados_local(cfg), "bytes": sum(a["Size"] for a in archivos)}


@app.get("/api/hoja")
async def api_hoja(pdf: str = "", pag: int = 0, job_id: str = ""):
    """Lectura OCR guardada de una hoja concreta (texto + cajas + ms + worker).

    Alimenta el visor maximizado cuando la hoja ya no está en memoria (o cuando
    el navegador acaba de abrir el panel).
    """
    if not pdf or not pag:
        return JSONResponse({"ok": False, "error": "faltan pdf y pag"}, status_code=400)
    cfg = _cfg()
    con = db.inicializar(cfg["db"])
    f = db.hoja_ocr(con, pdf, int(pag), job_id or None)
    if not f:
        return {"ok": True, "hoja": None}
    try:
        cajas = json.loads(f.get("cajas") or "[]")
    except (TypeError, ValueError):
        cajas = []
    return {"ok": True, "hoja": {
        "pdf": f["pdf"], "pag": f["pag"], "pag_total": f["pag_total"],
        "texto": f["texto"] or "", "sn": f["sn"] or "", "ms": f["ms_pagina"],
        "worker": (f["detalle"] or "").replace("worker ", "").strip(),
        "parcial": bool(f.get("parcial")), "cajas": cajas,
        "estado": f.get("estado_pag"), "sede": f.get("sede"), "marca": f.get("marca"),
        "job_id": f["job_id"], "ts": f["ts"],
    }}


@app.get("/api/resultados")
async def api_resultados():
    cfg = _cfg()
    corridas = await asyncio.to_thread(pl.listar_resultados, cfg)
    return {"ok": True, "corridas": corridas, "n": len(corridas),
            "ruta": pc.SALIDA_LOCAL}


@app.get("/api/descargar")
async def api_descargar(corrida: str, sede: str = "", carpeta: str = "", archivo: str = ""):
    """Descarga un archivo individual de los resultados (PDF o txt)."""
    cfg = _cfg()
    ruta = pl.ruta_resultado(cfg, corrida, sede, carpeta, archivo)
    if not ruta or not os.path.isfile(ruta):
        return JSONResponse({"ok": False, "error": "archivo no encontrado"}, status_code=404)
    tipo = "application/pdf" if ruta.lower().endswith(".pdf") else "text/plain; charset=utf-8"
    return FileResponse(ruta, media_type=tipo, filename=os.path.basename(ruta))


@app.get("/api/zip")
async def api_zip(corrida: str, sede: str = ""):
    """ZIP de una sede (EQUIPOS_OK + A_REVISAR + registro) o de la corrida completa.
    Sustituye al link de Drive: sin Drive, así se sacan los archivos del panel."""
    cfg = _cfg()
    os.makedirs(pc.TMP_DIR, exist_ok=True)
    etiqueta = f"{db.limpiar_nombre(sede) or 'corrida'}_{db.limpiar_nombre(corrida)}"
    destino = os.path.join(pc.TMP_DIR, f"{etiqueta}_{int(time.time())}.zip")
    ok, res = await asyncio.to_thread(pl.zip_resultados, cfg, corrida, sede, destino)
    if not ok:
        return JSONResponse({"ok": False, "error": res}, status_code=404)
    return FileResponse(destino, media_type="application/zip",
                        filename=f"{'Sede_' + sede if sede else corrida}.zip",
                        background=BackgroundTask(lambda p=destino: os.path.exists(p) and os.remove(p)))


@app.get("/api/espacio")
async def api_espacio():
    cfg = _cfg()
    return {"ok": True, **(await asyncio.to_thread(pl.espacio, cfg))}


@app.post("/api/limpiar")
async def api_limpiar(request: Request):
    """Borra corridas de resultados viejas (libera disco). Nunca borra la última."""
    cfg = _cfg()
    datos = await _cuerpo(request)
    que = (datos.get("que") or "corridas").lower()
    con = db.inicializar(cfg["db"])
    if pc.modo(cfg) != "local":
        return JSONResponse({"ok": False, "error": "limpieza solo disponible en modo local"},
                            status_code=400)
    if que == "todas":
        if not datos.get("confirmar"):
            return JSONResponse({"ok": False, "error": "hace falta confirmar:true"},
                                status_code=400)
        borradas, bytes_lib = await asyncio.to_thread(pl.borrar_todos_los_resultados, cfg)
    elif que == "corrida":
        if not datos.get("corrida"):
            return JSONResponse({"ok": False, "error": "falta 'corrida'"}, status_code=400)
        borradas, bytes_lib = await asyncio.to_thread(
            pl.limpiar_corridas, cfg, 0, datos["corrida"])
    else:
        borradas, bytes_lib = await asyncio.to_thread(
            pl.limpiar_corridas, cfg, int(datos.get("conservar", 2)))
    db.evento(con, tipo="estado", nivel="aviso",
              detalle=f"limpieza {que}: {len(borradas)} corrida(s), "
                      f"{bytes_lib / 1048576:.1f} MB liberados")
    return {"ok": True, "borradas": borradas, "bytes": bytes_lib,
            "espacio": await asyncio.to_thread(pl.espacio, cfg)}


# ------------------------------------------------------------------ miniaturas
@app.post("/api/ajustes")
async def api_ajustes(request: Request):
    """Cambia ajustes del panel que afectan al pipeline (lectura secuencial).

    Se guardan en el archivo de config para que el próximo job los use; el worker
    los lee al arrancar cada job (no hace falta reiniciar el servidor).
    """
    cfg = _cfg()
    datos = await _cuerpo(request)
    permitidos = {"ocr_secuencial": bool, "ocr_solo_sn": bool, "ocr_bandas": int}
    cambios = {}
    for clave, tipo in permitidos.items():
        if clave in datos:
            try:
                cambios[clave] = tipo(datos[clave])
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": f"valor inválido: {clave}"}, status_code=400)
    if "ocr_bandas" in cambios:
        cambios["ocr_bandas"] = max(2, min(12, cambios["ocr_bandas"]))
    ruta = cfg["_ruta"]
    actual = {k: v for k, v in cfg.items() if not k.startswith("_")}
    actual.update(cambios)
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(actual, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        return JSONResponse({"ok": False, "error": f"no pude guardar: {e}"}, status_code=500)
    con = db.inicializar(cfg["db"])
    db.evento(con, tipo="estado", nivel="info",
              detalle="ajustes: " + ", ".join(f"{k}={v}" for k, v in cambios.items()))
    return {"ok": True, "aplicado": cambios, "config": ruta,
            "nota": "el próximo job usará estos ajustes"}


@app.get("/api/ajustes")
async def api_ajustes_get():
    cfg = _cfg()
    return {"ok": True, "ocr_secuencial": bool(cfg.get("ocr_secuencial")),
            "ocr_solo_sn": bool(cfg.get("ocr_solo_sn")),
            "ocr_bandas": int(cfg.get("ocr_bandas") or 4)}


@app.get("/api/miniatura")
async def api_miniatura(pdf: str, pag: int = 1, ancho: int = 300):
    """PNG de UNA página del PDF en proceso: el visor "hoja en vivo" del panel.

    Busca el PDF en la entrada local y luego en procesados (en modo real el
    pipeline lo mueve al terminar). Cachea por (ruta, mtime, página, ancho).
    """
    cfg = _cfg()
    ruta = pl.ruta_pdf_proceso(cfg, pdf)
    if not ruta:
        return JSONResponse({"ok": False, "error": f"no encuentro el PDF: {pdf}"}, status_code=404)
    ancho = max(80, min(1200, int(ancho)))
    pag = max(1, int(pag))
    try:
        clave = f"{ruta}|{os.path.getmtime(ruta)}|{pag}|{ancho}"
    except OSError:
        return JSONResponse({"ok": False, "error": "PDF ilegible"}, status_code=404)
    nombre = hashlib.sha1(clave.encode()).hexdigest()[:20] + ".png"
    destino = os.path.join(CACHE_MINIATURAS, nombre)
    if not os.path.isfile(destino):
        os.makedirs(CACHE_MINIATURAS, exist_ok=True)
        tmp = destino[:-4] + ".parcial.png"   # el sufijo debe seguir siendo .png:
        # PyMuPDF deduce el formato de la extensión y falla con ".png.parcial".
        code, out, err = await asyncio.to_thread(
            _correr_py, MINIATURA_PY, [ruta, pag, ancho, tmp], 120)
        if code != 0 or not os.path.isfile(tmp):
            return JSONResponse({"ok": False, "error": (err or "no se pudo renderizar")[:300]},
                                status_code=500)
        os.replace(tmp, destino)
    return FileResponse(destino, media_type="image/png")


# ------------------------------------------------------------------ seriales (inventario)
@app.get("/api/seriales")
async def api_seriales(q: str = "", sede: str = "", marca: str = "", estatus: str = "",
                       zona: str = "", limite: int = 100, offset: int = 0, orden: str = "sn"):
    """Lista filtrable de seriales (tabla `equipos`) + valores para los filtros."""
    cfg = _cfg()
    con = db.inicializar(cfg["db"])
    filtros = {"q": q or None, "sede": sede or None, "marca": marca or None,
               "estatus": estatus or None, "zona": zona or None}
    total = db.contar_equipos(con, **filtros)
    equipos = db.listar_equipos(con, limite=max(1, min(500, limite)), offset=max(0, offset),
                                orden=orden, **filtros)
    xlsx = os.path.join(pc.inventarios_local(cfg), INVENTARIO_PANEL)
    return {"ok": True, "total": total, "equipos": equipos,
            "todos": db.contar_equipos(con),
            "filtros": {"sedes": db.valores_equipos(con, "sede"),
                        "marcas": db.valores_equipos(con, "marca"),
                        "estatus": db.valores_equipos(con, "estatus")},
            "exportado": ({"ruta": xlsx,
                           "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(xlsx)))}
                          if os.path.isfile(xlsx) else None)}


@app.post("/api/seriales")
async def api_serial_guardar(request: Request):
    """Añade o edita un serial (por SN) y reexporta el Excel del pipeline."""
    cfg = _cfg()
    datos = await _cuerpo(request)
    sn = (datos.get("sn") or "").strip()
    if not sn:
        return JSONResponse({"ok": False, "error": "falta el serial"}, status_code=400)
    con = db.inicializar(cfg["db"])
    permitidos = {c: datos.get(c, "") for c in db.CAMPOS_EQUIPO if c in datos}
    creado, sn = await asyncio.to_thread(db.upsert_equipo, con, sn, **permitidos)
    db.evento(con, tipo="log", nivel="ok",
              detalle=f"inventario: {'alta' if creado else 'edición'} de {sn}")
    ok, destino, salida = await asyncio.to_thread(_exportar_inventario, cfg)
    return {"ok": True, "sn": sn, "creado": creado, "total": db.contar_equipos(con),
            "exportado": ok, "destino": os.path.basename(destino), "detalle": salida[:200]}


@app.post("/api/seriales/borrar")
async def api_serial_borrar(request: Request):
    cfg = _cfg()
    datos = await _cuerpo(request)
    sn = (datos.get("sn") or "").strip().upper()
    if not sn:
        return JSONResponse({"ok": False, "error": "falta el serial"}, status_code=400)
    con = db.inicializar(cfg["db"])
    borrado = await asyncio.to_thread(db.borrar_equipo, con, sn)
    if borrado:
        db.evento(con, tipo="log", nivel="aviso", detalle=f"inventario: baja de {sn}")
        ok, _, _ = await asyncio.to_thread(_exportar_inventario, cfg)
    else:
        ok = False
    return {"ok": borrado, "sn": sn, "total": db.contar_equipos(con), "exportado": ok,
            "error": None if borrado else "ese serial no existe"}


@app.get("/api/inventario")
async def api_inventario_estado():
    cfg = _cfg()
    con = db.inicializar(cfg["db"])
    xlsx = os.path.join(pc.inventarios_local(cfg), INVENTARIO_PANEL)
    fuentes = []
    for f in (pl.inventarios(cfg) or []):
        fuentes.append(dict(f, es_panel=(f["Name"] == INVENTARIO_PANEL)))
    return {"ok": True, "total": db.contar_equipos(con), "fuentes": fuentes,
            "panel_xlsx": (os.path.basename(xlsx) if os.path.isfile(xlsx) else None),
            "sedes": db.valores_equipos(con, "sede"),
            "marcas": db.valores_equipos(con, "marca"),
            "estatus": db.valores_equipos(con, "estatus")}


@app.post("/api/inventario/importar")
async def api_inventario_importar():
    """Carga los Excel locales en la tabla `equipos` (importador)."""
    cfg = _cfg()
    rutas = [os.path.join(pc.inventarios_local(cfg), f["Name"])
             for f in (pl.inventarios(cfg) or [])
             if f["Name"] != INVENTARIO_PANEL]
    if not rutas:
        return JSONResponse({"ok": False, "error": "no hay Excel de origen en inventarios_local/"},
                            status_code=400)
    code, out, err = await asyncio.to_thread(
        _correr_py, INVENTARIO_PY, ["importar", cfg["db"]] + rutas, 600)
    if code != 0:
        return JSONResponse({"ok": False, "error": (err or out)[:300]}, status_code=500)
    con = db.inicializar(cfg["db"])
    ok, destino, salida = await asyncio.to_thread(_exportar_inventario, cfg)
    db.evento(con, tipo="log", nivel="ok",
              detalle=f"inventario importado desde {len(rutas)} Excel → {db.contar_equipos(con)} seriales")
    return {"ok": True, "importados": out[-120:], "total": db.contar_equipos(con),
            "exportado": ok, "destino": os.path.basename(destino)}


@app.post("/api/inventario/exportar")
async def api_inventario_exportar():
    """Regenera el Excel que el pipeline lee (inventario_panel.xlsx)."""
    cfg = _cfg()
    ok, destino, salida = await asyncio.to_thread(_exportar_inventario, cfg)
    con = db.inicializar(cfg["db"])
    return {"ok": ok, "destino": destino, "filas": db.contar_equipos(con),
            "detalle": salida[:200]}


# ------------------------------------------------------------------ control
@app.post("/api/pausar")
async def api_pausar(request: Request):
    cfg = _cfg()
    datos = await _cuerpo(request)
    con = db.inicializar(cfg["db"])
    cid = datos.get("id")
    if cid:
        db.marcar(con, int(cid), "pausado", mensaje="Pausado desde el panel")
        db.evento(con, cola_id=int(cid), tipo="estado", nivel="aviso", detalle="job pausado")
    else:
        db.control_set(con, "pausa_global", "1")
        db.evento(con, tipo="estado", nivel="aviso", detalle="pausa global activada")
    return {"ok": True, "pausa_global": db.pausa_global(con)}


@app.post("/api/reanudar")
async def api_reanudar(request: Request):
    cfg = _cfg()
    datos = await _cuerpo(request)
    con = db.inicializar(cfg["db"])
    cid = datos.get("id")
    if cid:
        db.marcar(con, int(cid), "pendiente", mensaje="Reanudado desde el panel")
        db.evento(con, cola_id=int(cid), tipo="estado", nivel="info", detalle="job reanudado")
    else:
        db.control_set(con, "pausa_global", "0")
        db.evento(con, tipo="estado", nivel="info", detalle="pausa global liberada")
    if datos.get("iniciar", True):
        await asyncio.to_thread(asegurar_worker, cfg)
    return {"ok": True, "pausa_global": db.pausa_global(con)}


@app.post("/api/reintentar")
async def api_reintentar(request: Request):
    cfg = _cfg()
    datos = await _cuerpo(request)
    cid = datos.get("id")
    if not cid:
        return JSONResponse({"ok": False, "error": "falta id"}, status_code=400)
    con = db.inicializar(cfg["db"])
    j = db.job(con, int(cid))
    if not j:
        return JSONResponse({"ok": False, "error": "job inexistente"}, status_code=404)
    db.marcar(con, int(cid), "pendiente", fase="", terminado_en=None, error="",
              mensaje=f"Reintento {j['intentos'] + 1} desde el panel")
    db.evento(con, cola_id=int(cid), tipo="estado", nivel="info",
              detalle=f"reintento del job #{cid}")
    pid, _ = await asyncio.to_thread(asegurar_worker, cfg)
    return {"ok": True, "worker_pid": pid}


@app.post("/api/cancelar")
async def api_cancelar(request: Request):
    cfg = _cfg()
    datos = await _cuerpo(request)
    cid = datos.get("id")
    if not cid:
        return JSONResponse({"ok": False, "error": "falta id"}, status_code=400)
    con = db.inicializar(cfg["db"])
    db.marcar(con, int(cid), "cancelado", fase="fin", terminado_en=db.ahora(),
              mensaje="Cancelado desde el panel")
    db.evento(con, cola_id=int(cid), tipo="estado", nivel="aviso",
              detalle=f"job #{cid} cancelado")
    return {"ok": True}


# ------------------------------------------------------------------ SSE
def _leer_novedades(cfg, ultimo, job_id):
    """Una sola conexión por ciclo: eventos nuevos + estado de la cola/worker."""
    con = db.inicializar(cfg["db"])
    nuevos = db.eventos_desde(con, ultimo, job_id=job_id or None)
    actual = db.job_en_proceso(con)
    visible = actual or db.job_ultimo(con)
    est = {"cola": db.cola(con), "worker": estado_worker(con), "actual": actual,
           "ultimo_job": visible if not actual else None,
           "resumen": (db.resumen_job(con, visible["job_id"])
                       if visible and visible.get("job_id") else {})}
    return nuevos, est


@app.get("/api/progreso")
async def api_progreso(request: Request, desde: int = 0, job_id: str = ""):
    """SSE. Al conectar reenvía SIEMPRE el estado actual (snapshot), así la UI no
    depende de lo que tuviera en memoria el navegador."""
    cfg = _cfg()

    async def generador():
        snap = await asyncio.to_thread(snapshot, cfg)
        yield sse("snapshot", snap)
        ultimo = int(desde or 0)
        if not ultimo:
            ultimo = snap["eventos"][-1]["id"] if snap["eventos"] else 0
        t_estado = 0.0
        while True:
            if await request.is_disconnected():
                break
            nuevos, est = await asyncio.to_thread(_leer_novedades, cfg, ultimo, job_id)
            for ev in nuevos:
                ultimo = max(ultimo, ev["id"])
                yield sse("evento", ev)
            if time.time() - t_estado > 2:
                t_estado = time.time()
                yield sse("estado", est)
                yield ": ping\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generador(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Panel web del separador de PDFs")
    ap.add_argument("--host", default=None)
    ap.add_argument("--puerto", type=int, default=None)
    args = ap.parse_args()
    cfg = pc.cargar_config()
    host = args.host or cfg["host"]
    puerto = args.puerto or int(cfg["puerto"])
    db.inicializar(cfg["db"])
    print(f"Panel separador PDFs → http://{host}:{puerto}")
    print(f"  config : {cfg['_ruta']}")
    print(f"  modo   : {pc.modo(cfg)}"
          + ("  (sin rclone/Drive)" if pc.modo(cfg) == "local" else "  (Drive activo)"))
    print(f"  db     : {cfg['db']}")
    print(f"  entrada: {_entrada_descripcion(cfg)['ruta']}")
    print(f"  resultados: {pc.SALIDA_LOCAL}")
    print(f"  dry por defecto: {cfg['dry_default']} · correo: {cfg['correo_activo']}")
    import uvicorn
    uvicorn.run(app, host=host, port=puerto, log_level="info")


if __name__ == "__main__":
    main()
