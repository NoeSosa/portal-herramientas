# Rendimiento posterior al OCR

**Fecha:** 2026-10-25  
**Caso medido:** `Nuevas_2.pdf`, 22 páginas, 5 PDFs de salida (~2 MB).  
**Servidor objetivo:** Oracle ARM64, 4 CPU, 24 GB RAM.

## Resumen

El OCR domina el tiempo del job. La etapa posterior (clasificación, unión de
PDFs y ZIP) es de decenas de milisegundos en este lote. La única mejora clara
es no recomprimir los PDFs dentro del ZIP.

## Mediciones

| Etapa | Tiempo medido |
|---|---:|
| Clasificación/agrupación (modelo OCR ya cargado) | ~14 ms |
| Unión de 5 PDFs de salida | ~19 ms |
| ZIP con deflate (PDFs + textos) | ~58 ms |
| ZIP guardando PDFs y comprimiendo solo textos | ~7 ms |
| Sincronización de 500 eventos OCR (una pasada completa) | ~16 ms |
| Inicialización de PaddleOCR por proceso/job | ~0.7 s |

La clasificación no es un cuello de botella: 22 páginas tardan ~14 ms y 500
páginas escalarían a menos de 1/3 de segundo, sin contar OCR.

## Cuadro comparativo

| Mejora | Antes | Después | Ganancia | Efecto negativo / riesgo | Recomendación |
|---|---:|---:|---:|---|---|
| ZIP sin recomprimir PDFs | 58 ms / 2 MB | 7 ms / 2 MB | ~8× más rápido | ZIP +0.05% (2,009,018 vs 2,008,040 bytes); si un PDF no está comprimido podría crecer más | **Implementado** |
| Compresión de textos | deflate | deflate | Sin cambio | Ninguno | Mantener |
| Sincronización incremental del JSONL | 16 ms por ciclo de 500 páginas | ~0.1 ms por ciclo | ~90 s menos en un job de 100 min | Más estado/offsets; riesgo de perder eventos si se reinicia | Opcional, no urgente |
| Evitar `actualizar_progreso` si el conteo no cambió | ~1 ms/ciclo | ~0 | Muy bajo | Ninguno | Se puede agrupar con la anterior |
| Unir PDFs en paralelo | 19 ms | ~10 ms | ~9 ms | Más complejidad y memoria; GIL/IO | No vale la pena |
| Mantener PaddleOCR “caliente” entre jobs | ~0.7 s por job | ~0 | ~0.7 s/job | Worker persistente más complejo; riesgo de estado compartido | No ahora |
| Guardar 20,000 caracteres OCR | 800 | 20,000 | Mejor diagnóstico | Más bytes en JSONL/SQLite | Mantener |

## Conclusión

- El sistema ya está cerca del óptimo en la etapa posterior al OCR.
- La mejora implementada es **ZIP_STORED para PDFs** y deflate solo para textos.
- La sincronización incremental es la siguiente candidata, pero para 5 usuarios y
  500 páginas/día ahorra aproximadamente minuto y medio en un job largo; no
  justifica el riesgo todavía.
- No conviene paralelizar clasificación ni unión de PDFs: el costo es mínimo y
  agregaría complejidad.
- El OCR (y la carga del modelo) sigue siendo el costo dominante.
