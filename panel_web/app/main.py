"""API pública del separador de PDFs.

Solo expone lo que un usuario necesita: entrar con Entra ID, subir sus PDFs, ver
su cola/ETA y descargar sus resultados. No hay endpoints de sistema, logs,
inventario ni acceso a trabajos de otros usuarios.
"""

from __future__ import annotations

import html
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import fitz
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, db, inventario
from .peticiones import GuardiaPeticiones
from .esquemas import BorradoEquiposEntrada, EquipoEntrada
from .normalizacion import normalizar_serial, normalizar_serial_alterno
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

# Los PDFs y artefactos nuevos nacen solo para el uid del panel.
os.umask(0o077)


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

app.add_middleware(GuardiaPeticiones)
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


@app.exception_handler(db.SerialAmbiguo)
async def serial_ambiguo(request: Request, exc: db.SerialAmbiguo):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(db.SerialInvalido)
async def serial_invalido(request: Request, exc: db.SerialInvalido):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


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
        return RedirectResponse("/login", status_code=303)
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
        "<link rel='stylesheet' href='/static/style.css?v=20261020c'></head>"
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
        "<link rel='stylesheet' href='/static/style.css?v=20261020c'></head>"
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


_AVISOS_BLOQUEANTES = {"JavaScript", "JS", "Launch", "EmbeddedFile", "RichMedia"}

_CLAVES_PDF_ACTIVAS = {
    "JavaScript": "JavaScript",
    "JS": "JS",
    "Launch": "Launch",
    "EmbeddedFile": "EmbeddedFile",
    "RichMedia": "RichMedia",
    "OpenAction": "OpenAction",
    "AA": "AdditionalActions",
    "XFA": "XFA",
}

_ACCIONES_PDF_ACTIVAS = (
    ("/JavaScript", "JavaScript"),
    ("/Launch", "Launch"),
    ("/EmbeddedFile", "EmbeddedFile"),
    ("/RichMedia", "RichMedia"),
)


def _avisos_pdf(doc, contenido: bytes) -> list[str]:
    """Detecta contenido activo en la estructura PDF, no en bytes comprimidos.

    Buscar ``/JS`` con ``in contenido`` produce falsos positivos: los flujos de
    imagen JPEG/Flate pueden contener esa secuencia por azar. Aquí se inspeccionan
    claves de objeto y valores de acciones; los flujos de imagen no participan.
    """
    avisos: set[str] = set()
    try:
        for xref in range(1, doc.xref_length()):
            try:
                claves = set(doc.xref_get_keys(xref))
            except Exception:
                continue
            for clave, etiqueta in _CLAVES_PDF_ACTIVAS.items():
                if clave in claves:
                    avisos.add(etiqueta)
            for clave in ("S", "Subtype"):
                if clave not in claves:
                    continue
                try:
                    valor = str(doc.xref_get_key(xref, clave)[1])
                except Exception:
                    continue
                for marca, etiqueta in _ACCIONES_PDF_ACTIVAS:
                    if marca in valor:
                        avisos.add(etiqueta)
        if doc.embfile_count():
            avisos.add("EmbeddedFile")
    except Exception:
        # Si la inspección estructural falla, no se inventa una alerta.
        pass
    if b"%%EOF" not in contenido[-4096:]:
        avisos.add("sin %%EOF")
    return sorted(avisos)


def _error_subida(
    status: int, codigo: str, mensaje: str,
) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"codigo": codigo, "mensaje": mensaje},
    )


