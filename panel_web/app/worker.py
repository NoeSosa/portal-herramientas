"""Worker del panel web: procesa un job a la vez y lo aísla por carpeta.

No comparte `entrada_local` ni `auto/salida` con el panel clásico: cada job vive
en `/data/users/<uid>/jobs/<job_id>` y el pipeline recibe esas rutas por entorno.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import db
from .config import Settings

# El worker solo procesa archivos: no debe necesitar secretos OIDC o de sesión.
settings = Settings.from_env(require_auth=False)
logger = logging.getLogger("panel_web.worker")
_PARAR = False


def _configurar_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _job_dir(owner_id: int, job_id: str) -> Path:
    base = (settings.users_dir / str(owner_id) / "jobs" / job_id).resolve()
    raiz = settings.data_dir.resolve()
    if not base.is_relative_to(raiz):
        raise RuntimeError("Ruta de job fuera de /data")
    return base


def _paginas_leidas(job_dir: Path, job_id: str) -> int:
    """Cuenta páginas distintas ya publicadas por el OCR (o por el detector)."""
    progress = job_dir / "progress"
    for nombre, tipo in ((f"{job_id}.ocr.jsonl", "ocr_pagina"),
                         (f"{job_id}.jsonl", "pagina")):
        ruta = progress / nombre
        if not ruta.is_file():
            continue
        vistas: set[int] = set()
        try:
            with ruta.open("r", encoding="utf-8", errors="replace") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    try:
                        ev = json.loads(linea)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("tipo") == tipo:
                        try:
                            vistas.add(int(ev.get("pag") or 0))
                        except (TypeError, ValueError):
                            continue
        except OSError:
            continue
        if vistas:
            return len(vistas)
    return 0


def _sincronizar_paginas(con: Any, job_id: str, job_dir: Path) -> int:
    """Lleva los eventos JSONL del pipeline a `job_pages` para la vista admin."""
    eventos: list[dict[str, Any]] = []
    for nombre in (f"{job_id}.jsonl", f"{job_id}.ocr.jsonl"):
        ruta = job_dir / "progress" / nombre
        if not ruta.is_file():
            continue
        try:
            with ruta.open("r", encoding="utf-8", errors="replace") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    try:
                        ev = json.loads(linea)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(ev, dict):
                        eventos.append(ev)
        except OSError:
            continue

    paginas: dict[int, dict[str, Any]] = {}
    for ev in eventos:
        tipo = ev.get("tipo")
        if tipo not in ("ocr_pagina", "pagina"):
            continue
        try:
            pag = int(ev.get("pag") or 0)
        except (TypeError, ValueError):
            continue
        if pag <= 0:
            continue
        datos = paginas.setdefault(pag, {})
        for campo in ("pdf", "sn", "sede", "marca", "estado", "texto",
                      "worker", "pag_total"):
            if ev.get(campo) not in (None, ""):
                datos[campo] = ev[campo]
        if tipo == "ocr_pagina":
            if ev.get("ms") is not None:
                datos["ms"] = ev["ms"]
            if ev.get("slot") is not None:
                datos["slot"] = ev["slot"]
            if ev.get("pid") is not None:
                datos["pid"] = ev["pid"]
        else:
            if ev.get("ms_pagina") is not None:
                datos["ms"] = ev["ms_pagina"]
        if ev.get("ts") is not None:
            datos["ts"] = ev["ts"]

    for pag, datos in paginas.items():
        db.upsert_pagina(con, job_id, pag, **datos)
    if paginas:
        con.commit()
    return len(paginas)


def _ultima_salida(job_dir: Path) -> Path | None:
    base = job_dir / "salida"
    if not base.is_dir():
        return None
    carpetas = [p for p in base.glob("Separador_*") if p.is_dir()]
    if not carpetas:
        return None
    return max(carpetas, key=lambda p: p.stat().st_mtime)


def _zip_resultado(job_dir: Path, carpeta: Path) -> Path:
    destino = job_dir / "resultado"
    if destino.with_suffix(".zip").exists():
        destino.with_suffix(".zip").unlink()
    shutil.make_archive(str(destino), "zip", root_dir=str(carpeta))
    return destino.with_suffix(".zip")


def _mensaje_error(rc: int) -> str:
    if rc == 124:
        return "El documento tardó demasiado y se canceló."
    return "No se pudo procesar el PDF. Revisa que no esté dañado e inténtalo de nuevo."


def _detener_proceso(proc: subprocess.Popen) -> None:
    """En Linux se termina también a los lectores OCR descendientes."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        elif proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # El padre puede salir antes que sus hijos: finalizar el grupo igualmente.
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.poll() is None:
            proc.kill()
        proc.wait()
    except ProcessLookupError:
        proc.wait()


def _entorno_base_ocr() -> dict[str, str]:
    """Solo heredar ajustes de runtime; los secretos web nunca llegan al motor."""
    return {k: v for k, v in os.environ.items() if k in {
        "PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP",
        "SYSTEMROOT", "SystemRoot", "WINDIR", "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "PANEL_OCR_SOLO_SN", "PANEL_OCR_FRANJA", "PANEL_OCR_SECUENCIAL", "PANEL_OCR_BANDAS",
    }}


