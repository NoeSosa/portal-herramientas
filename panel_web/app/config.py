"""Configuración del panel web público.

Todo se toma de variables de entorno. El proceso falla al arrancar si falta un
secreto o una regla de acceso: es preferible no levantar a levantar sin auth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _texto(nombre: str, defecto: str = "") -> str:
    return (os.getenv(nombre) or defecto).strip()


def _entero(nombre: str, defecto: int, minimo: int = 0) -> int:
    crudo = _texto(nombre, str(defecto))
    try:
        valor = int(crudo)
    except ValueError as exc:
        raise ValueError(f"{nombre} debe ser un entero, recibido: {crudo!r}") from exc
    if valor < minimo:
        raise ValueError(f"{nombre} debe ser >= {minimo}, recibido: {valor}")
    return valor


def _booleano(nombre: str, defecto: bool) -> bool:
    crudo = _texto(nombre, "1" if defecto else "0").lower()
    return crudo in {"1", "true", "yes", "si", "sí", "on"}


def _lista(nombre: str) -> frozenset[str]:
    crudo = _texto(nombre)
    return frozenset(x.strip().lower() for x in crudo.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    domain: str
    allowed_hosts: tuple[str, ...]

    data_dir: Path
    db_path: Path
    separator_code_dir: Path
    inventory_dir: Path
    static_dir: Path

    tenant_id: str
    authority_override: str
    client_id: str
    client_secret: str
    redirect_uri: str
    allowed_domain: str
    allowed_emails: frozenset[str]
    admin_emails: frozenset[str]
    session_secret: str
    cookie_secure: bool
    session_max_age: int

    max_upload_mb: int
    max_pages: int
    max_pages_per_day: int
    max_queued_jobs: int
    max_uploads_per_hour: int
    max_users: int
    ocr_workers: int
    job_timeout_seconds: int
    retention_days: int
    avg_seconds_per_page: float

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def authority(self) -> str:
        # Por defecto, nube pública de Microsoft Entra (workforce).
        # Para External ID (CIAM), nubes soberanas o B2C se define MS_AUTHORITY.
        return self.authority_override or (
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        )

    @property
    def metadata_url(self) -> str:
        return f"{self.authority}/.well-known/openid-configuration"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def correo_permitido(self, email: str) -> bool:
        correo = (email or "").strip().lower()
        if not correo or "@" not in correo:
            return False
        if self.allowed_emails:
            return correo in self.allowed_emails
        dominio = correo.rsplit("@", 1)[1]
        return dominio == self.allowed_domain or dominio.endswith("." + self.allowed_domain)

    def es_admin(self, email: str) -> bool:
        return (email or "").strip().lower() in self.admin_emails

    @classmethod
    def from_env(cls) -> "Settings":
        app_env = _texto("APP_ENV", "production").lower()
        domain = _texto("DOMAIN", "pdf-separator.duckdns.org")
        data_dir = Path(_texto("DATA_DIR", "/data")).resolve()

        tenant_id = _texto("MS_TENANT_ID")
        authority_override = _texto("MS_AUTHORITY").rstrip("/")
        client_id = _texto("MS_CLIENT_ID")
        client_secret = _texto("MS_CLIENT_SECRET")
        session_secret = _texto("SESSION_SECRET")
        allowed_domain = _texto("ALLOWED_DOMAIN").lower()
        allowed_emails = _lista("ALLOWED_EMAILS")
        admin_emails = _lista("ADMIN_EMAILS")

        faltantes = [
            nombre
            for nombre, valor in (
                ("MS_TENANT_ID", tenant_id),
                ("MS_CLIENT_ID", client_id),
                ("MS_CLIENT_SECRET", client_secret),
                ("SESSION_SECRET", session_secret),
            )
            if not valor
        ]
        if faltantes:
            raise ValueError(
                "Faltan variables obligatorias: " + ", ".join(faltantes)
            )
        if len(session_secret) < 32:
            raise ValueError("SESSION_SECRET debe tener al menos 32 caracteres.")
        if authority_override and not authority_override.startswith("https://"):
            raise ValueError("MS_AUTHORITY debe ser una URL https:// válida.")
        if not allowed_domain and not allowed_emails:
            raise ValueError(
                "Define ALLOWED_DOMAIN o ALLOWED_EMAILS: el panel no puede quedar abierto."
            )

        hosts = _lista("ALLOWED_HOSTS") or frozenset({domain, "localhost", "127.0.0.1"})
        if "*" in hosts:
            raise ValueError("ALLOWED_HOSTS no puede contener '*'.")

        return cls(
            app_name=_texto("APP_NAME", "Separador de PDFs"),
            app_env=app_env,
            domain=domain,
            allowed_hosts=tuple(sorted(hosts)),
            data_dir=data_dir,
            db_path=Path(_texto("DB_PATH", str(data_dir / "panel_web.db"))),
            separator_code_dir=Path(
                _texto("SEPARATOR_CODE_DIR", "/separador_pdfs")
            ),
            inventory_dir=Path(
                _texto("INVENTORY_DIR", str(data_dir / "inventarios"))
            ),
            static_dir=Path(__file__).resolve().parent.parent / "static",
            tenant_id=tenant_id,
            authority_override=authority_override,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=_texto(
                "MS_REDIRECT_URI", f"https://{domain}/auth/callback"
            ),
            allowed_domain=allowed_domain,
            allowed_emails=allowed_emails,
            admin_emails=admin_emails,
            session_secret=session_secret,
            cookie_secure=_booleano("COOKIE_SECURE", app_env != "development"),
            session_max_age=_entero("SESSION_MAX_AGE", 8 * 3600, 300),
            max_upload_mb=_entero("MAX_UPLOAD_MB", 50, 1),
            max_pages=_entero("MAX_PAGES", 500, 1),
            max_pages_per_day=_entero("MAX_PAGES_PER_DAY", 500, 1),
            max_queued_jobs=_entero("MAX_QUEUED_JOBS", 3, 1),
            max_uploads_per_hour=_entero("MAX_UPLOADS_PER_HOUR", 10, 1),
            max_users=_entero("MAX_USERS", 5, 1),
            ocr_workers=_entero("OCR_WORKERS", 2, 1),
            job_timeout_seconds=_entero("JOB_TIMEOUT_SECONDS", 7200, 60),
            retention_days=_entero("RETENTION_DAYS", 30, 1),
            avg_seconds_per_page=float(_texto("AVG_SECONDS_PER_PAGE", "12")),
        )


@lru_cache(maxsize=1)
def obtener_settings() -> Settings:
    return Settings.from_env()
