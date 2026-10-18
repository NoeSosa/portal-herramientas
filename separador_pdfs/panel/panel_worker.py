#!/home/node/.openclaw/workspace/venv/bin/python
"""
panel_worker.py — Worker del panel del separador de PDFs (proceso independiente).
================================================================================
Toma jobs de la cola SQLite y ejecuta el pipeline REAL como subproceso:

    venv-pdfseparador/bin/python separador_auto.py --job-id <id> [--dry]
    (con LD_LIBRARY_PATH=$HOME/.local/lib para libgomp/PaddleOCR)

Propiedades:
  · UN job a la vez, respetando el lock `auto/run.lock` del pipeline (si hay una
    corrida en curso —p. ej. el vigilante—, espera en vez de chocar).
  · El navegador no participa: cerrar la pestaña no afecta al worker.
  · Latido cada `latido_s`: actualiza `cola.latido_en` y traduce el jsonl de
    progreso del pipeline a `eventos` (de ahí sale el SSE del servidor).
  · Reanudable: al arrancar, los jobs 'en_proceso' SIN latido por > `stale_s`
    vuelven a 'pendiente' (worker muerto a mitad de camino).
  · Se apaga solo tras `worker_idle_exit_s` sin jobs (el panel/la UI lo relanza).

Uso:
  venv/bin/python separador_pdfs/panel/panel_worker.py             # daemon
  venv/bin/python separador_pdfs/panel/panel_worker.py --una-vez   # drena y sale
  PANEL_CONFIG=.../config.test.json … panel_worker.py --una-vez    # pruebas

✅ VALIDADO: 16/oct/2026 (Ches) — ejecuta un job --dry end-to-end sobre un
   sandbox rclone local: cola pendiente→en_proceso→ok con eventos de página.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_config as pc      # noqa: E402
import panel_db as db          # noqa: E402

_PARAR = False
_LOG = None


def log(*a):
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [worker:{os.getpid()}] " + " ".join(str(x) for x in a)
    print(linea, flush=True)
    global _LOG
    try:
        if _LOG is None:
            _LOG = open(os.path.join(pc.LOGS_DIR, "worker.log"), "a", encoding="utf-8")
        _LOG.write(linea + "\n")
        _LOG.flush()
    except Exception:
        pass


def _sigterm(signum, frame):
    global _PARAR
    if _PARAR:          # segundo SIGTERM: cortar el pipeline en curso
        log("segundo SIGTERM: saliendo de inmediato")
        sys.exit(130)
    _PARAR = True
    log("SIGTERM: terminaré el job en curso y saldré")


def pid_vivo(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"panel_worker.py" in f.read()
    except OSError:
        return True


def worker_activo(cfg):
    """PID del worker vivo, o 0 si no hay ninguno (evita duplicar workers)."""
    pids = [leer_pidfile()]
    try:
        con = db.inicializar(cfg["db"])
        pids.append(db.control_get(con, "worker_pid"))
    except Exception:
        pass
    for pid in pids:
        if pid_vivo(pid):
            return int(pid)
    return 0


_PID_FILE = os.path.join(pc.LOGS_DIR, "worker.pid")


def leer_pidfile():
    try:
        with open(_PID_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


# ---------------------------------------------------------------- log del job
# El OCR imprime una línea por página:
#     [worker 0-2] pág 3/5 · 1834ms · sn~CNB1NDR0XQ
# El panel las convierte en eventos `ocr_pagina` en vivo (progreso real hoja a
# hoja, con el worker que la hizo y el serial candidato del OCR).
RE_OCR_PAGINA = re.compile(
    r"\[worker\s+(\d+)-(\d+)\]\s+p[áa]g\s+(\d+)/(\d+)"
    r"(?:\s*·\s*(\d+)ms)?(?:\s*·\s*sn~([A-Z0-9]+))?")
RE_PROCESANDO = re.compile(r"\[3\]\s+Procesando\s+(.+?)\s+\(")


def ingerir_ocr_jsonl(con, job_id, cola_id, offset, estado):
    """Lecturas de cada hoja publicadas por los workers OCR (texto en vivo).

    Archivo: auto/progress/<job_id>.ocr.jsonl (una línea por hoja, escrita por
    separador_pdf.publicar_lectura). Devuelve el nuevo offset.

    Esta es la fuente de verdad de las hojas: trae el TEXTO que leyó el OCR y las
    CAJAS de cada renglón. Si el archivo se recrea (o encoge), el offset vuelve a
    cero para no perderse lecturas.
    """
    ruta = db.ruta_progreso(pc.PROGRESS_DIR, job_id)[:-6] + ".ocr.jsonl"
    try:
        tam = os.path.getsize(ruta)
    except OSError:
        return offset
    if tam < (offset or 0):
        offset = 0          # el archivo se recreó: releer desde el principio
    nuevo, eventos = db.leer_jsonl_nuevas(ruta, offset)
    if not eventos:
        return nuevo
    procesadas = estado.setdefault("paginas_ocr", {})
    bandas_vistas = estado.setdefault("bandas_ocr", set())
    for ev in eventos:
        try:
            pag = int(ev.get("pag") or 0)
        except (TypeError, ValueError):
            continue
        if not pag:
            continue
        tipo = ev.get("tipo") or "ocr_pagina"
        cajas_json = json.dumps(ev.get("cajas") or [], ensure_ascii=False, separators=(",", ":"))[:20000]
        if tipo == "ocr_banda":
            clave_b = (ev.get("pdf"), pag, int(ev.get("banda") or 0))
            if clave_b in bandas_vistas:
                continue
            bandas_vistas.add(clave_b)
            con.execute(
                "INSERT INTO eventos(ts, job_id, cola_id, tipo, nivel, pdf, pag, pag_total,"
                " sn, ms_pagina, texto, detalle, banda, bandas, y0, y1, parcial, cajas,"
                " pid_worker, slot_worker)"
                " VALUES(?,?,?,'ocr_banda','info',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [ev.get("ts") or time.time(), job_id, cola_id, ev.get("pdf"), pag,
                 ev.get("pag_total"), ev.get("sn") or "", ev.get("ms"),
                 (ev.get("texto") or "")[:900], ev.get("worker") or "",
                 ev.get("banda"), ev.get("bandas"), ev.get("y0"), ev.get("y1"),
                 1 if ev.get("parcial") else 0, cajas_json, ev.get("pid"), ev.get("slot")])
            continue
        clave = (ev.get("pdf"), pag)
        if clave in procesadas:
            # La hoja ya estaba (normalmente anunciada desde el log, sin texto):
            # se completa la fila con la lectura real para no perderla.
            con.execute(
                "UPDATE eventos SET texto=?, cajas=?, ms_pagina=COALESCE(?, ms_pagina),"
                " sn=CASE WHEN COALESCE(sn,'')='' THEN ? ELSE sn END,"
                " pid_worker=COALESCE(pid_worker, ?), slot_worker=COALESCE(slot_worker, ?)"
                " WHERE job_id=? AND tipo='ocr_pagina' AND pdf=? AND pag=?"
                " AND (texto IS NULL OR texto='')",
                [(ev.get("texto") or "")[:900], cajas_json, ev.get("ms"), ev.get("sn") or "",
                 ev.get("pid"), ev.get("slot"), job_id, ev.get("pdf"), pag])
            continue
        procesadas[clave] = True
        con.execute(
            "INSERT INTO eventos(ts, job_id, cola_id, tipo, nivel, pdf, pag, pag_total,"
            " sn, ms_pagina, texto, detalle, parcial, cajas, pid_worker, slot_worker)"
            " VALUES(?,?,?,'ocr_pagina','info',?,?,?,?,?,?,?,?,?,?,?)",
            [ev.get("ts") or time.time(), job_id, cola_id, ev.get("pdf"), pag,
             ev.get("pag_total"), ev.get("sn") or "", ev.get("ms"),
             (ev.get("texto") or "")[:900], ev.get("worker") or "",
             1 if ev.get("parcial") else 0, cajas_json, ev.get("pid"), ev.get("slot")])
    con.commit()
    return nuevo


def _hay_ocr_jsonl(job_id):
    """¿El pipeline publica las lecturas con texto? (archivo con datos)."""
    try:
        return os.path.getsize(db.ruta_progreso(pc.PROGRESS_DIR, job_id)[:-6] + ".ocr.jsonl") > 0
    except OSError:
        return False


def ingerir_log(con, job_id, cola_id, ruta, offset, estado=None, usar_jsonl=False):
    """Copia las líneas NUEVAS del log del pipeline a `eventos` (log en vivo).

    Las líneas de progreso del OCR se convierten en eventos `ocr_pagina` (para el
    avance hoja a hoja) y NO se duplican como log genérico. `estado` es un dict
    mutable del job donde se recuerda el PDF en curso y las páginas ya vistas.
    """
    if not os.path.isfile(ruta):
        return offset
    if estado is None:
        estado = {}
    procesadas = estado.setdefault("paginas_ocr", {})
    nuevos = 0
    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        while nuevos < 250:
            linea = f.readline()
            if not linea:
                break
            offset = f.tell()
            linea = linea.rstrip()
            if not linea.strip():
                continue
            nuevos += 1
            m_proc = RE_PROCESANDO.search(linea)
            if m_proc:
                estado["pdf"] = m_proc.group(1).strip()
            m = RE_OCR_PAGINA.search(linea)
            if m:
                ini, fin, pag, fin_rango, ms, sn = m.groups()
                if usar_jsonl:
                    continue          # las hojas las registra el JSONL (con texto)
                clave = (estado.get("pdf"), int(pag))
                if clave in procesadas:
                    continue          # ya avisado (reintento de re-OCR)
                procesadas[clave] = True
                con.execute(
                    "INSERT INTO eventos(ts, job_id, cola_id, tipo, nivel, pdf, pag,"
                    " pag_total, sn, ms_pagina, detalle) VALUES(?,?,?,'ocr_pagina','info',?,?,?,?,?,?)",
                    [time.time(), job_id, cola_id, estado.get("pdf"), int(pag),
                     int(fin_rango), sn or "", (float(ms) if ms else None),
                     f"worker {ini}-{fin}"])
                con.commit()
                continue
            con.execute(
                "INSERT INTO eventos(ts, job_id, cola_id, tipo, nivel, detalle)"
                " VALUES(?,?,?,'log',?,?)",
                [time.time(), job_id, cola_id,
                 "error" if "[ERROR]" in linea else "info", linea[:500]])
            con.commit()
    return offset


# ---------------------------------------------------------------- ejecución
def ejecutar_job(con, cfg, job):
    cola_id = job["id"]
    # Un job_id POR INTENTO: cada corrida escribe su propio jsonl y sus eventos,
    # así los contadores de un reintento no se suman a los del intento anterior.
    job_id = db.nuevo_job_id(cola_id)
    ruta_jsonl = db.ruta_progreso(pc.PROGRESS_DIR, job_id)
    ruta_log = os.path.join(pc.LOGS_DIR, f"job_{cola_id}.log")

    db.marcar(con, cola_id, "en_proceso", job_id=job_id, fase="preparando",
              intentos=(job["intentos"] or 0) + 1, iniciado_en=db.ahora(),
              latido_en=db.ahora(), error="", pag_hechas=0,
              mensaje=f"Job {job_id} iniciado")
    db.evento(con, job_id=job_id, cola_id=cola_id, tipo="estado", nivel="info",
              detalle=f"en_proceso · modo={pc.modo(cfg)} · "
                      f"{'--dry' if job['dry'] else 'REAL'}"
                      f"{' · --sin-correo' if job.get('sin_correo', 1) else ''} · "
                      f"intento {((job['intentos'] or 0) + 1)}")
    log(f"job #{cola_id} ({job['nombre']}) → {job_id} "
        f"[{'dry' if job['dry'] else 'REAL'}] modo={pc.modo(cfg)}"
        f"{' sin-correo' if job.get('sin_correo', 1) else ''}")

    for _r in (ruta_jsonl, ruta_jsonl[:-6] + ".ocr.jsonl"):
        if os.path.exists(_r):
            os.remove(_r)       # progreso limpio por corrida (incluye lecturas OCR)

    cmd = [pc.PY_VENV_PDF, pc.SEPARADOR_AUTO, "--modo", pc.modo(cfg),
           "--job-id", job_id]
    if job["dry"]:
        cmd.append("--dry")
    if job.get("sin_correo", 1):
        cmd.append("--sin-correo")
    env = pc.entorno_rclone(cfg)
    env["PYTHONUNBUFFERED"] = "1"

    offset_jsonl, offset_log, offset_ocr = 0, 0, 0
    estado_log = {}
    try:
        with open(ruta_log, "a", encoding="utf-8") as lf:
            lf.write(f"\n===== job #{cola_id} {job_id} {db.ahora()} "
                     f"{'--dry' if job['dry'] else 'REAL'} =====\n")
            lf.flush()
            offset_log = os.path.getsize(ruta_log)
            proc = subprocess.Popen(cmd, cwd=pc.SEP_DIR, env=env, stdout=lf,
                                    stderr=subprocess.STDOUT)
            db.marcar(con, cola_id, pid=proc.pid)
            while proc.poll() is None:
                offset_jsonl, _ = db.ingerir_progreso(con, pc.PROGRESS_DIR, job_id,
                                                      cola_id, offset_jsonl)
                offset_ocr = ingerir_ocr_jsonl(con, job_id, cola_id, offset_ocr, estado_log)
                offset_log = ingerir_log(con, job_id, cola_id, ruta_log, offset_log, estado_log,
                                         usar_jsonl=_hay_ocr_jsonl(job_id))
                db.latido(con, cola_id, proc.pid)
                # El avance del OCR se refleja ya en la cola (progreso real, no al final)
                n_ocr = con.execute(
                    "SELECT COUNT(*) FROM eventos WHERE cola_id=? AND tipo='ocr_pagina'",
                    (cola_id,)).fetchone()[0]
                if n_ocr:
                    con.execute(
                        "UPDATE cola SET pag_hechas=MAX(pag_hechas, ?), fase='ocr', latido_en=?"
                        " WHERE id=?", (n_ocr, db.ahora(), cola_id))
                    con.commit()
                if _PARAR:
                    break
                time.sleep(max(1, int(cfg["latido_s"])))
            rc = proc.wait()
    except Exception as e:
        rc = 99
        db.evento(con, job_id=job_id, cola_id=cola_id, tipo="error", nivel="error",
                  detalle=f"no se pudo lanzar el pipeline: {e}")
        log(f"ERROR lanzando pipeline: {e}")

    offset_jsonl, n_ev = db.ingerir_progreso(con, pc.PROGRESS_DIR, job_id, cola_id,
                                             offset_jsonl)
    offset_ocr = ingerir_ocr_jsonl(con, job_id, cola_id, offset_ocr, estado_log)
    offset_log = ingerir_log(con, job_id, cola_id, ruta_log, offset_log, estado_log,
                             usar_jsonl=_hay_ocr_jsonl(job_id))

    # ¿El pipeline se salió porque había otra corrida (lock)?
    cola_log = ""
    try:
        with open(ruta_log, "r", encoding="utf-8", errors="replace") as f:
            cola_log = f.read()[-4000:]
    except OSError:
        pass

    if "Corrida previa en curso" in cola_log:
        db.marcar(con, cola_id, "pendiente", fase="",
                  mensaje="Otra corrida tenía el lock; se reintenta")
        db.evento(con, job_id=job_id, cola_id=cola_id, tipo="aviso", nivel="aviso",
                  detalle="lock ocupado (otra corrida del pipeline); job de vuelta a pendiente")
        log(f"job #{cola_id}: lock ocupado → pendiente")
        return "lock"

    if rc == 0:
        res = db.resumen_job(con, job_id)
        n_pag = sum(res["por_estado"].values())
        msg = (f"completado ({n_pag} pág. procesadas)"
               if n_pag else "sin PDFs nuevos que procesar")
        db.marcar(con, cola_id, "ok", fase="fin", terminado_en=db.ahora(),
                  mensaje=msg)
        db.evento(con, job_id=job_id, cola_id=cola_id, tipo="estado", nivel="ok",
                  detalle=f"ok · {msg}")
        log(f"job #{cola_id}: ok ({msg})")
        if not job["dry"]:
            marcar_duplicados(con, cfg, job_id)
        return "ok"

    tail = "\n".join(cola_log.strip().splitlines()[-int(cfg["worker_log_retro_lineas"]):])
    db.marcar(con, cola_id, "error", fase="fin", terminado_en=db.ahora(),
              error=f"rc={rc}", mensaje=f"Error del pipeline (rc={rc})")
    db.evento(con, job_id=job_id, cola_id=cola_id, tipo="error", nivel="error",
              detalle=f"rc={rc}\n{tail}"[:1500])
    log(f"job #{cola_id}: ERROR rc={rc}\n{tail}")
    return "error"


def marcar_duplicados(con, cfg, job_id):
    """Tras una corrida REAL, los jobs pendientes cuyo PDF ya no está en la
    entrada (el pipeline lo movió a Procesados/) se marcan ok: ya se procesó."""
    archivos, err = pc.listar_entrada(cfg)
    if archivos is None:
        return
    presentes = {a["Name"].lower() for a in archivos}
    for f in con.execute("SELECT id, nombre, job_id FROM cola WHERE estado='pendiente'"
                         ).fetchall():
        if f["nombre"].lower() not in presentes:
            db.marcar(con, f["id"], "ok", fase="fin", terminado_en=db.ahora(),
                      mensaje=f"ya procesado por {job_id} (PDF movido a Procesados/)")
            db.evento(con, job_id=f["job_id"] or job_id, cola_id=f["id"],
                      tipo="estado", nivel="ok",
                      detalle="duplicado: el PDF ya se procesó en esta corrida")


# ---------------------------------------------------------------- bucle
def main():
    ap = argparse.ArgumentParser(description="Worker del panel del separador")
    ap.add_argument("--una-vez", action="store_true",
                    help="procesa la cola y sale (no espera jobs nuevos)")
    ap.add_argument("--idle-exit", type=int, default=None,
                    help="segundos sin jobs antes de salir (default: config)")
    args = ap.parse_args()

    cfg = pc.cargar_config()
    idle_exit = args.idle_exit if args.idle_exit is not None else int(cfg["worker_idle_exit_s"])
    con = db.inicializar(cfg["db"])

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _sigterm)

    try:
        with open(_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    db.control_set(con, "worker_pid", os.getpid())
    db.control_set(con, "worker_iniciado_en", db.ahora())

    rec = db.reap_stale(con, int(cfg["stale_s"]))
    log(f"arranque pid={os.getpid()} db={cfg['db']} cfg={cfg['_ruta']} "
        f"dry_default={cfg['dry_default']} recuperados={rec}")

    idle_desde = time.time()
    try:
        while not _PARAR:
            if db.pausa_global(con):
                db.control_set(con, "worker_estado", "pausado")
                time.sleep(2)
                idle_desde = time.time()
                continue
            job = db.siguiente_pendiente(con)
            if not job:
                db.control_set(con, "worker_estado", "esperando")
                if args.una_vez or (time.time() - idle_desde) > idle_exit:
                    log("cola vacía: salgo")
                    break
                time.sleep(2)
                continue
            db.control_set(con, "worker_estado", "trabajando")
            idle_desde = time.time()
            res = ejecutar_job(con, cfg, job)
            if res == "lock":
                time.sleep(int(cfg["espera_lock_s"]))
    finally:
        db.control_set(con, "worker_estado", "detenido")
        db.control_set(con, "worker_pid", "")
        try:
            os.remove(_PID_FILE)
        except OSError:
            pass
        log("worker detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
