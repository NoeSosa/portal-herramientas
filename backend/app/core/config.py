"""Configuración de la aplicación, cargada desde variables de entorno.

Sigue el patrón de Pydantic Settings: una única fuente de verdad para toda la
configuración, validada al arranque. Si falta algo crítico, el proceso no
levanta (fail-fast) en lugar de fallar a mitad de una petición.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración validada del portal."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Aplicación ---------------------------------------------------------
    app_name: str = "Portal de Herramientas"
    app_env: str = "production"
    log_level: str = "INFO"

    # --- Límites ------------------------------------------------------------
    max_upload_mb: int = Field(default=50, ge=1, le=2048)
    max_pdf_pages: int = Field(default=500, ge=1, le=20000)
    job_timeout_seconds: int = Field(default=1800, ge=30, le=86400)

    # --- Rutas --------------------------------------------------------------
    data_dir: Path = Path("/data")
    upload_dir: Path = Path("/data/uploads")
    output_dir: Path = Path("/data/outputs")

    # --- Servicios ----------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"

    # --- Claves de herramientas --------------------------------------------
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3-flash-preview"

    herramientas_scripts_dir: Path = Path("/home/node/.openclaw/workspace/scripts")
    """Carpeta con los scripts de Python (tts.py, transcribir_av.py…).

    Se monta desde el volumen de OpenClaw en SOLO LECTURA: el portal nunca
    debe poder modificar los scripts del agente.
    """

    herramientas_venv_bin: Path = Path("/home/node/.openclaw/workspace/venv/bin")
    """Binarios del venv de OpenClaw (piper, python con sus dependencias)."""

    herramientas_modelos_dir: Path = Path("/home/node/.openclaw/workspace/tts_models")
    """Modelos de voz de Piper (es_MX-ald-medium.onnx y compañía)."""

    ocr_ld_library_path: str | None = None

    # --- Correo (desactivado por defecto) -----------------------------------
    gmail_user: str | None = None
    gmail_app_password: str | None = None

    @field_validator("log_level")
    @classmethod
    def _validar_log_level(cls, valor: str) -> str:
        permitidos = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalizado = valor.upper()
        if normalizado not in permitidos:
            raise ValueError(
                f"LOG_LEVEL debe ser uno de {sorted(permitidos)}, recibido: {valor!r}"
            )
        return normalizado

    @property
    def max_upload_bytes(self) -> int:
        """Límite de subida en bytes, derivado del límite en MB."""
        return self.max_upload_mb * 1024 * 1024

    @property
    def correo_habilitado(self) -> bool:
        """True solo si hay credenciales de correo completas."""
        return bool(self.gmail_user and self.gmail_app_password)

    def preparar_directorios(self) -> None:
        """Crea los directorios de datos si no existen.

        Idempotente: se puede llamar en cada arranque sin efectos adversos.
        """
        for ruta in (self.data_dir, self.upload_dir, self.output_dir):
            ruta.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def obtener_settings() -> Settings:
    """Devuelve la configuración, cacheada para reutilizarla en todo el proceso."""
    return Settings()
