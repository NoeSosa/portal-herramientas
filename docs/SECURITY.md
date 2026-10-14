# Modelo de seguridad

Este documento explica **por qué** el portal es seguro, no solo qué medidas
tiene. Está pensado para revisarse antes de cambiar algo de infraestructura.

---

## 1. Modelo de amenaza

### Qué protegemos

- **Credenciales** en `.env`: claves de API (Gemini), contraseña de aplicación
  de Gmail, y cualquier credencial corporativa que se integre más adelante.
- **Datos personales:** el ledger de finanzas, transcripciones, imágenes.
- **El servidor:** donde corren Pi-hole, Tailscale y otros servicios de Noe.

### De quién nos protegemos

| Actor | Riesgo | Mitigación principal |
|---|---|---|
| Internet | Escaneo y explotación | El servicio nunca se publica a internet |
| Red local | Acceso no autorizado | Bind a `127.0.0.1` |
| Otros contenedores | Movimiento lateral | Red bridge aislada, sin `docker.sock` |
| La propia app | Bugs de validación | Validación de entrada + límites |

### Supuesto de confianza explícito

**El túnel SSH es la frontera de seguridad.** Quien tenga tu llave privada y
acceso al servidor entra al portal. Esto es una decisión deliberada: es el
mismo modelo que usa el dashboard de OpenClaw.

Consecuencia: **la llave SSH debe tener passphrase**, y el usuario `ubuntu` del
servidor debe tener autenticación por llave únicamente (sin contraseña).

---

## 2. Medidas por capa

### Red

```
✅ frontend:  ports: 127.0.0.1:8080:80     ← solo loopback
✅ backend:   sin ports:, solo expose:8000
✅ worker:    sin ports:, sin expose
✅ redis:     sin ports:, sin expose
✅ red bridge propia y aislada
✅ SIN labels de Traefik → invisible para el proxy público
```

**Por qué `expose` y no `ports` en el backend:** `expose` solo documenta que el
puerto se usa dentro de la red de Docker; no publica nada al host. El backend
solo es alcanzable por nginx a través de la red `portal`.

### Contenedores

```yaml
security_opt: [no-new-privileges:true]   # bloquea escalada vía setuid
cap_drop: [ALL]                          # sin capacidades de kernel
user: portal (uid 1001)                  # nunca root
read_only: true (frontend)               # raíz de solo lectura
tmpfs: [...]                             # lo mínimo escribible
```

**`no-new-privileges`** es importante: aunque un proceso consiguiera ejecutar un
binario con setuid, el kernel le negaría la elevación.

### Aplicación

- **`X-Forwarded-For` no se confía a ciegas.** nginx lo establece; el backend
  solo lo usa porque está detrás de nginx en la misma red.
- **CORS restringido** a los orígenes locales. En producción el navegador
  llega por el mismo origen, así que CORS es defensa adicional.
- **`docs` y `openapi.json` deshabilitados en producción.** No hay razón para
  exponer el esquema de la API a nadie.

### Entrada de datos

La validación de archivos (`app/core/seguridad.py`) es el punto más sensible:

1. **Magic bytes, no extensión.** Un archivo llamado `foto.jpg` que en
   realidad sea un ejecutable se detecta y se rechaza.
2. **Límite de tamaño** antes de procesar: evita agotamiento de memoria.
3. **Rechazo de rutas en el nombre.** `/`, `\`, `..` y `NUL` se bloquean: el
   cliente no decide dónde se guarda nada.
4. **Nombres generados por el servidor.** Siempre `uuid4()`, nunca el nombre
   del cliente, para escribir en disco.
5. **Límite de páginas en PDF.** Un PDF de 50 000 páginas agotaría la memoria
   durante el OCR.

### Ejecución de scripts

Al envolver los scripts existentes:

```python
✅ Comando como lista (sin shell):
   subprocess.run(["python3", str(script), texto, "--output", str(destino)])

❌ Nunca:
   subprocess.run(f"python3 {script} {texto}", shell=True)
```

Con `shell=True`, un texto como `; rm -rf /` se ejecutaría. Con lista de
argumentos, el texto es un argumento literal, no código.

Además: **`timeout` siempre.** Un script colgado no puede bloquear el worker
indefinidamente.

### Secretos

| Práctica | Razón |
|---|---|
| `.env` con `chmod 600` | Solo el dueño lee |
| `.env` en `.gitignore` | Nunca llega al repositorio |
| `.env.example` sin valores | Documenta sin filtrar |
| Sin secretos en `Dockerfile` | Quedarían en la historia de las capas |
| Sin secretos en `docker-compose.yml` | Se commitea; usa `env_file` |

**Lo que NO se hace:** pasar secretos como `ARG` o `ENV` en el build. Cualquiera
con acceso a la imagen puede leerlos con `docker history`.

### nginx

```nginx
X-Frame-Options: DENY              # sin clickjacking
X-Content-Type-Options: nosniff    # sin sniffing de tipos
Referrer-Policy: no-referrer       # sin fugas de URL
Content-Security-Policy: ...       # solo recursos propios
```

La CSP restringe `script-src` a `'self'`: aunque alguien lograra inyectar HTML,
no podría cargar un script externo.

**Deuda técnica conocida:** `style-src` incluye `'unsafe-inline'` porque Vite y
React inyectan estilos. Se puede endurecer migrando a CSS con hashes, pero el
beneficio es marginal en un servicio tras túnel SSH.

---

## 3. Lo que este portal NO hace (y por qué)

| No hace | Razón |
|---|---|
| Login propio | El túnel SSH ya autentica. Añadir login sin HTTPS propio daría falsa sensación de seguridad. |
| HTTPS interno | El tráfico no sale de la máquina (`127.0.0.1`). |
| Enviar correo desde la web | Desactivado por defecto: es una acción externa irreversible. |
| Tocar credenciales corporativas | ServiceNow y Humand quedan fuera. Regla de oro del workspace. |
| `docker.sock` en los contenedores | Un contenedor comprometido podría controlar todo el host. |

---

## 4. Lista de verificación antes de exponer algo nuevo

Si algún día decides publicar el portal por Traefik en vez de por túnel SSH:

- [ ] **Autenticación obligatoria.** Sin excepción.
- [ ] HTTPS con certificado válido (Traefik + Let's Encrypt ya lo da).
- [ ] Rate limiting en Traefik.
- [ ] Revisar que ninguna herramienta ejecute acciones irreversibles sin
      confirmación explícita.
- [ ] Rotar todas las claves que estén en `.env`.
- [ ] Revisar logs para asegurar que no filtran datos personales.
- [ ] Considerar `fail2ban` o similar contra fuerza bruta.
- [ ] Añadir cabecera `Strict-Transport-Security`.

---

## 5. Respuesta a incidentes

Si sospechas que el portal fue comprometido:

```bash
# 1. Cortar el acceso inmediatamente
docker compose down

# 2. Revisar logs antes de que rotes
docker compose logs backend worker > /tmp/incidente.log

# 3. Revisar si hubo escritura inesperada
docker compose exec backend find /data -newermt '-24 hours' -type f

# 4. Rotar credenciales del .env (Gemini, Gmail, y lo que haya)

# 5. Recostruir desde cero
docker compose build --no-cache
```

**Rotación prioritaria:** primero las claves de API, luego las contraseñas de
aplicación, y por último (si aplica) la llave SSH del servidor.
