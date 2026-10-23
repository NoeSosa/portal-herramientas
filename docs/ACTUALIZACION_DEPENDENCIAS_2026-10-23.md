# Actualización compatible de dependencias

Fecha: 2026-10-23. Cambios locales, sin despliegue ni push.

## Cambios aplicados

| Área | Antes | Ahora |
|---|---|---|
| Backend privado, FastAPI | 0.115.6 | 0.161.1 |
| Backend privado, Starlette | Rango transitivo <0.42 | Pin 1.9.0 |
| Backend privado, Uvicorn | 0.34.0 | 0.54.0, conserva extras standard |
| Backend privado, Pydantic | 2.10.4 | 2.14.1 |
| Backend privado, python-multipart | 0.0.20 | 0.0.32 |
| Panel público, FastAPI | 0.159.1 | 0.161.1 |
| Frontend, React/react-dom | 19.3.0 en lockfile | 19.3.1 |
| Frontend, Vitest | 2.1.9 | 5.0.5 |
| Frontend, Vite | 6.4.4 en lockfile | Conserva 6.4.4; rango ~6.4.4 |
| Imagen Node del build | 22-alpine flotante | 22.23.4-alpine |
| Imagen nginx | 1.27-alpine | 1.30.6-alpine |
| Inicializadores Alpine | 3.19 | 3.24.3 |

Se regeneró `frontend/package-lock.json`. Vitest 5 admite Vite >=6.4 y Node
>=22.12; no fue necesario migrar Vite a otro major. Desaparecieron las copias
anidadas antiguas de Vite/esbuild y la dependencia tinypool del árbol de pruebas.
El manifest declara versiones de Node con el parche de seguridad del 21 de
octubre y `frontend/.nvmrc` fija 22.23.4.

Se conserva RQ 2.1.0 y redis-py 5.2.1 para no introducir migraciones de cola ni
del cliente; no aparecían avisos conocidos en la revisión. Se mantiene también
pydantic-settings 2.7.0, cuya compatibilidad con el Pydantic actualizado se probó.
No se hicieron migraciones mayores de ESLint/TypeScript ajenas a estos avisos.

## Protección del motor OCR

No cambian los archivos `backend/Dockerfile.ocr`, `backend/requirements-ocr.txt`,
el código del separador ni la lógica de la aplicación. Permanecen PaddlePaddle
2.6.2, PaddleOCR 2.7.0.3 y PyMuPDF 1.26.0. No se reinstaló OCR en producción.

El Dockerfile OCR instala el requirements común antes del motor y contiene
dependencias auxiliares sin pins. Una reconstrucción completa podría resolver
esas bibliotecas otra vez aunque los tres pins del motor no cambien. Por eso:

- `scripts/desplegar.sh` conserva imagen y worker OCR por defecto.
- Una reconstrucción del motor requiere `--reconstruir-ocr`; si falta la imagen
  validada, el script se detiene antes de modificar contenedores.
- `scripts/sincronizar.sh` no reconstruye OCR ante un cambio normal del backend;
  requiere `RECONSTRUIR_OCR_AUTORIZADO=1` para habilitarlo.
- Los cambios HTTP se aplican al backend ligero y a la capa del panel público
  construida sobre la imagen OCR existente.

Esto conserva también las bibliotecas nativas y modelos de la imagen OCR que
ya funciona. La imagen vieja no adquiere automáticamente los nuevos parches HTTP;
sus servicios internos siguen necesitando una revisión separada si se quiere
reemplazarla mediante una imagen validada sin alterar el motor.

## Validación ejecutada

| Comprobación | Resultado |
|---|---|
| Panel público | 39/39 pruebas correctas |
| Backend con dependencias nuevas | 215 pasan, 7 fallan |
| Backend con stack HTTP anterior aislado | Los mismos 215 pasan y los mismos 7 fallan |
| Compilación frontend | Correcta con Node 22.23.4, TypeScript y Vite 6.4.4 |
| Arranque/configuración Vitest 5 | Correcto; no hay archivos de tests frontend |
| npm audit, incluyendo desarrollo | 0 avisos conocidos; antes 6 paquetes afectados |
| pip-audit del entorno actualizado | Sin vulnerabilidades conocidas |
| pip check | Sin requisitos incompatibles |
| Scripts de despliegue | Sintaxis Bash correcta y 4 pruebas con Docker ficticio correctas |
| Arquitecturas de las imágenes nuevas | Tags oficiales existentes, incluidos amd64 y arm64 |

Los siete fallos del backend no son regresiones de esta actualización: se
reprodujeron con FastAPI 0.115.6, Starlette 0.41.3, Pydantic 2.10.4, Uvicorn
0.34.0 y python-multipart 0.0.20 en un target Python aislado:

1. Una prueba de subida del separador intenta contactar un Redis que no existe
   en esta máquina.
2. Tres pruebas de ledger necesitan permisos Windows para crear symlinks.
3. Dos pruebas de compatibilidad OCR necesitan NumPy, ausente en este entorno
   ligero de pruebas. No se instaló el stack OCR para hacerlas pasar.
4. Una prueba de visión espera el intérprete externo de OpenClaw, ausente aquí.

Las cuatro pruebas de despliegue ejecutan el script real con un comando Docker
ficticio: comprueban conservación de OCR por defecto, compatibilidad de
`--sin-ocr`, reconstrucción solo explícita y parada previa si falta la imagen.
No se modificó ningún contenedor para probarlo.

## Aplicación al servidor

**Importante:** el repositorio documenta que el sincronizador conserva sus
propias copias de `sincronizar.sh` y `desplegar.sh`. Antes de permitir que un
sincronizador antiguo aplique esta actualización, hay que sustituir ambas
copias por las nuevas. El antiguo reconstruye OCR automáticamente al detectar
cambios en backend.

En un servidor ya instalado, con scripts actualizados y la imagen OCR validada:

```sh
bash scripts/desplegar.sh --sin-ocr
```

Alternativa con Compose para actualizar solo los servicios ligeros del portal:

```sh
docker compose build --pull frontend backend worker
docker compose run --rm --no-deps frontend nginx -t
docker compose up -d --no-deps --no-build frontend backend worker
```

Para el panel público, reconstruir su capa web sobre la imagen OCR local
existente y recrear web/worker en una ventana sin trabajos. No reconstruir ni
hacer pull de `herramientas-web-ocr:local` como parte de este mantenimiento.

No está disponible Docker en esta máquina. No se verificó la construcción o
el arranque real de nginx/Alpine en Linux/ARM64; los tags y su arquitectura se
comprobaron en Docker Hub. Antes de producción faltan esa comprobación, la
conexión real a Redis y una prueba OCR con la imagen existente. Los escaneos
locales no equivalen a Trivy ni a un inventario de toda la imagen desplegada.

El Node global del equipo no se sustituyó: para compilar se descargó una copia
oficial de Node 22.23.4 dentro de `.venv/tools`, verificada contra SHASUMS256 del
sitio oficial. La actualización global puede afectar otros proyectos y se dejó
separada; `.nvmrc` y engines indican el runtime requerido para este frontend.
