"""Router del separador de PDFs.

Expone el flujo asíncrono: subir → encolar → consultar estado → descargar.

El identificador que ve el usuario es el **job id de RQ**, no uno propio: así
hay una sola fuente de verdad y no hay que mantener un mapa entre ambos.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import obtener_settings
from app.core.seguridad import ArchivoInvalidoError, validar_archivo
from app.jobs.cola import cola_pesada
from app.jobs.tareas import procesar_separador

logger = logging.getLogger(__name__)

router = APIRouter()

# Tipos aceptados por el separador.
TIPOS_PDF = {"application/pdf"}


class RespuestaEncolado(BaseModel):
    """Confirmación de que el trabajo quedó en la cola."""

    job_id: str
    mensaje: str


class EstadoJob(BaseModel):
    """Estado actual de un trabajo encolado."""

    job_id: str
    estado: str          # queued | started | finished | failed
    resultado: dict | None = None
    error: str | None = None


@router.post("/separador", response_model=RespuestaEncolado, status_code=202)
async def subir_pdf(archivo: UploadFile = File(...)) -> RespuestaEncolado:
    """Recibe un PDF y lo encola para separación por OCR.

    Responde 202 (Accepted) de inmediato: el procesamiento real ocurre en el
    worker OCR y puede tardar minutos.
    """
    settings = obtener_settings()

    contenido = await archivo.read()

    try:
        validado = validar_archivo(
            archivo.filename or "",
            contenido,
            tipos_permitidos=TIPOS_PDF,
            max_bytes=settings.max_upload_bytes,
        )
    except ArchivoInvalidoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Un solo identificador para todo: el que devuelve el POST, la carpeta de
    # salida del worker y la descarga. Se usa el mismo id que asigna RQ
    # (`job_id=`), en vez de generar uno propio aparte.
    nombre_entrada = uuid.uuid4().hex[:12]

    ruta_entrada = settings.upload_dir / f"separador-{nombre_entrada}.pdf"
    ruta_entrada.write_bytes(validado.contenido)

    job = cola_pesada().enqueue(
        procesar_separador,
        str(ruta_entrada),
        id_trabajo=nombre_entrada,
        job_id=nombre_entrada,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=86400,  # el resultado se conserva 24 h
    )

    job_id = job.id

    logger.info("Separador encolado: job=%s archivo=%s", job_id, validado.nombre)

    return RespuestaEncolado(
        job_id=job_id,
        mensaje=(
            "PDF recibido. El OCR puede tardar varios minutos en función del "
            "número de páginas. Puedes cerrar esta pestaña: el trabajo continúa."
        ),
    )


@router.get("/separador/{job_id}", response_model=EstadoJob)
async def consultar_estado(job_id: str) -> EstadoJob:
    """Consulta el estado de un trabajo de separación."""
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    _validar_job_id(job_id)

    from app.jobs.cola import obtener_conexion

    try:
        job = Job.fetch(job_id, connection=obtener_conexion())
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.") from None
    except Exception as exc:
        logger.exception("Error consultando job %s", job_id)
        raise HTTPException(
            status_code=500, detail="No se pudo consultar el estado del trabajo."
        ) from exc

    return _estado_desde_job(job_id, job.get_status(), job.result, job.exc_info)


@router.get("/separador/descargar/{carpeta}/{archivo}")
async def descargar(carpeta: str, archivo: str) -> FileResponse:
    """Sirve el ZIP de resultados de un trabajo."""
    settings = obtener_settings()

    _validar_nombre_seguro(carpeta)
    _validar_nombre_seguro(archivo)

    if not archivo.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Solo se sirven archivos ZIP.")

    ruta = (settings.output_dir / carpeta / archivo).resolve()
    base = settings.output_dir.resolve()

    # Confirma que la ruta resuelta no escapó del directorio de salidas.
    if not ruta.is_relative_to(base):
        raise HTTPException(status_code=400, detail="Ruta fuera del directorio permitido.")

    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")

    return FileResponse(path=ruta, media_type="application/zip", filename=archivo)


def _validar_job_id(job_id: str) -> None:
    """Valida que el id tenga un formato plausible de RQ."""
    if not job_id or len(job_id) > 64:
        raise HTTPException(status_code=400, detail="Identificador de trabajo no válido.")
    if not all(c.isalnum() or c in "-_" for c in job_id):
        raise HTTPException(status_code=400, detail="Identificador de trabajo no válido.")


def _validar_nombre_seguro(nombre: str) -> None:
    """Rechaza nombres con separadores de ruta o traversal."""
    if not nombre or any(c in nombre for c in ("/", "\\", "..", "\x00")):
        raise HTTPException(status_code=400, detail="Nombre no válido.")


def _estado_desde_job(
    job_id: str, estado_rq: str, resultado: object, exc_info: str | None
) -> EstadoJob:
    """Traduce el estado crudo de RQ a nuestro modelo."""
    mapa = {
        "queued": "queued",
        "deferred": "queued",
        "scheduled": "queued",
        "started": "started",
        "finished": "finished",
        "failed": "failed",
        "stopped": "failed",
        "canceled": "failed",
    }
    estado = mapa.get(estado_rq, "queued")

    error = None
    if estado == "failed":
        # No exponer el traceback completo: puede filtrar rutas internas.
        error = "El procesamiento falló. Revisa los registros del worker."

    return EstadoJob(
        job_id=job_id,
        estado=estado,
        resultado=resultado if isinstance(resultado, dict) else None,
        error=error,
    )
