"""API pública del separador de PDFs.

Solo expone lo que un usuario necesita: entrar con Entra ID, subir sus PDFs, ver
su cola/ETA y descargar sus resultados. No hay endpoints de sistema, logs,
inventario ni acceso a trabajos de otros usuarios.
"""

from __future__ import annotations

import html
import logging
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import fitz
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, db, inventario
from .auth import (
    autenticar_callback,
    cerrar_sesion,
    ip_cliente,
    limite_subidas,
    oauth,
    settings,
    token_csrf,
    usuario_actual,
    verificar_csrf,
)

logger = logging.getLogger("panel_web")


def _configurar_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.inicializar(settings.db_path)
    settings.users_dir.mkdir(parents=True, exist_ok=True)
    settings.inventory_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Panel web iniciado en %s", settings.domain)
    yield
    logger.info("Panel web detenido")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="pdfsep_session",
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=settings.cookie_secure,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))


@app.middleware("http")
async def cabeceras_seguridad(request: Request, call_next):
    respuesta = await call_next(request)
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["X-Frame-Options"] = "DENY"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    respuesta.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), payment=()"
    )
    respuesta.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self'; "
        "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "object-src 'none'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    respuesta.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    respuesta.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if settings.cookie_secure:
        respuesta.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    if request.url.path.startswith("/api/") or request.url.path.startswith("/auth/"):
        respuesta.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/"):
        # El HTML referencia style.css/app.js con ?v=...; además forzamos
        # revalidación para que un redeploy no deje assets viejos en caché.
        respuesta.headers["Cache-Control"] = "no-cache"
    return respuesta


