"""Puente hacia los scripts de OpenClaw.

Contrato único para invocar cualquier script del workspace del agente desde el
portal. Existe porque los scripts comparten tres peculiaridades que, resueltas
por separado en cada herramienta, se acaban implementando mal:

  1. **El volumen es de solo lectura.** Muchos scripts crean su carpeta
     `temp/` relativa a sí mismos al arrancar (`os.makedirs(..., exist_ok=True)`),
     y eso falla con `EROFS`. Solución: **copiar el script** a un directorio
     escribible del portal antes de ejecutarlo. El original nunca se toca.

  2. **Las rutas son relativas al script.** `tts.py` resuelve sus modelos como
     `../tts_models`, `analyze_image.py` lee `GEMINI_API_KEY` de `../.env` y
     escribe su contador de uso en `../temp/`. Al copiar el script a otro
     directorio esas rutas se rompen, así que la copia se coloca respetando la
     MISMA profundidad (`<raiz>/scripts/<script>.py`).

  3. **El intérprete no es `python3` del sistema.** El venv de OpenClaw lleva
     rutas absolutas horneadas en el shebang, y sus dependencias (google-genai,
     faster-whisper, pandas…) viven ahí. Siempre se ejecuta con
     `/home/node/.openclaw/workspace/venv/bin/python`, nunca con el python del
     contenedor.

Uso típico desde un servicio de herramienta::

    codigo = copiar_script(settings, "analyze_image.py")
    resultado = ejecutar_python([str(codigo), str(imagen), "--prompt", prompt],
                                cwd=codigo.parent, timeout=90)
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, obtener_settings

# Raíz de OpenClaw tal como está montada en los contenedores del portal.
RAIZ_OPENCLAW = Path("/home/node/.openclaw")

# Intérprete del venv. El shebang de los scripts apunta aquí.
PYTHON_VENV = RAIZ_OPENCLAW / "workspace" / "venv" / "bin" / "python"


class ErrorPuente(RuntimeError):
    """El script no pudo ejecutarse o devolvió un fallo."""


@dataclass(frozen=True)
class ResultadoScript:
    """Salida de un script del workspace."""

    stdout: str
    stderr: str
    codigo: int

    @property
    def ok(self) -> bool:
        return self.codigo == 0

    @property
    def texto(self) -> str:
        """Salida limpia: sin espacios sobrantes en los extremos."""
        return self.stdout.strip()


def entorno_openclaw_disponible(settings: Settings | None = None) -> tuple[bool, str]:
    """Comprueba que el workspace de OpenClaw está montado.

    Devuelve `(disponible, motivo)`. Un motivo concreto ahorra media hora de
    depuración cuando el contenedor arranca sin los volúmenes montados.
    """
    settings = settings or obtener_settings()

    if not PYTHON_VENV.exists():
        return False, f"No se encontró el intérprete del venv en {PYTHON_VENV}."

    if not settings.herramientas_scripts_dir.is_dir():
        return False, (
            f"No se encontró la carpeta de scripts en "
            f"{settings.herramientas_scripts_dir}."
        )

    return True, ""


def copiar_script(nombre: str, settings: Settings | None = None) -> Path:
    """Copia un script del workspace a un directorio escribible.

    La copia se coloca en `<portal>/scripts/<nombre>`, es decir, un nivel por
    debajo de la raíz de trabajo del portal. Así las rutas relativas del script
    (`../temp/`, `../tts_models/`, `../.env`) resuelven a los directorios del
    portal, que sí son escribibles, y no al volumen de OpenClaw.

    Args:
        nombre: nombre del archivo, p. ej. ``analyze_image.py``.
        settings: configuración a usar; por defecto la del proceso.

    Returns:
        Ruta absoluta de la copia, lista para ejecutar.

    Raises:
        ErrorPuente: si el script original no existe.
    """
    settings = settings or obtener_settings()

    origen = settings.herramientas_scripts_dir / nombre
    if not origen.is_file():
        raise ErrorPuente(f"No se encontró el script {nombre} en {origen}.")

    destino = settings.data_dir / "runtime" / "scripts" / nombre
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origen, destino)

    return destino


def ejecutar_python(
    argumentos: list[str],
    *,
    cwd: Path,
    timeout: int,
    entorno_extra: dict[str, str] | None = None,
) -> ResultadoScript:
    """Ejecuta el intérprete del venv con los argumentos dados.

    No lanza excepción por código de salida distinto de cero: devuelve el
    resultado para que cada herramienta decida si un fallo es recuperable
    (p. ej. cuota de Gemini agotada → caer al motor local).
    """
    from app.core.proceso import entorno_limpio

    entorno = entorno_limpio(entorno_extra)

    proceso = subprocess.run(
        [str(PYTHON_VENV), *argumentos],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=entorno,
    )

    return ResultadoScript(
        stdout=proceso.stdout or "",
        stderr=proceso.stderr or "",
        codigo=proceso.returncode,
    )


def guardar_subida(contenido: bytes, sufijo: str, *,
                   settings: Settings | None = None) -> Path:
    """Guarda bytes subidos con un nombre generado por el servidor.

    El nombre nunca viene del cliente: se compone con ``uuid4`` y un sufijo
    derivado del tipo ya validado por magic bytes.
    """
    settings = settings or obtener_settings()
    settings.preparar_directorios()

    identificador = uuid.uuid4().hex[:12]
    ruta = settings.upload_dir / f"{identificador}{sufijo}"
    ruta.write_bytes(contenido)
    return ruta


def borrar_silencioso(ruta: Path) -> None:
    """Elimina un archivo temporal sin propagar errores."""
    try:
        ruta.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - defensivo
        pass
