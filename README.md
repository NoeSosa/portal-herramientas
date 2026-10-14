# Portal de Herramientas

Herramientas internas servidas por web, accesibles **solo por túnel SSH**.

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
| `frontend` | nginx + build de React | Sirve el SPA, proxya `/api` |
| `backend` | python:3.11-slim | API HTTP (FastAPI) |
| `worker` | misma que backend | Procesa trabajos pesados |
| `redis` | redis:7-alpine | Cola de trabajos y caché |

### Por qué hay una cola de trabajos

El OCR de PDFs y la transcripción tardan **minutos**. Si corrieran dentro de la
petición HTTP, el navegador cortaría la conexión y el trabajo se perdería. Con
RQ, la petición responde en milisegundos con un identificador, y el resultado se
consulta después.

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
docker compose up -d --build
docker compose ps
```

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
├─ docker-compose.yml       # los 4 servicios
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
| Analizar imagen | ⏳ Pendiente |
| Transcribir audio/video | ⏳ Pendiente |
| Identificar canción | ⏳ Pendiente |
| Comprobante a finanzas | ⏳ Pendiente |
| Separador por sede | ⏳ Pendiente |
| Panel de finanzas | ⏳ Pendiente |

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