@app.exception_handler(Exception)
async def error_no_controlado(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Error no controlado en %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------
def _pagina(nombre: str) -> FileResponse:
    return FileResponse(
        settings.static_dir / nombre,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", include_in_schema=False)
async def inicio(request: Request):
    if not request.session.get("uid"):
        return _pagina("login.html")
    return _pagina("index.html")


@app.get("/login", include_in_schema=False)
async def login_pagina(request: Request):
    if request.session.get("uid"):
        return RedirectResponse("/", status_code=303)
    return _pagina("login.html")


def admin_actual(usuario=Depends(usuario_actual)):
    if usuario["role"] != "admin":
        raise HTTPException(status_code=403, detail="Se requiere perfil de administrador.")
    return usuario


@app.get("/admin", include_in_schema=False)
async def admin_pagina(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return _pagina("login.html")
    con = db.conectar(settings.db_path)
    try:
        usuario = db.usuario_por_id(con, int(uid))
    finally:
        con.close()
    if not usuario or usuario["role"] != "admin":
        return _error_admin()
    return _pagina("admin.html")


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return HTMLResponse("User-agent: *\nDisallow: /\n", media_type="text/plain")


@app.get("/auth/login", include_in_schema=False)
async def auth_login(request: Request):
    # En Workforce forzamos selección de cuenta para no reutilizar una personal.
    # En CIAM (MS_AUTHORITY definido) no imponemos prompt: su flujo de usuario
    # puede no soportar `select_account`.
    kwargs: dict[str, str] = {}
    if not settings.authority_override:
        kwargs["prompt"] = "select_account"
    return await oauth.microsoft.authorize_redirect(
        request, settings.redirect_uri, **kwargs
    )


@app.get("/auth/callback", include_in_schema=False)
async def auth_callback(request: Request):
    # Entra puede volver con error=... antes de entregar el código. Mostramos
    # el motivo (escapado) en vez de un 500 opaco.
    if request.query_params.get("error"):
        detalle = (
            request.query_params.get("error_description")
            or request.query_params.get("error")
            or "Error de autenticación."
        )[:300]
        return _error_login(
            "Microsoft rechazó el inicio de sesión: " + html.escape(detalle),
            400,
        )
    try:
        await autenticar_callback(request)
    except HTTPException as exc:
        detalle = html.escape(str(exc.detail))
        return _error_login(detalle, exc.status_code)
    except Exception:
        logger.exception("Error en el callback de Entra ID")
        return _error_login("No se pudo completar el inicio de sesión.", 400)
    return RedirectResponse("/", status_code=303)


def _error_login(detalle: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Acceso denegado</title>"
        "<link rel='stylesheet' href='/static/style.css?v=20261020b'></head>"
        "<body class='login-body'><main class='login-card'>"
        "<div class='login-brand'><span class='brand-mark'>PDF</span>"
        "<span class='brand-name'>Separador de PDFs</span></div>"
        "<h1>No se pudo iniciar sesión</h1>"
        f"<p class='login-lead'>{detalle}</p>"
        "<a class='btn primary' href='/login'>Volver</a>"
        "</main></body></html>",
        status_code=status_code,
    )


def _error_admin() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Administración</title>"
        "<link rel='stylesheet' href='/static/style.css?v=20261020b'></head>"
        "<body class='login-body'><main class='login-card'>"
        "<div class='login-brand'><span class='brand-mark'>PDF</span>"
        "<span class='brand-name'>Separador de PDFs</span></div>"
        "<h1>Se requiere administrador</h1>"
        "<p class='login-lead'>Tu cuenta no tiene el rol de administrador. "
        "Pide que incluyan tu correo en <code>ADMIN_EMAILS</code> y vuelve a "
        "iniciar sesión.</p>"
        "<a class='btn primary' href='/'>Volver al panel</a>"
        "</main></body></html>",
        status_code=403,
    )


@app.get("/auth/logout", include_in_schema=False)
async def auth_logout(request: Request):
    cerrar_sesion(request)
    return RedirectResponse("/login", status_code=303)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _job_dir(owner_id: int, job_id: str) -> Path:
    base = (settings.users_dir / str(owner_id) / "jobs" / job_id).resolve()
    raiz = settings.data_dir.resolve()
    if not base.is_relative_to(raiz):
        raise HTTPException(status_code=400, detail="Ruta de trabajo inválida.")
    return base


def _nombre_seguro(nombre: str | None) -> str:
    nombre = Path(nombre or "documento.pdf").name
    nombre = re.sub(r"[^\w .()\-]", "_", nombre, flags=re.UNICODE).strip(" .")
    return (nombre[:120] or "documento.pdf")


def _validar_pdf(contenido: bytes) -> int:
    if len(contenido) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="El archivo supera el límite permitido.")
    if b"%PDF-" not in contenido[:1024]:
        raise HTTPException(status_code=400, detail="El archivo no es un PDF válido.")
    doc = None
    try:
        doc = fitz.open(stream=contenido, filetype="pdf")
        if doc.is_encrypted:
            raise HTTPException(status_code=400, detail="El PDF está protegido con contraseña.")
        paginas = int(doc.page_count)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="No se pudo leer el PDF.") from None
    finally:
        if doc is not None:
            doc.close()
    if paginas < 1:
        raise HTTPException(status_code=400, detail="El PDF no tiene páginas.")
    if paginas > settings.max_pages:
        raise HTTPException(
            status_code=400,
            detail=f"El PDF supera el máximo de {settings.max_pages} páginas.",
        )
    return paginas


def _job_publico(job: Any, stats: dict[str, int | float]) -> dict[str, Any]:
    paginas = int(job["pages"] or 0)
    hechas = int(job["pages_done"] or 0)
    return {
        "id": job["id"],
        "filename": job["filename"],
        "pages": paginas,
        "size": int(job["size"] or 0),
        "status": job["status"],
        "pages_done": hechas,
        "progress": min(100, round(hechas / paginas * 100)) if paginas else 0,
        "message": job["message"] or "",
        "eta_seconds": stats.get("eta_seconds", 0),
        "position": stats.get("posicion", 0),
        "pages_ahead": stats.get("pages_ahead", 0),
        "created_at": job["created_at"],
        "finished_at": job["finished_at"],
        "download_ready": job["status"] == "done" and bool(job["result_path"]),
        "error": job["error"] if job["status"] == "error" else None,
    }


