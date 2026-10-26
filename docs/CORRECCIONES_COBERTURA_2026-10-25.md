# Correcciones de integridad y usabilidad

## Comportamiento implementado

- La migración completa las claves normalizadas de equipos existentes. La edición verifica la identidad y los permisos de sede dentro de una transacción. Las claves ambiguas requieren la serie exacta; no se fusionan equipos existentes.
- Se conserva la serie escrita en inventario, con mayúsculas y sin espacios extremos, y se usa una clave separada para comparar lecturas OCR. No es posible recuperar caracteres eliminados por importaciones anteriores.
- La limpieza automática por antigüedad elimina los archivos y el trabajo, pero conserva las lecturas y la cobertura, incluido el nombre del documento. El borrado manual del trabajo elimina su aportación a cobertura. Las exclusiones recalculan los duplicados.
- El inventario del mes abierto se actualiza con altas, cambios y bajas. Al cambiar de mes, la copia del mes anterior queda fija. Las correcciones administrativas de escaneos siguen disponibles.
- Los meses antiguos sin una copia previa se reconstruyen con el inventario disponible y se identifican como reconstruidos en pantalla. No representan una recuperación exacta de inventarios pasados ni de registros ya eliminados antes de esta corrección.
- Las altas de inventario reconcilian lecturas del mes abierto sin repetir OCR, incluso cuando el PDF ya caducó. Se respetan exclusiones y reinicios administrativos.
- Una subida con los mismos bytes que un trabajo conservado del mismo usuario muestra una advertencia y permite confirmar el reprocesamiento. Se compara SHA-256, no el nombre; no se revelan trabajos de otros usuarios. Los trabajos previos a esta versión no tienen huella y los eliminados dejan de participar en esta advertencia.
- Las páginas con texto corto se conservan. Un OCR vacío sólo descarta la hoja si la comprobación de imagen confirma que está prácticamente blanca; si la imagen no se puede comprobar, se conserva para revisión.
- La cobertura se consulta por tiempo transcurrido y descarta respuestas anteriores cuando ya existe una consulta más reciente. Las tablas y formularios tienen texto más legible; los meses históricos indican su procedencia y ocultan las acciones de edición del inventario.

## Instalación y verificación

La base de datos se migra al iniciar la aplicación. Respaldar la base y los datos antes del despliegue; reiniciar panel y worker con la misma versión. No se modificaron versiones de PaddleOCR, PaddlePaddle ni PyMuPDF.

Validación local: 112 pruebas y 39 subpruebas aprobadas, incluyendo peticiones HTTP con sesiones firmadas, permisos, CSRF, duplicación de PDF, historial y clasificación de los dos separadores. Las pruebas de clasificación usan texto OCR controlado y PDFs reales; no miden la precisión del motor sobre nuevos escaneos. Comprobación de sintaxis de ambos JavaScript aprobada. Queda pendiente la validación visual en navegador y la prueba operativa con escaneos reales en el servidor.
