# Revisión de seguridad y diseño de panel_web

Fecha de inicio: 2026-10-21, America/Mexico_City. Revisión independiente de código, documentación y exposición pública.

## Resultado ejecutivo

El panel tiene una base razonable: autenticación delegada a Entra, comprobación de propietario en las consultas de trabajos, CSRF en operaciones de API, CSP restrictiva y contenedores con privilegios reducidos. Las comprobaciones públicas realizadas exigieron autenticación donde correspondía.

Sin embargo, **la auditoría anterior no basta para concluir que la aplicación está verificada como segura**. Hay una dependencia transitiva afectada por un aviso de DoS en la construcción documentada, una vinculación de identidad por correo que permite reasignar una cuenta local a otro identificador y sesiones sin revocación individual. También se reprodujo que borrar un trabajo devuelve su cuota diaria.

No se modificó código funcional ni se desplegaron cambios. Este documento contiene hallazgos y recomendaciones, no una certificación ni un pentest con explotación en producción.

## Alcance, método y límites

- Carpeta solicitada como `portal_web`: en este repositorio corresponde a `panel_web`.
- Revisados: `panel_web/app`, estáticos, Dockerfile, ambos Compose, Caddyfile, README, SECURITY y DESIGN; dependencias heredadas de `backend/Dockerfile.ocr` y fragmentos del motor `separador_pdfs` relacionados con ejecución de procesos.
- Skills instaladas: `security-best-practices` y `security-threat-model` de `openai/skills`, `frontend-design` de `anthropics/skills`. Aplicadas las guías de seguridad FastAPI/JavaScript y los criterios de diseño. No se presenta un modelo formal de amenazas separado.
- Modelo documentado: sitio público con hasta cinco usuarios corporativos, documentos privados por usuario y administración con acceso transversal al progreso y originales. Esta configuración real de Entra no se pudo inspeccionar.
- Pruebas públicas de bajo impacto: ocho GET y negociación TLS. Sin intentos de contraseña, ráfagas, carga de archivos, modificación de datos ni acceso autenticado.
- Diseño: login publicado observado en navegador; código HTML/CSS/JS de vistas privadas. Las capturas locales no prueban por sí mismas el estado actual de producción; `panel-movil.png` corresponde a otra interfaz y no se usó como evidencia móvil de este panel.
- Sin acceso a SSH, firewall, configuración efectiva de Traefik, imagen desplegada, logs privados, backups o políticas del tenant. No se ejecutó un escaneo integral de CVEs ni de puertos.

## Hallazgos de prioridad alta

### AUD-01 — Dependencia transitiva vulnerable en la construcción declarada

**Ubicación:** `backend/requirements.txt:1`, `backend/Dockerfile.ocr:80`, `panel_web/Dockerfile:12`, `panel_web/app/main.py:385`.

**Evidencia:** la base instala `fastapi==0.115.6`; sus metadatos oficiales exigen `starlette>=0.40.0,<0.42.0`. Ese rango está afectado por GHSA-2c2j-9gv5-cj73 / CVE-2025-54121, cuyo parche se publicó en Starlette 0.47.2. La aplicación recibe formularios multipart con `UploadFile`.

**Impacto:** el paso de archivos multipart a disco puede bloquear el hilo principal y afectar la disponibilidad; que la ruta termine exigiendo sesión no demuestra que el parseo anterior a las dependencias esté protegido.

**Corrección:** actualizar FastAPI y Starlette conjuntamente a versiones compatibles y corregidas, reconstruir la base OCR y la imagen web, y probar subidas/OIDC/descargas. Auditar el entorno completo con `pip-audit`, incluyendo transitivas, y la imagen con Trivy. No basta con consultar únicamente los paquetes directos.

**Límite de confianza:** está demostrada la incompatibilidad del rango declarado con ese parche. No se obtuvo el inventario instalado en producción ni se explotó el aviso; una imagen modificada manualmente podría diferir.