def procesar(job: Any) -> None:
    job_id = job["id"]
    owner_id = int(job["owner_id"])
    job_dir = _job_dir(owner_id, job_id)
    entrada = job_dir / "input"
    progreso = job_dir / "progress"
    for ruta in (entrada, progreso):
        ruta.mkdir(parents=True, exist_ok=True)

    log_path = job_dir / "pipeline.log"
    env = _entorno_base_ocr()
    env.update({
        "HOME": str(settings.data_dir),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "America/Mexico_City",
        "SEPARADOR_LOCAL": str(job_dir),
        "SEPARADOR_ENTRADA_LOCAL": str(entrada),
        "SEPARADOR_INVENTARIOS_LOCAL": str(settings.inventory_dir),
        "SEPARADOR_WORKERS": str(settings.ocr_workers),
    })
    cmd = [
        sys.executable,
        str(settings.separator_code_dir / "separador_auto.py"),
        "--modo", "local",
        "--job-id", job_id,
        "--sin-correo",
    ]

    con = db.conectar(settings.db_path)
    inicio = time.monotonic()
    parada = False
    proc: subprocess.Popen | None = None
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n===== job {job_id} {db.ahora()} =====\n")
            log.flush()
            proc = subprocess.Popen(
                cmd,
                cwd=str(settings.separator_code_dir),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )
            logger.info("Job %s iniciado (pid %s)", job_id, proc.pid)

            while proc.poll() is None:
                if _PARAR:
                    parada = True
                    _detener_proceso(proc)
                    break
                if time.monotonic() - inicio > settings.job_timeout_seconds:
                    _detener_proceso(proc)
                    db.fallar_job(con, job_id, "El documento tardó demasiado y se canceló.")
                    logger.error("Job %s agotó el tiempo", job_id)
                    return
                try:
                    leidas = _sincronizar_paginas(con, job_id, job_dir)
                except Exception:
                    logger.exception("No se pudo sincronizar job_pages de %s", job_id)
                    leidas = _paginas_leidas(job_dir, job_id)
                if leidas:
                    db.actualizar_progreso(
                        con, job_id, leidas,
                        f"Procesando {leidas}/{int(job['pages'] or 0)} páginas",
                    )
                time.sleep(1)

            rc = proc.wait()
            try:
                _sincronizar_paginas(con, job_id, job_dir)
            except Exception:
                logger.exception("No se pudo sincronizar el final de %s", job_id)
            if parada:
                db.fallar_job(con, job_id, "Procesamiento interrumpido.")
                logger.info("Job %s interrumpido por parada del worker", job_id)
                return
    except Exception as exc:
        logger.exception("Job %s no pudo lanzarse", job_id)
        db.fallar_job(con, job_id, "No se pudo iniciar el procesamiento.")
        return
    finally:
        if proc is not None and proc.poll() is None:
            _detener_proceso(proc)
        con.close()

    con = db.conectar(settings.db_path)
    try:
        if rc != 0:
            logger.error("Job %s terminó con código %s", job_id, rc)
            db.fallar_job(con, job_id, _mensaje_error(rc))
            db.auditar(
                con, user_id=owner_id, email=job["owner_email"],
                action="job_error", job_id=job_id, detail=f"rc={rc}",
            )
            return

        salida = _ultima_salida(job_dir)
        if not salida:
            logger.error("Job %s terminó sin carpeta de salida", job_id)
            db.fallar_job(con, job_id, "El procesamiento no generó resultados.")
            return

        zip_path = _zip_resultado(job_dir, salida)
        paginas = int(job["pages"] or 0)
        db.finalizar_job(con, job_id, str(zip_path), paginas)
        db.auditar(
            con, user_id=owner_id, email=job["owner_email"],
            action="job_done", job_id=job_id, detail=f"{paginas} págs.",
        )
        logger.info("Job %s listo: %s", job_id, zip_path.name)
        # Conservamos input/procesados hasta la retención: Administración puede
        # renderizar cualquier hoja del PDF original, no solo la que estaba en vivo.
    finally:
        con.close()


def limpiar_antiguos() -> None:
    con = db.conectar(settings.db_path)
    try:
        for job in db.jobs_vencidos(con, settings.retention_days):
            shutil.rmtree(_job_dir(int(job["owner_id"]), job["id"]), ignore_errors=True)
            db.borrar_job_por_id(con, job["id"])
            db.auditar(
                con, user_id=int(job["owner_id"]), email=job["owner_email"],
                action="retention_delete", job_id=job["id"],
                detail=f"{settings.retention_days} días",
            )
            logger.info("Retención: borrado job %s", job["id"])
    finally:
        con.close()


def _senal(_sig, _frame):
    global _PARAR
    _PARAR = True


def main() -> int:
    _configurar_logging()
    db.inicializar(settings.db_path)
    settings.users_dir.mkdir(parents=True, exist_ok=True)
    settings.inventory_dir.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, _senal)
    signal.signal(signal.SIGINT, _senal)

    ultima_limpieza = 0.0
    logger.info(
        "Worker iniciado. Código: %s · workers OCR: %s",
        settings.separator_code_dir, settings.ocr_workers,
    )
    while not _PARAR:
        if time.monotonic() - ultima_limpieza > 3600:
            try:
                limpiar_antiguos()
            except Exception:
                logger.exception("Error en limpieza por retención")
            ultima_limpieza = time.monotonic()

        try:
            con = db.conectar(settings.db_path)
            try:
                job = db.claim_siguiente(con)
            finally:
                con.close()
        except Exception:
            logger.exception("Error reclamando el siguiente job")
            time.sleep(5)
            continue
        if not job:
            time.sleep(2)
            continue
        try:
            procesar(job)
        except Exception:
            logger.exception("Job %s falló en el post-proceso", job["id"])
            con = db.conectar(settings.db_path)
            try:
                fila = db.job_por_id(con, job["id"])
                if fila and fila["status"] not in ("done", "error"):
                    db.fallar_job(
                        con, job["id"],
                        "No se pudo finalizar el documento. Inténtalo de nuevo.",
                    )
            finally:
                con.close()
    logger.info("Worker detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
