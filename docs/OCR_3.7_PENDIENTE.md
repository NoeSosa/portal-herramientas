# Actualización de PaddleOCR 3.7 — pendiente

**Estado:** diferida (no iniciada en producción)  
**Fecha de decisión:** 2026-10-21  
**Alcance:** motor OCR de `separador_pdfs` y worker del panel público.

## Decisión

No se actualiza PaddleOCR en esta versión. El motor actual sigue siendo:

```text
paddlepaddle==2.6.2
paddleocr==2.7.0.3
Python 3.11
ARM64 / CPU
```

La actualización recomendada para una versión posterior es:

```text
paddlepaddle==3.2.0
paddleocr==3.7.0
Python 3.11
ARM64 / CPU
```

## Por qué no ahora

1. **No es un cambio de pin.** PaddleOCR 3.x cambió la API:
   - `show_log` ya no existe.
   - `use_angle_cls` se reemplaza por opciones de orientación de línea.
   - `ocr.ocr()` se reemplaza por `predict()` y devuelve objetos distintos.
   - Entra `paddlex` como dependencia.
2. Hay que adaptar las cuatro inicializaciones de `PaddleOCR` y normalizar los
   resultados al formato que usa `separador_auto.py`.
3. Los modelos cambian a PP-OCRv5/v6. El worker corre sin red, así que hay que
   pre-descargar los modelos en un `HOME`/volumen separado y probar el arranque
   offline.
4. Hay que comparar precisión, no solo que compile. Un cambio de modelo puede
   alterar la lectura de seriales y contadores.
5. El hardware ARM64 necesita wheels manylinux; PaddlePaddle **3.2.0** sí publica
   `manylinux2014_aarch64` para Python 3.11. PaddlePaddle **3.3.1** no muestra
   wheel Linux aarch64 en PyPI al 2026-10-21; no conviene compilar desde fuente.

## Versiones y compatibilidad

- Última estable: **PaddleOCR 3.7.0** (11-jun-2026, PP-OCRv6).
- Framework: **PaddlePaddle 3.2.0** por el wheel aarch64.
- Python 3.11 (Debian bookworm del `Dockerfile.ocr`).
- Fallback si 3.7.0 no resuelve en ARM64: **PaddleOCR 3.3.2** con Paddle 3.2.0.

Referencias:

- <https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0>
- <https://www.paddleocr.ai/latest/update/upgrade_notes.html>
- <https://pypi.org/project/paddlepaddle/3.2.0/#files>

## Plan de validación antes de migrar

No usar el worker de producción. Crear una imagen y un stack staging:

1. `backend/Dockerfile.ocr37` con `paddlepaddle==3.2.0` y `paddleocr==3.7.0`.
2. Imagen `herramientas-web-ocr:ocr37` y `pdf-separator-web:ocr37`.
3. Compose staging con:
   - volumen de datos distinto;
   - base de datos copia o exclusiva;
   - cola/worker exclusivo;
   - `HOME=/data/ocr37` para modelos;
   - warmup con red y luego worker `network_mode: none`.
4. Set de 50–100 páginas reales: HP, Ricoh, Kyocera, Pantum, 1/2/3/5 páginas,
   rotadas, claras y sucias.
5. Comparar 2.7 contra 3.7:
   - serial exacto y normalizado;
   - sede y modelo;
   - agrupación y conteo de páginas;
   - contadores;
   - p50/p95;
   - RAM máxima;
   - errores.
6. Criterio de aceptación:

```text
0 crashes
precisión de serial >= base actual
p95 <= base + 20 %
RAM del worker <= 4 GB
casos críticos sin regresión
```

7. Rollback: conservar `pdf-separator-web:ocr27`; cambiar la etiqueta de imagen.

## Qué queda pendiente de código

- [ ] Adaptador de API 2.x/3.x para `PaddleOCR`.
- [ ] Dockerfile OCR 3.7 con pins y dependencias `paddlex`.
- [ ] Warmup de modelos PP-OCRv6 en volumen separado.
- [ ] Prueba offline del worker.
- [ ] Set de PDFs y script de comparación.
- [ ] Cambio de imagen en producción solo después de aprobar el set.

## Riesgo de no actualizar

PaddleOCR 2.7 es una versión antigua y sin mantenimiento activo. El riesgo se
acepta temporalmente porque:

- los PDFs son de usuarios de confianza;
- el worker no tiene red y corre sin privilegios;
- los archivos se validan y se procesan como datos, no como código;
- el volumen es de 500 páginas/día y el motor actual es estable.

Se debe reevaluar si:

- se abren usuarios externos;
- se necesita mejor precisión;
- hay un aviso de seguridad del motor;
- la versión 2.7 deja de instalar en una actualización de Python/OS.