[Metadatos oficiales de FastAPI 0.115.6](https://raw.githubusercontent.com/fastapi/fastapi/0.115.6/pyproject.toml) · [Aviso del mantenedor de Starlette](https://github.com/Kludex/starlette/security/advisories/GHSA-2c2j-9gv5-cj73).

### AUD-02 — Reasignación de identidad local por coincidencia de correo

**Ubicación:** `panel_web/app/db.py:160–181`, `panel_web/app/auth.py:130–155`.

**Evidencia:** `SELECT id FROM users WHERE oid=? OR email=?`, seguido de `UPDATE users SET oid=?`. La admisión y el rol administrador se deciden por `email`, `preferred_username` o `upn`.

**Reproducción local:** crear `oid-original` con `test@example.invalid` y después autenticar/upsert con `oid-distinto` y el mismo correo devuelve en ambos casos `id=1` y reemplaza el OID. Los trabajos anteriores permanecen vinculados a ese ID.

**Impacto:** otra identidad válida de Entra con un correo reutilizado o modificable puede heredar documentos de la cuenta anterior. El impacto administrativo depende de la allowlist y de quién controla esos atributos en el tenant. No se afirma que un visitante anónimo pueda falsificar un token de Microsoft.

**Corrección:** identificar cuentas por `(iss, sub)` o `(tid, oid)` validado exactamente; rechazar colisiones de correo y hacer cualquier migración mediante una operación administrativa explícita. Usar app roles o IDs estables para permisos; conservar el correo como atributo de presentación/contacto.

**Compensación:** asignación obligatoria en Entra y un grupo reducido disminuyen probabilidad, pero no corrigen la mezcla de identidad y correo. Microsoft advierte contra autorizar por atributos mutables.

[Referencia oficial de claims de identidad](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference).

### AUD-03 — Sesiones sin revocación individual ni baja efectiva inmediata

**Ubicación:** `panel_web/app/main.py:79–86`, `panel_web/app/auth.py:69–94`, `panel_web/app/main.py:255–257`.

**Evidencia:** cookie firmada con `uid/email/csrf`; logout solo vacía la cookie del navegador. `usuario_actual()` comprueba que existe el usuario, sin estado habilitado, versión de sesión ni nueva evaluación de la allowlist. No hay registro servidor de sesiones revocadas.

**Impacto:** una cookie previamente copiada puede seguir utilizándose después de logout; deshabilitar una cuenta en Entra o quitarla de la allowlist no invalida inmediatamente la sesión local existente. La firma impide modificarla, pero no su reutilización. El `Max-Age` de ocho horas no constituye por sí solo un tiempo absoluto de sesión: el middleware puede renovar cookies con actividad.

**Corrección:** sesiones opacas almacenadas y revocables en servidor, con expiración absoluta/inactividad y baja de usuario; o versión de sesión validada en cada petición. Revocar al salir y al retirar acceso. El rol ya se consulta en BD, lo cual ayuda si se cambia efectivamente allí.

**Límite:** requiere obtener previamente una sesión o mantener una sesión que debería darse de baja; no es un bypass anónimo.

## Hallazgos de prioridad media

### AUD-04 — Borrar trabajos restituye la cuota diaria

**Ubicación:** `panel_web/app/db.py:259–268`, `panel_web/app/db.py:333–338`, `panel_web/app/main.py:413`.

**Evidencia:** la cuota suma `pages` de los registros actuales en `jobs`; el borrado elimina el registro. Reproducción SQLite en memoria: cuota con trabajo de 500 páginas = `500`; después de `borrar_job` = `0`.

**Impacto:** un usuario autorizado puede repetir procesar/borrar/subir y superar 500 páginas/día. Permanece el límite temporal de subidas, pero no la cuota diaria prometida.

**Corrección:** consumo diario persistente independiente de la retención/borrado, reservado transaccionalmente con la admisión. El número de trabajos y páginas se consulta antes de crear el trabajo en otra conexión; si se añaden procesos API, las cuotas necesitan además protección frente a concurrencia.

### AUD-05 — Límites de cuerpo y concurrencia incompletos en la variante Dokploy

**Ubicación:** `panel_web/Caddyfile:9–11`, `panel_web/docker-compose.dokploy.yml:72–82`, `panel_web/app/main.py:385–400`.

**Evidencia:** Caddy limita 55 MB. La variante Dokploy no declara un middleware equivalente de buffering/límite de cuerpo, y `archivo.read(max+1)` se ejecuta después de que el framework haya recibido/procesado multipart. No aparecen límites globales de concurrencia o cuota de disco.

**Impacto:** peticiones grandes o concurrentes pueden consumir temporales, ancho de banda y disponibilidad antes del rechazo funcional. La memoria y tmpfs acotadas contienen el daño al contenedor, pero no evitan caída o reinicios.

**Corrección:** límite total de cuerpo y tiempos en Traefik, límites multipart, control de concurrencia, límites antes del parser y vigilancia de disco. Mantener validación en aplicación.

**Por verificar:** Dokploy puede aplicar controles globales externos a este repositorio; no se ha demostrado que estén ausentes en producción.

### AUD-06 — Protección de acceso y de ráfagas no demostrada de extremo a extremo

**Ubicación:** `panel_web/app/auth.py:27–50`, `panel_web/app/main.py:181–216`, ambos Compose.

**Evidencia:** solo se observa un limitador en memoria para subidas autenticadas por UID. No hay throttling declarado para `/auth/login`, `/auth/callback` o tráfico API general; no hay auditoría persistente de todos los rechazos de login. El limitador se reinicia y no se comparte entre procesos.

**Impacto:** las rutas públicas pueden recibir ráfagas; los fallos de autenticación resultan menos observables. No equivale a un formulario local susceptible de probar contraseñas: la contraseña se valida en Microsoft.

**Corrección:** rate limit por rutas e IP confiable en el borde, límites globales, respuesta 429 y alertas sobre fallos. En Entra verificar asignación requerida, MFA resistente a phishing para administradores, Smart Lockout y revisión de sign-in logs. Evaluar Conditional Access según licencias y política.

**Por verificar:** políticas del tenant y del proxy; no se intentó fuerza bruta en producción. El número `MAX_USERS=5` no limita intentos de acceso.

[Microsoft Smart Lockout](https://learn.microsoft.com/en-us/azure/active-directory/authentication/howto-password-smart-lockout) · [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html).

### AUD-07 — El aislamiento OCR comparte datos y secretos de toda la aplicación

**Ubicación:** `panel_web/docker-compose.yml:80–85`, `panel_web/docker-compose.dokploy.yml:96–101`, `panel_web/app/worker.py:166`, `panel_web/app/main.py:400`.

**Evidencia:** worker y web reciben el mismo `.env` y montan todo `/data` con el mismo UID. El subprocess OCR hereda `os.environ.copy()`. El parser PDF previo corre dentro del proceso web, aunque en threadpool.

**Impacto:** si se explota una vulnerabilidad del parser/OCR, el proceso puede acceder a la BD, documentos de otros usuarios, inventario y secretos de sesión/OIDC presentes en su entorno. `network_mode: none` limita salida de red, no acceso al volumen ni manipulación de datos. Esto exige explotación adicional; no se demostró ejecución arbitraria.

**Corrección:** separar configuración web de worker, no entregar secretos OIDC/sesión al worker, entorno explícito de subprocess y aislamiento por trabajo con acceso mínimo a archivos. Separar validación nativa de PDF en un proceso con límites y sin red. La aceptación documentada de no instalar antivirus permanece; antivirus no sustituye este aislamiento.

### AUD-08 — El timeout no garantiza detener todos los procesos OCR

**Ubicación:** `panel_web/app/worker.py:190–212`, `separador_pdfs/separador_auto.py:806`.

**Evidencia:** el motor lanza subprocesos; el worker hace `proc.kill()` al padre y retorna, sin crear/terminar un grupo de procesos ni esperar en esa rama.

**Impacto:** procesos hijos pueden seguir consumiendo recursos o escribiendo mientras se inicia otro trabajo. Riesgo de disponibilidad y coherencia, sin explotación reproducida en esta máquina Windows.

**Corrección:** crear una sesión/grupo en Linux, terminar todo el grupo con gracia y luego SIGKILL si procede, y hacer wait/reap en todas las salidas. Probar con un motor simulado que lance un hijo duradero.

### AUD-09 — Auditoría de IP confía en cualquier remitente del proxy

**Ubicación:** `panel_web/Dockerfile:41`, `panel_web/app/auth.py:53–58`, `panel_web/docker-compose.dokploy.yml:66–68`.

**Evidencia:** Uvicorn usa `--forwarded-allow-ips=*` y la aplicación toma el primer `X-Forwarded-For`. Dokploy conecta la web a una red compartida.

**Impacto:** un contenedor alcanzable desde esa red puede falsificar atribución de IP; según cómo el proxy sobrescriba cabeceras, también puede haber diferencias en tráfico público. No se verificó falsificación pública.

**Corrección:** limitar proxies confiables y normalizar cabeceras en el borde; obtener la IP de un único mecanismo de confianza. Resolver esto antes de basar controles antiabuso en IP.

## Mejoras de prioridad baja y operativas

- Logout por GET: CSRF de cierre de sesión ya aceptado en `panel_web/SECURITY.md`; migrar a POST con CSRF cuando se trabaje la revocación.
- El init hace `chmod -R 750 /data` en cada ejecución (`docker-compose.yml:23` y Dokploy `:25`): puede cambiar archivos previos de 0600 a 0750, contradiciendo la garantía documental de no ejecutables. Aplicar permisos por tipo y evitar cambios recursivos indiscriminados.
- Los cuerpos JSON administrativos carecen de modelos con longitudes/tipos explícitos (`main.py:782`, `:810`). Hay allowlist de campos, pero tipos inesperados pueden terminar en 500. Añadir esquemas y errores 422.
- Dependencias con rangos amplios y sin lock completo; ejecutar escaneo del entorno instalado y generar inventario reproducible. No se atribuyen CVEs adicionales sin evidencia.
- El fallback CIAM compara el tenant como subcadena del issuer (`auth.py:126`). Sustituir por issuer exacto validado. No se considera bypass probado: Authlib también valida el token y su issuer según metadata.
- Importación XLSX en threadpool con 20 MB comprimidos/50.000 filas válidas: no es un límite de descompresión ni de filas realmente recorridas. Al estar restringida a admin, es un riesgo secundario; aislar y acotar recursos si se amplía uso.

## Diseño, experiencia y accesibilidad

La composición publicada tiene una dirección coherente con `DESIGN.md`: marca navy, tipografía IBM Plex, fondo papel, acción Microsoft reconocible y separación clara entre presentación y acceso. Conviene conservar esa coherencia y corregir los siguientes problemas funcionales antes de rediseñar por estética.

| ID | Prioridad | Evidencia | Recomendación |
|---|---|---|---|
| UI-01 | Media | `style.css:10`, `:661`, `:711`, `:771`: `#8a867c` sobre `#f2eee6` da **3,14:1**. Se usa para metadatos y Borrar activos. | Texto normal necesita 4,5:1 para WCAG AA. Oscurecer el token y verificar fondos/estados reales; hay además abundantes textos de 8–10 px. |
| UI-02 | Media | `style.css:1089`: a 900 px o menos se oculta `.sidebar-foot`, donde está el enlace Salir de `index.html:50`. | Mantener salida de sesión accesible en móvil y comprobar 320/390/768 px. |
| UI-03 | Media | `index.html:102` presenta un `role=button` que contiene otro botón; `:208` tiene progreso sin nombre accesible. | Un único control de selección de archivo; nombre de progreso asociado al documento. Probar teclado y lector de pantalla. |
| UI-04 | Baja | `index.html:95–98` fija 50 MB/500 páginas/30 días, aunque configuración y `/api/me` son variables. | Renderizar límites reales también en las especificaciones; mostrar tamaño rechazado antes de transferir sin quitar validación del servidor. |
| UI-05 | Baja | `index.html:43` afirma Servicio activo de forma estática; `app.js:186` oculta errores de polling silencioso. | Mostrar desconexión/última actualización y reintento; evitar aparentar progreso vigente si falla la API. |
| UI-06 | Baja | Login promete que solo el usuario verá sus documentos; administración puede renderizar originales (`main.py:695`). | Explicar el acceso de administradores autorizados y la retención para que la promesa de privacidad sea precisa. |

Fortalezas observadas en código: `textContent` para datos en ambas interfaces, ausencia de scripts externos de terceros, foco visible, skip link, estados con texto, `aria-live`, y soporte de movimiento reducido. Hay reglas responsive, pero no se realizó una auditoría dinámica móvil autenticada ni se afirma conformidad WCAG completa.

[WCAG 2.2 — Contraste mínimo](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

## Evidencia de ejecución

### Sitio publicado

Destino: `https://pdf-separator.duckdns.org`, sin sesión. Verificación de certificado habilitada.

| Ruta GET | Resultado observado |
|---|---|
| `/` | 200, login |
| `/api/health` | 200 |
| `/api/jobs` | 401 |
| `/api/admin/overview` | 401 |
| `/admin` | 303 a `/login` |
| `/static/admin.html` | 404 |
| `/.env` | 404 |
| `/.git/config` | 404 |

Las ocho respuestas incluyeron CSP, HSTS, X-Frame-Options DENY y nosniff; no se observó `Server`. TLS 1.3 negociado; certificado Let's Encrypt, emisor YR1, vencimiento 2027-01-18 19:12:25 UTC. No se inspeccionó cookie de login ni se evaluaron versiones TLS anteriores en esta revisión.

### Pruebas locales

- `python -m unittest discover -s panel_web/tests -v`: siete pruebas de BD pasaron inicialmente y dos de Excel fallaron por permisos del temporal del sandbox. Al ejecutar la misma suite con `tempfile.tempdir` dentro del workspace, **9/9 pasaron**; no fue necesario cambiar la aplicación.
- `node --check panel_web/static/app.js` y `node --check panel_web/static/admin.js`: correctos.
- Reproducciones SQLite en memoria: reasignación de OID por correo y reintegro de cuota por borrado, confirmadas.
- Cálculo de contraste por luminancia sRGB: 3,14:1 para muted-2/paper.
- FastAPI y Authlib no están instalados en el Python local inspeccionado; no se ejecutó TestClient de la API, pruebas OIDC completas ni imágenes Docker. Las pruebas existentes cubren BD/inventario, no demuestran controles HTTP completos.

## Orden de atención

1. Actualizar la cadena FastAPI/Starlette y comprobar versiones efectivas de producción; revisar acceso por identidad estable y revocación de sesiones.
2. Corregir consumo diario, límites de entrada de Dokploy y políticas de acceso/antiabuso de Entra y proxy.
3. Reducir secretos/volúmenes del worker, aislar parser y terminar todo el árbol OCR al cancelar.
4. Mejorar contraste, salida móvil y semántica de selección/progreso; añadir pruebas HTTP de 401/403/CSRF/aislamiento, sin limitarse a consultas SQLite.
5. Registrar configuración efectiva, dependencias transitivas, backups/restauración y políticas del tenant. Actualizar `panel_web/SECURITY.md`: sus afirmaciones de ausencia de avisos y aislamiento deben quedar acotadas por estos hallazgos.