def _conexion():
    return db.conectar(settings.db_path)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.get("/api/health")
async def salud() -> dict[str, Any]:
    return {"ok": True, "app": settings.app_name, "version": __version__}


@app.get("/api/me")
async def yo(request: Request, usuario=Depends(usuario_actual)) -> dict[str, Any]:
    return {
        "email": usuario["email"],
        "name": usuario["name"] or usuario["email"],
        "role": usuario["role"],
        "csrf": token_csrf(request),
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pages": settings.max_pages,
            "max_pages_per_day": settings.max_pages_per_day,
            "max_queued_jobs": settings.max_queued_jobs,
        },
    }


@app.post("/api/jobs", status_code=201)
async def crear_job(
    request: Request,
    archivo: UploadFile = File(...),
    usuario=Depends(usuario_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    uid = int(usuario["id"])
    if not limite_subidas.permitir(uid):
        raise HTTPException(
            status_code=429,
            detail="Demasiadas subidas en poco tiempo. Espera unos minutos.",
        )

    contenido = await archivo.read(settings.max_upload_bytes + 1)
    paginas = _validar_pdf(contenido)
    nombre = _nombre_seguro(archivo.filename)

    con = _conexion()
    try:
        if db.contar_activos(con, uid) >= settings.max_queued_jobs:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Ya tienes {settings.max_queued_jobs} documentos en cola. "
                    "Espera a que terminen antes de subir más."
                ),
            )
        consumidas = db.contar_paginas_hoy(con, uid)
        if consumidas + paginas > settings.max_pages_per_day:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Superarías el límite diario de {settings.max_pages_per_day} "
                    "páginas."
                ),
            )
    finally:
        con.close()

    job_id = uuid.uuid4().hex
    job_dir = _job_dir(uid, job_id)
    entrada = job_dir / "input"
    entrada.mkdir(parents=True, exist_ok=True)
    stored_name = f"{job_id}.pdf"
    (entrada / stored_name).write_bytes(contenido)

    con = _conexion()
    try:
        job = db.crear_job(
            con,
            job_id=job_id,
            owner_id=uid,
            owner_email=usuario["email"],
            filename=nombre,
            stored_name=stored_name,
            size=len(contenido),
            pages=paginas,
            client_ip=ip_cliente(request),
        )
        db.auditar(
            con,
            user_id=uid,
            email=usuario["email"],
            action="upload",
            job_id=job_id,
            ip=ip_cliente(request),
            detail=f"{nombre} · {paginas} págs.",
        )
        promedio = db.promedio_segundos_por_pagina(con)
        stats = db.estadisticas_cola(con, job, promedio)
        return _job_publico(job, stats)
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    finally:
        con.close()


