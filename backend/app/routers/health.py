"""Endpoints de salud y metadatos del servicio."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core.config import obtener_settings

router = APIRouter()


@router.get("/health")
async def salud() -> dict[str, str]:
    """Comprobación de vida usada por el healthcheck de Docker."""
    return {"status": "ok", "version": __version__}


@router.get("/info")
async def info() -> dict[str, object]:
    """Metadatos del servicio y capacidades detectadas.

    Útil para que el frontend sepa qué herramientas están realmente activas
    (por ejemplo, si hay clave de Gemini configurada).
    """
    settings = obtener_settings()
    return {
        "app": settings.app_name,
        "version": __version__,
        "entorno": settings.app_env,
        "limites": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pdf_pages": settings.max_pdf_pages,
            "job_timeout_seconds": settings.job_timeout_seconds,
        },
        "capacidades": {
            "vision": bool(settings.gemini_api_key),
            "transcripcion": bool(settings.gemini_api_key),
            "correo": settings.correo_habilitado,
        },
    }
