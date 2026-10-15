"""Herramienta: Finanzas personales.

Envuelve dos scripts del workspace:

  * ``finanzas.py`` — consultas del ledger (movimientos, resumen, presupuesto,
    cuentas, tarjetas, metas).
  * ``procesar_comprobante.py`` — analiza una foto de ticket y registra el
    movimiento con la cuenta y categoría que infiere la visión.

**Regla de oro del proyecto: esto solo lee y registra lo que el usuario
confirma.** No hay transferencias, ni pagos, ni cambios de saldo automáticos
desde el portal. Las acciones que mueven dinero siguen siendo conversación con
el agente, donde hay contexto y confirmación.

El usuario del ledger se deja configurable (`FINANZAS_USUARIO`), con `noe` por
defecto: es el único titular cargado hoy, pero el script es multi-usuario.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import Settings, obtener_settings
from app.core.proceso import mensaje_legible
from app.services import puente

logger = logging.getLogger(__name__)

TIMEOUT_CONSULTA = 60
TIMEOUT_COMPROBANTE = 180

# Acciones de solo lectura que el portal puede pedir. Todo lo que modifique
# saldos queda fuera de esta lista a propósito.
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


class ErrorFinanzas(RuntimeError):
    """La consulta financiera falló."""


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


def _ejecutar(argumentos: list[str], *, timeout: int,
              settings: Settings | None = None) -> dict:
    """Ejecuta `finanzas.py` y devuelve su JSON ya parseado."""
    settings = settings or obtener_settings()

    script = puente.copiar_script("finanzas.py", settings)

    try:
        resultado = puente.ejecutar_python(
            [str(script), *argumentos],
            cwd=script.parent,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - defensivo
        logger.exception("Fallo ejecutando finanzas.py")
        raise ErrorFinanzas("No se pudo ejecutar la consulta de finanzas.") from exc

    datos = _extraer_json(resultado.texto)

    if datos is None:
        detalle = mensaje_legible(resultado.stderr, resultado.codigo)
        logger.warning("Finanzas sin JSON utilizable: %s", detalle)
        raise ErrorFinanzas(f"No se pudo leer la respuesta: {detalle}")

    if datos.get("status") == "error":
        raise ErrorFinanzas(str(datos.get("message", "La consulta falló."))[:300])

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
    limite: int | None = None,
    settings: Settings | None = None,
) -> dict:
    """Consulta el ledger. Solo acciones de lectura.

    Args:
        accion: una de las claves de `ACCIONES_LECTURA`.
        periodo: para ``resumen`` (mensual, mes_anterior, anual, semanal).
        categoria: filtro exacto por categoría en ``listar``.
        buscar: texto libre en nota/categoría en ``listar``.
        limite: máximo de movimientos en ``listar``.
    """
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
        if limite:
            argumentos += ["--limite", str(int(limite))]
        # El portal muestra primero lo reciente.
        argumentos += ["--orden", "fecha", "--desc"]

    return _ejecutar(argumentos, timeout=TIMEOUT_CONSULTA, settings=settings)


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
        registrar: ``False`` solo analiza (dry-run). El portal siempre llama
            primero en modo análisis para que el usuario confirme.
    """
    settings = settings or obtener_settings()

    entorno_ok, motivo = puente.entorno_openclaw_disponible(settings)
    if not entorno_ok:
        raise ErrorFinanzas(motivo)

    script = puente.copiar_script("procesar_comprobante.py", settings)

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

    return datos
