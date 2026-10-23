# Portal de Herramientas

Herramientas internas servidas por web, accesibles **solo por túnel SSH**.

En el mismo repositorio vive el taller del separador de PDFs (`separador_pdfs/`):
el pipeline de OCR y el panel que el portal publica en `/panel/`.

## Piezas

| Ruta | Qué es |
|---|---|
| `frontend/` | Portal web (React + Vite). nginx sirve la app y hace de proxy al backend. |
| `backend/` | API en FastAPI: finanzas, visión, transcripción, texto a voz, separador, canción. |
| `separador/` | Copia del motor del separador que se hornea en la imagen OCR. Si cambias el motor en `separador_pdfs/`, replica el cambio aquí antes de desplegar. |
| `separador_pdfs/` | Taller del separador: pipeline de OCR, panel completo y sus pruebas. |
| `panel_web/` | Edición pública multi-usuario con Microsoft Entra ID. Servicio separado; ver `panel_web/README.md`. |
| `docker-compose.yml` | Servicios: frontend, backend, worker, worker OCR, redis y panel. |
| `scripts/desplegar.sh` | Reconstruye imágenes y recrea contenedores sin `docker compose`. |
| `scripts/preparar_separador.sh` | Copia `separador/` a `backend/separador/` antes de construir la imagen OCR. |
| `docs/` | Diseño y notas de seguridad. |

El panel del separador se sirve en `/panel/` y su API en `/api/panel/`. El servicio
`panel` no publica puertos: nginx lo alcanza por la red interna del portal.

## Cómo se levanta

```bash
cp .env.example .env      # completar valores locales
bash scripts/desplegar.sh
```

`docker-compose.yml` es la fuente de verdad de la topología. Si cambia un puerto,
un volumen o una variable, hay que reflejarlo también en `scripts/desplegar.sh`.

El despliegue normal conserva la imagen OCR validada. En una instalación inicial,
si todavía no existe `herramientas-web-ocr:local`, su construcción es explícita:
`bash scripts/desplegar.sh --reconstruir-ocr`. No uses esa opción para aplicar
actualizaciones de bibliotecas HTTP o del frontend.

## Datos que no van al repositorio

`.env`, bases de datos, `node_modules/`, resultados y documentos escaneados quedan
fuera por `.gitignore`. Cada máquina aporta su configuración local.

## Qué es

Un portal que expone como interfaz web las herramientas que antes solo vivían
como scripts de línea de comandos en el workspace de OpenClaw.

**Filosofía del proyecto:**

- **Nada se expone a internet.** El frontend escucha únicamente en `127.0.0.1`
  del servidor. El único camino de entrada es un túnel SSH.
- **Sin autenticación en la aplicación.** La seguridad la aporta el túnel y tu
  llave SSH. Añadir login cuando ya hay túnel solo agrega fricción.
- **No reinventar lo que ya existe.** Para operaciones genéricas de PDF
  (unir, dividir, comprimir, firmar) usamos Stirling-PDF, que ya está
  desplegado. Este portal aporta la lógica propia.

> **Excepción deliberada:** `panel_web/` es una edición pública separada del
> separador, con Microsoft Entra ID y aislamiento por usuario. No forma parte
> del portal que va por túnel SSH ni monta el workspace de OpenClaw. Su modelo
> de seguridad está en `panel_web/README.md`.

## Arquitectura

```
    Tu máquina                        Servidor Oracle
┌──────────────────┐            ┌────────────────────────────────┐
│                  │            │                                │
│  navegador       │            │   docker compose               │
│  localhost:8080  │            │  ┌──────────────────────────┐  │
│       │          │            │  │ frontend (nginx)         │  │
│       │          │            │  │  127.0.0.1:8080 ◄────────┼──┼── solo loopback
│       ▼          │    SSH     │  └────────┬─────────────────┘  │
│  túnel ssh -L    │◄──────────►│           │ /api               │
│  8080:127.0.0.1  │            │  ┌────────▼─────────────────┐  │
│    :8080         │            │  │ backend (FastAPI)        │  │
│                  │            │  └────────┬─────────────────┘  │
└──────────────────┘            │           │                    │
                                │  ┌────────▼──────┐ ┌─────────┐ │
                                │  │ worker (RQ)   │ │ redis   │ │
                                │  └───────────────┘ └─────────┘ │
                                └────────────────────────────────┘
```

