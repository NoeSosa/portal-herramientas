"""Router de la herramienta «Finanzas personales».

Dos flujos:

  * **Panel** (`GET /api/finanzas/panel`): resumen, presupuestos, cuentas y
    tarjetas en una sola respuesta. El frontend no encadena cuatro llamadas.
  * **Movimientos**: listar, crear, editar y eliminar. El ledger se mantiene a
    mano desde el portal.

Límite de la herramienta, a propósito: **no mueve dinero**. No hay
transferencias, ni pagos de tarjeta, ni cambios de saldo automáticos. Escribir
en el registro sí; mover fondos no.

El borrado exige `confirmar=true` en el cuerpo: es la única operación sin
vuelta atrás, y el script no guarda historial de lo eliminado.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

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


class Movimiento(BaseModel):
    """Un movimiento del ledger tal como lo edita la interfaz."""

    id: str
    fecha: str | None = None
    tipo: str | None = None
    categoria: str | None = None
    monto: float | None = None
    nota: str | None = None
    cuenta: str | None = None


class ListaMovimientos(BaseModel):
    """Resultado del listado, con totales del filtro aplicado."""

    total_transacciones: int
    total_ingresos: float
    total_gastos: float
    balance: float
    transacciones: list[Movimiento]


class PeticionMovimiento(BaseModel):
    """Cuerpo para crear un movimiento."""

    monto: float = Field(..., gt=0)
    tipo: str = Field(..., pattern="^(ingreso|gasto)$")
    categoria: str = Field(..., min_length=1, max_length=60)
    nota: str = Field(default="", max_length=300)
    fecha: str | None = None
    cuenta: str | None = None


class PeticionEdicion(BaseModel):
    """Cuerpo para editar: solo se aplican los campos enviados."""

    monto: float | None = Field(default=None, gt=0)
    categoria: str | None = Field(default=None, min_length=1, max_length=60)
    nota: str | None = Field(default=None, max_length=300)
    fecha: str | None = None
    cuenta: str | None = None


class PeticionSaldo(BaseModel):
    """Cuerpo para fijar el saldo de una cuenta."""

    cuenta: str = Field(..., min_length=1, max_length=60)
    saldo: float
    tipo: str = Field(default="debito", pattern="^(debito|credito|fisico|prepago)$")


class PeticionPresupuesto(BaseModel):
    """Cuerpo para definir el límite mensual de una categoría."""

    categoria: str = Field(..., min_length=1, max_length=60)
    limite: float = Field(..., ge=0)


class ResultadoOperacion(BaseModel):
    """Confirmación de una escritura en el ledger."""

    ok: bool
    mensaje: str
    #: True si el cambio no pudo copiarse al workspace (el agente no lo verá).
    aviso_sincronizacion: str | None = None


@router.get("/finanzas/estado")
async def estado_motor() -> dict[str, object]:
    """Informa si el panel puede leer y escribir el ledger."""
    settings = obtener_settings()
    disponible, motivo = svc.disponible(settings)

    return {
        "disponible": disponible,
        "motivo": motivo,
        "usuario": settings.finanzas_usuario,
        "periodos": sorted(svc.PERIODOS),
        "tipos_movimiento": sorted(svc.TIPOS_MOVIMIENTO),
        "tipos_cuenta": sorted(svc.TIPOS_CUENTA),
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


@router.get("/finanzas/movimientos", response_model=ListaMovimientos)
async def movimientos(
    buscar: str = "",
    categoria: str = "",
    tipo: str = "",
    desde: str = "",
    hasta: str = "",
    limite: int = 200,
) -> ListaMovimientos:
    """Lista los movimientos con filtros opcionales."""
    settings = obtener_settings()

    try:
        datos = await asyncio.to_thread(
            svc.consultar,
            "listar",
            buscar=buscar or None,
            categoria=categoria or None,
            tipo=tipo or None,
            desde=desde or None,
            hasta=hasta or None,
            limite=limite,
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ListaMovimientos(
        total_transacciones=datos.get("total_transacciones", 0),
        total_ingresos=datos.get("total_ingresos", 0.0),
        total_gastos=datos.get("total_gastos", 0.0),
        balance=datos.get("balance", 0.0),
        transacciones=[Movimiento(**m) for m in datos.get("transacciones", [])],
    )


@router.post("/finanzas/movimientos", response_model=ResultadoOperacion, status_code=201)
async def crear_movimiento(peticion: PeticionMovimiento) -> ResultadoOperacion:
    """Registra un movimiento nuevo."""
    settings = obtener_settings()

    try:
        datos = await asyncio.to_thread(
            svc.crear_movimiento,
            monto=peticion.monto,
            tipo=peticion.tipo,
            categoria=peticion.categoria,
            nota=peticion.nota,
            fecha=peticion.fecha,
            cuenta=peticion.cuenta,
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "Movimiento creado: %s %s %s",
        peticion.tipo,
        peticion.monto,
        peticion.categoria,
    )

    return ResultadoOperacion(
        ok=True,
        mensaje=f"Movimiento registrado con el id {datos.get('id', '?')}.",
        aviso_sincronizacion=datos.get("_aviso_sync"),
    )


@router.patch("/finanzas/movimientos/{movimiento_id}", response_model=ResultadoOperacion)
async def editar_movimiento(
    movimiento_id: str, peticion: PeticionEdicion
) -> ResultadoOperacion:
    """Actualiza los campos indicados de un movimiento.

    Los campos que no llegan en el cuerpo no se tocan.
    """
    settings = obtener_settings()

    if not any(
        valor is not None
        for valor in (
            peticion.monto,
            peticion.categoria,
            peticion.nota,
            peticion.fecha,
            peticion.cuenta,
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="No se envió ningún campo que actualizar.",
        )

    try:
        await asyncio.to_thread(
            svc.editar_movimiento,
            movimiento_id,
            monto=peticion.monto,
            categoria=peticion.categoria,
            nota=peticion.nota,
            fecha=peticion.fecha,
            cuenta=peticion.cuenta,
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("Movimiento %s actualizado", movimiento_id)

    return ResultadoOperacion(
        ok=True,
        mensaje=f"Movimiento {movimiento_id} actualizado.",
    )


@router.delete("/finanzas/movimientos/{movimiento_id}", response_model=ResultadoOperacion)
async def eliminar_movimiento(movimiento_id: str, confirmar: bool = False) -> ResultadoOperacion:
    """Borra un movimiento.

    Exige `confirmar=true` como parámetro de consulta: es la única operación
    sin vuelta atrás y el script no conserva lo eliminado.
    """
    if not confirmar:
        raise HTTPException(
            status_code=400,
            detail=(
                "Falta la confirmación. Añade ?confirmar=true: el borrado no se "
                "puede deshacer."
            ),
        )

    settings = obtener_settings()

    try:
        await asyncio.to_thread(
            svc.eliminar_movimiento, movimiento_id, settings=settings
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.warning("Movimiento %s ELIMINADO desde el portal", movimiento_id)

    return ResultadoOperacion(
        ok=True,
        mensaje=f"Movimiento {movimiento_id} eliminado.",
    )


@router.put("/finanzas/cuentas", response_model=ResultadoOperacion)
async def fijar_saldo(peticion: PeticionSaldo) -> ResultadoOperacion:
    """Fija el saldo de una cuenta.

    Es la forma de corregir un saldo a mano sin inventar un movimiento.
    """
    settings = obtener_settings()

    try:
        await asyncio.to_thread(
            svc.fijar_saldo_cuenta,
            peticion.cuenta,
            peticion.saldo,
            peticion.tipo,
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("Saldo de %s fijado a %s", peticion.cuenta, peticion.saldo)

    return ResultadoOperacion(
        ok=True,
        mensaje=f"Saldo de {peticion.cuenta} actualizado.",
    )


@router.put("/finanzas/presupuesto", response_model=ResultadoOperacion)
async def fijar_presupuesto(peticion: PeticionPresupuesto) -> ResultadoOperacion:
    """Define el límite mensual de una categoría."""
    settings = obtener_settings()

    try:
        await asyncio.to_thread(
            svc.fijar_limite_presupuesto,
            peticion.categoria,
            peticion.limite,
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "Presupuesto de %s fijado a %s", peticion.categoria, peticion.limite
    )

    return ResultadoOperacion(
        ok=True,
        mensaje=f"Presupuesto de {peticion.categoria} actualizado.",
    )


# --- Comprobantes ---------------------------------------------------------


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


@router.get("/finanzas/comprobante/estado")
async def estado_comprobante() -> dict[str, object]:
    """Informa si se puede analizar un comprobante."""
    settings = obtener_settings()
    disponible, motivo = svc.disponible(settings)

    if not settings.gemini_api_key:
        disponible, motivo = False, (
            "Falta GEMINI_API_KEY: *** visión no se puede leer el ticket."
        )

    return {
        "disponible": disponible,
        "motivo": motivo,
        "tipos": sorted(TIPOS_COMPROBANTE),
        "max_upload_mb": settings.max_upload_mb,
    }


async def _recibir_comprobante(
    archivo: UploadFile, cuenta: str, *, registrar: bool
) -> dict:
    """Valida la imagen, la guarda y llama al servicio. Borra al terminar."""
    settings = obtener_settings()

    disponible, motivo = svc.disponible(settings)
    if not disponible:
        raise HTTPException(status_code=503, detail=f"Finanzas no disponible. {motivo}")

    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="Falta GEMINI_API_KEY: *** visión no se puede leer el ticket.",
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
        return await asyncio.to_thread(
            svc.analizar_comprobante,
            ruta,
            cuenta=cuenta or None,
            registrar=registrar,
            settings=settings,
        )
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        puente.borrar_silencioso(ruta)


@router.post("/finanzas/comprobante", response_model=RespuestaAnalisis)
async def analizar_comprobante(
    archivo: UploadFile = File(...),
    cuenta: str = Form(default=""),
) -> RespuestaAnalisis:
    """Analiza la foto de un ticket **sin** registrar nada."""
    datos = await _recibir_comprobante(archivo, cuenta, registrar=False)
    return _traducir_analisis(datos, registrado=False)


@router.post("/finanzas/comprobante/registrar", response_model=RespuestaAnalisis)
async def registrar_comprobante(
    archivo: UploadFile = File(...),
    cuenta: str = Form(default=""),
) -> RespuestaAnalisis:
    """Analiza el ticket y **registra** el movimiento en el ledger."""
    datos = await _recibir_comprobante(archivo, cuenta, registrar=True)

    logger.info("Comprobante registrado: %s movimiento(s)", datos.get("total", 0))

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