def _validar_pdf(contenido: bytes) -> tuple[int, list[str]]:
    if len(contenido) > settings.max_upload_bytes:
        raise _error_subida(
            413, "pdf_demasiado_grande",
            f"El PDF supera el máximo de {settings.max_upload_mb} MB.",
        )
    # Cabecera real de PDF (1.x/2.x), no solo la extensión ni un byte perdido.
    if not re.search(rb"%PDF-[12]\.\d", contenido[:1024]):
        raise _error_subida(
            400, "pdf_invalido",
            "El archivo no tiene una cabecera PDF válida. Puede estar dañado, "
            "truncado o no ser realmente un PDF.",
        )
    doc = None
    try:
        doc = fitz.open(stream=contenido, filetype="pdf")
        if doc.is_encrypted:
            raise _error_subida(
                400, "pdf_cifrado",
                "El PDF está protegido con contraseña. Quita la protección antes de subirlo.",
            )
        paginas = int(doc.page_count)
        avisos_pdf = _avisos_pdf(doc, contenido)
    except HTTPException:
        raise
    except Exception:
        raise _error_subida(
            400, "pdf_ilegible",
            "No se pudo leer el PDF. Puede estar dañado o usar una estructura no soportada.",
        ) from None
    finally:
        if doc is not None:
            doc.close()
    if paginas < 1:
        raise _error_subida(
            400, "pdf_sin_paginas",
            "El PDF no tiene páginas. Revisa el archivo original.",
        )
    if paginas > settings.max_pages:
        raise _error_subida(
            400, "pdf_demasiadas_paginas",
            f"El PDF tiene {paginas} páginas y el máximo es {settings.max_pages}.",
        )
    return paginas, avisos_pdf


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


def _validar_ubicacion_nueva(
    con, datos: dict[str, Any], sede: str = ""
) -> None:
    """Frena ubicaciones nuevas parecidas y obliga a confirmar y dar piso.

    No normaliza ni fusiona: solo compara contra las ubicaciones que ya existen
    en la misma sede. Una coincidencia exacta no se bloquea.
    """
    nombre = re.sub(r"\s+", " ", str(datos.get("ubicacion") or "")).strip()
    if not nombre:
        return
    if db.ubicacion_exacta(con, nombre, sede):
        return
    similares = db.ubicaciones_similares(con, nombre, sede)
    if not similares:
        return
    if not datos.get("confirmar_ubicacion"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Ya existe una ubicación parecida: "
                + ", ".join(similares[:5])
                + ". Elige una existente o confirma que deseas crear una nueva."
            ),
        )
    if not str(datos.get("piso") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Para crear una ubicación nueva, indica el piso.",
        )


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.get("/api/health")
async def salud() -> dict[str, Any]:
    return {"ok": True, "app": settings.app_name, "version": __version__}


