# Panel web público del separador

Edición web multi-usuario del separador de PDFs. Cada usuario entra con su
cuenta de **Microsoft Entra ID**, sube sus propios PDFs, ve su cola y descarga
sus resultados. No puede ver ni descargar los de otro.

Es un servicio **separado** del panel clásico. El panel clásico sigue siendo la
consola de administración por túnel SSH.

## Arquitectura

```text
Internet
  │  HTTPS (Caddy / Let's Encrypt)
  ▼
panel-web (FastAPI + OIDC Entra)
  │  sesión, CSRF, autorización por owner_id
  ├── SQLite WAL: users, jobs, audit
  ├── /data/users/<uid>/jobs/<job_id>/...
  └── /data/panel_web.db

panel-worker (sin red)
  └── separador_auto.py con input/output por job
```

- `panel-web` y `panel-worker` comparten la imagen OCR y el volumen, no el
  workspace de OpenClaw.
- El worker corre con `network_mode: none`.
- Solo Caddy publica `80/443`; el panel y el worker no exponen puertos.

## Datos que necesito de Entra ID

En el `.env` del servidor, nunca en el chat:

| Variable | Dónde se obtiene |
|---|---|
| `MS_TENANT_ID` | Entra ID → Información general → **Id. de directorio (inquilino)** |
| `MS_CLIENT_ID` | App registration → Información general → **Id. de aplicación (cliente)** |
| `MS_CLIENT_SECRET` | Certificados y secretos → **Valor** del secreto |
| `MS_REDIRECT_URI` | `https://pdf-separator.duckdns.org/auth/callback` |
| `ALLOWED_DOMAIN` | Dominio corporativo, p. ej. `tuempresa.com` |
| `ALLOWED_EMAILS` | Opcional: los 5 correos exactos, separados por coma |
| `SESSION_SECRET` | `openssl rand -hex 32` |
| `ADMIN_EMAILS` | Opcional; en esta versión no hay pantallas de admin |

### Configuración de la app en Entra

1. **Registros de aplicaciones** → tu app → **Autenticación**:
   - Plataforma **Web**.
   - URI de redirección: `https://pdf-separator.duckdns.org/auth/callback`.
   - No uses `localhost` en producción.
2. **Certificados y secretos** → nuevo secreto de cliente. Copia el **Valor**
   a `MS_CLIENT_SECRET`; no el ID.
3. **Permisos de API** → Microsoft Graph → permisos delegados:
   `openid`, `profile`, `email`, `User.Read` (este último es opcional).
   Concede consentimiento de administrador.
4. **Configuración de token** (opcional pero recomendable): agrega el claim
   `email` si tu tenant no lo incluye por defecto. El panel acepta
   `email`, `preferred_username` o `upn`.
5. **Aplicaciones empresariales** → tu app → **Propiedades**:
   - Asignación requerida: **Sí**.
   - Asigna solo los 5 usuarios o el grupo autorizado.
6. Si usas `ALLOWED_EMAILS`, no hace falta que el dominio esté en
   `ALLOWED_DOMAIN`; el panel exige que el correo esté en la lista.
   Si el UPN de Entra termina en `@tuempresa.onmicrosoft.com`, usa la lista
   exacta de correos o configura el claim `email` con el dominio corporativo.

El flujo es **authorization code + OIDC**. La app valida el `tid` contra
`MS_TENANT_ID` y el correo contra la lista/dominio. No se guardan contraseñas.

### Si el tenant es External ID (CIAM)

`AADSTS500208` (“The domain is not a valid login domain for the account type”)
significa que el tipo de cuenta no coincide con el tenant/app. Si tu tenant es
**External ID for Customers (CIAM)**, la autoridad no es
`login.microsoftonline.com`, sino `ciamlogin.com`:

```dotenv
MS_AUTHORITY=https://<subdominio>.ciamlogin.com/<tenant-id>/v2.0
```

El resto de la configuración (client ID, secreto, redirect URI y tenant ID)
sigue igual. Si tu tenant es **Workforce** normal, deja `MS_AUTHORITY` vacío.

Antes de cambiar código, confirma en Entra:

- El dominio del correo está en **Configuración → Dominios** y está **verificado**.
- El usuario es **Member** del mismo tenant, no una cuenta personal ni un invitado
  de otro tenant.
- La app es **single-tenant** solo si los 5 usuarios pertenecen a ese tenant.
- En **Monitoring & health → Sign-in logs**, el fallo 500208 muestra el usuario,
  la app y el motivo exacto.

## SSL y DNS

1. En DuckDNS, el dominio `pdf-separator.duckdns.org` debe apuntar a la IP
   pública del servidor.