### Servicios

| Servicio | Imagen | Función |
|---|---|---|
| `frontend` | nginx + build de React | Sirve el SPA, proxya `/api` y `/panel/` |
| `backend` | python:3.11-slim | API HTTP (FastAPI) |
| `worker` | misma que backend | Procesa trabajos pesados |
| `panel` | misma que el worker OCR | **Panel del separador** (cola, progreso SSE, resultados y ZIPs). Servido en `/panel/` |
| `redis` | redis:7-alpine | Cola de trabajos y caché |

### Por qué hay una cola de trabajos

El OCR de PDFs y la transcripción tardan **minutos**. Si corrieran dentro de la
petición HTTP, el navegador cortaría la conexión y el trabajo se perdería. Con
RQ, la petición responde en milisegundos con un identificador, y el resultado se
consulta después.

### Cómo el portal accede a los scripts de OpenClaw

Todas las herramientas que envuelven un script del agente (`analyze_image.py`,
`transcribir_av.py`, `identify_song.py`, `finanzas.py`…) pasan por
`app/services/puente.py`. Resuelve tres cosas que, hechas por separado en cada
herramienta, se implementan mal:

| Problema | Solución en el puente |
|---|---|
| Varios scripts hacen `makedirs` de su `temp/` y no deben tocar el workspace | El script se **copia** a `/data/runtime/scripts/` antes de ejecutarlo |
| Cada script resuelve rutas **relativas a sí mismo** (`../.env`, `../tts_models`) | La copia vive a la misma profundidad, así que las rutas siguen resolviendo |
| Las dependencias viven en el **venv** de OpenClaw, no en el python del contenedor | Siempre se ejecuta con `/home/node/.openclaw/workspace/venv/bin/python` |

La clave de API se inyecta por **variable de entorno**, nunca por línea de
comandos: los argumentos de un proceso son visibles para cualquiera que liste
procesos en el host.

## Panel del separador (en vivo)

La herramienta **Panel del separador** no reimplementa nada: es el panel completo
(cola de trabajos, progreso por página por SSE, entrada local, explorador de
resultados, descarga individual y ZIP por sede, uso de disco y limpieza) que vive
en `separador_pdfs/panel/` del workspace.

- Corre como servicio **`panel`** (`hw-panel`) con la imagen del worker OCR: ya
trae FastAPI/uvicorn/PyMuPDF y libgomp en `/opt/lib`.
- **No publica puertos**: nginx lo alcanza por el alias DNS `panel:8791` y lo sirve
en **`/panel/`** (UI) y **`/api/panel/`** (API), así que se usa por el mismo túnel
SSH: `http://localhost:8080/panel/`. La página del SPA lo muestra en un iframe.
- El **workspace va en escritura** (es el único junto al backend): ahí escribe su
BD (`panel.db`), la entrada local (`entrada_local/`) y los resultados
(`auto/salida/Separador_<fecha>/`).
- Modo **local** (sin Drive): el panel no habla con rclone. En la BD del panel hay
una tabla `cola`, otra `eventos` y tablas reservadas para el futuro inventario.

⚠️ **Dos detalles que cuestan tiempo si se olvidan**:
1. **Precalentar los modelos OCR.** `warmup_ocr.py` se ejecuta antes de arrancar el
   panel. Sin él, los N workers OCR descargan los modelos (~23 MB) a la vez al
   mismo directorio, uno falla y **el documento se procesa a medias en silencio**.
   Los modelos viven en el volumen `datos` (`HOME=/data` → `/data/.paddleocr`), así
   que sobreviven a las recreaciones del contenedor.
2. **`SEPARADOR_WORKERS=2`**: el contenedor tiene 2 vCPU (el host usa 3).

Comandos útiles:

