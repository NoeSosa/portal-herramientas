"""Router de la herramienta «Transcribir audio o video».

Trabajo de minutos ⇒ cola pesada: subir → encolar → consultar → descargar.

El resultado es texto plano, que se guarda en `outputs/<job_id>/transcripcion.txt`
para que la descarga use el mismo endpoint de archivos que el separador (solo
que aquí el archivo es .txt).

Nota de diseño: el texto se devuelve **también** en la respuesta de estado. La
descarga existe para quien quiera el archivo, pero leer el resultado no debería
obligar a bajar nada.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import obtener_settings
from app.core.seguridad import ArchivoInvalidoError, validar_archivo
from app.jobs.cola import cola_pesada
from app.jobs.tareas import procesar_transcripcion
from app.services import transcripcion

logger = logging.getLogger(__name__)

router = APIRouter()

MOTORES = {"auto", "gemini", "whisper"}
IDIOMAS_PERMITIDOS = {"", "es", "en", "pt", "fr", "de", "it"}

NOMBRE_TEXTO = "transcripcion.txt"


class RespuestaEncolado(BaseModel):
    """Confirmación de que el trabajo quedó en la cola."""

    job_id: str
    mensaje: str


class EstadoTranscripcion(BaseModel):
    """Estado del trabajo, con el texto cuando ya está listo."""

    job_id: str
    estado: str
    texto: str | None = None
    caracteres: int | None = None
    archivo: str | None = None
    error: str | None = None


@router.get("/transcribir/estado")
async def estado_motor() -> dict[str, object]:
    """Informa si el transcriptor puede operar."""
    settings = obtener_settings()
    disponible, motivo = transcripcion.disponible(settings)

    return {
        "disponible": disponible,
        "motivo": motivo,
        "tipos": sorted(transcripcion.TIPOS_AUDIO_VIDEO),
        "max_upload_mb": settings.max_upload_mb,
        "motores": sorted(MOTORES),
        "idiomas": sorted(i for i in IDIOMAS_PERMITIDOS if i),
    }


@router.post("/transcribir", response_model=RespuestaEncolado, status_code=202)
async def subir_archivo(
    archivo: UploadFile = File(...),
    idioma: str = Form(default=""),
    motor: str = Form(default="auto"),
) -> RespuestaEncolado:
    """Recibe un audio o video y lo encola para transcripción."""
    settings = obtener_settings()

    disponible, motivo = transcripcion.disponible(settings)
    if not disponible:
        raise HTTPException(
            status_code=503,
            detail=f"El transcriptor no está disponible. {motivo}",
        )

    if motor not in MOTORES:
        raise HTTPException(
            status_code=400,
            detail=f"Motor no válido. Opciones: {', '.join(sorted(MOTORES))}.",
        )

    idioma = idioma.strip().lower()
    if idioma not in IDIOMAS_PERMITIDOS:
        raise HTTPException(
            status_code=400,
            detail="Idioma no soportado. Déjalo vacío para autodetección.",
        )

    contenido = await archivo.read()

    try:
        validado = validar_archivo(
            archivo.filename or "",
            contenido,
            tipos_permitidos=transcripcion.TIPOS_AUDIO_VIDEO,
            max_bytes=settings.max_upload_bytes,
        )
    except ArchivoInvalidoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sufijo = transcripcion.SUFIJO_POR_TIPO[validado.tipo_detectado]
    job_id = uuid.uuid4().hex[:12]

    ruta_entrada = settings.upload_dir / f"transcribir-{job_id}{sufijo}"
    ruta_entrada.write_bytes(validado.contenido)

    cola_pesada().enqueue(
        procesar_transcripcion,
        str(ruta_entrada),
        job_id=job_id,
        idioma=idioma or None,
        motor=motor,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=86400,
    )

    logger.info(
        "Transcripción encolada: job=%s tipo=%s bytes=%d",
        job_id,
        validado.tipo_detectado,
        validado.tamano_bytes,
    )

    aviso = ""
    if sufijo in transcripcion.FORMATOS_CON_EXTRACCION:
        aviso = " Es video: primero se extrae la pista de audio."

    return RespuestaEncolado(
        job_id=job_id,
        mensaje=(
            "Archivo recibido. La transcripción puede tardar varios minutos."
            f"{aviso} Puedes cerrar esta pestaña: el trabajo continúa."
        ),
    )


@router.get("/transcribir/{job_id}", response_model=EstadoTranscripcion)
async def consultar_estado(job_id: str) -> EstadoTranscripcion:
    """Consulta el estado de una transcripción."""
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    from app.jobs.cola import obtener_conexion

    _validar_job_id(job_id)

    try:
        job = Job.fetch(job_id, connection=obtener_conexion())
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.") from None
    except Exception as exc:
        logger.exception("Error consultando job %s", job_id)
        raise HTTPException(
            status_code=500, detail="No se pudo consultar el estado del trabajo."
        ) from exc

    estado = _estado_rq(job.get_status())
    resultado = job.result if isinstance(job.result, dict) else None

    if estado == "finished" and resultado:
        if not resultado.get("ok", False):
            return EstadoTranscripcion(
                job_id=job_id,
                estado="failed",
                error=str(resultado.get("error", "La transcripción falló."))[:400],
            )

        texto = str(resultado.get("texto", ""))
        return EstadoTranscripcion(
            job_id=job_id,
            estado="finished",
            texto=texto,
            caracteres=resultado.get("caracteres") or len(texto),
            archivo=NOMBRE_TEXTO,
        )

    if estado == "failed":
        return EstadoTranscripcion(
            job_id=job_id,
            estado="failed",
            error=(
                "La transcripción falló. Revisa el formato del archivo o "
                "inténtalo con un fragmento más corto."
            ),
        )

    return EstadoTranscripcion(job_id=job_id, estado=estado)


@router.get("/transcribir/descargar/{job_id}")
async def descargar(job_id: str) -> FileResponse:
    """Devuelve el texto transcrito como archivo."""
    settings = obtener_settings()

    _validar_job_id(job_id)

    ruta = (settings.output_dir / job_id / NOMBRE_TEXTO).resolve()

    if not ruta.is_relative_to(settings.output_dir.resolve()):
        raise HTTPException(status_code=400, detail="Ruta fuera del directorio permitido.")

    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Transcripción no encontrada.")

    return FileResponse(
        path=ruta,
        media_type="text/plain; charset=utf-8",
        filename=f"transcripcion-{job_id}.txt",
    )


def _validar_job_id(job_id: str) -> None:
    """Acepta solo identificadores con el formato que genera este portal."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id or ""):
        raise HTTPException(status_code=400, detail="Identificador no válido.")


def _estado_rq(estado: str) -> str:
    """Traduce los estados de RQ a los cuatro que ve el usuario."""
    return {
        "queued": "queued",
        "deferred": "queued",
        "started": "started",
        "finished": "finished",
        "failed": "failed",
        "stopped": "failed",
        "canceled": "failed",
    }.get(estado, "queued")
