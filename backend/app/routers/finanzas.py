"""Router de la herramienta «Finanzas personales».

Dos flujos:

  * **Panel** (`GET /api/finanzas/panel?periodo=…`): resumen del periodo,
    presupuestos, cuentas y tarjetas en una sola respuesta. El frontend no
    tiene que encadenar cuatro llamadas.
  * **Comprobante** (`GET /api/finanzas/comprobante/estado`, `POST …`,
    `POST …/registrar`): analizar la foto de un ticket primero, mostrarla al
    usuario, y registrar solo si confirma.

Registrar dinero es una acción con consecuencias: el portal nunca registra en
el mismo paso en que analiza.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import obtener_settings
from app.core.seguridad import ArchivoInvalidoError, validar_archivo
from app.services import finanzas as svc
from app.services import puente, vision

logger = logging.getLogger(__name__)

router = APIRouter()

# Tipos aceptados para un comprobante: fotos de ticket. PDF queda fuera a
# propósito (habría que renderizarlo antes de mandarlo a visión).
TIPOS_COMPROBANTE = {"image/jpeg", "image/png", "image/webp"}


class PanelFinanzas(BaseModel):
    """Todo lo que muestra el panel, en una respuesta."""

    usuario: str
    periodo: str
    resumen: dict
    presupuesto: dict
    cuentas: dict
    tarjetas: dict
    metas: dict | None = None


class FilaComprobante(BaseModel):
    """Un comprobante analizado."""

    estado: str
    datos: dict | None = None
    mensaje: str | None = None


class RespuestaAnalisis(BaseModel):
    """Resultado del análisis previo al registro."""

    analizado: bool
    registrado: bool
    filas: list[FilaComprobante]
    resumen: str


@router.get("/finanzas/estado")
async def estado_motor() -> dict[str, object]:
    """Informa si el panel puede leer el ledger."""
    settings = obtener_settings()
    disponible, motivo = svc.disponible(settings)

    return {
        "disponible": disponible,
        "motivo": motivo,
        "usuario": settings.finanzas_usuario,
        "periodos": sorted(svc.PERIODOS),
        "comprobante": bool(settings.gemini_api_key),
    }


@router.get("/finanzas/panel", response_model=PanelFinanzas)
async def panel(periodo: str = "mensual") -> PanelFinanzas:
    """Devuelve el panel completo del periodo pedido."""
    settings = obtener_settings()

    disponible, motivo = svc.disponible(settings)
    if not disponible:
        raise HTTPException(
            status_code=503,
            detail=f"El panel de finanzas no está disponible. {motivo}",
        )

    if periodo not in svc.PERIODOS:
        raise HTTPException(
            status_code=400,
            detail=f"Periodo no válido. Opciones: {', '.join(sorted(svc.PERIODOS))}.",
        )

    def _leer() -> PanelFinanzas:
        resumen = svc.consultar("resumen", periodo=periodo, settings=settings)
        presupuesto = svc.consultar("presupuesto", settings=settings)
        cuentas = svc.consultar("cuentas", settings=settings)
        tarjetas = svc.consultar("tarjetas", settings=settings)

        metas: dict | None
        try:
            metas = svc.consultar("metas", settings=settings)
        except svc.ErrorFinanzas:
            # Las metas son un extra del panel: si fallan, no se cae el resto.
            logger.warning("No se pudieron leer las metas; se omite esa sección.")
            metas = None

        return PanelFinanzas(
            usuario=settings.finanzas_usuario,
            periodo=periodo,
            resumen=resumen,
            presupuesto=presupuesto,
            cuentas=cuentas,
            tarjetas=tarjetas,
            metas=metas,
        )

    try:
        return await asyncio.to_thread(_leer)
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/finanzas/movimientos")
async def movimientos(
    buscar: str = "",
    categoria: str = "",
    limite: int = 50,
) -> dict:
    """Lista los movimientos recientes con filtros opcionales."""
    settings = obtener_settings()

    try:
        return await asyncio.to_thread(
            svc.consultar,
            "listar",
            buscar=buscar or None,
            categoria=categoria or None,
            limite=max(1, min(limite, 500)),
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/finanzas/comprobante/estado")
async def estado_comprobante() -> dict[str, object]:
    """Informa si se puede analizar un comprobante."""
    settings = obtener_settings()
    disponible, motivo = svc.disponible(settings)

    if not settings.gemini_api_key:
        disponible, motivo = False, (
            "Falta GEMINI_API_KEY: sin visión no se puede leer el ticket."
        )

    return {
        "disponible": disponible,
        "motivo": motivo,
        "tipos": sorted(TIPOS_COMPROBANTE),
        "max_upload_mb": settings.max_upload_mb,
    }


@router.post("/finanzas/comprobante", response_model=RespuestaAnalisis)
async def analizar_comprobante(
    archivo: UploadFile = File(...),
    cuenta: str = Form(default=""),
) -> RespuestaAnalisis:
    """Analiza la foto de un ticket **sin** registrar nada.

    Devuelve lo que la visión entendió para que el usuario lo revise. El
    registro es un segundo paso explícito.
    """
    settings = obtener_settings()

    disponible, motivo = svc.disponible(settings)
    if not disponible:
        raise HTTPException(status_code=503, detail=f"Finanzas no disponible. {motivo}")

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="Falta GEMINI_API_KEY: sin visión no se puede leer el ticket.",
        )

    contenido = await archivo.read()

    try:
        validado = validar_archivo(
            archivo.filename or "",
            contenido,
            tipos_permitidos=TIPOS_COMPROBANTE,
            max_bytes=settings.max_upload_bytes,
        )
    except ArchivoInvalidoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sufijo = vision.SUFIJO_POR_TIPO[validado.tipo_detectado]
    ruta = puente.guardar_subida(validado.contenido, sufijo, settings=settings)

    try:
        datos = await asyncio.to_thread(
            svc.analizar_comprobante,
            ruta,
            cuenta=cuenta or None,
            registrar=False,  # análisis previo, sin tocar el ledger
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        puente.borrar_silencioso(ruta)

    return _traducir_analisis(datos, registrado=False)


@router.post("/finanzas/comprobante/registrar", response_model=RespuestaAnalisis)
async def registrar_comprobante(
    archivo: UploadFile = File(...),
    cuenta: str = Form(default=""),
) -> RespuestaAnalisis:
    """Analiza el ticket y **registra** el movimiento en el ledger.

    Es el paso que modifica datos. El usuario llega aquí solo después de ver
    el análisis y confirmarlo en la interfaz.
    """
    settings = obtener_settings()

    disponible, motivo = svc.disponible(settings)
    if not disponible:
        raise HTTPException(status_code=503, detail=f"Finanzas no disponible. {motivo}")

    contenido = await archivo.read()

    try:
        validado = validar_archivo(
            archivo.filename or "",
            contenido,
            tipos_permitidos=TIPOS_COMPROBANTE,
            max_bytes=settings.max_upload_bytes,
        )
    except ArchivoInvalidoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sufijo = vision.SUFIJO_POR_TIPO[validado.tipo_detectado]
    ruta = puente.guardar_subida(validado.contenido, sufijo, settings=settings)

    try:
        datos = await asyncio.to_thread(
            svc.analizar_comprobante,
            ruta,
            cuenta=cuenta or None,
            registrar=True,
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        puente.borrar_silencioso(ruta)

    logger.info(
        "Comprobante registrado: %s movimiento(s)",
        datos.get("total", 0),
    )

    return _traducir_analisis(datos, registrado=True)


def _traducir_analisis(datos: dict, *, registrado: bool) -> RespuestaAnalisis:
    """Convierte la salida del script en la respuesta del portal.

    El script devuelve tres bolsas: `resultados` (registrados o analizados),
    `errores` y `necesita_preguntar`. Se aplanan a una lista de filas para que
    la interfaz las pinte sin lógica condicional.
    """
    filas: list[FilaComprobante] = []

    for resultado in datos.get("resultados", []):
        datos_mov = resultado.get("_datos") or {}
        filas.append(
            FilaComprobante(
                estado="registrado" if registrado else "listo",
                datos={
                    "monto": datos_mov.get("monto"),
                    "tipo": datos_mov.get("tipo"),
                    "concepto": datos_mov.get("concepto"),
                    "categoria": resultado.get("_categoria") or datos_mov.get("categoria_sugerida"),
                    "cuenta": resultado.get("cuenta") or datos_mov.get("cuenta_sugerida"),
                    "fecha": datos_mov.get("fecha"),
                },
            )
        )

    for fila in datos.get("necesita_preguntar", []):
        # Falta un dato clave (cuenta o visión). El script NO registró nada.
        filas.append(
            FilaComprobante(
                estado="falta-dato",
                datos=fila.get("datos") or None,
                mensaje=fila.get("message") or "Falta información para registrar.",
            )
        )

    for fila in datos.get("errores", []):
        filas.append(
            FilaComprobante(
                estado="descartado",
                mensaje=fila.get("message") or "No se reconoció como comprobante.",
            )
        )

    registrados = len(datos.get("resultados", []))
    pendientes = len(datos.get("necesita_preguntar", []))
    descartados = len(datos.get("errores", []))

    if registrado:
        resumen = f"{registrados} movimiento(s) registrado(s)"
    elif registrados:
        resumen = f"{registrados} comprobante(s) reconocido(s), sin registrar"
    else:
        resumen = "No se reconoció ningún comprobante en la imagen"

    if pendientes:
        resumen += f" · {pendientes} a completar a mano"
    if descartados:
        resumen += f" · {descartados} descartado(s)"

    return RespuestaAnalisis(
        analizado=True,
        registrado=registrado and registrados > 0,
        filas=filas,
        resumen=resumen,
    )
