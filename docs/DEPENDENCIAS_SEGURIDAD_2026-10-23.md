# Estado de dependencias y avisos de seguridad

Fecha: **2026-10-23**, America/Mexico_City. Investigación con la skill
`security-best-practices`, registros oficiales PyPI/npm y avisos de mantenedores.
No se actualizaron paquetes, lockfiles, imágenes ni el motor OCR.

## Conclusión

**El panel público está cubierto frente a los avisos conocidos consultados para
su stack Python probado, pero el repositorio completo no está al día.**

- `panel_web`: 10 dependencias directas sin avisos en PyPI; 9 coinciden con la
  última versión. FastAPI tiene una versión posterior, sin aviso conocido para
  la versión fijada. El escaneo completo del entorno local (55 distribuciones)
  terminó sin vulnerabilidades conocidas.
- Backend privado: `python-multipart==0.0.20` y la rama Starlette compatible
  con FastAPI 0.115.6 conservan vulnerabilidades conocidas.
- Frontend: npm audit devuelve **6 paquetes afectados**, con clasificación
  agregada de 2 críticos, 1 alto y 3 moderados. Son herramientas de desarrollo/
  pruebas y sus transitivas; **auditando solo producción devuelve 0**.
- Infraestructura: comprobar Node tras el parche del 21 de octubre, renovar
  nginx 1.27 y sustituir Alpine 3.19. Etiquetas flotantes no prueban que la
  imagen actualmente desplegada esté actualizada.

## DEP-01 — Panel público: cubierto en el entorno probado

Evidencia: `panel_web/requirements.txt:2–11` y nueva ejecución de pip-audit.

| Paquete | Fijado | Última versión consultada | Avisos para la versión fijada |
|---|---|---|---|
| FastAPI | 0.159.1 | 0.161.1 | Ninguno registrado |
| Starlette | 1.9.0 | 1.9.0 | Ninguno registrado |
| Pydantic | 2.14.1 | 2.14.1 | Ninguno registrado |
| Uvicorn | 0.54.0 | 0.54.0 | Ninguno registrado |
| Authlib | 1.9.1 | 1.9.1 | Ninguno registrado |
| cryptography | 50.0.2 | 50.0.2 | Ninguno registrado |
| httpx | 0.28.1 | 0.28.1 | Ninguno registrado |
| itsdangerous | 2.2.0 | 2.2.0 | Ninguno registrado |
| openpyxl | 3.1.5 | 3.1.5 | Ninguno registrado |
| python-multipart | 0.0.32 | 0.0.32 | Ninguno registrado |

