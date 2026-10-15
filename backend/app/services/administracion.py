"""Administración completa del ledger de finanzas.

`scripts/finanzas.py` cubre bien el día a día (registrar, listar, resumir) pero
**solo crea** en las entidades de configuración: no hay forma de borrar una
tarjeta, una meta, un presupuesto, un recordatorio ni una cuenta. Ese límite
venía de que el script se escribió para conversar con el agente, donde borrar
algo se resuelve pidiéndolo.

El portal necesita más: control total sobre los registros. Este módulo cubre
esas operaciones que faltan, escribiendo **directamente sobre los mismos
archivos JSON** que usa `finanzas.py`, con su mismo formato.

Reglas que sigue, para no corromper los datos del agente:

1. **Mismo formato exacto.** Los archivos son la interfaz: si el portal escribe
   una clave distinta, el script del agente deja de entenderlos.
2. **Escritura atómica.** Se escribe a un temporal y se reemplaza, para que una
   interrupción no deje el JSON a medias.
3. **Respaldo antes de escribir.** Cada operación guarda una copia `.bak` del
   archivo, así un error es recuperable sin recurrir a un backup completo.
4. **Validación de forma.** Se comprueba que lo leído es lo que se espera antes
   de tocarlo; si el archivo está corrupto, se falla sin sobrescribir.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path

from app.core.config import Settings, obtener_settings

logger = logging.getLogger(__name__)

#: Carpetas de datos dentro del workspace de OpenClaw. El script las resuelve
#: como `../finanzas` relativo a `scripts/`, así que la ruta es la misma.
CARPETA_DATOS = ("finanzas", "users")

#: Entidades que el portal administra, con su archivo y su forma esperada.
ARCHIVOS = {
    "cuentas": "cuentas.json",
    "tarjetas": "tarjetas.json",
    "presupuesto": "presupuesto.json",
    "metas": "metas.json",
    "recordatorios": "recordatorios.json",
}

TIPOS_CUENTA = {"debito", "credito", "fisico", "prepago"}


class ErrorAdministracion(RuntimeError):
    """La operación de administración falló."""


def _carpeta_usuario(settings: Settings) -> Path:
    """Directorio de datos del usuario (`.../finanzas/users/<usuario>`)."""
    return (
        settings.herramientas_scripts_dir.parent
        / CARPETA_DATOS[0]
        / CARPETA_DATOS[1]
        / settings.finanzas_usuario
    )


def _ruta(entidad: str, settings: Settings) -> Path:
    if entidad not in ARCHIVOS:
        raise ErrorAdministracion(f"Entidad desconocida: {entidad}.")
    return _carpeta_usuario(settings) / ARCHIVOS[entidad]


def _leer(entidad: str, settings: Settings) -> dict:
    """Lee un archivo JSON de datos, validando que sea un objeto."""
    ruta = _ruta(entidad, settings)

    if not ruta.is_file():
        # No es un error: el script crea los archivos vacíos al arrancar.
        return {}

    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorAdministracion(
            f"El archivo {ruta.name} está corrupto y no se tocará: {exc}. "
            "Revisa la copia .bak."
        ) from exc

    if not isinstance(datos, dict):
        raise ErrorAdministracion(
            f"El archivo {ruta.name} no tiene el formato esperado (se esperaba "
            "un objeto)."
        )

    return datos


def _escribir(entidad: str, datos: dict, settings: Settings) -> None:
    """Escribe el JSON de forma atómica, con respaldo previo.

    Atómico = escribir a un temporal y renombrar. `os.replace` es atómico dentro
    del mismo sistema de archivos, así que el agente nunca ve un archivo a
    medias.
    """
    ruta = _ruta(entidad, settings)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    # Respaldo: solo la primera vez de la sesión de escritura, para no perder el
    # estado bueno con una cadena de cambios.
    respaldo = ruta.with_suffix(ruta.suffix + ".bak")
    if ruta.is_file() and not respaldo.is_file():
        shutil.copy2(ruta, respaldo)

    temporal = ruta.with_suffix(f"{ruta.suffix}.{uuid.uuid4().hex[:8]}.tmp")
    temporal.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporal, ruta)
    logger.info("Escrito %s (%d entradas)", ruta.name, len(datos))


def _clave(nombre: str) -> str:
    """Normaliza una clave igual que el script: minúsculas y sin espacios.

    `finanzas.py` hace `.lower()` sobre los nombres, así que el portal debe
    normalizar igual o crearía entradas duplicadas.
    """
    limpio = nombre.strip().lower().replace(" ", "_")
    if not limpio:
        raise ErrorAdministracion("El nombre no puede estar vacío.")
    if len(limpio) > 60:
        raise ErrorAdministracion("El nombre es demasiado largo.")
    return limpio


# --- Cuentas ---------------------------------------------------------------


def listar_cuentas(settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    datos = _leer("cuentas", settings)
    return {
        "cuentas": datos,
        "total": round(sum(c.get("saldo", 0) for c in datos.values()), 2),
    }


def guardar_cuenta(
    nombre: str,
    saldo: float,
    tipo: str = "debito",
    *,
    settings: Settings | None = None,
) -> dict:
    """Crea o actualiza una cuenta."""
    settings = settings or obtener_settings()

    clave = _clave(nombre)
    if tipo not in TIPOS_CUENTA:
        raise ErrorAdministracion(f"Tipo de cuenta no válido: {tipo}.")

    datos = _leer("cuentas", settings)
    datos[clave] = {"saldo": round(saldo, 2), "tipo": tipo}
    _escribir("cuentas", datos, settings)

    return {"ok": True, "cuenta": clave, "saldo": round(saldo, 2)}


def borrar_cuenta(nombre: str, *, settings: Settings | None = None) -> dict:
    """Elimina una cuenta.

    No comprueba si tiene movimientos asociados: el ledger guarda el nombre de
    la cuenta como texto, así que borrarla no rompe el historial. Se avisa en la
    interfaz de que los movimientos quedan sin cuenta.
    """
    settings = settings or obtener_settings()
    clave = _clave(nombre)

    datos = _leer("cuentas", settings)
    if clave not in datos:
        raise ErrorAdministracion(f"La cuenta {clave} no existe.")

    del datos[clave]
    _escribir("cuentas", datos, settings)

    return {"ok": True, "cuenta": clave}


def duplicar_cuenta(
    origen: str, nuevo_nombre: str, *, settings: Settings | None = None
) -> dict:
    """Duplica una cuenta con otro nombre."""
    settings = settings or obtener_settings()

    datos = _leer("cuentas", settings)
    fuente = _clave(origen)
    if fuente not in datos:
        raise ErrorAdministracion(f"La cuenta {fuente} no existe.")

    destino = _clave(nuevo_nombre)
    if destino in datos:
        raise ErrorAdministracion(f"La cuenta {destino} ya existe.")

    datos[destino] = dict(datos[fuente])
    _escribir("cuentas", datos, settings)

    return {"ok": True, "cuenta": destino}


# --- Presupuestos ----------------------------------------------------------


def listar_presupuestos(settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    return {"presupuesto": _leer("presupuesto", settings)}


def guardar_presupuesto(
    categoria: str,
    limite: float,
    periodo: str = "mensual",
    *,
    settings: Settings | None = None,
) -> dict:
    """Define o actualiza el límite de una categoría."""
    settings = settings or obtener_settings()

    if limite < 0:
        raise ErrorAdministracion("El límite no puede ser negativo.")

    clave = _clave(categoria)
    datos = _leer("presupuesto", settings)
    datos[clave] = {"limite": round(limite, 2), "periodo": periodo}
    _escribir("presupuesto", datos, settings)

    return {"ok": True, "categoria": clave, "limite": round(limite, 2)}


def borrar_presupuesto(categoria: str, *, settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    clave = _clave(categoria)

    datos = _leer("presupuesto", settings)
    if clave not in datos:
        raise ErrorAdministracion(f"No hay presupuesto para {clave}.")

    del datos[clave]
    _escribir("presupuesto", datos, settings)

    return {"ok": True, "categoria": clave}


# --- Metas -----------------------------------------------------------------


def listar_metas(settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    return {"metas": _leer("metas", settings)}


def guardar_meta(
    nombre: str,
    objetivo: float,
    ahorrado: float = 0.0,
    fecha_limite: str | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    """Crea o actualiza una meta de ahorro."""
    settings = settings or obtener_settings()

    if objetivo <= 0:
        raise ErrorAdministracion("El objetivo debe ser mayor que cero.")
    if ahorrado < 0:
        raise ErrorAdministracion("Lo ahorrado no puede ser negativo.")
    if fecha_limite:
        _validar_fecha(fecha_limite)

    clave = _clave(nombre)
    datos = _leer("metas", settings)

    meta: dict = {"objetivo": round(objetivo, 2), "ahorrado": round(ahorrado, 2)}
    if fecha_limite:
        meta["fecha_limite"] = fecha_limite

    datos[clave] = meta
    _escribir("metas", datos, settings)

    return {"ok": True, "meta": clave}


def borrar_meta(nombre: str, *, settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    clave = _clave(nombre)

    datos = _leer("metas", settings)
    if clave not in datos:
        raise ErrorAdministracion(f"La meta {clave} no existe.")

    del datos[clave]
    _escribir("metas", datos, settings)

    return {"ok": True, "meta": clave}


# --- Recordatorios ---------------------------------------------------------


def listar_recordatorios(settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    return {"recordatorios": _leer("recordatorios", settings)}


def guardar_recordatorio(
    nombre: str,
    monto: float,
    dia: int,
    nota: str = "",
    *,
    settings: Settings | None = None,
) -> dict:
    """Crea o actualiza un recordatorio de pago periódico."""
    settings = settings or obtener_settings()

    if not 1 <= dia <= 31:
        raise ErrorAdministracion("El día debe estar entre 1 y 31.")
    if monto < 0:
        raise ErrorAdministracion("El monto no puede ser negativo.")

    clave = _clave(nombre)
    datos = _leer("recordatorios", settings)
    datos[clave] = {"monto": round(monto, 2), "dia": dia, "nota": nota.strip()}
    _escribir("recordatorios", datos, settings)

    return {"ok": True, "recordatorio": clave}


def borrar_recordatorio(nombre: str, *, settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    clave = _clave(nombre)

    datos = _leer("recordatorios", settings)
    if clave not in datos:
        raise ErrorAdministracion(f"El recordatorio {clave} no existe.")

    del datos[clave]
    _escribir("recordatorios", datos, settings)

    return {"ok": True, "recordatorio": clave}


# --- Tarjetas: configurar y borrar ----------------------------------------


def listar_tarjetas(settings: Settings | None = None) -> dict:
    settings = settings or obtener_settings()
    return {"tarjetas": _leer("tarjetas", settings)}


def guardar_tarjeta(
    nombre: str,
    limite: float = 0.0,
    corte: int = 1,
    pago: int = 15,
    *,
    settings: Settings | None = None,
) -> dict:
    """Crea o reconfigura una tarjeta, conservando sus compras."""
    settings = settings or obtener_settings()

    if not 1 <= corte <= 31 or not 1 <= pago <= 31:
        raise ErrorAdministracion("Los días de corte y pago deben estar entre 1 y 31.")
    if limite < 0:
        raise ErrorAdministracion("El límite no puede ser negativo.")

    clave = _clave(nombre)
    datos = _leer("tarjetas", settings)

    tarjeta = datos.get(clave, {"compras": []})
    if not isinstance(tarjeta, dict):
        raise ErrorAdministracion(f"La tarjeta {clave} tiene un formato inesperado.")

    tarjeta["limite"] = round(limite, 2)
    tarjeta["corte"] = corte
    tarjeta["pago"] = pago
    tarjeta.setdefault("compras", [])

    datos[clave] = tarjeta
    _escribir("tarjetas", datos, settings)

    return {"ok": True, "tarjeta": clave}


def borrar_tarjeta(nombre: str, *, settings: Settings | None = None) -> dict:
    """Elimina una tarjeta y sus compras."""
    settings = settings or obtener_settings()
    clave = _clave(nombre)

    datos = _leer("tarjetas", settings)
    if clave not in datos:
        raise ErrorAdministracion(f"La tarjeta {clave} no existe.")

    del datos[clave]
    _escribir("tarjetas", datos, settings)

    return {"ok": True, "tarjeta": clave}


# --- Tarjetas: compras (MSI) ----------------------------------------------


def guardar_compra(
    tarjeta: str,
    concepto: str,
    total: float,
    plazo: int = 1,
    inicio: str | None = None,
    compra_id: str | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    """Crea o actualiza una compra dentro de una tarjeta.

    Con `compra_id` actualiza la existente respetando lo que no se envía; sin él
    crea una nueva. `mensualidad` se recalcula como `total / plazo`, que es lo
    que hace `finanzas.py` al agregar.
    """
    settings = settings or obtener_settings()

    if total <= 0:
        raise ErrorAdministracion("El total debe ser mayor que cero.")
    if plazo < 1:
        raise ErrorAdministracion("El plazo debe ser de al menos 1 mes.")
    if not concepto.strip():
        raise ErrorAdministracion("El concepto es obligatorio.")
    if len(concepto) > 200:
        raise ErrorAdministracion("El concepto es demasiado largo.")
    if inicio:
        _validar_fecha(inicio)

    clave = _clave(tarjeta)
    datos = _leer("tarjetas", settings)

    if clave not in datos:
        raise ErrorAdministracion(f"La tarjeta {clave} no existe.")

    tarjeta_datos = datos[clave]
    compras = tarjeta_datos.setdefault("compras", [])
    mensualidad = round(total / plazo, 2)

    if compra_id:
        compra = next((c for c in compras if c.get("id") == compra_id), None)
        if compra is None:
            raise ErrorAdministracion(f"La compra {compra_id} no existe.")

        compra["concepto"] = concepto.strip()
        compra["total"] = round(total, 2)
        compra["plazo"] = plazo
        compra["mensualidad"] = mensualidad
        # No se toca `restantes`: editar el plazo no debe "regalar" pagos ya
        # hechos. Si el usuario quiere, lo corrige aparte.
    else:
        compra = {
            "id": uuid.uuid4().hex[:8],
            "concepto": concepto.strip(),
            "total": round(total, 2),
            "mensualidad": mensualidad,
            "restantes": plazo,
            "plazo": plazo,
            "inicio": inicio or str(date.today()),
        }
        compras.append(compra)

    _escribir("tarjetas", datos, settings)

    return {"ok": True, "compra": compra["id"], "mensualidad": mensualidad}


def borrar_compra(
    tarjeta: str, compra_id: str, *, settings: Settings | None = None
) -> dict:
    """Elimina una compra de una tarjeta."""
    settings = settings or obtener_settings()

    clave = _clave(tarjeta)
    datos = _leer("tarjetas", settings)

    if clave not in datos:
        raise ErrorAdministracion(f"La tarjeta {clave} no existe.")

    compras = datos[clave].get("compras", [])
    restantes = [c for c in compras if c.get("id") != compra_id]

    if len(restantes) == len(compras):
        raise ErrorAdministracion(f"La compra {compra_id} no existe.")

    datos[clave]["compras"] = restantes
    _escribir("tarjetas", datos, settings)

    return {"ok": True, "compra": compra_id}


def duplicar_compra(
    tarjeta: str, compra_id: str, *, settings: Settings | None = None
) -> dict:
    """Duplica una compra (útil para cargos repetidos del mismo mes)."""
    settings = settings or obtener_settings()

    clave = _clave(tarjeta)
    datos = _leer("tarjetas", settings)

    if clave not in datos:
        raise ErrorAdministracion(f"La tarjeta {clave} no existe.")

    compras = datos[clave].setdefault("compras", [])
    original = next((c for c in compras if c.get("id") == compra_id), None)

    if original is None:
        raise ErrorAdministracion(f"La compra {compra_id} no existe.")

    copia = dict(original)
    copia["id"] = uuid.uuid4().hex[:8]
    copia["concepto"] = f"{original.get('concepto', 'Compra')} (copia)"
    compras.append(copia)

    _escribir("tarjetas", datos, settings)

    return {"ok": True, "compra": copia["id"]}


def _validar_fecha(fecha: str) -> None:
    """Exige el formato `YYYY-MM-DD` que usan todos los archivos."""
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError as exc:
        raise ErrorAdministracion("La fecha debe tener el formato AAAA-MM-DD.") from exc
