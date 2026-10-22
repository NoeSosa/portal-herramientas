# Correcciones y verificación de peticiones del panel público

Fecha: 2026-10-22, America/Mexico_City.

## Resultado

Se aplicaron correcciones de infraestructura y validación que conservan el flujo
normal de acceso, carga, cola, descarga y administración. **39 pruebas locales
pasaron**, incluidas las nueve existentes. `pip check` no encontró incompatibilidades
y `pip-audit` no reportó vulnerabilidades conocidas en el entorno de pruebas
actualizado. Se usó la skill `security-best-practices`, con sus referencias
FastAPI y JavaScript. No se desplegó en producción.

## Cambios aplicados

| Área | Corrección | Archivos |
|---|---|---|
| Dependencias HTTP | Versiones explícitas del panel: FastAPI 0.159.1, Starlette 1.9.0, Pydantic 2.14.1, Uvicorn 0.54.0, Authlib 1.9.1 y python-multipart 0.0.32; también se fijaron cryptography, httpx, itsdangerous y openpyxl a las versiones probadas. La capa del panel reemplaza las versiones HTTP heredadas; el portal privado conserva sus requirements. | `panel_web/requirements.txt` |
| Autenticación antes del cuerpo | Comprobación de sesión para las API privadas antes de parsear JSON/multipart; los endpoints conservan sus dependencias y controles por rol/propietario. | `app/peticiones.py`, `app/main.py` |
| CSRF | Se comprueba antes de consumir archivos; token requerido y validación adicional del `Origin` recibido. Comparación segura también ante cabeceras no ASCII. | `app/auth.py`, `app/peticiones.py` |
| Tamaño | Límite de cuerpo por ruta, validación de Content-Length y conteo de bytes recibidos para solicitudes sin longitud/chunked. | `app/peticiones.py` |
| Ráfagas de autenticación | Ventana de 120/minuto por IP observada y proceso, almacenamiento acotado a 2048 claves, 429 con Retry-After. | `app/peticiones.py` |
| JSON administrativo | Modelos de entrada rechazan objetos/listas en campos de texto y longitudes excesivas; conservan nombres de campos y compatibilidad de contadores numéricos. | `app/esquemas.py`, `app/main.py` |
| Proxy de Dokploy | Buffering de cuerpo, rate limit y límite de solicitudes en vuelo en el router HTTPS. | `docker-compose.dokploy.yml` |
| IP confiable | Se eliminó `--forwarded-allow-ips=*`; la auditoría consume la IP normalizada por Uvicorn, sin tomar directamente X-Forwarded-For. Configuración de proxies explícitos mediante FORWARDED_ALLOW_IPS. | `Dockerfile`, `app/auth.py`, `app/config.py`, `.env.example` |
| Secretos del OCR | Worker sin env_file compartido: recibe solo variables operativas. Su configuración puede arrancar sin secretos de autenticación; el servidor web los sigue exigiendo. El motor hereda una allowlist de variables de runtime. | Ambos Compose, `app/config.py`, `app/worker.py` |
| Cancelación | El motor se ejecuta en un grupo propio en Linux; parada/timeout terminan también lectores hijos, aplican TERM/KILL y esperan al proceso. | `app/worker.py` |
| Permisos | Init asigna 0700 a directorios y 0600 a archivos, sin convertir documentos/modelos existentes en ejecutables. | Ambos Compose |

## Qué se verificó en HTTP local

Las pruebas usan el middleware real de sesión y las dependencias reales de
autorización. Las cookies de prueba se firman con una clave ficticia; no existe
un endpoint para generar sesiones de prueba y no se habilitó ningún bypass.

- 401 en API privadas y escrituras anónimas, antes de parsear archivos.
- 403 en consultas y escritura administrativas con usuario normal.
- Aislamiento entre usuarios en listado, consulta, descarga y borrado.
- CSRF ausente, incorrecto y no ASCII; origen ajeno incluso con token válido.
- Funcionamiento de cliente API sin Origin con cookie y CSRF válidos.
- Cookies alteradas o vencidas rechazadas; flags HttpOnly/Secure/SameSite/Path.
- Host no autorizado rechazado; sin CORS permisivo para un origen ajeno.
- Límites de cuerpo por Content-Length y por bytes realmente recibidos, con
  JSON chunked y multipart chunked.