Starlette 1.9.0 y python-multipart 0.0.32 están por encima de los parches de
los avisos de formularios, rangos y reconstrucción de URL enumerados abajo.
Actualizar FastAPI a 0.161.1 puede ser mantenimiento normal, pero el registro
consultado no justifica describir 0.159.1 como vulnerable por no ser la última.
[Versión oficial posterior](https://github.com/fastapi/fastapi/releases/tag/0.161.1).

**Límite:** pip-audit inspeccionó el entorno local preparado para el panel. No
se obtuvo `pip freeze` de producción ni se inspeccionó la imagen completa OCR
que sirve como base; su conjunto de paquetes puede ser mayor o diferente.

## DEP-02 — Backend privado: prioridad alta de mantenimiento de seguridad

Evidencia: `backend/requirements.txt:1–8`; `backend/Dockerfile.ocr` también
instala este archivo, pero la capa de `panel_web` reemplaza sus paquetes HTTP.
El portal privado conserva el stack antiguo y está documentado detrás de SSH.

| Paquete | Fijado | Última versión | Resultado |
|---|---|---|---|
| FastAPI | 0.115.6 | 0.161.1 | Obliga Starlette >=0.40.0,<0.42.0 |
| Uvicorn | 0.34.0 | 0.54.0 | Antiguo; sin aviso directo devuelto por PyPI |
| Pydantic | 2.10.4 | 2.14.1 | Antiguo; sin aviso directo devuelto por PyPI |
| pydantic-settings | 2.7.0 | 2.16.1 | Antiguo; sin aviso directo devuelto por PyPI |
| python-multipart | 0.0.20 | 0.0.32 | Afectado por avisos conocidos |
| redis, cliente Python | 5.2.1 | 8.1.0 | Antiguo; sin aviso directo devuelto por PyPI |
| RQ | 2.1.0 | 2.12.0 | Antiguo; sin aviso directo devuelto por PyPI |
| httpx | 0.28.1 | 0.28.1 | Actual; sin aviso directo devuelto por PyPI |

Para Starlette se consultó 0.41.3 como resolución representativa del rango
permitido, **no como versión observada en el servidor**. Ese rango no permite
instalar los parches siguientes sin actualizar conjuntamente FastAPI:

| Aviso | Corrección publicada | Alcance relevante |
|---|---|---|
| CVE-2025-54121 | Starlette 0.47.2 | Bloqueo del event loop al pasar multipart a disco |
| CVE-2025-62727 | Starlette 0.49.1 | Agotamiento de CPU con Range en FileResponse |
| CVE-2026-48710 | Starlette 1.0.1 | Interpretación de Host al reconstruir URL |
| CVE-2026-48818 | Starlette 1.1.0 | StaticFiles/UNC en Windows; no es el entorno Linux documentado |
| CVE-2026-48817 | Starlette 1.1.0 | Métodos no estándar en HTTPEndpoint; exige uso específico |
| CVE-2026-54282 | Starlette 1.3.0 | Reconstrucción de hostname a partir de un path no validado |
| CVE-2026-54283 | Starlette 1.3.1 | Límites ignorados en formularios urlencoded |

python-multipart 0.0.20 figura en seis avisos distintos: CVE-2026-24486,
CVE-2026-40347, CVE-2026-42561, CVE-2026-53538, CVE-2026-53539 y
CVE-2026-53540. Sus parches respectivos llegan hasta 0.0.31. Algunos exigen
llamar directamente parse_form o configuraciones no usadas por FastAPI; otros
afectan el parser multipart expuesto a archivos, como encabezados sin límite.
No se afirma que todos sean explotables en nuestras rutas.
[Aviso del parser multipart](https://github.com/Kludex/python-multipart/security/advisories/GHSA-pp6c-gr5w-3c5g) ·
[Aviso de reconstrucción de URL](https://github.com/Kludex/starlette/security/advisories/GHSA-jp82-jpqv-5vv3).

**Acción recomendada:** actualizar la capa web del backend privado con pruebas
de sus herramientas y cola, sin cambiar PaddleOCR/PaddlePaddle/PyMuPDF. El
aislamiento por SSH reduce exposición, pero no aplica parches al software.

## DEP-03 — Frontend: herramientas de pruebas vulnerables

Evidencia: `frontend/package.json:31`, versiones efectivas en
`frontend/package-lock.json` y npm audit con `--package-lock-only`.

| Paquete afectado | Versión(es) del lockfile | Observación |
|---|---|---|
| Vitest | 2.1.9 | Dependencia directa; auditoría la clasifica crítica |
| tinypool | 1.1.1 | Transitiva de pruebas; auditoría la clasifica crítica |
| @vitest/mocker | 2.1.9 | Traversal/inyección bajo condiciones específicas |
| Vite transitivo | 5.4.21 | Arrastrado por Vitest y vite-node |
| esbuild transitivo | 0.21.5 | Arrastrado por el stack de pruebas |
| vite-node | 2.1.9 | Propaga los avisos de Vite |

Son seis **paquetes afectados**, no seis CVEs ni seis ataques demostrados.
La clasificación npm incluye dependencias heredadas y puede diferir de la
gravedad/intervalos publicados por el mantenedor. Vitest UI necesita estar
activo/expuesto para el aviso de lectura/ejecución; el gadget de tinypool
requiere contaminación de prototipos y un uso concreto de opciones.
[Aviso oficial Vitest UI](https://github.com/vitest-dev/vitest/security/advisories/GHSA-5xrq-8626-4rwp) ·
[Aviso oficial tinypool](https://github.com/tinylibs/tinypool/security/advisories/GHSA-85c8-ppgw-ccpr).

Vite **directo** está en 6.4.4 y esbuild directo en 0.25.12. El hallazgo no
significa que Vite 6.4.4 sea la copia afectada: hay copias antiguas anidadas.
Vite 6.4 sigue recibiendo parches de seguridad; no es obligatorio saltar a Vite
8 únicamente por no ser el major más reciente.
[Política de soporte de Vite](https://vite.dev/releases).

Últimas versiones consultadas: Vitest 5.0.5 y Vite 8.3.5. npm propone Vitest
5.0.5 para resolver el conjunto; es una migración mayor que debe verificarse,
no una razón para ejecutar `npm audit fix --force` sin revisión.

**Exposición:** el Dockerfile compila con Node y entrega únicamente `dist` a
nginx; Vite/Vitest no son servidores de producción de esa imagen. La prueba
`npm audit --omit=dev` devolvió **cero avisos**. Tampoco se configura `host`
público en Vite. El riesgo principal aquí es desarrollo, pruebas y construcción.

## DEP-04 — React y avisos recientes de Server Components

El lockfile contiene React/react-dom 19.3.0; npm publica 19.3.1. La release del
14 de octubre modifica el transporte de Server Components.
[Release oficial](https://github.com/react/react/releases/tag/v19.3.1).

No hay paquetes react-server-dom, Next.js ni plugin RSC en el lockfile; el
frontend es un cliente Vite servido estáticamente por nginx. No se encontró la
superficie de ejecución de Server Components que describen estos avisos.
La documentación oficial distingue expresamente las aplicaciones sin ese
soporte. No corresponde atribuir RCE al sitio por usar React 19.
[Alcance oficial de los avisos RSC](https://react.dev/blog/2025/12/03/critical-security-vulnerability-in-react-server-components).

Recomendación: llevar React/react-dom juntos a 19.3.1 como mantenimiento de
parche; no se encontró un aviso aplicable a sus paquetes cliente en npm audit.

## DEP-05 — Infraestructura y parche de Node de esta semana

- **Node:** `frontend/Dockerfile:8` usa `node:22-alpine` para construir. Node
  publicó parches el **21 de octubre de 2026**, incluidas correcciones de DNS,
  certificados y Brotli; para la rama 22 se publicó **22.23.4**, y para la rama
  24 **24.22.1**. La versión local observada es **24.14.1**, anterior al parche:
  conviene actualizar también el Node de desarrollo. La etiqueta Docker flotante
  no acredita qué binario quedó en una imagen ya construida; hace falta renovar
  la base y comprobar versión efectiva. Node no está en la etapa nginx final.
  [Aviso oficial del 21 de octubre](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases).
- **nginx:** `frontend/Dockerfile:22` fija `nginx:1.27-alpine`. Los avisos
  actuales publican correcciones en ramas posteriores, entre ellas 1.30.6 y
  1.31.7 para varias fallas. La aplicabilidad depende de módulos/configuración;
  este frontend escucha HTTP en 80 y no configura HTTP/3/TLS en nginx. No se
  verificó explotación ni se atribuyen todas las fallas a la configuración.
  Renovar a una rama con parches y escanear la imagen.
  [Avisos oficiales nginx](https://nginx.org/en/security_advisories.html).
- **Alpine 3.19:** aparece en inicializadores del portal y del panel público.
  Terminó el soporte regular el **2025-11-01**; solo recibe arreglos a petición.
  Es un servicio temporal y no el servidor público, pero conviene sustituirlo
  por una rama soportada. [Soporte oficial Alpine](https://alpinelinux.org/releases/).
- **Caddy 2, Redis servidor 7, Debian bookworm:** sus tags no dan una versión
  exacta de la imagen existente. Sin Docker/SSH no es posible verificar digest,
  paquetes apt/apk, OpenSSL/Go ni parches efectivos. No confundir redis-py 5.2.1
  con el proceso Redis 7: son productos/versiones distintos.

## Excepción OCR y límites

Se conserva PaddlePaddle 2.6.2, PaddleOCR 2.7.0.3 y PyMuPDF 1.26.0 con su
conjunto compatible. No se propone una actualización del motor ni de sus
dependencias acopladas. Esta excepción **no** exime de actualizar las bibliotecas
HTTP del backend o las imágenes auxiliares ajenas al motor.

No se inspeccionó el servidor ni se ejecutó Trivy, Docker build, pentest o
pruebas de explotación. Los paquetes auxiliares del OCR sin versión fijada
requieren inventario de la imagen para una conclusión exacta; no se deduce su
versión de un requirements sin pins. Ausencia de avisos registrados no es una
garantía de ausencia de vulnerabilidades.

## Orden de atención propuesto

1. Comprobar/actualizar Node de desarrollo y de construcción al parche de esta
   semana; renovar nginx y las imágenes auxiliares antiguas.
2. Actualizar la capa HTTP del backend privado, conservando el motor OCR.
3. Migrar Vitest y regenerar su árbol/lockfile para eliminar las copias antiguas.
4. Parches de mantenimiento FastAPI y React; otros majors antiguos solo con
   necesidad y pruebas de compatibilidad.
5. Auditar el inventario de imágenes desplegadas y automatizar avisos sobre
   lockfiles/requirements. No se efectuaron cambios ni push en esta investigación.
