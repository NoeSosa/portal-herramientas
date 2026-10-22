"""Autenticación con Microsoft Entra ID y utilidades de sesión/CSRF."""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request

from . import db
from .config import Settings, obtener_settings

settings: Settings = obtener_settings()

oauth = OAuth()
oauth.register(
    name="microsoft",
    client_id=settings.client_id,
    client_secret=settings.client_secret,
    server_metadata_url=settings.metadata_url,
    client_kwargs={"scope": "openid profile email"},
)


class LimiteSubidas:
    """Límite simple en memoria para evitar ráfagas de subida."""

    def __init__(self, maximo: int, ventana_s: int = 3600) -> None:
        self.maximo = maximo
        self.ventana = ventana_s
        self._eventos: dict[int, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, uid: int) -> bool:
        ahora = time.monotonic()
        with self._lock:
            cola = self._eventos[uid]
            while cola and (ahora - cola[0]) > self.ventana:
                cola.popleft()
            if len(cola) >= self.maximo:
                return False
            cola.append(ahora)
            return True


limite_subidas = LimiteSubidas(settings.max_uploads_per_hour)


def ip_cliente(request: Request) -> str:
    """Uvicorn normaliza la IP solo si el peer es un proxy autorizado."""
    return (request.client.host if request.client else "")[:64]


def token_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def crear_sesion(request: Request, usuario: sqlite3.Row) -> None:
    """Regenera la sesión al autenticar (evita fijación de sesión)."""
    request.session.clear()
    request.session["uid"] = int(usuario["id"])
    request.session["email"] = usuario["email"]
    request.session["csrf"] = secrets.token_urlsafe(32)


def cerrar_sesion(request: Request) -> None:
    request.session.clear()


def usuario_actual(request: Request) -> sqlite3.Row:
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="No autenticado.")
    con = db.conectar(settings.db_path)
    try:
        usuario = db.usuario_por_id(con, int(uid))
    finally:
        con.close()
    if not usuario:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sesión inválida.")
    return usuario


def verificar_csrf(request: Request) -> None:
    # El token sigue siendo obligatorio; Origin añade defensa frente a sitios ajenos.
    origen = request.headers.get("origin")
    destino = urlsplit(settings.redirect_uri)
    if origen and origen != f"{destino.scheme}://{destino.netloc}":
        raise HTTPException(status_code=403, detail="Origen de petición no permitido.")
    esperado = request.session.get("csrf")
    recibido = request.headers.get("x-csrf-token")
    if (
        not isinstance(esperado, str)
        or not recibido
        or not secrets.compare_digest(esperado.encode("utf-8"), recibido.encode("utf-8"))
    ):
        raise HTTPException(status_code=403, detail="Token CSRF inválido.")


def _campo(userinfo: dict, *nombres: str) -> str:
    for nombre in nombres:
        valor = userinfo.get(nombre)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return ""


async def autenticar_callback(request: Request) -> tuple[sqlite3.Row, dict]:
    """Canjea el código, valida tenant/correo y devuelve el usuario local."""
    token = await oauth.microsoft.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await oauth.microsoft.parse_id_token(request, token)
    if not userinfo:
        raise HTTPException(status_code=401, detail="Microsoft no devolvió identidad.")

    tid = _campo(userinfo, "tid")
    issuer = _campo(userinfo, "iss")
    tenant_ok = bool(tid and tid.lower() == settings.tenant_id.lower())
    # En algunos flujos de External ID/CIAM el claim `tid` no llega en
    # userinfo; el issuer sí contiene el tenant y sirve como validación.
    if not tenant_ok and not tid and issuer:
        tenant_ok = settings.tenant_id.lower() in issuer.lower()
    if not tenant_ok:
        raise HTTPException(status_code=403, detail="Tenant de Microsoft no permitido.")

    email = _campo(userinfo, "email", "preferred_username", "upn").lower()
    if not settings.correo_permitido(email):
        raise HTTPException(
            status_code=403,
            detail="Tu cuenta no está autorizada para este panel.",
        )

    oid = _campo(userinfo, "oid", "sub")
    if not oid:
        raise HTTPException(status_code=401, detail="Identidad de Microsoft incompleta.")

    nombre = _campo(userinfo, "name", "displayName") or email
    rol = "admin" if settings.es_admin(email) else "user"

    con = db.conectar(settings.db_path)
    try:
        existente = db.usuario_por_email(con, email)
        if not existente and db.contar_usuarios(con) >= settings.max_users:
            raise HTTPException(
                status_code=403,
                detail=f"El panel alcanzó el máximo de {settings.max_users} usuarios.",
            )
        usuario = db.upsert_usuario(con, oid, email, nombre, rol)
        db.auditar(
            con,
            user_id=int(usuario["id"]),
            email=email,
            action="login",
            ip=ip_cliente(request),
            detail="Entra ID",
        )
    finally:
        con.close()

    crear_sesion(request, usuario)
    return usuario, userinfo