@app.get("/api/jobs")
async def mis_jobs(usuario=Depends(usuario_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        promedio = db.promedio_segundos_por_pagina(con)
        trabajos = [
            _job_publico(job, db.estadisticas_cola(con, job, promedio))
            for job in db.listar_jobs(con, int(usuario["id"]))
        ]
        activos = [j for j in trabajos if j["status"] in ("queued", "running")]
        eta = max((j["eta_seconds"] for j in activos), default=0)
        return {
            "jobs": trabajos,
            "queue": {
                "active": len(activos),
                "pages": sum(j["pages"] for j in activos),
                "eta_seconds": eta,
            },
        }
    finally:
        con.close()


@app.get("/api/jobs/{job_id}")
async def un_job(job_id: str, usuario=Depends(usuario_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id, int(usuario["id"]))
        if not job:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        promedio = db.promedio_segundos_por_pagina(con)
        return _job_publico(job, db.estadisticas_cola(con, job, promedio))
    finally:
        con.close()


@app.get("/api/jobs/{job_id}/download")
async def descargar(
    request: Request, job_id: str, usuario=Depends(usuario_actual)
):
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id, int(usuario["id"]))
        if not job:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        if job["status"] != "done" or not job["result_path"]:
            raise HTTPException(status_code=409, detail="El resultado todavía no está listo.")
        ruta = Path(job["result_path"]).resolve()
        if not ruta.is_relative_to(settings.data_dir.resolve()) or not ruta.is_file():
            raise HTTPException(status_code=404, detail="Resultado no encontrado.")
        db.auditar(
            con,
            user_id=int(usuario["id"]),
            email=usuario["email"],
            action="download",
            job_id=job_id,
            ip=ip_cliente(request),
        )
        descarga = f"separador-{Path(job['filename']).stem[:80]}.zip"
        return FileResponse(ruta, media_type="application/zip", filename=descarga)
    finally:
        con.close()


@app.delete("/api/jobs/{job_id}", status_code=204)
async def borrar(
    request: Request,
    job_id: str,
    usuario=Depends(usuario_actual),
    _csrf=Depends(verificar_csrf),
):
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id, int(usuario["id"]))
        if not job:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        if job["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail="El documento se está procesando; espera a que termine.",
            )
        db.borrar_job(con, job_id, int(usuario["id"]))
        db.auditar(
            con,
            user_id=int(usuario["id"]),
            email=usuario["email"],
            action="delete",
            job_id=job_id,
            ip=ip_cliente(request),
            detail=job["filename"],
        )
    finally:
        con.close()
    shutil.rmtree(_job_dir(int(usuario["id"]), job_id), ignore_errors=True)
    return None


# --------------------------------------------------------------------------
# Administración
# --------------------------------------------------------------------------
def _job_admin(job: Any, current_page: int = 0) -> dict[str, Any]:
    paginas = int(job["pages"] or 0)
    hechas = int(job["pages_done"] or 0)
    datos = {
        "id": job["id"],
        "owner_email": job["owner_email"],
        "owner_name": job["owner_name"] if "owner_name" in job.keys() else job["owner_email"],
        "filename": job["filename"],
        "pages": paginas,
        "pages_done": hechas,
        "progress": min(100, round(hechas / paginas * 100)) if paginas else 0,
        "status": job["status"],
        "message": job["message"] or "",
        "error": job["error"] if job["status"] == "error" else None,
        "size": int(job["size"] or 0),
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "download_ready": job["status"] == "done" and bool(job["result_path"]),
        "current_page": int(current_page or hechas or 0),
    }
    return datos


def _exportar_inventario(con) -> tuple[bool, str, Path]:
    destino = settings.inventory_dir / "inventario_panel.xlsx"
    try:
        filas = inventario.exportar_xlsx(con, destino)
        return True, f"{filas} seriales", destino
    except Exception as exc:
        logger.exception("No se pudo exportar el inventario")
        return False, f"No se pudo escribir {destino}: {exc}", destino


@app.get("/api/admin/overview")
async def admin_overview(admin=Depends(admin_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        activos = con.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')"
        ).fetchone()["n"]
        corriendo = con.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status='running'"
        ).fetchone()["n"]
        paginas = con.execute(
            "SELECT COALESCE(SUM(pages_done),0) AS n FROM jobs"
        ).fetchone()["n"]
        sedes = db.resumen_sedes(con)
        archivo = inventario.info_exportado(settings.inventory_dir / "inventario_panel.xlsx")
        return {
            "active": int(activos or 0),
            "running": int(corriendo or 0),
            "pages_done": int(paginas or 0),
            "seriales": db.contar_equipos(con),
            "sedes": len(sedes),
            "inventario": archivo,
        }
    finally:
        con.close()


@app.get("/api/admin/jobs")
async def admin_jobs(admin=Depends(admin_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        return {"jobs": [_job_admin(j) for j in db.listar_jobs_admin(con, 80)]}
    finally:
        con.close()


@app.get("/api/admin/jobs/{job_id}/progress")
async def admin_job_progress(
    job_id: str, admin=Depends(admin_actual)
) -> dict[str, Any]:
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        paginas = db.paginas_job(con, job_id)
        con_ocr = [p for p in paginas if p.get("ocr") or p.get("estado")]
        actual = max((p["pag"] for p in con_ocr), default=int(job["pages_done"] or 0))
        ultima = con.execute(
            "SELECT pag, texto FROM job_pages WHERE job_id=? AND texto IS NOT NULL"
            " AND texto<>'' ORDER BY ts DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        promedio = db.promedio_segundos_por_pagina(con)
        stats = db.estadisticas_cola(con, job, promedio)
        datos_job = _job_admin(job, actual)
        datos_job["eta_seconds"] = stats["eta_seconds"]
        return {
            "job": datos_job,
            "pages": paginas,
            "workers": db.trabajadores_job(con, job_id),
            "resumen": db.resumen_paginas(con, job_id),
            "current_page": actual,
            "ultima_lectura": ultima["texto"] if ultima else "",
            "ultima_pagina": ultima["pag"] if ultima else 0,
        }
    finally:
        con.close()


@app.get("/api/admin/jobs/{job_id}/page/{pag}.png")
async def admin_job_page(
    job_id: str, pag: int, admin=Depends(admin_actual)
):
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id)
    finally:
        con.close()
    if not job:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    if pag < 1 or pag > int(job["pages"] or 0):
        raise HTTPException(status_code=404, detail="Página fuera de rango.")
    job_dir = _job_dir(int(job["owner_id"]), job_id)
    thumb = job_dir / "thumb" / f"{pag}.png"
    if thumb.is_file():
        return Response(
            content=thumb.read_bytes(), media_type="image/png",
            headers={"Cache-Control": "private, max-age=300"},
        )
    candidatos = [
        job_dir / "input" / job["stored_name"],
        job_dir / "input" / "procesados" / job["stored_name"],
    ]
    pdf = next((p for p in candidatos if p.is_file()), None)
    if not pdf:
        raise HTTPException(status_code=404, detail="La hoja ya no está disponible.")
    try:
        doc = fitz.open(pdf)
        try:
            page = doc.load_page(pag - 1)
            pix = page.get_pixmap(dpi=110)
            contenido = pix.tobytes("png")
        finally:
            doc.close()
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(contenido)
        return Response(
            content=contenido, media_type="image/png",
            headers={"Cache-Control": "private, max-age=300"},
        )
    except Exception:
        logger.exception("No se pudo renderizar %s pág %s", job_id, pag)
        raise HTTPException(status_code=500, detail="No se pudo renderizar la hoja.") from None


@app.get("/api/admin/inventario")
async def admin_inventario(admin=Depends(admin_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        return {
            "total": db.contar_equipos(con),
            "sedes": db.resumen_sedes(con),
            "marcas": db.valores_equipos(con, "marca"),
            "estatus": db.valores_equipos(con, "estatus"),
            "archivo": inventario.info_exportado(
                settings.inventory_dir / "inventario_panel.xlsx"
            ),
        }
    finally:
        con.close()


@app.get("/api/admin/equipos")
async def admin_equipos(
    q: str = "", sede: str = "", marca: str = "", estatus: str = "",
    zona: str = "", limite: int = 100, offset: int = 0, orden: str = "sn",
    admin=Depends(admin_actual),
) -> dict[str, Any]:
    con = _conexion()
    try:
        filtros = {"q": q or None, "sede": sede or None, "marca": marca or None,
                   "estatus": estatus or None, "zona": zona or None}
        return {
            "total": db.contar_equipos(con, **filtros),
            "todos": db.contar_equipos(con),
            "equipos": db.listar_equipos(
                con, limite=limite, offset=offset, orden=orden, **filtros
            ),
            "filtros": {
                "sedes": db.valores_equipos(con, "sede"),
                "marcas": db.valores_equipos(con, "marca"),
                "estatus": db.valores_equipos(con, "estatus"),
            },
        }
    finally:
        con.close()


@app.post("/api/admin/equipos")
async def admin_guardar_equipo(
    request: Request, admin=Depends(admin_actual), _csrf=Depends(verificar_csrf)
) -> dict[str, Any]:
    datos = await request.json()
    sn = (datos.get("sn") or "").strip()
    if not sn:
        raise HTTPException(status_code=400, detail="Falta el serial.")
    con = _conexion()
    try:
        permitidos = {c: datos.get(c, "") for c in db.CAMPOS_EQUIPO if c in datos}
        creado, sn = db.upsert_equipo(con, sn, **permitidos)
        ok, detalle, _ = _exportar_inventario(con)
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="inventario_alta" if creado else "inventario_edicion",
            ip=ip_cliente(request), detail=f"{sn}",
        )
        return {"ok": ok, "sn": sn, "creado": creado,
                "total": db.contar_equipos(con), "exportado": ok,
                "detalle": detalle}
    finally:
        con.close()


@app.delete("/api/admin/equipos/{sn}")
async def admin_borrar_equipo(
    sn: str, request: Request, admin=Depends(admin_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    con = _conexion()
    try:
        borrado = db.borrar_equipo(con, sn)
        ok, detalle, _ = _exportar_inventario(con)
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="inventario_baja", ip=ip_cliente(request), detail=sn,
        )
        return {"ok": borrado, "sn": sn.upper(), "total": db.contar_equipos(con),
                "exportado": ok, "detalle": detalle}
    finally:
        con.close()


@app.post("/api/admin/inventario/importar")
async def admin_importar_inventario(
    request: Request,
    archivo: UploadFile = File(...),
    admin=Depends(admin_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    nombre = _nombre_seguro(archivo.filename or "inventario.xlsx")
    if not nombre.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Sube un archivo .xlsx.")
    contenido = await archivo.read(20 * 1024 * 1024 + 1)
    if len(contenido) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El Excel supera 20 MB.")
    settings.inventory_dir.mkdir(parents=True, exist_ok=True)
    tmp = settings.inventory_dir / f"import_{uuid.uuid4().hex}.xlsx"
    tmp.write_bytes(contenido)
    con = _conexion()
    try:
        resultado = inventario.importar_xlsx(con, tmp)
        ok, detalle, _ = _exportar_inventario(con)
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="inventario_importar", ip=ip_cliente(request),
            detail=f"{nombre} · {resultado.get('leidos', 0)} filas",
        )
        return {"ok": ok, **resultado, "total": db.contar_equipos(con),
                "exportado": ok, "detalle": detalle}
    except Exception as exc:
        logger.exception("No se pudo importar el inventario")
        raise HTTPException(status_code=400, detail=f"No se pudo leer el Excel: {exc}") from None
    finally:
        con.close()
        tmp.unlink(missing_ok=True)


@app.post("/api/admin/inventario/exportar")
async def admin_exportar_inventario(
    request: Request, admin=Depends(admin_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    con = _conexion()
    try:
        ok, detalle, destino = _exportar_inventario(con)
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="inventario_exportar", ip=ip_cliente(request),
            detail=destino.name,
        )
        return {"ok": ok, "detalle": detalle,
                "filas": db.contar_equipos(con), "destino": destino.name}
    finally:
        con.close()


@app.get("/api/admin/sedes")
async def admin_sedes(admin=Depends(admin_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        return {"sedes": db.resumen_sedes(con), "total": db.contar_equipos(con)}
    finally:
        con.close()


# --------------------------------------------------------------------------
# Estáticos
# --------------------------------------------------------------------------
app.mount(
    "/static",
    StaticFiles(directory=str(settings.static_dir)),
    name="static",
)
