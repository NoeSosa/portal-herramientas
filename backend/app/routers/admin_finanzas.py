"""Router de administración del ledger de finanzas.

Cubre lo que el módulo principal no alcanza: configuración de tarjetas,
presupuestos, metas, recordatorios y cuentas, con crear, editar, borrar y
duplicar. Más las dos operaciones que mueven dinero: **transferir entre cuentas**
y **pagar una tarjeta**.

Sobre mover dinero: el usuario pidió control total y estas dos van con
confirmación explícita (`?confirmar=true`), porque modifican saldos de verdad y
`finanzas.py` no guarda historial de pagos de tarjeta.

Todo escribe sobre los mismos archivos JSON que usa el agente, con el mismo
formato, así que el agente ve los cambios al instante y sigue entendiendo los
datos.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import obtener_settings
from app.services import administracion as admin
from app.services import finanzas as svc

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Modelos ---------------------------------------------------------------


class Cuenta(BaseModel):
    nombre: str
    saldo: float
    tipo: str = "debito"


class CuentaGuardada(BaseModel):
    ok: bool
    mensaje: str


class Presupuesto(BaseModel):
    categoria: str = Field(..., min_length=1, max_length=60)
    limite: float = Field(..., ge=0)
    periodo: str = "mensual"


class Meta(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=60)
    objetivo: float = Field(..., gt=0)
    ahorrado: float = Field(default=0, ge=0)
    fecha_limite: str | None = None


class Recordatorio(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=60)
    monto: float = Field(..., ge=0)
    dia: int = Field(..., ge=1, le=31)
    nota: str = Field(default="", max_length=200)


class Tarjeta(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=60)
    limite: float = Field(default=0, ge=0)
    corte: int = Field(default=1, ge=1, le=31)
    pago: int = Field(default=15, ge=1, le=31)


class Compra(BaseModel):
    """Una compra dentro de una tarjeta (MSI o de contado)."""

    concepto: str = Field(..., min_length=1, max_length=200)
    total: float = Field(..., gt=0)
    plazo: int = Field(default=1, ge=1, le=120)
    inicio: str | None = None


class Transferencia(BaseModel):
    """Mueve dinero entre dos cuentas propias."""

    origen: str = Field(..., min_length=1)
    destino: str = Field(..., min_length=1)
    monto: float = Field(..., gt=0)
    nota: str = Field(default="", max_length=200)
    fecha: str | None = None


class PagoTarjeta(BaseModel):
    """Paga una tarjeta: descuenta de una cuenta y avanza los MSI un mes."""

    tarjeta: str = Field(..., min_length=1)
    cuenta: str | None = None
    monto: float | None = Field(default=None, gt=0)
    fecha: str | None = None


class Resultado(BaseModel):
    ok: bool
    mensaje: str
    detalle: dict | None = None


def _fallo(exc: admin.ErrorAdministracion) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# --- Cuentas ---------------------------------------------------------------


@router.get("/finanzas/cuentas")
async def cuentas() -> dict:
    """Lista las cuentas con su total."""
    return await asyncio.to_thread(admin.listar_cuentas)


@router.put("/finanzas/cuentas/{nombre}")
async def guardar_cuenta(nombre: str, cuenta: Cuenta) -> Resultado:
    """Crea o actualiza una cuenta."""
    try:
        await asyncio.to_thread(
            admin.guardar_cuenta, nombre, cuenta.saldo, cuenta.tipo
        )
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Cuenta {nombre} guardada.")


@router.post("/finanzas/cuentas/{nombre}/duplicar")
async def duplicar_cuenta(nombre: str, nuevo: str = Query(..., min_length=1)) -> Resultado:
    """Duplica una cuenta con otro nombre."""
    try:
        await asyncio.to_thread(admin.duplicar_cuenta, nombre, nuevo)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Cuenta duplicada como {nuevo}.")


@router.delete("/finanzas/cuentas/{nombre}")
async def borrar_cuenta(nombre: str) -> Resultado:
    """Elimina una cuenta.

    Los movimientos que la mencionaban quedan sin cuenta, pero no se borran: el
    nombre se guarda como texto en el ledger.
    """
    try:
        await asyncio.to_thread(admin.borrar_cuenta, nombre)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    logger.warning("Cuenta %s ELIMINADA desde el portal", nombre)

    return Resultado(
        ok=True,
        mensaje=(
            f"Cuenta {nombre} eliminada. Sus movimientos siguen en el ledger, "
            "sin cuenta asignada."
        ),
    )


# --- Presupuestos ----------------------------------------------------------


@router.get("/finanzas/presupuestos")
async def presupuestos() -> dict:
    return await asyncio.to_thread(admin.listar_presupuestos)


@router.put("/finanzas/presupuestos/{categoria}")
async def guardar_presupuesto(categoria: str, peticion: Presupuesto) -> Resultado:
    try:
        await asyncio.to_thread(
            admin.guardar_presupuesto, categoria, peticion.limite, peticion.periodo
        )
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Presupuesto de {categoria} guardado.")


@router.delete("/finanzas/presupuestos/{categoria}")
async def borrar_presupuesto(categoria: str) -> Resultado:
    try:
        await asyncio.to_thread(admin.borrar_presupuesto, categoria)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Presupuesto de {categoria} eliminado.")


# --- Metas -----------------------------------------------------------------


@router.get("/finanzas/metas")
async def metas() -> dict:
    return await asyncio.to_thread(admin.listar_metas)


@router.put("/finanzas/metas/{nombre}")
async def guardar_meta(nombre: str, meta: Meta) -> Resultado:
    try:
        await asyncio.to_thread(
            admin.guardar_meta,
            nombre,
            meta.objetivo,
            meta.ahorrado,
            meta.fecha_limite,
        )
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Meta {nombre} guardada.")


@router.delete("/finanzas/metas/{nombre}")
async def borrar_meta(nombre: str) -> Resultado:
    try:
        await asyncio.to_thread(admin.borrar_meta, nombre)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Meta {nombre} eliminada.")


# --- Recordatorios ---------------------------------------------------------


@router.get("/finanzas/recordatorios")
async def recordatorios() -> dict:
    return await asyncio.to_thread(admin.listar_recordatorios)


@router.put("/finanzas/recordatorios/{nombre}")
async def guardar_recordatorio(nombre: str, peticion: Recordatorio) -> Resultado:
    try:
        await asyncio.to_thread(
            admin.guardar_recordatorio,
            nombre,
            peticion.monto,
            peticion.dia,
            peticion.nota,
        )
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Recordatorio {nombre} guardado.")


@router.delete("/finanzas/recordatorios/{nombre}")
async def borrar_recordatorio(nombre: str) -> Resultado:
    try:
        await asyncio.to_thread(admin.borrar_recordatorio, nombre)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Recordatorio {nombre} eliminado.")


# --- Tarjetas y compras ----------------------------------------------------


@router.get("/finanzas/tarjetas")
async def tarjetas() -> dict:
    return await asyncio.to_thread(admin.listar_tarjetas)


@router.put("/finanzas/tarjetas/{nombre}")
async def guardar_tarjeta(nombre: str, tarjeta: Tarjeta) -> Resultado:
    """Crea o reconfigura una tarjeta, conservando sus compras."""
    try:
        await asyncio.to_thread(
            admin.guardar_tarjeta,
            nombre,
            tarjeta.limite,
            tarjeta.corte,
            tarjeta.pago,
        )
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje=f"Tarjeta {nombre} guardada.")


@router.delete("/finanzas/tarjetas/{nombre}")
async def borrar_tarjeta(nombre: str) -> Resultado:
    """Elimina una tarjeta **y sus compras**. Irreversible."""
    try:
        await asyncio.to_thread(admin.borrar_tarjeta, nombre)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    logger.warning("Tarjeta %s ELIMINADA desde el portal (con sus compras)", nombre)

    return Resultado(
        ok=True,
        mensaje=f"Tarjeta {nombre} eliminada junto con sus compras.",
    )


@router.post("/finanzas/tarjetas/{nombre}/compras", status_code=201)
async def crear_compra(nombre: str, compra: Compra) -> Resultado:
    try:
        datos = await asyncio.to_thread(
            admin.guardar_compra,
            nombre,
            compra.concepto,
            compra.total,
            compra.plazo,
            compra.inicio,
        )
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(
        ok=True,
        mensaje=f"Compra registrada: {datos['mensualidad']:.2f}/mes.",
        detalle=datos,
    )


@router.patch("/finanzas/tarjetas/{nombre}/compras/{compra_id}")
async def editar_compra(nombre: str, compra_id: str, compra: Compra) -> Resultado:
    """Actualiza una compra.

    No toca `restantes`: editar el plazo no debe regalar pagos ya hechos.
    """
    try:
        datos = await asyncio.to_thread(
            admin.guardar_compra,
            nombre,
            compra.concepto,
            compra.total,
            compra.plazo,
            compra.inicio,
            compra_id,
        )
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(
        ok=True,
        mensaje=f"Compra actualizada: {datos['mensualidad']:.2f}/mes.",
        detalle=datos,
    )


@router.post("/finanzas/tarjetas/{nombre}/compras/{compra_id}/duplicar", status_code=201)
async def duplicar_compra(nombre: str, compra_id: str) -> Resultado:
    try:
        datos = await asyncio.to_thread(admin.duplicar_compra, nombre, compra_id)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje="Compra duplicada.", detalle=datos)


@router.delete("/finanzas/tarjetas/{nombre}/compras/{compra_id}")
async def borrar_compra(nombre: str, compra_id: str) -> Resultado:
    try:
        await asyncio.to_thread(admin.borrar_compra, nombre, compra_id)
    except admin.ErrorAdministracion as exc:
        raise _fallo(exc) from exc

    return Resultado(ok=True, mensaje="Compra eliminada.")


# --- Operaciones que mueven dinero -----------------------------------------


@router.post("/finanzas/transferir")
async def transferir(
    peticion: Transferencia, confirmar: bool = False
) -> Resultado:
    """Transfiere dinero entre dos cuentas propias.

    Exige `?confirmar=true`: modifica dos saldos a la vez y el script no
    registra la operación en el ledger de movimientos (solo en el historial de
    transferencias).
    """
    if not confirmar:
        raise HTTPException(
            status_code=400,
            detail="Falta la confirmación. Añade ?confirmar=true para mover dinero.",
        )

    argumentos = [
        "--action", "transferir",
        "--t-origen", peticion.origen,
        "--t-destino", peticion.destino,
        "--monto", str(peticion.monto),
    ]
    if peticion.nota.strip():
        argumentos += ["--nota", peticion.nota.strip()]
    if peticion.fecha:
        argumentos += ["--fecha", peticion.fecha]

    try:
        datos = await asyncio.to_thread(svc._ejecutar, argumentos, timeout=60)
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.warning(
        "TRANSFERENCIA desde el portal: %s -> %s $%s",
        peticion.origen,
        peticion.destino,
        peticion.monto,
    )

    return Resultado(
        ok=True,
        mensaje=(
            f"Transferidos {peticion.monto:.2f} de {peticion.origen} a "
            f"{peticion.destino}."
        ),
        detalle=datos,
    )


@router.post("/finanzas/tarjetas/{nombre}/pagar")
async def pagar_tarjeta(
    nombre: str, peticion: PagoTarjeta, confirmar: bool = False
) -> Resultado:
    """Registra el pago de una tarjeta y avanza un mes las compras MSI.

    Exige `?confirmar=true`. Efectos: suma un gasto en el ledger, resta un mes
    a las compras con `restantes > 0` y descuenta el monto de la cuenta si se
    indica una.
    """
    if not confirmar:
        raise HTTPException(
            status_code=400,
            detail="Falta la confirmación. Añade ?confirmar=true para pagar.",
        )

    argumentos = ["--action", "tarjetas", "--set-tarjeta", "--t-nombre", nombre,
                  "--t-accion", "pagar"]
    if peticion.monto is not None:
        argumentos += ["--monto", str(peticion.monto)]
    if peticion.cuenta:
        argumentos += ["--cuenta", peticion.cuenta]
    if peticion.fecha:
        argumentos += ["--fecha", peticion.fecha]

    try:
        datos = await asyncio.to_thread(svc._ejecutar, argumentos, timeout=60)
    except svc.ErrorFinanzas as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pagado = datos.get("pagado", 0)

    logger.warning("PAGO de tarjeta %s desde el portal: $%s", nombre, pagado)

    return Resultado(
        ok=True,
        mensaje=f"Pago de {nombre} registrado: {pagado:.2f}.",
        detalle=datos,
    )
