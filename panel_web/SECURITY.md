# Auditoría de seguridad — panel público

**Fecha:** 2026-10-21  
**Alcance:** `panel_web` (FastAPI + Entra ID), worker OCR, inventario, ZIPs,
contenedores y exposición de `pdf-separator.duckdns.org`.  
**Método:** revisión manual de código, pruebas automatizadas, consulta de avisos
en OSV y comprobaciones HTTP/TLS **no invasivas**. No se hizo pentest ni
explotación de vulnerabilidades.

No hay una skill de seguridad instalada en este harness; se aplicó revisión
OWASP/ASVS y se consultó [OSV](https://osv.dev/) para las versiones fijadas.

## Resumen ejecutivo

No se encontró bypass de autenticación, IDOR, SQL injection ni XSS reflejado en
los flujos revisados. Los controles de autorización por dueño, CSRF, CSP,
cookies, aislamiento del worker y hardening de contenedores están presentes.

Hallazgos relevantes:

| ID | Severidad | Estado | Hallazgo |
|---|---|---|---|
| SEC-01 | Media | Abierto | El PDF se parsea con PyMuPDF en el panel web público; riesgo de DoS/parser exploit y falta antivirus. |
| SEC-02 | Media | Corregido | Inyección de fórmulas al exportar el inventario a Excel. |
| SEC-03 | Baja | Corregido | `/static/admin.html` era accesible sin sesión. |
| SEC-04 | Baja | Aceptado | Logout por GET (CSRF de cierre de sesión). |
| SEC-05 | Baja | Abierto | Sin escaneo antimalware de PDFs. |
| SEC-06 | Baja | Aceptado | Rate limit de subidas en memoria; se reinicia con el proceso. |
| SEC-07 | Baja | Aceptado | Importación de Excel solo admin; puede consumir CPU/memoria. |
| SEC-08 | Informativa | Aceptado | CSP depende de Google Fonts para IBM Plex. |
| SEC-09 | Informativa | Aceptado | Cookie de sesión sin prefijo `__Host-` (flags correctos). |
| SEC-10 | Informativa | Abierto | Dependencias antiguas (PaddleOCR 2.7) y sin escaneo continuo de CVEs. |

## Hallazgos

### SEC-01 — Parseo de PDF en el panel web (Media, abierto)

`_validar_pdf()` abre el PDF con PyMuPDF en `panel-web` antes de encolarlo. Un
PDF malformado o muy complejo puede consumir CPU/memoria o explotar un fallo del
parser nativo. El worker corre sin red, pero el panel web sí tiene red.

Mitigaciones actuales:

- Límite de 50 MB y 500 páginas.
- Contenedor no-root, `read_only`, `cap_drop: ALL`, `mem_limit: 1g`.
- Parseo movido a `run_in_threadpool` para no bloquear el event loop.
- Worker OCR con `network_mode: none`.

Recomendación:

1. Validar páginas en un proceso/contenedor aislado sin red y con timeout, o
   aceptar el archivo y validarlo en el worker (que ya es network-none).
2. Añadir ClamAV/antivirus o un servicio de scanning antes de procesar.
3. Mantener PyMuPDF actualizado y ejecutar `pip-audit`/Trivy en CI.

### SEC-02 — Inyección de fórmulas en Excel (Media, corregido)

Los valores importados (serial, sede, domicilio, etc.) se exportaban a
`inventario_panel.xlsx`. Un valor que empiece con `=`, `+`, `-` o `@` podía
interpretarse como fórmula al abrir el Excel.

Corrección: `_celda_segura()` antepone `'` en `panel_web/app/inventario.py` y en
`separador_pdfs/panel/inventario.py`. Hay prueba automatizada.

### SEC-03 — HTML de administración estático (Baja, corregido)

`/static/admin.html` respondía 200 sin sesión. No contenía datos, pero exponía la
estructura de la consola. Se agregó una ruta que responde 404; la consola solo se
sirve por `/admin`, que valida sesión y rol. `/admin` sin sesión ahora redirige a
`/login`.

### SEC-04 — Logout por GET (Baja, aceptado)

`GET /auth/logout` puede ser disparado por un tercero (CSRF de logout). Solo
cierra la sesión; no expone datos. Si se quiere eliminar, convertir el enlace en
POST con token CSRF.

### SEC-05 — Sin antivirus (Baja, abierto)

No hay scanning de malware. Los PDFs se validan por estructura/tamaño/páginas,
pero no por contenido malicioso. Para 5 usuarios corporativos el riesgo es
acotado; se recomienda ClamAV o un servicio externo.

### SEC-06 — Rate limit en memoria (Baja, aceptado)

`LimiteSubidas` es por proceso y se reinicia al recrear el contenedor. Hay
cuotas diarias y máximo de jobs en SQLite, que sí persisten. Para 5 usuarios es
suficiente; si se escala, mover el limitador a Redis.

### SEC-07 — Importación de Excel (Baja, aceptado)

Solo un administrador puede importar. Límite de 20 MB y 50,000 filas; openpyxl
corre en hilo aparte. Un archivo comprimido malicioso podría consumir recursos.
Recomendación: límite de tiempo y ejecución en un proceso separado si se abre a
más administradores.

### SEC-08 — Google Fonts en CSP (Informativa, aceptado)

La CSP permite `fonts.googleapis.com` y `fonts.gstatic.com` para IBM Plex. Es una
dependencia de disponibilidad/privacidad, no una vulnerabilidad del panel. Se
puede self-hostear los WOFF2 y eliminar esos orígenes de la CSP.

### SEC-09 — Cookie de sesión (Informativa, aceptado)

La cookie `pdfsep_session` tiene `HttpOnly`, `Secure`, `SameSite=Lax`, `path=/`
y `Max-Age`. No usa prefijo `__Host-`; añadirlo es una mejora de defensa en
profundidad, no un fallo explotable.

### SEC-10 — Dependencias (Informativa, abierto)

Consulta OSV del 2026-10-21 para las versiones fijadas:

- `fastapi 0.115.6`, `uvicorn 0.34.0`, `pymupdf 1.26.0`,
  `paddleocr 2.7.0.3`, `numpy 2.4.6`, `opencv-python-headless 5.0.0.93`,
  `pandas 3.0.5`, `openpyxl 3.1.5`: **0 avisos conocidos** en OSV al momento.
- PaddleOCR 2.7 es una versión antigua; conviene planear actualización.
- No hay escaneo continuo de CVEs en el pipeline.

Comandos recomendados en el servidor:

```bash
pip-audit -r panel_web/requirements.txt
trivy image pdf-separator-web:local
docker compose -f panel_web/docker-compose.dokploy.yml config
```

## Controles que sí están bien

- OIDC con `state`/`nonce`, tenant validado, allowlist de correo y máximo 5
  usuarios.
- CSRF en POST/DELETE de la API; cookies `HttpOnly/Secure/SameSite`.
- Cada job, descarga, borrado y página se filtra por `owner_id`; admin separado.
- Sin SQL concatenado con datos de usuario; consultas parametrizadas.
- Nombres de archivo saneados y rutas validadas con `is_relative_to`.
- CSP, HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`,
  `Permissions-Policy`.
- `panel-web`: non-root, `read_only`, `cap_drop: ALL`,
  `no-new-privileges`.
- `panel-worker`: `network_mode: none`, sin workspace de OpenClaw, mismas
  restricciones.
- ZIPs y descargas solo para el dueño; inventario solo admin.
- Auditoría de login, subida, descarga, borrado, inventario y jobs.

## Evidencia de producción (no invasiva)

- `GET /api/jobs` y `/api/admin/overview` sin sesión: **401 JSON**.
- `/.env`, `/.git/config`, `/server-status`: **404**.
- `OPTIONS /api/admin/overview` y `TRACE /`: **405**.
- TLS: Let's Encrypt, CN `pdf-separator.duckdns.org`, válido hasta
  2027-01-18.
- HSTS de 1 año, CSP, `X-Frame-Options: DENY`, `nosniff`, sin header `Server`.
- Cookie de login: `HttpOnly; Secure; SameSite=Lax`.
- Antes de la corrección: `/static/admin.html` respondía 200; queda 404 al
  desplegar el commit actual.

## Prioridad sugerida

1. Desplegar las correcciones de esta auditoría y verificar `/static/admin.html`
   y `/admin`.
2. Añadir antivirus/aislamiento del parser de PDFs.
3. Automatizar `pip-audit` + Trivy en CI y revisar PaddleOCR.
4. Backup cifrado y disco cifrado para `/data`.
5. Opcional: logout POST, cookie `__Host-`, self-host de fuentes.

> Este informe es una revisión de código y configuración, no una garantía ni un
> pentest formal. Para producción regulada, complementar con un pentest externo.