```bash
# Recrear solo el panel tras cambiar su código o config
docker rm -f hw-panel && bash scripts/desplegar.sh   # el script ya lo incluye

# Logs
docker logs -f hw-panel
```

## Puesta en marcha

### 1. Preparar el entorno

```bash
cd herramientas-web
cp .env.example .env
chmod 600 .env
# Edita .env y rellena GEMINI_API_KEY, etc.
```

### 2. Levantar el stack

```bash
# El Dockerfile del worker OCR hace `COPY separador/` con contexto ./backend,
# así que primero hay que materializar la copia.
bash scripts/preparar_separador.sh

docker compose up -d --build
docker compose ps
```

`scripts/desplegar.sh` ya llama a `preparar_separador.sh` por su cuenta; el paso
manual solo hace falta al construir con `docker compose`.

### 3. Abrir el túnel desde tu máquina

```bash
ssh -L 8080:127.0.0.1:8080 -i mi-llave.key ubuntu@<servidor>
```

Luego abre <http://localhost:8080> en el navegador.

> **Nota:** el puerto del túnel es `8080`. Si prefieres otro en tu equipo,
> cambia el primer número: `-L 9090:127.0.0.1:8080`.

## Añadir una herramienta

El portal está diseñado para que añadir una herramienta sea mecánico:

1. **Backend:** crea `app/routers/mi_herramienta.py` con tus endpoints.
2. **Registro:** añade el router en `app/main.py` con
   `app.include_router(mi_herramienta.router, prefix="/api")`.
3. **Catálogo:** añade la entrada en `frontend/src/lib/herramientas.ts`.
4. **Página:** crea `frontend/src/pages/PaginaMiHerramienta.tsx`.
5. **Ruta:** añade el caso en el `switch` de vista de `frontend/src/App.tsx`.

### Reglas al envolver un script existente

- **Valida siempre la entrada.** Usa `app.core.seguridad.validar_archivo`.
- **Nunca pases rutas del usuario a `subprocess`.** Genera tú el nombre del
  archivo con `uuid4()` y guarda dentro de `settings.upload_dir`.
- **Tiempo límite siempre.** Ningún script debe poder colgar el worker.
- **El worker no es confiable.** Aunque el script sea tuyo, trátalo como
  código que puede fallar y devuelve un error claro al usuario.

## Seguridad

Resumen del modelo; el detalle está en [`docs/SECURITY.md`](docs/SECURITY.md).

- Frontend en `127.0.0.1` únicamente — nunca `0.0.0.0`.
- Ningún servicio con labels de Traefik.
- Contenedores non-root, `cap_drop: ALL`, `no-new-privileges`.
- Validación por *magic bytes*, no por extensión.
- Sin `docker.sock` montado dentro de los contenedores.
- Secretos en `.env` con `chmod 600`, nunca en la imagen.
- CSP estricta y cabeceras de seguridad en nginx.

## Estructura

```
herramientas-web/
├─ docker-compose.yml       # los servicios del stack
├─ .env.example             # plantilla de configuración
├─ backend/
│  ├─ Dockerfile            # multi-stage, non-root
│  ├─ requirements.txt
│  ├─ app/
│  │  ├─ main.py            # app FastAPI
│  │  ├─ worker.py          # worker de RQ
│  │  ├─ core/              # config, seguridad, logging
│  │  ├─ routers/           # un módulo por herramienta
│  │  └─ jobs/              # utilidades de cola
│  └─ tests/                # pytest
├─ frontend/
│  ├─ Dockerfile
│  ├─ nginx.conf            # proxy /api + cabeceras
│  ├─ src/
│  │  ├─ App.tsx            # enrutado
│  │  ├─ lib/               # api, catálogo de herramientas
│  │  ├─ components/        # UI reutilizable
│  │  └─ pages/             # una página por herramienta
│  └─ package.json
└─ docs/
   └─ SECURITY.md
```

## Estado actual

| Herramienta | Estado |
|---|---|
| Texto a voz (TTS) | ✅ Implementada |
| Analizar imagen | ✅ Implementada |
| Transcribir audio/video | ✅ Implementada |
| Identificar canción | ✅ Implementada |
| Comprobante a finanzas | ✅ Implementada |
| Separador por sede | ✅ Implementada |
| Panel del separador (en vivo) | ✅ Implementada |
| Panel de finanzas | ✅ Implementada |