- Ráfagas locales de callback: 429 y Retry-After; una cabecera X-Forwarded-For
  añadida directamente no cambia la clave del limitador en la aplicación.
- Redirección OIDC con state, nonce, response_type=code y callback fijo.
- Callback con state inventado rechazado antes de canjear código: se usa Authlib
  real y se comprueba que no se llama a su método de canje.
- Escape de HTML en errores de callback; CSP, nosniff, DENY y no-store.
- `.env`, `.git/config`, documentación OpenAPI y HTML admin estático inaccesibles.
- Esquemas administrativos inválidos devuelven 422 y consulta con texto de
  inyección SQL no amplía resultados.
- Subida de un PDF real de una página, lectura de su trabajo y descarga propia;
  alta y edición de inventario con exportación XLSX.

Los tests de worker verifican ausencia de secretos, arranque separado de web y
llamadas de terminación de grupo. En esta máquina Windows, las llamadas POSIX se
simulan: no se ha ejecutado el árbol OCR real de Linux.

## Reproducción

Con Python 3.11+ y un entorno virtual activo, desde la raíz:

```sh
python -m pip install -r panel_web/requirements-test.txt
python -m unittest discover -s panel_web/tests -v
python -m pip check
python -m pip_audit
node --check panel_web/static/app.js
node --check panel_web/static/admin.js
```

En el sandbox Windows se usó Python 3.12.14 y un temporal dentro de `.venv/tmp`;
el cliente HTTP local requirió ejecución fuera del sandbox de red para funcionar.
El setuptools antiguo creado inicialmente por venv produjo avisos; se actualizó
a 84.2.1 en ese entorno y el escaneo final terminó sin avisos conocidos.

Ambos Compose se pudieron parsear como YAML y se comprobaron worker sin red ni
env_file y servidor sin puertos publicados. No está disponible Docker en esta
máquina: **no se ejecutó `docker compose config`, build ni Trivy**.

## Aplicación en servidor

1. Configurar FORWARDED_ALLOW_IPS con la IP o CIDR exclusivo del proxy real.
   Sin ese ajuste se usa la IP del proxy para auditoría y limitación de auth;
   no se debe confiar en una red compartida completa.
2. Reconstruir la imagen del panel y recrear web/worker; aplicar los labels de
   middleware si el despliegue usa Dokploy. Si el proxy tiene IP dinámica,
   usar un mecanismo de red exclusivo y estable para fijar la confianza.
3. Validar OIDC con una cuenta autorizada, subida/descarga y rechazo de usuarios
   ajenos; comprobar límites/cabeceras del proxy y auditar la imagen efectiva.

La verificación de HTTP de este cambio es **local**, sin ataques ni fuerza bruta
contra el servidor público. El escaneo de paquetes corresponde al entorno local,
no a todas las dependencias OCR/sistema de la imagen de producción. El uso actual
de httpx con las versiones nuevas emite avisos de deprecación; sigue funcionando
en las pruebas y no se cambió a httpx2 para evitar otra migración en este alcance.

## Riesgos que siguen abiertos

Estos cambios no convierten la auditoría previa en una certificación de seguridad.
Se mantuvieron separados los cambios que afectan identidad, persistencia o reglas
de negocio:

- **AUD-02:** unión de cuentas por correo/OID y autorización por correo: requiere
  revisar identidades existentes y política de permisos antes de migrar.
- **AUD-03:** revocación individual de sesiones y baja inmediata en Entra: requiere
  persistencia/versión de sesiones. Logout sigue siendo GET y no revoca copias.
- **AUD-04:** cuota diaria restituida al borrar un trabajo: sigue pendiente un
  contador persistente y una política de reserva transaccional.
- **AUD-07, parcial:** worker ya no recibe secretos web, pero continúa compartiendo
  `/data` y UID con el servidor. El parseo PDF previo sigue en el proceso web.
- **AUD-06, parcial:** limitador de auth es local al proceso y no distribuye estado;
  no sustituye un WAF, límites de conexión/tiempo ni controles del tenant.
- MFA, Smart Lockout, asignación requerida, políticas Entra y configuración real
  de Traefik/firewall necesitan verificación con acceso al entorno correspondiente.
- Se conserva el motor OCR y la política documentada de no instalar antivirus;
  no se efectuó una actualización del motor ni una prueba OCR ARM64 completa.

Las mejoras visuales/de accesibilidad de la auditoría anterior no se incluyeron
en este cambio de seguridad.
