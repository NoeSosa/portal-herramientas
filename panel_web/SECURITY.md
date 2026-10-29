# Auditoría de seguridad — panel público

> Actualización 2026-10-22: se implementaron controles HTTP, actualización de
> dependencias del panel y endurecimiento de worker/proxy. El estado actual,
> las 39 pruebas locales y los riesgos aún abiertos están en
> [SEGURIDAD_PETICIONES_2026-10-22.md](../docs/SEGURIDAD_PETICIONES_2026-10-22.md).
> Las conclusiones históricas de dependencias/aislamiento que siguen abajo no
> sustituyen esa verificación ni describen por sí solas la imagen desplegada.

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
| SEC-01 | Media | Aceptado con compensaciones | PDF parseado en el panel web; riesgo de DoS/parser exploit. Sin antivirus por decisión de riesgo. |
| SEC-02 | Media | Corregido | Inyección de fórmulas al exportar el inventario a Excel. |
| SEC-03 | Baja | Corregido | `/static/admin.html` era accesible sin sesión. |
| SEC-04 | Baja | Aceptado | Logout por GET (CSRF de cierre de sesión). |
| SEC-05 | Baja | Aceptado | Sin escaneo antimalware; usuarios de confianza y PDFs normalizados como datos. |
| SEC-06 | Baja | Aceptado | Rate limit de subidas en memoria; se reinicia con el proceso. |
| SEC-07 | Baja | Aceptado | Importación de Excel solo admin; puede consumir CPU/memoria. |
| SEC-08 | Informativa | Aceptado | CSP depende de Google Fonts para IBM Plex. |
| SEC-09 | Informativa | Aceptado | Cookie de sesión sin prefijo `__Host-` (flags correctos). |
| SEC-10 | Informativa | Aceptado temporalmente | PaddleOCR 2.7 pendiente de actualización; OSV sin avisos al corte. |

## Hallazgos

### SEC-01 — Parseo de PDF en el panel web (Media, aceptado con compensaciones)

`_validar_pdf()` abre el PDF con PyMuPDF en `panel-web` antes de encolarlo. Un
PDF malformado o muy complejo puede consumir CPU/memoria o explotar un fallo del
parser nativo. El worker corre sin red, pero el panel web sí tiene red.

Mitigaciones actuales:

- Límite de 50 MB y 500 páginas.
- Contenedor no-root, `read_only`, `cap_drop: ALL`, `mem_limit: 1g`.
- Parseo movido a `run_in_threadpool` para no bloquear el event loop.
- Worker OCR con `network_mode: none`.

Decisión (2026-10-21): no se instala ClamAV en esta versión. El producto se
sirve a un grupo de 5 personas de confianza, los archivos son escaneos y el
worker no tiene red. Se acepta el riesgo residual del parser con estas
compensaciones:

- Validación real de cabecera `%PDF-1.x`/`2.x`, apertura con PyMuPDF, rechazo de
  cifrados, límite de tamaño y páginas.
- Detección de contenido activo (`/JavaScript`, `/JS`, `/Launch`,
  `/EmbeddedFile`, `/RichMedia`, `/OpenAction`, `/AA`, `/XFA`) y de `%%EOF`.
  Los marcadores de alto riesgo (`JavaScript`, `JS`, `Launch`, `EmbeddedFile`,
  `RichMedia`) **se rechazan** en la subida; el resto se registra como aviso
  para revisión. La inspección es estructural (claves y acciones PDF), no sobre
  los bytes comprimidos de las imágenes, para evitar falsos positivos.
- El PDF se guarda como dato con modo `0600`; directorios `0700`; sin bit de
  ejecución; el worker no tiene red.
- Si aparece contenido activo o un PDF raro, se revisa manualmente.

Recomendación si cambia el modelo de confianza: ClamAV en modo sombra y luego
`enforce`, y aislamiento del parser en un proceso sin red. Mantener PyMuPDF
actualizado y ejecutar `pip-audit`/Trivy.

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

### SEC-05 — Sin antivirus (Baja, aceptado)

No hay scanning de malware. Los PDFs se validan por estructura/tamaño/páginas y
se revisan avisos de contenido activo; no se ejecuta nada del archivo. Para 5
usuarios corporativos el riesgo es acotado y se acepta conscientemente. Se
recomienda ClamAV solo si se abren usuarios externos, se reciben adjuntos de
terceros o se exige por cumplimiento.

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

### SEC-10 — Dependencias (Informativa, aceptado temporalmente)

Consulta OSV del 2026-10-21 para las versiones fijadas:

- `fastapi 0.115.6`, `uvicorn 0.34.0`, `pymupdf 1.26.0`,
  `paddleocr 2.7.0.3`, `numpy 2.4.6`, `opencv-python-headless 5.0.0.93`,
  `pandas 3.0.5`, `openpyxl 3.1.5`: **0 avisos conocidos** en OSV al momento.
- PaddleOCR 2.7 es una versión antigua; la actualización a 3.7.0 está
  documentada y diferida en [`docs/OCR_3.7_PENDIENTE.md`](../docs/OCR_3.7_PENDIENTE.md).
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
- PDFs guardados con permisos `0600` y directorios `0700`; sin bit ejecutable.
- Validación de cabecera real de PDF, estructura, cifrado y número de páginas;
  avisos de contenido activo sin bloqueo automático.
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
2. Mantener el control de contenido activo y revisar los avisos del audit.
3. Automatizar `pip-audit` + Trivy en CI y revisar PaddleOCR.
4. Backup cifrado y disco cifrado para `/data`.
5. Opcional: logout POST, cookie `__Host-`, self-host de fuentes.
6. Reevaluar ClamAV si cambia el modelo de confianza o lo exige cumplimiento.

> Este informe es una revisión de código y configuración, no una garantía ni un
> pentest formal. Para producción regulada, complementar con un pentest externo.
