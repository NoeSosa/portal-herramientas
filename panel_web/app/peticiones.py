"""Controles HTTP antes de que FastAPI parsee cuerpos multipart o JSON."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import auth


class LimiteAutenticacion:
    """Ventana por origen de red, con memoria acotada; complemento del proxy."""

    def __init__(self, maximo: int = 120, ventana: int = 60, claves: int = 2048):
        self.maximo, self.ventana, self.claves = maximo, ventana, claves
        self.eventos: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = threading.Lock()

    def permitir(self, clave: str) -> bool:
        ahora = time.monotonic()
        with self.lock:
            while self.eventos:
                _, primera = next(iter(self.eventos.items()))
                if primera and ahora - primera[-1] < self.ventana:
                    break
                self.eventos.popitem(last=False)
            cola = self.eventos.get(clave)
            if cola is None:
                # No expulsar una IP bloqueada para admitir claves nuevas.
                if len(self.eventos) >= self.claves:
                    return False
                cola = self.eventos[clave] = deque()
            self.eventos.move_to_end(clave)
            while cola and ahora - cola[0] >= self.ventana:
                cola.popleft()
            if len(cola) >= self.maximo:
                return False
            cola.append(ahora)
            return True


class GuardiaPeticiones:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.limite_auth = LimiteAutenticacion()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        ruta = scope["path"]
        try:
            if ruta in {"/auth/login", "/auth/callback"}:
                # No usar X-Forwarded-For sin validar para el limitador.
                clave = request.client.host if request.client else "desconocido"
                if not self.limite_auth.permitir(clave):
                    raise HTTPException(429, "Demasiados intentos. Espera un minuto.",
                                        headers={"Retry-After": "60"})

            if ruta.startswith("/api/") and ruta != "/api/health":
                # Las dependencias del endpoint siguen validando permisos y dueño.
                # Aquí se evita recibir/parsing de archivos de sesiones no válidas.
                auth.usuario_actual(request)
                if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                    auth.verificar_csrf(request)

            limite = 1024 * 1024  # JSON y peticiones sin archivo: 1 MiB.
            if ruta == "/api/jobs" and scope["method"] == "POST":
                limite += auth.settings.max_upload_bytes
            elif ruta == "/api/admin/inventario/importar" and scope["method"] == "POST":
                limite += 20 * 1024 * 1024
            longitudes = request.headers.getlist("content-length")
            if len(longitudes) > 1:
                raise HTTPException(400, "Content-Length inválido.")
            if longitudes:
                longitud = longitudes[0]
                if len(longitud) > 20 or not longitud.isascii() or not longitud.isdecimal():
                    raise HTTPException(400, "Content-Length inválido.")
                if int(longitud) > limite:
                    raise HTTPException(413, "La petición supera el límite permitido.")
        except HTTPException as exc:
            respuesta = JSONResponse({"detail": exc.detail}, exc.status_code,
                                     headers={"Cache-Control": "no-store", **(exc.headers or {})})
            await respuesta(scope, receive, send)
            return

        recibidos = 0

        async def recibir_limitado():
            nonlocal recibidos
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibidos += len(mensaje.get("body", b""))
                if recibidos > limite:
                    # ExceptionMiddleware del endpoint transforma esto en 413.
                    raise HTTPException(413, "La petición supera el límite permitido.")
            return mensaje

        await self.app(scope, recibir_limitado, send)