@app.get("/api/me")
async def yo(request: Request, usuario=Depends(usuario_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        sedes = (db.listar_sedes_historicas(con) if usuario["role"] == "admin"
                 else db.sedes_usuario(con, int(usuario["id"])))
    finally:
        con.close()
    return {
        "email": usuario["email"],
        "name": usuario["display_name"] or usuario["name"] or usuario["email"],
        "role": usuario["role"],
        "sedes": sedes,
        "csrf": token_csrf(request),
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pages": settings.max_pages,
            "max_pages_per_day": settings.max_pages_per_day,
            "max_queued_jobs": settings.max_queued_jobs,
        },
    }


@app.patch("/api/me")
async def actualizar_me(
    request: Request, usuario=Depends(usuario_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    datos = await request.json()
    nombre = re.sub(r"\s+", " ", str(datos.get("name") or "")).strip()
    if not nombre or len(nombre) > 120:
        raise HTTPException(status_code=400, detail="El nombre debe tener entre 1 y 120 caracteres.")
    con = _conexion()
    try:
        db.actualizar_display_name(con, int(usuario["id"]), nombre)
        db.auditar(
            con, user_id=int(usuario["id"]), email=usuario["email"],
            action="perfil_nombre", ip=ip_cliente(request), detail=nombre,
        )
    finally:
        con.close()
    return {"ok": True, "name": nombre}


@app.get("/api/cobertura")
async def cobertura(
    request: Request, periodo: str = "", sede: str = "",
    usuario=Depends(usuario_actual),
) -> dict[str, Any]:
    if periodo and (not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", periodo) or periodo > db.periodo_de()):
        raise HTTPException(status_code=400, detail="Periodo inválido; usa YYYY-MM.")
    periodo = periodo or db.periodo_de()
    con = _conexion()
    try:
        if usuario["role"] == "admin":
            sedes = [sede] if sede else db.listar_sedes_historicas(con)
            db.backfill_cobertura(con, periodo)
            datos = [db.cobertura_sede(con, s, periodo) for s in sedes]
            return {
                "periodo": periodo,
                "sedes": datos,
                "esperados": sum(d["esperados"] for d in datos),
                "escaneados": sum(d["escaneados"] for d in datos),
                "faltantes": sum(len(d["faltantes"]) for d in datos),
                "duplicados": sum(len(d["duplicados"]) for d in datos),
            }
        return db.cobertura_para_usuario(con, int(usuario["id"]), periodo, sede or None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    finally:
        con.close()


@app.get("/api/ubicaciones")
async def ubicaciones_usuario(
    q: str = "", sede: str = "", usuario=Depends(usuario_actual)
) -> dict[str, Any]:
    con = _conexion()
    try:
        return {
            "ubicaciones": db.sugerencias_ubicacion(con, q, sede),
            "similares": db.ubicaciones_similares(con, q, sede),
        }
    finally:
        con.close()


@app.post("/api/equipos")
async def guardar_equipo_usuario(
    request: Request, entrada: EquipoEntrada,
    usuario=Depends(usuario_actual), _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    datos = entrada.model_dump(exclude_unset=True)
    sn = (datos.get("sn") or "").strip()
    if not db.normalizar_serial(sn):
        raise HTTPException(status_code=400, detail="Falta el serial.")
    con = _conexion()
    try:
        permitidas = None if usuario["role"] == "admin" else set(
            db.sedes_usuario(con, int(usuario["id"]))
        )
        # La autorización y la escritura observan la misma versión del equipo.
        con.execute("BEGIN IMMEDIATE")
        existente = db.buscar_equipo(con, sn)
        sede = (datos.get("sede") or "").strip()
        if existente:
            if permitidas is not None and (existente["sede"] or "") not in permitidas:
                raise HTTPException(status_code=403, detail="Esa sede no está asignada a tu usuario.")
            if not sede:
                sede = existente["sede"] or ""
            if permitidas is not None and sede not in permitidas:
                raise HTTPException(status_code=403, detail="No puedes mover el equipo a otra sede.")
        else:
            if permitidas is not None:
                if not sede and len(permitidas) == 1:
                    sede = next(iter(permitidas))
                if sede not in permitidas:
                    raise HTTPException(status_code=403, detail="La sede no está asignada a tu usuario.")
            if not sede:
                raise HTTPException(status_code=400, detail="Falta la sede.")
        datos["sede"] = sede
        permitidos = {c: datos.get(c, "") for c in db.CAMPOS_EQUIPO if c in datos}
        if not (permitidos.get("sede") or "").strip() and permitidos.get("domicilio"):
            permitidos["sede"] = permitidos["domicilio"]
        _validar_ubicacion_nueva(con, datos, permitidos.get("sede") or "")
        creado, sn = db.upsert_equipo(con, sn, **permitidos)
        ok, detalle, _ = _exportar_inventario(con)
        db.auditar(
            con, user_id=int(usuario["id"]), email=usuario["email"],
            action="inventario_usuario_alta" if creado else "inventario_usuario_edicion",
            ip=ip_cliente(request), detail=f"{sn} · {sede}",
        )
        return {"ok": ok, "sn": sn, "creado": creado,
                "total": db.contar_equipos(con), "exportado": ok,
                "detalle": detalle}
    finally:
        con.close()


@app.post("/api/jobs", status_code=201)
async def crear_job(
    request: Request,
    archivo: UploadFile = File(...),
    reprocesar: bool = Form(False),
    usuario=Depends(usuario_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    uid = int(usuario["id"])
    if not limite_subidas.permitir(uid):
        raise _error_subida(
            429, "limite_subidas",
            "Demasiadas subidas en poco tiempo. Espera unos minutos y vuelve a intentarlo.",
        )

    try:
        contenido = await archivo.read(settings.max_upload_bytes + 1)
    except Exception:
        raise _error_subida(
            400, "subida_interrumpida",
            "La subida se interrumpió antes de recibir el archivo completo. "
            "Revisa tu conexión y vuelve a intentarlo.",
        ) from None
    if len(contenido) > settings.max_upload_bytes:
        raise _error_subida(
            413, "pdf_demasiado_grande",
            f"El PDF supera el máximo de {settings.max_upload_mb} MB.",
        )
    huella = hashlib.sha256(contenido).hexdigest()
    con = _conexion()
    try:
        previo = db.job_por_hash(con, uid, huella)
        if previo and not reprocesar:
            raise HTTPException(status_code=409, detail={
                "codigo": "pdf_repetido", "job_id": previo["id"],
                "mensaje": "Este mismo PDF ya está en tus documentos. ¿Quieres procesarlo otra vez?",
            })
    finally:
        con.close()
    # PyMuPDF es CPU-bound: no bloquear el event loop con un PDF grande.
    paginas, avisos_pdf = await run_in_threadpool(_validar_pdf, contenido)
    riesgos = sorted(set(avisos_pdf) & _AVISOS_BLOQUEANTES)
    if riesgos:
        raise _error_subida(
            400, "pdf_contenido_activo",
            "El PDF contiene contenido activo de alto riesgo ("
            + ", ".join(riesgos)
            + "). Por seguridad no se acepta; exporta una copia plana del documento.",
        )
    nombre = _nombre_seguro(archivo.filename)

    con = _conexion()
    try:
        if db.contar_activos(con, uid) >= settings.max_queued_jobs:
            raise _error_subida(
                429, "demasiados_activos",
                f"Ya tienes {settings.max_queued_jobs} documentos en cola. "
                "Espera a que terminen antes de subir más.",
            )
        consumidas = db.contar_paginas_hoy(con, uid)
        if consumidas + paginas > settings.max_pages_per_day:
            raise _error_subida(
                429, "limite_paginas",
                f"Superarías el límite diario de {settings.max_pages_per_day} páginas.",
            )
    finally:
        con.close()

    job_id = uuid.uuid4().hex
    job_dir = _job_dir(uid, job_id)
    entrada = job_dir / "input"
    job_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    entrada.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(job_dir, 0o700)
    os.chmod(entrada, 0o700)
    stored_name = f"{job_id}.pdf"
    ruta_pdf = entrada / stored_name
    ruta_pdf.write_bytes(contenido)
    # El PDF es dato, nunca ejecutable, y solo lo lee el uid del panel/worker.
    os.chmod(ruta_pdf, 0o600)

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
            file_hash=huella,
        )
        db.auditar(
            con,
            user_id=uid,
            email=usuario["email"],
            action="upload",
            job_id=job_id,
            ip=ip_cliente(request),
            detail=(
                f"{nombre} · {paginas} págs."
                + (f" · avisos PDF: {', '.join(avisos_pdf)}" if avisos_pdf else "")
            ),
        )
        promedio = db.promedio_segundos_por_pagina(con)
        stats = db.estadisticas_cola(con, job, promedio)
        respuesta = _job_publico(job, stats)
        if avisos_pdf:
            respuesta["advertencias"] = avisos_pdf
        return respuesta
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


def _inventario_normalizado(con) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
    """Índices primario y alterno O→0 del inventario, sin fusionar filas."""
    primario: dict[str, list[Any]] = {}
    alterno: dict[str, list[Any]] = {}
    for fila in con.execute("SELECT sn, sede FROM equipos").fetchall():
        primario.setdefault(normalizar_serial(fila["sn"]), []).append(fila)
        alterno.setdefault(normalizar_serial_alterno(fila["sn"]), []).append(fila)
    return primario, alterno


@app.get("/api/jobs/{job_id}/resultado")
async def resultado_job(
    job_id: str, usuario=Depends(usuario_actual)
) -> dict[str, Any]:
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id, int(usuario["id"]))
        if not job:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        if job["status"] != "done" or not job["result_path"]:
            raise HTTPException(status_code=409, detail="El resultado todavía no está listo.")
        filas = con.execute(
            "SELECT estado, sn, sede FROM job_pages WHERE job_id=?", (job_id,)
        ).fetchall()
        por_estado: dict[str, int] = {}
        seriales: set[str] = set()
        sede_lectura: dict[str, str] = {}
        for fila in filas:
            estado = (fila["estado"] or "SIN_ESTADO").upper()
            por_estado[estado] = por_estado.get(estado, 0) + 1
            if fila["sn"] and estado != "DESCARTADA":
                seriales.add(fila["sn"])
                if fila["sede"]:
                    sede_lectura.setdefault(fila["sn"], fila["sede"])
        primario, alterno = _inventario_normalizado(con)
        es_admin = usuario["role"] == "admin"
        mis_sedes = (db.listar_sedes(con) if es_admin
                     else db.sedes_usuario(con, int(usuario["id"])))
        no_inventario: list[str] = []
        desconocidos: list[dict[str, Any]] = []
        otra_sede: list[dict[str, Any]] = []
        sin_sede: list[str] = []
        ambiguos: list[str] = []
        for sn in sorted(seriales):
            coincidencias = primario.get(normalizar_serial(sn), [])
            if not coincidencias:
                alterna = normalizar_serial_alterno(sn)
                if alterna != normalizar_serial(sn):
                    coincidencias = alterno.get(alterna, [])
            if len(coincidencias) == 1:
                fila = coincidencias[0]
                sede = fila["sede"] or ""
                if not sede:
                    sin_sede.append(fila["sn"])
                elif not es_admin and sede not in mis_sedes:
                    otra_sede.append({"sn": fila["sn"], "sede": sede})
                continue
            if len(coincidencias) > 1:
                ambiguos.append(sn)
                continue
            no_inventario.append(sn)
            sugerida = sede_lectura.get(sn, "")
            if sugerida not in mis_sedes:
                sugerida = mis_sedes[0] if mis_sedes else ""
            desconocidos.append({"sn": sn, "sede_sugerida": sugerida})
    finally:
        con.close()

    ruta = Path(job["result_path"]).resolve()
    if not ruta.is_relative_to(settings.data_dir.resolve()) or not ruta.is_file():
        raise HTTPException(status_code=404, detail="Resultado no encontrado.")
    documentos = []
    try:
        with zipfile.ZipFile(ruta) as z:
            for info in z.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                    continue
                partes = Path(info.filename).parts
                estado = "POR_VERIFICAR" if "POR_VERIFICAR" in partes else (
                    "VERIFICADO" if "VERIFICADOS" in partes else "OTRO"
                )
                nombre = Path(info.filename).name
                documentos.append({
                    "nombre": nombre,
                    "ruta": info.filename,
                    "bytes": info.file_size,
                    "estado": estado,
                    "serial": Path(nombre).stem.split("__", 1)[0],
                })
    except (OSError, zipfile.BadZipFile):
        raise HTTPException(status_code=500, detail="No se pudo abrir el ZIP.") from None
    documentos.sort(key=lambda d: (d["estado"], d["serial"], d["nombre"]))
    return {
        "job": {"id": job_id, "filename": job["filename"], "pages": int(job["pages"] or 0)},
        "resumen": {
            "por_estado": por_estado,
            "seriales": len(seriales),
            "no_inventario": no_inventario,
            "desconocidos": desconocidos,
            "otra_sede": otra_sede,
            "sin_sede": sin_sede,
            "ambiguos": ambiguos,
            "puede_agregar": bool(mis_sedes),
            "mis_sedes": mis_sedes,
            "mensaje_otra_sede": (
                "Algunos seriales pertenecen a otra sede. No puedes agregarlos "
                "ni modificarlos; pide apoyo a administración."
            ) if otra_sede else "",
        },
        "documentos": documentos,
    }


@app.get("/api/jobs/{job_id}/documento")
async def descargar_documento(
    request: Request, job_id: str, ruta: str = "",
    usuario=Depends(usuario_actual),
):
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id, int(usuario["id"]))
        if not job or job["status"] != "done" or not job["result_path"]:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        zip_path = Path(job["result_path"]).resolve()
        if not zip_path.is_relative_to(settings.data_dir.resolve()) or not zip_path.is_file():
            raise HTTPException(status_code=404, detail="Resultado no encontrado.")
        with zipfile.ZipFile(zip_path) as z:
            try:
                info = z.getinfo(ruta)
            except KeyError:
                raise HTTPException(status_code=404, detail="Hoja no encontrada.") from None
            if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=404, detail="Archivo inválido.")
            contenido = z.read(info)
        db.auditar(
            con, user_id=int(usuario["id"]), email=usuario["email"],
            action="download_documento", job_id=job_id,
            ip=ip_cliente(request), detail=info.filename,
        )
    finally:
        con.close()
    nombre = Path(info.filename).name
    return Response(
        content=contenido, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


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


def _render_pagina_pdf(pdf: Path, pag: int) -> bytes:
    doc = fitz.open(pdf)
    try:
        page = doc.load_page(pag - 1)
        return page.get_pixmap(dpi=110).tobytes("png")
    finally:
        doc.close()


def _importar_inventario_archivo(tmp: Path) -> dict[str, int]:
    con = db.conectar(settings.db_path)
    try:
        return inventario.importar_xlsx(con, tmp)
    finally:
        con.close()


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


def _eventos_ocr_jsonl(job_dir: Path, job_id: str) -> list[dict[str, Any]]:
    ruta = job_dir / "progress" / f"{job_id}.ocr.jsonl"
    if not ruta.is_file():
        return []
    eventos: list[dict[str, Any]] = []
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
        return []
    return eventos


@app.get("/api/admin/jobs/{job_id}/ocr/{pag}")
async def admin_job_ocr(
    job_id: str, pag: int, admin=Depends(admin_actual)
) -> dict[str, Any]:
    """Texto y cajas OCR de una hoja, para diagnosticar qué leyó el motor."""
    con = _conexion()
    try:
        job = db.job_por_id(con, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Documento no encontrado.")
        total = int(job["pages"] or 0)
        if pag < 1 or pag > total:
            raise HTTPException(status_code=404, detail="Página fuera de rango.")
        fila = con.execute(
            "SELECT texto, sn, estado, worker, ms FROM job_pages"
            " WHERE job_id=? AND pag=?",
            (job_id, pag),
        ).fetchone()
    finally:
        con.close()

    job_dir = _job_dir(int(job["owner_id"]), job_id)
    eventos = [
        ev for ev in _eventos_ocr_jsonl(job_dir, job_id)
        if int(ev.get("pag") or 0) == pag
        and ev.get("tipo") in ("ocr_pagina", "ocr_banda")
    ]
    finales = [
        ev for ev in eventos
        if ev.get("tipo") == "ocr_pagina" and not ev.get("parcial")
    ]
    elegidos: list[dict[str, Any]] = []
    parcial = False
    if finales:
        elegidos = [max(finales, key=lambda ev: float(ev.get("ts") or 0))]
        parcial = False
    elif eventos:
        elegidos = sorted(
            eventos,
            key=lambda ev: (
                int(ev.get("banda") or 0),
                float(ev.get("ts") or 0),
            ),
        )
        parcial = True

    texto = " ".join(
        (ev.get("texto") or "").strip() for ev in elegidos if ev.get("texto")
    ).strip()
    cajas: list[dict[str, Any]] = []
    for ev in elegidos:
        for caja in ev.get("cajas") or []:
            if isinstance(caja, dict):
                cajas.append(caja)
    cajas = cajas[:120]
    fuente = "ocr_jsonl" if elegidos else "db"
    return {
        "job_id": job_id,
        "pag": pag,
        "pag_total": total,
        "texto": texto or (fila["texto"] if fila else "") or "",
        "cajas": cajas,
        "sn": (elegidos[-1].get("sn") if elegidos else None)
              or (fila["sn"] if fila else "") or "",
        "estado": (fila["estado"] if fila else "") or "",
        "worker": (elegidos[-1].get("worker") if elegidos else None)
                  or (fila["worker"] if fila else "") or "",
        "ms": (elegidos[-1].get("ms") if elegidos else
               (fila["ms"] if fila else None)),
        "parcial": parcial,
        "fuente": fuente,
    }


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
        # Renderizar en hilo aparte: un PDF grande no debe congelar la API.
        contenido = await run_in_threadpool(_render_pagina_pdf, pdf, pag)
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
    zona: str = "", piso: str = "", limite: int = 100, offset: int = 0,
    orden: str = "sn",
    admin=Depends(admin_actual),
) -> dict[str, Any]:
    con = _conexion()
    try:
        filtros = {"q": q or None, "sede": sede or None, "marca": marca or None,
                   "estatus": estatus or None, "zona": zona or None,
                   "piso": piso or None}
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
                "pisos": db.valores_equipos(con, "piso"),
            },
        }
    finally:
        con.close()


@app.post("/api/admin/equipos")
async def admin_guardar_equipo(
    request: Request, entrada: EquipoEntrada,
    admin=Depends(admin_actual), _csrf=Depends(verificar_csrf)
) -> dict[str, Any]:
    datos = entrada.model_dump(exclude_unset=True)
    sn = (datos.get("sn") or "").strip()
    if not sn:
        raise HTTPException(status_code=400, detail="Falta el serial.")
    con = _conexion()
    try:
        permitidos = {c: datos.get(c, "") for c in db.CAMPOS_EQUIPO if c in datos}
        # SEDE vacía se deriva del domicilio, igual que el pipeline local.
        if not (permitidos.get("sede") or "").strip() and permitidos.get("domicilio"):
            permitidos["sede"] = permitidos["domicilio"]
        _validar_ubicacion_nueva(con, datos, permitidos.get("sede") or "")
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


@app.post("/api/admin/equipos/borrar")
async def admin_borrar_equipos(
    request: Request, entrada: BorradoEquiposEntrada,
    admin=Depends(admin_actual), _csrf=Depends(verificar_csrf)
) -> dict[str, Any]:
    datos = entrada.model_dump(exclude_unset=True)
    if (datos.get("confirm") or "").strip().upper() != "BORRAR":
        raise HTTPException(
            status_code=400,
            detail="Confirma la operación escribiendo BORRAR.",
        )
    filtros = {
        "q": (datos.get("q") or "").strip() or None,
        "sede": (datos.get("sede") or "").strip() or None,
        "marca": (datos.get("marca") or "").strip() or None,
        "estatus": (datos.get("estatus") or "").strip() or None,
        "zona": (datos.get("zona") or "").strip() or None,
        "piso": (datos.get("piso") or "").strip() or None,
    }
    con = _conexion()
    try:
        antes = db.contar_equipos(con)
        borrados = db.borrar_equipos(con, **filtros)
        ok, detalle, _ = _exportar_inventario(con)
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="inventario_borrado_masivo", ip=ip_cliente(request),
            detail=f"{borrados} de {antes} · {filtros}",
        )
        return {"ok": ok, "borrados": borrados, "antes": antes,
                "total": db.contar_equipos(con), "exportado": ok,
                "detalle": detalle, "filtros": filtros}
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
        resultado = await run_in_threadpool(_importar_inventario_archivo, tmp)
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


@app.get("/api/admin/usuarios")
async def admin_usuarios(admin=Depends(admin_actual)) -> dict[str, Any]:
    con = _conexion()
    try:
        return {
            "usuarios": db.listar_usuarios(con),
            "sedes": db.listar_sedes(con),
        }
    finally:
        con.close()


@app.put("/api/admin/usuarios/{uid}")
async def admin_guardar_usuario(
    uid: int, request: Request, admin=Depends(admin_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    datos = await request.json()
    sedes = datos.get("sedes")
    if not isinstance(sedes, list):
        raise HTTPException(status_code=400, detail="Envia una lista de sedes.")
    con = _conexion()
    try:
        usuario = db.usuario_por_id(con, uid)
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        validas = set(db.listar_sedes(con))
        pedidas = [str(s).strip() for s in sedes if str(s).strip()]
        if any(s not in validas for s in pedidas):
            raise HTTPException(status_code=400, detail="Hay una sede que no existe en el inventario.")
        db.asignar_sedes(con, uid, pedidas)
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="usuario_sedes", ip=ip_cliente(request),
            detail=f"{usuario['email']}: {', '.join(sorted(pedidas))}",
        )
        return {"ok": True, "usuario": db.usuario_por_id(con, uid)["email"],
                "sedes": db.sedes_usuario(con, uid)}
    finally:
        con.close()


@app.get("/api/admin/ubicaciones")
async def admin_ubicaciones(
    q: str = "", sede: str = "", admin=Depends(admin_actual)
) -> dict[str, Any]:
    con = _conexion()
    try:
        return {
            "ubicaciones": db.sugerencias_ubicacion(con, q, sede),
            "similares": db.ubicaciones_similares(con, q, sede),
        }
    finally:
        con.close()


@app.post("/api/admin/cobertura/excluir")
async def admin_excluir_cobertura(
    request: Request, admin=Depends(admin_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    datos = await request.json()
    try:
        cid = int(datos.get("id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        raise HTTPException(status_code=400, detail="Escaneo inválido.")
    con = _conexion()
    try:
        ok = db.excluir_cobertura(con, cid, str(datos.get("motivo") or ""))
        if not ok:
            raise HTTPException(status_code=404, detail="Escaneo no encontrado.")
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="cobertura_excluir", ip=ip_cliente(request), detail=str(cid),
        )
        return {"ok": True}
    finally:
        con.close()


@app.post("/api/admin/cobertura/reiniciar")
async def admin_reiniciar_cobertura(
    request: Request, admin=Depends(admin_actual),
    _csrf=Depends(verificar_csrf),
) -> dict[str, Any]:
    datos = await request.json()
    periodo = str(datos.get("periodo") or "")
    sede = str(datos.get("sede") or "")
    if not re.fullmatch(r"\d{4}-\d{2}", periodo) or not sede:
        raise HTTPException(status_code=400, detail="Periodo y sede son obligatorios.")
    if str(datos.get("confirm") or "").strip().upper() != "REINICIAR":
        raise HTTPException(status_code=400, detail="Escribe REINICIAR para confirmar.")
    con = _conexion()
    try:
        borrados = db.reiniciar_cobertura(con, periodo, sede)
        db.auditar(
            con, user_id=int(admin["id"]), email=admin["email"],
            action="cobertura_reiniciar", ip=ip_cliente(request),
            detail=f"{periodo} {sede}: {borrados}",
        )
        return {"ok": True, "borrados": borrados}
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
@app.get("/static/admin.html", include_in_schema=False)
async def admin_html_no_publico():
    # La consola solo se entrega por /admin, que valida la sesión y el rol.
    raise HTTPException(status_code=404, detail="No encontrado.")


app.mount(
    "/static",
    StaticFiles(directory=str(settings.static_dir)),
    name="static",
)