2. Abre `80/tcp` y `443/tcp` (y `443/udp` si usas HTTP/3).
3. Caddy obtiene el certificado de Let's Encrypt automáticamente al arrancar.
4. Si el puerto 80 está bloqueado, usa el reto DNS-01 de Caddy o el Traefik de
   Dokploy con su propio certificado.

Si ya usas Dokploy/Traefik para el 80/443, usa
[`docker-compose.dokploy.yml`](docker-compose.dokploy.yml): no incluye Caddy y
publica `panel-web` por la red de Dokploy. También puedes quitar el bloque
`caddy` de `docker-compose.yml`, pero la variante de Dokploy evita ese paso.

## Despliegue

En el servidor, desde la raíz del repositorio:

```bash
# 1. Asegurar la imagen OCR base (si ya existe, se reutiliza)
bash scripts/preparar_separador.sh
docker build -t herramientas-web-ocr:local -f backend/Dockerfile.ocr ./backend

# 2. Configuración
cd panel_web
cp .env.example .env
chmod 600 .env
# edita .env con los datos de Entra y ALLOWED_DOMAIN/EMAILS

# 3. Inventario (opcional, ver abajo)
mkdir -p inventarios

# 4. Levantar
docker compose up -d --build
docker compose ps
docker compose logs -f panel-web
```

Abre `https://pdf-separator.duckdns.org`. El primer acceso debe pasar por
Microsoft; si el correo no está autorizado, verás “Tu cuenta no está autorizada”.

### Con Dokploy

- Crea un proyecto/servicio de **Docker Compose** (no Stack) apuntando a
  `panel_web/docker-compose.dokploy.yml`; ya no incluye Caddy.
- Carga las variables de `.env.example` en el entorno de Dokploy.
- Asigna el dominio `pdf-separator.duckdns.org` al servicio `panel-web`, puerto
  `8000`, HTTPS con `letsencrypt`; después **redespliega** para aplicar los
  labels de Traefik.
- Mantén `panel-web` en la red privada; no expongas `panel-worker`.
- Protege el panel de Dokploy con SSO/IP allowlist.

## Inventario

El pipeline usa los Excel de inventario para validar seriales y asignar sede.
Colócalos en `panel_web/inventarios/` (se montan en `/data/inventarios:ro`).
Sin inventario, los equipos pueden terminar en la carpeta de revisión; el
panel funciona igual, pero la clasificación es menos precisa.

## Límites y capacidad

| Límite | Valor por defecto |
|---|---|
| Tamaño por PDF | 50 MB |
| Páginas por PDF | 500 |
| Páginas por usuario/día | 500 |
| Jobs activos por usuario | 3 |
| Subidas por hora | 10 |
| Usuarios autorizados | 5 |
| Retención de resultados | 30 días |
| Workers OCR | 2 |

Para 5 usuarios y 500 páginas/día, un worker y 2 OCR son suficientes. La cola
es global y se atiende de una en una; el panel muestra posición, páginas por
delante y ETA aproximada.

## Seguridad aplicada

- OIDC con tenant restringido, allowlist de correo y asignación en Entra.
- Sesión firmada, `HttpOnly`, `Secure`, `SameSite=Lax`; CSRF en POST/DELETE.
- CSP estricta, HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`.
- Autorización por `owner_id` en cada job, descarga, borrado y miniatura.
- PDF validado por magic bytes, tamaño y páginas; no se confía en el nombre.
- Límites de subida, cuota diaria y rate limit por usuario.
- Auditoría de login, subida, descarga, borrado, fin y error.
- Worker sin red, sin workspace de OpenClaw, non-root, `cap_drop: ALL`,
  `read_only`, `no-new-privileges`.
- Sin endpoints de sistema, logs, seriales ni control global para usuarios.
- Retención y borrado por usuario.

## Operación

```bash
# Logs
docker compose logs -f panel-web panel-worker

# Salud
curl https://pdf-separator.duckdns.org/api/health

# Backup de la base (con el stack parado o usando sqlite3 .backup)
docker compose exec panel-web python3 - <<'PY'
import sqlite3
src = sqlite3.connect('/data/panel_web.db')
dst = sqlite3.connect('/data/backup.db')
src.backup(dst)
dst.close(); src.close()
print('backup listo')
PY

# Estado de la cola (solo conteos; no hay panel admin público)
docker compose exec panel-web python3 - <<'PY'
import sqlite3
c = sqlite3.connect('/data/panel_web.db')
print(c.execute("select status, count(*), sum(pages) from jobs group by status").fetchall())
PY
```

## Validación local

```bash
python -m py_compile panel_web/app/*.py separador_pdfs/separador_auto.py
node --check panel_web/static/app.js
python -m unittest discover -s panel_web/tests -v
```

No hay credenciales de Entra en el repositorio. Sin ellas, la app no arranca:
es intencional.
