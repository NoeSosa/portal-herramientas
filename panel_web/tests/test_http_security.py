"""Peticiones HTTP locales: cookies reales firmadas, sin depender de Microsoft.

Las identidades son fixtures; no se sustituyen las dependencias de autorización.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import fitz
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Solo credenciales ficticias. Nunca leer .env ni contactar Entra en estas pruebas.
TEST_ENV = {
    "MS_TENANT_ID": "test-tenant", "MS_CLIENT_ID": "test-client",
    "MS_CLIENT_SECRET": "not-a-real-client-secret",
    "SESSION_SECRET": "test-only-session-key-with-at-least-32-characters",
    "ALLOWED_DOMAIN": "example.invalid", "ALLOWED_EMAILS": "",
    "ADMIN_EMAILS": "admin@example.invalid", "ALLOWED_HOSTS": "testserver",
    "DOMAIN": "testserver", "MS_AUTHORITY": "",
    "MS_REDIRECT_URI": "https://testserver/auth/callback",
    "COOKIE_SECURE": "true", "APP_ENV": "production",
}
with patch.dict(os.environ, TEST_ENV):
    from panel_web.app import auth, db
    main = importlib.import_module("panel_web.app.main")

from panel_web.app.peticiones import LimiteAutenticacion


class SeguridadHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        config = replace(main.settings, data_dir=data, db_path=data / "panel.db",
                         inventory_dir=data / "inventarios", max_upload_mb=1)
        self.enterContext(patch.object(main, "settings", config))
        self.enterContext(patch.object(auth, "settings", config))
        self.enterContext(patch.object(auth, "limite_subidas", auth.LimiteSubidas(100)))
        self.enterContext(patch.object(main, "limite_subidas", auth.limite_subidas))
        # Nueva instancia de middleware por prueba, incluida la ventana antiabuso.
        main.app.middleware_stack = None
        self.client = self.enterContext(TestClient(main.app, base_url="https://testserver"))
        con = db.conectar(config.db_path)
        try:
            self.ana = db.upsert_usuario(con, "oid-ana", "ana@example.invalid", "Ana", "user")
            self.beto = db.upsert_usuario(con, "oid-beto", "beto@example.invalid", "Beto", "user")
            self.admin = db.upsert_usuario(con, "oid-admin", "admin@example.invalid", "Admin", "admin")
            for usuario, jid in ((self.ana, "job-ana"), (self.beto, "job-beto")):
                db.crear_job(con, job_id=jid, owner_id=usuario["id"],
                             owner_email=usuario["email"], filename="documento.pdf",
                             stored_name="documento.pdf", size=50, pages=1, client_ip=None)
        finally:
            con.close()
        self.csrf = "test-csrf-token"

    def sesion(self, usuario=None, *, incluir_csrf=True):
        usuario = usuario or self.ana
        payload = {"uid": usuario["id"], "email": usuario["email"], "csrf": self.csrf}
        if not incluir_csrf:
            payload.pop("csrf")
        token = TimestampSigner(main.settings.session_secret).sign(
            base64.b64encode(json.dumps(payload).encode())
        ).decode()
        self.client.cookies.clear()
        self.client.cookies.set("pdfsep_session", token)
        return token

    def headers(self):
        return {"X-CSRF-Token": self.csrf, "Origin": "https://testserver"}

    @staticmethod
    def pdf():
        with fitz.open() as doc:
            doc.new_page()
            return doc.tobytes()

    def test_apis_privadas_requieren_sesion(self):
        for ruta in ("/api/me", "/api/jobs", "/api/jobs/job-ana",
                     "/api/jobs/job-ana/download", "/api/admin/overview",
                     "/api/admin/jobs", "/api/admin/jobs/job-ana/progress",
                     "/api/admin/jobs/job-ana/page/1.png", "/api/admin/inventario",
                     "/api/admin/equipos", "/api/admin/sedes"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 401)

    def test_escrituras_anonimas_se_rechazan_antes_de_parsear(self):
        with patch.object(main, "_validar_pdf") as validar:
            r = self.client.post("/api/jobs", content=b"invalid multipart",
                                 headers={"Content-Type": "multipart/form-data"})
            self.assertEqual(r.status_code, 401)
            validar.assert_not_called()
        for metodo, ruta in (("POST", "/api/admin/equipos"),
                             ("POST", "/api/admin/equipos/borrar"),
                             ("DELETE", "/api/admin/equipos/SERIAL"),
                             ("DELETE", "/api/jobs/job-ana"),
                             ("POST", "/api/admin/inventario/importar"),
                             ("POST", "/api/admin/inventario/exportar")):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.request(metodo, ruta).status_code, 401)

    def test_usuario_no_puede_acceder_a_admin(self):
        self.sesion()
        for ruta in ("/api/admin/overview", "/api/admin/jobs", "/api/admin/inventario",
                     "/api/admin/equipos", "/api/admin/sedes",
                     "/api/admin/jobs/job-beto/progress", "/api/admin/jobs/job-beto/page/1.png"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 403)
        self.assertEqual(self.client.get("/admin").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/equipos", json={"sn": "X"},
                                         headers=self.headers()).status_code, 403)

    def test_aislamiento_lectura_descarga_y_borrado(self):
        self.sesion()
        jobs = self.client.get("/api/jobs").json()["jobs"]
        self.assertEqual([j["id"] for j in jobs], ["job-ana"])
        for sufijo in ("", "/download"):
            self.assertEqual(self.client.get("/api/jobs/job-beto" + sufijo).status_code, 404)
        self.assertEqual(self.client.delete("/api/jobs/job-beto", headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/job-ana").status_code, 200)

    def test_csrf_ausente_incorrecto_y_unicode(self):
        self.sesion(self.admin)
        for token in (None, "incorrecto", "áéí"):
            headers = {} if token is None else {"X-CSRF-Token": token.encode()}
            for ruta in ("/api/jobs", "/api/admin/equipos", "/api/admin/equipos/borrar",
                         "/api/admin/inventario/importar", "/api/admin/inventario/exportar"):
                with self.subTest(token=token, ruta=ruta):
                    self.assertEqual(self.client.post(ruta, headers=headers).status_code, 403)
            self.assertEqual(self.client.delete("/api/jobs/job-ana", headers=headers).status_code, 403)

    def test_origen_ajeno_aun_con_csrf_valido(self):
        self.sesion()
        for origin in ("https://evil.invalid", "null", "https://testserver.evil.invalid"):
            r = self.client.delete("/api/jobs/job-ana",
                                   headers={"X-CSRF-Token": self.csrf, "Origin": origin})
            self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/jobs/job-ana").status_code, 200)

    def test_subida_valida_y_descarga_del_dueno(self):
        self.sesion()
        r = self.client.post("/api/jobs", headers=self.headers(),
                             files={"archivo": ("escaneo.pdf", self.pdf(), "application/pdf")})
        self.assertEqual(r.status_code, 201, r.text)
        job = r.json()
        self.assertEqual(job["pages"], 1)
        self.assertEqual(job["status"], "queued")
        con = db.conectar(main.settings.db_path)
        try:
            zip_path = main._job_dir(self.ana["id"], job["id"]) / "resultado.zip"
            zip_path.write_bytes(b"PK\x03\x04test-result")
            db.finalizar_job(con, job["id"], str(zip_path), 1)
        finally:
            con.close()
        r = self.client.get(f"/api/jobs/{job['id']}/download")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"PK"))
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_pdf_invalido_no_crea_trabajo(self):
        self.sesion()
        r = self.client.post("/api/jobs", headers=self.headers(),
                             files={"archivo": ("fake.pdf", b"not a PDF", "application/pdf")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self.client.get("/api/jobs").json()["jobs"]), 1)

    def test_content_length_sobredimensionado_se_rechaza(self):
        self.sesion()
        for ruta in ("/api/jobs", "/api/admin/equipos"):
            r = self.client.post(ruta, headers={**self.headers(), "Content-Length": "999999999"},
                                 content=b"x")
            self.assertEqual(r.status_code, 413)

    def test_limite_tambien_sin_content_length(self):
        self.sesion()
        r = self.client.post("/api/jobs", headers=self.headers(), files={
            "archivo": ("big.pdf", b"x" * (2 * 1024 * 1024 + 1), "application/pdf")})
        self.assertEqual(r.status_code, 413)
        # El generador usa Transfer-Encoding chunked y evita el precheck de longitud.
        self.sesion(self.admin)
        r = self.client.post("/api/admin/equipos", headers={**self.headers(),
                             "Content-Type": "application/json"},
                             content=iter([b'{"sn":"', b"x" * (1024 * 1024), b'"}']))
        self.assertEqual(r.status_code, 413, r.text)

    def test_multipart_chunked_sobredimensionado(self):
        self.sesion()
        partes = [b'--audit\r\nContent-Disposition: form-data; name="archivo"; filename="large.pdf"\r\n'
                  b'Content-Type: application/pdf\r\n\r\n',
                  b"x" * (2 * 1024 * 1024 + 1), b"\r\n--audit--\r\n"]
        r = self.client.post("/api/jobs", headers={**self.headers(),
                             "Content-Type": "multipart/form-data; boundary=audit"},
                             content=iter(partes))
        self.assertEqual(r.status_code, 413, r.text)

    def test_ip_audit_no_toma_cabecera_sin_validar(self):
        from starlette.requests import Request
        request = Request({"type": "http", "client": ("192.0.2.7", 1234),
                           "headers": [(b"x-forwarded-for", b"203.0.113.99")]})
        self.assertEqual(auth.ip_cliente(request), "192.0.2.7")

    def test_cookie_modificada_no_autentica(self):
        token = self.sesion()
        self.client.cookies.clear()
        payload, timestamp, signature = token.rsplit(".", 2)
        signature = ("a" if signature[0] != "a" else "b") + signature[1:]
        self.client.cookies.set("pdfsep_session", ".".join((payload, timestamp, signature)))
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_cookie_tiene_flags_seguros(self):
        # El middleware actual solo reemite la cookie cuando cambia la sesión.
        self.sesion(incluir_csrf=False)
        r = self.client.get("/api/me")
        cookie = r.headers["set-cookie"].lower()
        for flag in ("httponly", "secure", "samesite=lax", "path=/"):
            self.assertIn(flag, cookie)

    def test_host_invalido_y_cors_no_permisivo(self):
        self.assertEqual(self.client.get("/", headers={"Host": "evil.invalid"}).status_code, 400)
        r = self.client.options("/api/jobs", headers={"Origin": "https://evil.invalid",
                                      "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", r.headers)

    def test_cabeceras_en_exito_y_rechazos(self):
        for ruta, status in (("/", 200), ("/api/jobs", 401), ("/.env", 404)):
            r = self.client.get(ruta)
            self.assertEqual(r.status_code, status)
            self.assertEqual(r.headers["x-content-type-options"], "nosniff")
            self.assertEqual(r.headers["x-frame-options"], "DENY")
            self.assertIn("script-src 'self'", r.headers["content-security-policy"])
            self.assertNotIn("unsafe-inline", r.headers["content-security-policy"])
        self.assertEqual(self.client.get("/api/jobs").headers["cache-control"], "no-store")

    def test_rutas_sensibles_y_documentacion_no_publicas(self):
        for ruta in ("/.env", "/.git/config", "/docs", "/redoc", "/openapi.json", "/static/admin.html"):
            self.assertEqual(self.client.get(ruta).status_code, 404)
        r = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/login")

    def test_callback_escapa_html_y_limita_rafagas(self):
        r = self.client.get("/auth/callback", params={"error": "access_denied",
                                                   "error_description": "<script>alert(1)</script>"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("<script>", r.text)
        self.assertIn("&lt;script&gt;", r.text)
        # Sin llamadas a Microsoft; todos son callbacks de error fabricados localmente.
        for _ in range(119):
            self.assertEqual(self.client.get("/auth/callback?error=access_denied").status_code, 400)
        r = self.client.get("/auth/callback?error=access_denied",
                            headers={"X-Forwarded-For": "203.0.113.123"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["retry-after"], "60")

    def test_cliente_api_con_csrf_y_sin_origin_sigue_funcionando(self):
        self.sesion()
        r = self.client.delete("/api/jobs/job-ana", headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(r.status_code, 204)

    def test_oidc_redirige_con_state_nonce_y_callback_fijo(self):
        metadata = {"authorization_endpoint": "https://login.example.invalid/authorize",
                    "token_endpoint": "https://login.example.invalid/token"}
        with patch.object(auth.oauth.microsoft, "load_server_metadata",
                          new=AsyncMock(return_value=metadata)):
            r = self.client.get("/auth/login", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        url = urlsplit(r.headers["location"])
        params = parse_qs(url.query)
        self.assertEqual(url.netloc, "login.example.invalid")
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["redirect_uri"], ["https://testserver/auth/callback"])
        self.assertTrue(params["state"][0])
        self.assertTrue(params["nonce"][0])

    def test_oidc_state_falso_no_canjea_codigo(self):
        with patch.object(auth.oauth.microsoft, "fetch_access_token", new=AsyncMock()) as canje, \
             self.assertLogs("panel_web", level="ERROR"):
            r = self.client.get("/auth/callback?state=inventado&code=falso")
        self.assertEqual(r.status_code, 400)
        canje.assert_not_awaited()
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_cookie_vencida_no_autentica(self):
        import time
        with patch("itsdangerous.timed.time.time", return_value=time.time() - 9 * 3600):
            self.sesion()
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_busqueda_sql_no_amplia_resultados(self):
        self.sesion(self.admin)
        r = self.client.get("/api/admin/equipos", params={"q": "' OR 1=1 --"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["equipos"], [])

    def test_json_admin_invalido_no_produce_500(self):
        self.sesion(self.admin)
        for datos in ([], {"sn": {"nested": "value"}}, {"sn": "x" * 257}):
            self.assertEqual(self.client.post("/api/admin/equipos", json=datos,
                                             headers=self.headers()).status_code, 422)
        r = self.client.post("/api/admin/equipos/borrar", json={"confirm": ["BORRAR"]},
                             headers=self.headers())
        self.assertEqual(r.status_code, 422)

    def test_alta_edicion_inventario_valida(self):
        self.sesion(self.admin)
        r = self.client.post("/api/admin/equipos", json={"sn": "ABC", "domicilio": "Sede A",
                             "contador_inicial": 123}, headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        r = self.client.post("/api/admin/equipos", json={"sn": "ABC", "marca": "Marca"},
                             headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        equipos = self.client.get("/api/admin/equipos").json()["equipos"]
        self.assertEqual(len(equipos), 1)
        self.assertEqual(equipos[0]["sede"], "Sede A")
        self.assertEqual(equipos[0]["marca"], "Marca")


class LimiteAutenticacionTest(unittest.TestCase):
    def test_ventana_y_memoria_acotada(self):
        limite = LimiteAutenticacion(maximo=2, ventana=60, claves=2)
        with patch("panel_web.app.peticiones.time.monotonic", return_value=0):
            self.assertTrue(limite.permitir("a"))
            self.assertTrue(limite.permitir("a"))
            self.assertFalse(limite.permitir("a"))
            self.assertTrue(limite.permitir("b"))
            self.assertFalse(limite.permitir("c"))
        with patch("panel_web.app.peticiones.time.monotonic", return_value=61):
            self.assertTrue(limite.permitir("c"))


if __name__ == "__main__":
    unittest.main()