## Script de despliegue

`scripts/desplegar.sh` reconstruye backend/frontend y recrea el stack replicando
el compose. Por defecto reutiliza la imagen OCR y conserva su worker.
Solo con `--reconstruir-ocr` materializa `backend/separador/` desde `separador/`
y reconstruye la imagen pesada con `scripts/preparar_separador.sh`.
El sincronizador tampoco reconstruye OCR automáticamente: requiere
`RECONSTRUIR_OCR_AUTORIZADO=1` para habilitar esa operación deliberada.

La actualización de dependencias del 2026-10-23 y su validación están en
[`docs/ACTUALIZACION_DEPENDENCIAS_2026-10-23.md`](docs/ACTUALIZACION_DEPENDENCIAS_2026-10-23.md).

> **Nota:** `sincronizar.sh` y `desplegar.sh` se conservan a propósito durante
> la sincronización. Si cambias alguno, el servidor seguirá usando su copia
> vieja hasta que la reemplaces a mano.

Se ejecuta desde el contenedor de OpenClaw:

```bash
bash /home/node/.openclaw/workspace/herramientas-web/scripts/desplegar.sh
```

## Comandos útiles

```bash
# Ver logs
docker compose logs -f backend worker

# Reiniciar solo el backend tras un cambio
docker compose up -d --build backend

# Ejecutar las pruebas
docker compose exec backend python -m pytest tests/ -v

# Consola de Redis (inspeccionar colas)
docker compose exec redis redis-cli
> KEYS rq:*
```

---

## Despliegue desde el agente

El `docker-socket-proxy` tiene **BUILD habilitado**, así que el stack se puede
construir y recrear desde aquí sin entrar al servidor:

```bash
export DOCKER_HOST=tcp://dockerproxy:2375
cd herramientas-web

# 1. Construir las tres imágenes
docker build -t herramientas-web-backend:local ./backend
docker build -t herramientas-web-frontend:local ./frontend
bash scripts/preparar_separador.sh   # materializa backend/separador
docker build -t herramientas-web-ocr:local -f backend/Dockerfile.ocr ./backend

# 2. Recrear los contenedores (ver el script sección «Comandos útiles»)
```

`docker compose` **no está disponible** en el CLI del contenedor: hay que usar
`docker build` / `docker run` con los mismos parámetros que el compose. El
compose sigue siendo la fuente de verdad; los `docker run` deben replicarlo.

### Detalles del entorno que no son obvios

| Detalle | Por qué |
|---|---|
| Backend y worker usan **debian + python3 de apt** | El venv de OpenClaw lleva rutas horneadas (`home = /usr/bin`). Las imágenes `python:*` lo instalan en `/usr/local/bin` y el venv resuelve a una stdlib incompleta |
| El worker OCR ejecuta **`python3`**, no `python` | Con debian no existe el alias `python` |
| El worker OCR **no** lleva el venv en el PATH | Ejecuta su propio código con el python del sistema; con el venv delante cargaría su stdlib y fallaría con `No module named '_csv'` |
| El worker OCR sí monta el workspace (solo lectura) | La cola pesada también transcribe, y eso necesita los scripts y el intérprete del venv |
| Un solo id por trabajo | Se pasa `job_id=` a RQ para que el id del POST, la carpeta de salida y la descarga sean el mismo |
| Alias DNS de red | Compose los crea por nombre de servicio (`backend`, `redis`). Con `docker run` hay que añadirlos con `--alias`, o nginx responde 502 |
| El **backend** y el **panel** montan el workspace en `rw`; el resto en `ro` | El panel de finanzas edita el ledger real: si escribiera una copia, el agente seguiría viendo los datos viejos. El panel guarda ahí su BD y sus resultados. Un submount `:rw` dentro de un padre `:ro` **no** funciona: Docker reemplaza el directorio por la raíz del volumen y el ledger desaparece |

