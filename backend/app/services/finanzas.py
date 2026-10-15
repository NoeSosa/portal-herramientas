"""Herramienta: Finanzas personales.

Envuelve dos scripts del workspace:

  * ``finanzas.py`` — ledger (movimientos, resumen, presupuesto, cuentas,
    tarjetas, metas) y las operaciones de escritura: agregar, editar, eliminar.
  * ``procesar_comprobante.py`` — analiza una foto de ticket y registra el
    movimiento con la cuenta y categoría que infiere la visión.

Sobre la escritura: el portal **no** mueve dinero entre cuentas, no paga
tarjetas y no toca saldos por su cuenta. Lo que sí permite es mantener el
registro: crear, corregir y borrar movimientos, porque un ledger con datos
equivocados no sirve para nada. Cada cambio se le muestra al usuario antes de
guardarse.

Todas las lecturas pasan por `_copiar_ledger`: el volumen de OpenClaw es de solo
lectura y `finanzas.py` resuelve su base de datos como `../finanzas` relativo a
sí mismo, así que se copia (refrescada) a un directorio escribible.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path

from app.core.config import Settings, obtener_settings
from app.core.proceso import mensaje_legible
from app.services import puente

logger = logging.getLogger(__name__)

TIMEOUT_CONSULTA = 60
TIMEOUT_COMPROBANTE = 180

# Acciones de solo lectura que el portal puede pedir.
ACCIONES_LECTURA = {
    "listar": ["--listar"],
    "resumen": ["--mostrar"],
    "presupuesto": ["--mostrar"],
    "cuentas": ["--mostrar"],
    "tarjetas": ["--listar"],
    "metas": ["--mostrar"],
    "recordatorios": ["--mostrar"],
}

PERIODOS = {"mensual", "mes_anterior", "anual", "semanal"}

# Tipos de movimiento y de cuenta aceptados por el script. Se validan aquí para
# que un valor inventado no llegue a la base de datos.
TIPOS_MOVIMIENTO = {"ingreso", "gasto"}
TIPOS_CUENTA = {"debito", "credito", "fisico", "prepago"}

# Límites defensivos de los campos libres.
MAX_NOTA = 300
MAX_CATEGORIA = 60
MONTO_MAXIMO = 10_000_000


class ErrorFinanzas(RuntimeError):
    """La operación financiera falló."""


def disponible(settings: Settings | None = None) -> tuple[bool, str]:
    """Comprueba entorno y datos del ledger antes de mostrar el panel."""
    settings = settings or obtener_settings()

    entorno_ok, motivo = puente.entorno_openclaw_disponible(settings)
    if not entorno_ok:
        return False, motivo

    if not (settings.herramientas_scripts_dir / "finanzas.py").is_file():
        return False, "No se encontró el script finanzas.py."

    ledger = (
        settings.herramientas_scripts_dir.parent
        / "finanzas"
        / "users"
        / settings.finanzas_usuario
    )
    if not (ledger / "ledger.db").is_file() and not (ledger / "ledger.csv").is_file():
        return (
            False,
            f"No hay datos de finanzas para el usuario "
            f"'{settings.finanzas_usuario}'.",
        )

    return True, ""


def _copiar_ledger(settings: Settings) -> Path:
    """Devuelve la copia del script y deja la carpeta de datos lista.

    `finanzas.py` resuelve sus datos como `../finanzas` relativo a sí mismo.

    Hay dos escenarios y el código se adapta solo:

    * **Volumen escribible** (el caso del backend en el compose): los datos se
      escriben en el ORIGINAL. Es lo que se quiere: el agente ve los cambios al
      instante y no hay nada que sincronizar.
    * **Volumen de solo lectura** (workers, o un despliegue sin permiso de
      escritura): se copia la carpeta a un directorio escribible y los cambios
      quedan en la copia.

    Para no arrastrar datos viejos entre ambos escenarios, cuando el original
    es escribible se trabaja directamente sobre él y se ignora cualquier copia
    anterior.
    """
    script = puente.copiar_script("finanzas.py", settings)

    original = settings.herramientas_scripts_dir.parent / "finanzas"
    copia = settings.data_dir / "runtime" / "finanzas"

    if _escribible(original):
        # El script copiado vive en `<data>/runtime/scripts/`, así que `../finanzas`
        # apunta a `<data>/runtime/finanzas`. Puede haber ahí una copia vieja
        # (de un despliegue con el volumen en solo lectura) o un enlace de una
        # ejecución anterior. Se limpia para volver a enlazar al original.
        if copia.is_symlink() or copia.is_file():
            copia.unlink(missing_ok=True)
        elif copia.is_dir():
            shutil.rmtree(copia, ignore_errors=True)

        # El script debe seguir resolviendo `../finanzas`. Se crea un enlace
        # simbólico a la carpeta real, que es el original escribible.
        copia.parent.mkdir(parents=True, exist_ok=True)
        copia.symlink_to(original, target_is_directory=True)

        logger.info("Ledger escribible: el portal trabaja sobre los datos reales.")
    else:
        puente.copiar_contexto("finanzas", settings)
        logger.info(
            "Ledger en solo lectura: el portal trabaja sobre una copia y los "
            "cambios no llegarán al workspace."
        )

    return script


def _escribible(ruta: Path) -> bool:
    """True si se puede crear un archivo dentro de `ruta`.

    Se comprueba de verdad (probando a abrir un archivo) en vez de mirar los
    permisos: en un montaje de solo lectura los permisos parecen correctos y el
    kernel rechaza la escritura igualmente.
    """
    if not ruta.is_dir():
        return False

    prueba = ruta / ".escritura_portal"
    try:
        prueba.touch()
        prueba.unlink()
        return True
    except OSError:
        return False


def sincronizar_ledger(settings: Settings | None = None) -> tuple[bool, str]:
    """Copia los cambios del portal al workspace de OpenClaw, si hace falta.

    Con el volumen escribible (el caso normal del backend) el script ya escribió
    el original, así que esto no tiene nada que hacer y devuelve éxito.

    Solo sirve para el escenario degradado en el que se trabajó sobre una copia:
    ahí avisa de que los cambios se quedaron en el portal.

    Returns:
        `(ok, mensaje)`. No lanza: el portal avisa pero no falla la operación,
        porque el cambio ya está aplicado en su copia.
    """
    settings = settings or obtener_settings()

    original = settings.herramientas_scripts_dir.parent / "finanzas"

    if _escribible(original):
        # Se escribió el original: no hay nada que sincronizar.
        return True, ""

    return False, (
        "El cambio se guardó en el portal, pero el workspace de OpenClaw está "
        "en solo lectura: el agente no verá esta edición hasta que se "
        "reconstruya el backend con el volumen en escritura."
    )


def _ejecutar(argumentos: list[str], *, timeout: int,
              settings: Settings | None = None) -> dict:
    """Ejecuta `finanzas.py` y devuelve su JSON ya parseado."""
    settings = settings or obtener_settings()

    script = _copiar_ledger(settings)

    try:
        resultado = puente.ejecutar_python(
            [str(script), *argumentos],
            cwd=script.parent,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - defensivo
        logger.exception("Fallo ejecutando finanzas.py")
        raise ErrorFinanzas("No se pudo ejecutar la operación de finanzas.") from exc

    datos = _extraer_json(resultado.texto)

    if datos is None:
        detalle = mensaje_legible(resultado.stderr, resultado.codigo)
        logger.warning("Finanzas sin JSON utilizable: %s", detalle)
        raise ErrorFinanzas(f"No se pudo leer la respuesta: {detalle}")

    if datos.get("status") == "error":
        raise ErrorFinanzas(str(datos.get("message", "La operación falló."))[:300])

    return datos


def _extraer_json(salida: str) -> dict | None:
    """Parsea el JSON del script tolerando ruido alrededor."""
    texto = (salida or "").strip()
    if not texto:
        return None

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        return None

    try:
        return json.loads(texto[inicio : fin + 1])
    except json.JSONDecodeError:
        return None


def consultar(
    accion: str,
    *,
    periodo: str = "mensual",
    categoria: str | None = None,
    buscar: str | None = None,
    tipo: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    limite: int | None = None,
    settings: Settings | None = None,
) -> dict:
    """Consulta el ledger. Solo acciones de lectura."""
    settings = settings or obtener_settings()

    if accion not in ACCIONES_LECTURA:
        raise ErrorFinanzas(
            f"Acción no permitida: {accion}. "
            f"Disponibles: {', '.join(sorted(ACCIONES_LECTURA))}."
        )

    argumentos = [
        "--user",
        settings.finanzas_usuario,
        "--action",
        accion,
        *ACCIONES_LECTURA[accion],
    ]

    if accion == "resumen":
        if periodo not in PERIODOS:
            raise ErrorFinanzas(f"Periodo no válido: {periodo}.")
        argumentos += ["--periodo", periodo]

    if accion == "listar":
        if categoria:
            argumentos += ["--categoria", categoria]
        if buscar:
            argumentos += ["--buscar", buscar]
        if tipo in TIPOS_MOVIMIENTO:
            argumentos += ["--tipo", tipo]
        if desde:
            argumentos += ["--fecha-inicio", desde]
        if hasta:
            argumentos += ["--fecha-fin", hasta]
        # El límite se acota: pedir 99999 no debe devolver el ledger entero.
        argumentos += ["--limite", str(max(1, min(limite or 200, 500)))]
        argumentos += ["--orden", "fecha", "--desc"]

    return _ejecutar(argumentos, timeout=TIMEOUT_CONSULTA, settings=settings)


def crear_movimiento(
    *,
    monto: float,
    tipo: str,
    categoria: str,
    nota: str = "",
    fecha: str | None = None,
    cuenta: str | None = None,
    settings: Settings | None = None,
) -> dict:
    """Registra un movimiento nuevo en el ledger."""
    settings = settings or obtener_settings()
    _validar_movimiento(monto, tipo, categoria, nota, fecha)

    argumentos = [
        "--user", settings.finanzas_usuario,
        "--action", "agregar",
        "--monto", str(monto),
        "--tipo", tipo,
        "--categoria", categoria.strip(),
    ]
    if nota.strip():
        argumentos += ["--nota", nota.strip()]
    if fecha:
        argumentos += ["--fecha", fecha]
    if cuenta:
        argumentos += ["--cuenta", cuenta.strip().lower()]

    datos = _ejecutar(argumentos, timeout=TIMEOUT_CONSULTA, settings=settings)
    ok, aviso = sincronizar_ledger(settings)

    if not ok:
        datos["_aviso_sync"] = aviso

    return datos


def editar_movimiento(
    movimiento_id: str,
    *,
    monto: float | None = None,
    categoria: str | None = None,
    nota: str | None = None,
    fecha: str | None = None,
    cuenta: str | None = None,
    settings: Settings | None = None,
) -> dict:
    """Actualiza los campos indicados de un movimiento.

    Solo se envían los campos presentes: el script ignora los `None`, así que
    una edición parcial no borra lo que no se tocó.
    """
    settings = settings or obtener_settings()

    if not movimiento_id or not movimiento_id.strip():
        raise ErrorFinanzas("Falta el identificador del movimiento.")

    argumentos = [
        "--user", settings.finanzas_usuario,
        "--action", "editar",
        "--id", movimiento_id.strip(),
    ]

    if monto is not None:
        if not (0 < monto <= MONTO_MAXIMO):
            raise ErrorFinanzas("El monto debe ser mayor que cero y razonable.")
        argumentos += ["--monto", str(monto)]

    if categoria is not None:
        if not categoria.strip() or len(categoria.strip()) > MAX_CATEGORIA:
            raise ErrorFinanzas("La categoría es obligatoria y no muy larga.")
        argumentos += ["--categoria", categoria.strip()]

    if nota is not None:
        if len(nota) > MAX_NOTA:
            raise ErrorFinanzas(f"La nota no puede pasar de {MAX_NOTA} caracteres.")
        # `--nota` acepta vacío (nargs="?"), así que se puede limpiar la nota.
        argumentos += ["--nota", nota.strip()]

    if fecha is not None:
        _validar_fecha(fecha)
        argumentos += ["--fecha", fecha]

    if cuenta is not None:
        argumentos += ["--cuenta", cuenta.strip().lower()]

    datos = _ejecutar(argumentos, timeout=TIMEOUT_CONSULTA, settings=settings)
    ok, aviso = sincronizar_ledger(settings)

    if not ok:
        datos["_aviso_sync"] = aviso

    return datos


def eliminar_movimiento(
    movimiento_id: str, *, settings: Settings | None = None
) -> dict:
    """Borra un movimiento del ledger.

    Es una operación destructiva: el router exige confirmación explícita del
    usuario antes de llegar aquí, y se registra en el log con el concepto.
    """
    settings = settings or obtener_settings()

    if not movimiento_id or not movimiento_id.strip():
        raise ErrorFinanzas("Falta el identificador del movimiento.")

    datos = _ejecutar(
        [
            "--user", settings.finanzas_usuario,
            "--action", "eliminar",
            "--id", movimiento_id.strip(),
        ],
        timeout=TIMEOUT_CONSULTA,
        settings=settings,
    )
    ok, aviso = sincronizar_ledger(settings)

    if not ok:
        datos["_aviso_sync"] = aviso

    return datos


def fijar_saldo_cuenta(
    cuenta: str, saldo: float, tipo: str = "debito",
    *, settings: Settings | None = None,
) -> dict:
    """Fija el saldo de una cuenta existente o crea una nueva.

    Es la forma de corregir un saldo a mano sin registrar un movimiento falso.
    """
    settings = settings or obtener_settings()

    if not cuenta or not cuenta.strip():
        raise ErrorFinanzas("Falta el nombre de la cuenta.")
    if tipo not in TIPOS_CUENTA:
        raise ErrorFinanzas(f"Tipo de cuenta no válido: {tipo}.")
    if not (-MONTO_MAXIMO <= saldo <= MONTO_MAXIMO):
        raise ErrorFinanzas("El saldo está fuera de un rango razonable.")

    datos = _ejecutar(
        [
            "--user", settings.finanzas_usuario,
            "--action", "cuentas",
            "--set-cuenta", cuenta.strip().lower(),
            "--set-saldo", str(saldo),
            "--set-tipo", tipo,
        ],
        timeout=TIMEOUT_CONSULTA,
        settings=settings,
    )
    ok, aviso = sincronizar_ledger(settings)

    if not ok:
        datos["_aviso_sync"] = aviso

    return datos


def fijar_limite_presupuesto(
    categoria: str, limite: float, *, settings: Settings | None = None
) -> dict:
    """Define o cambia el límite mensual de una categoría."""
    settings = settings or obtener_settings()

    if not categoria or not categoria.strip():
        raise ErrorFinanzas("Falta la categoría del presupuesto.")
    if not (0 <= limite <= MONTO_MAXIMO):
        raise ErrorFinanzas("El límite debe estar entre 0 y un valor razonable.")

    datos = _ejecutar(
        [
            "--user", settings.finanzas_usuario,
            "--action", "presupuesto",
            "--set-cat", categoria.strip().lower(),
            "--set-limite", str(limite),
        ],
        timeout=TIMEOUT_CONSULTA,
        settings=settings,
    )
    ok, aviso = sincronizar_ledger(settings)

    if not ok:
        datos["_aviso_sync"] = aviso

    return datos


def _validar_movimiento(
    monto: float, tipo: str, categoria: str, nota: str, fecha: str | None
) -> None:
    """Valida los campos de un movimiento nuevo antes de tocar el ledger."""
    if tipo not in TIPOS_MOVIMIENTO:
        raise ErrorFinanzas(f"Tipo no válido: {tipo}. Debe ser ingreso o gasto.")
    if not (0 < monto <= MONTO_MAXIMO):
        raise ErrorFinanzas("El monto debe ser mayor que cero y razonable.")
    if not categoria or not categoria.strip():
        raise ErrorFinanzas("La categoría es obligatoria.")
    if len(categoria.strip()) > MAX_CATEGORIA:
        raise ErrorFinanzas(f"La categoría no puede pasar de {MAX_CATEGORIA} caracteres.")
    if len(nota) > MAX_NOTA:
        raise ErrorFinanzas(f"La nota no puede pasar de {MAX_NOTA} caracteres.")
    if fecha:
        _validar_fecha(fecha)


def _validar_fecha(fecha: str) -> None:
    """Exige el formato `YYYY-MM-DD`, que es el que usa el ledger."""
    try:
        date.fromisoformat(fecha)
    except ValueError as exc:
        raise ErrorFinanzas("La fecha debe tener el formato AAAA-MM-DD.") from exc


def analizar_comprobante(
    ruta_imagen: Path,
    *,
    cuenta: str | None = None,
    registrar: bool,
    settings: Settings | None = None,
) -> dict:
    """Analiza una foto de ticket y, opcionalmente, registra el movimiento.

    Args:
        ruta_imagen: imagen ya validada y guardada en el portal.
        cuenta: cuenta o tarjeta explícita; vacío = la que infiera la visión.
        registrar: ``False`` solo analiza (dry-run).
    """
    settings = settings or obtener_settings()

    entorno_ok, motivo = puente.entorno_openclaw_disponible(settings)
    if not entorno_ok:
        raise ErrorFinanzas(motivo)

    script = puente.copiar_script("procesar_comprobante.py", settings)
    puente.copiar_contexto("finanzas", settings)

    argumentos = [
        str(script),
        str(ruta_imagen),
        "--usuario",
        settings.finanzas_usuario,
    ]
    if cuenta:
        argumentos += ["--cuenta", cuenta]
    if not registrar:
        argumentos += ["--dry-run"]

    try:
        resultado = puente.ejecutar_python(
            argumentos,
            cwd=script.parent,
            timeout=TIMEOUT_COMPROBANTE,
            entorno_extra={"GEMINI_API_KEY": settings.gemini_api_key or ""},
        )
    except Exception as exc:  # pragma: no cover - defensivo
        logger.exception("Fallo ejecutando procesar_comprobante.py")
        raise ErrorFinanzas("No se pudo analizar el comprobante.") from exc

    datos = _extraer_json(resultado.texto)

    if datos is None:
        detalle = mensaje_legible(resultado.stderr, resultado.codigo)
        logger.warning("Comprobante sin JSON utilizable: %s", detalle)
        raise ErrorFinanzas(f"No se pudo leer la respuesta: {detalle}")

    if registrar and datos.get("total"):
        sincronizar_ledger(settings)

    return datos
