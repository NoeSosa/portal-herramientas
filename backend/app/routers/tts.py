"""Herramienta: Texto a voz (TTS).

Envuelve `scripts/tts.py` (Piper, voces en español mexicano).

Entorno de ejecución: el compose monta el volumen de OpenClaw como solo lectura
en `/scripts` (código), `/venv` (binarios) y `/modelos` (modelos de voz). El
PDF de entrada y el WAV de salida van al volumen de datos del portal, que sí
es escribible.

Piper tarda segundos, no minutos: va en la cola rápida y se ejecuta en un hilo
para no bloquear el event loop de FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import obtener_settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Límite defensivo: un texto enorme saturaría la síntesis y el disco.
MAX_CARACTERES = 5000

# Voces disponibles en el script existente.
VOCES_VALIDAS = {"ald", "claude"}

# Tiempo máximo para una síntesis. Piper tarda segundos; 60 s es holgado.
TIMEOUT_SINTESIS = 60


class PeticionTTS(BaseModel):
    """Cuerpo de la petición de síntesis."""

    texto: str = Field(..., min_length=1, max_length=MAX_CARACTERES)
    voz: str = Field(default="ald")
    velocidad: float = Field(default=1.0, ge=0.5, le=2.0)


class RespuestaTTS(BaseModel):
    """Resultado de la síntesis."""

    archivo: str
    nombre_descarga: str
    caracteres: int


def _entorno_disponible() -> tuple[bool, str]:
    """Comprueba que el entorno de OpenClaw está montado.

    Devuelve (disponible, motivo). Un motivo claro ahorra depurar cuando el
    contenedor arranca sin los volúmenes montados.
    """
    settings = obtener_settings()

    script = settings.herramientas_scripts_dir / "tts.py"
    if not script.is_file():
        return False, f"No se encontró el script en {script}."

    piper = settings.herramientas_venv_bin / "piper"
    if not piper.exists():
        return False, f"No se encontró el binario piper en {piper}."

    modelos = settings.herramientas_modelos_dir
    if not modelos.is_dir() or not any(modelos.glob("*.onnx")):
        return False, f"No se encontraron modelos de voz en {modelos}."

    return True, ""


def _preparar_ejecucion(script_origen: Path, modelos: Path, destino: Path) -> Path:
    """Copia el script a un directorio escribible y ajusta sus rutas.

    Por qué no se ejecuta el script directamente desde el volumen:

      1. El volumen se monta en SOLO LECTURA, y `tts.py` hace
         `os.makedirs(OUTPUT_DIR)` incondicionalmente, aunque se le pase
         `--output`. Sin este truco, el script muere al crear su carpeta.

      2. El script resuelve los modelos como `../tts_models` relativo a sí
         mismo. Al copiarlo a otro directorio, esa ruta se rompe.

    La solución: copiar el script a `<destino>/tts.py`, sustituir su `TTS_DIR`
    por la ruta absoluta real de los modelos, y crear el `temp/tts_output`
    que espera. Así el original nunca se modifica.

    Returns:
        La ruta del script copiado, listo para ejecutar.
    """
    destino.mkdir(parents=True, exist_ok=True)

    codigo = script_origen.read_text(encoding="utf-8")

    # Reemplaza la resolución relativa de TTS_DIR por la ruta absoluta.
    marca = 'TTS_DIR = os.path.join(os.path.dirname(__file__), "..", "tts_models")'
    if marca in codigo:
        codigo = codigo.replace(marca, f'TTS_DIR = {str(modelos)!r}')
    else:
        # Si el script cambia esa línea, se antepone una asignación explícita
        # tras los imports para no depender del formato exacto.
        logger.warning(
            "No se encontró la línea de TTS_DIR en %s; se inyecta al inicio.",
            script_origen.name,
        )
        codigo = f"import os as _os\nTTS_DIR = {str(modelos)!r}\n" + codigo

    script_local = destino / script_origen.name
    script_local.write_text(codigo, encoding="utf-8")

    # El script hace makedirs de ../temp/tts_output relativo a sí mismo.
    (destino.parent / "temp" / "tts_output").mkdir(parents=True, exist_ok=True)

    return script_local


@router.get("/tts/estado")
async def estado_motor() -> dict[str, object]:
    """Informa si el motor de voz está operativo y qué voces hay."""
    settings = obtener_settings()
    disponible, motivo = _entorno_disponible()

    voces: list[str] = []
    if settings.herramientas_modelos_dir.is_dir():
        voces = sorted(p.stem for p in settings.herramientas_modelos_dir.glob("*.onnx"))

    return {"disponible": disponible, "motivo": motivo, "voces": voces}


@router.post("/tts", response_model=RespuestaTTS)
async def sintetizar(peticion: PeticionTTS) -> RespuestaTTS:
    """Convierte texto a audio y devuelve la referencia de descarga."""
    settings = obtener_settings()

    if peticion.voz not in VOCES_VALIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Voz no válida. Opciones: {', '.join(sorted(VOCES_VALIDAS))}.",
        )

    texto = peticion.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="El texto está vacío.")

    disponible, motivo = _entorno_disponible()
    if not disponible:
        logger.error("Motor TTS no disponible: %s", motivo)
        raise HTTPException(
            status_code=503,
            detail=f"El motor de voz no está disponible. {motivo}",
        )

    identificador = uuid.uuid4().hex[:12]
    destino = settings.output_dir / f"tts-{identificador}.wav"

    # El script se copia a un directorio escribible y se le apunta a los
    # modelos reales. Ver _preparar_ejecucion para el porqué.
    trabajo = settings.data_dir / "tts-runtime"
    script = _preparar_ejecucion(
        settings.herramientas_scripts_dir / "tts.py",
        settings.herramientas_modelos_dir,
        trabajo,
    )

    # El shebang del venv es `#!/home/node/.openclaw/workspace/venv/bin/python`,
    # que resuelve porque el volumen está montado en esa misma ruta.
    comando = [
        "/home/node/.openclaw/workspace/venv/bin/python",
        str(script),
        texto,
        "--voice",
        peticion.voz,
        "--speed",
        str(peticion.velocidad),
        "--output",
        str(destino),
    ]

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_ejecutar, comando, trabajo, TIMEOUT_SINTESIS),
            timeout=TIMEOUT_SINTESIS + 5,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail="La síntesis tardó demasiado."
        ) from exc
    except subprocess.CalledProcessError as exc:
        # El stderr del script es útil para el operador, pero puede contener
        # rutas internas: se registra completo y se devuelve un mensaje genérico.
        logger.error("TTS falló (código %s)\n%s", exc.returncode, (exc.stderr or "")[-2000:])
        raise HTTPException(
            status_code=500, detail="Falló la síntesis de voz."
        ) from exc

    if not destino.is_file():
        logger.error("TTS no produjo archivo en %s", destino)
        raise HTTPException(
            status_code=500, detail="El motor de voz no generó ningún archivo."
        )

    logger.info("TTS generado: %s (%d caracteres)", destino.name, len(texto))

    return RespuestaTTS(
        archivo=destino.name,
        nombre_descarga=f"voz-{identificador}.wav",
        caracteres=len(texto),
    )


@router.get("/tts/descargar/{archivo}")
async def descargar(archivo: str) -> FileResponse:
    """Sirve el audio generado.

    El nombre se valida contra un patrón estricto para impedir que alguien
    pida rutas arbitrarias del sistema (path traversal).
    """
    settings = obtener_settings()

    # Solo se aceptan nombres con el formato que genera este módulo.
    if (
        not archivo.startswith("tts-")
        or not archivo.endswith(".wav")
        or any(c in archivo for c in ("/", "\\", "..", "\x00"))
    ):
        raise HTTPException(status_code=400, detail="Nombre de archivo no válido.")

    ruta = (settings.output_dir / archivo).resolve()

    # Confirma que la ruta resuelta sigue dentro del directorio de salidas.
    if not ruta.is_relative_to(settings.output_dir.resolve()):
        raise HTTPException(
            status_code=400, detail="Ruta fuera del directorio permitido."
        )

    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Audio no encontrado.")

    return FileResponse(path=ruta, media_type="audio/wav", filename=archivo)


def _ejecutar(comando: list[str], cwd: Path, timeout: int) -> None:
    """Ejecuta el script de TTS de forma síncrona (se llama en un hilo)."""
    subprocess.run(
        comando,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd),
    )
