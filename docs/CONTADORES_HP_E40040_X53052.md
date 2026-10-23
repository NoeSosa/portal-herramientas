# Contadores HP E40040 y X53052

Fecha: 23 de octubre de 2026.

## Documento revisado y alcance

Se revisaron visualmente las 22 páginas de `Nuevas_2.pdf`, suministrado por el
usuario. Es un escaneo sin capa de texto. La función existente del sistema es
separar las hojas del contador en PDFs por equipo: esta ampliación mantiene ese
contrato, sin convertir los valores de las tablas a datos numéricos ni sumar
impresiones, equivalentes Carta/A4 y escaneados, que son magnitudes distintas.

En la muestra, las hojas de uso están en las posiciones originales (base 1):

| Modelo | Páginas del archivo | Hojas de uso |
| --- | --- | --- |
| E40040 | 1 | Una |
| E40040 | 9 | Una |
| X53052 | 13–14 | Dos |
| X53052 | 17 | Una |
| X53052 | 21–22 | Dos |

Las páginas 2, 10 y 18 aparecen vacías; las otras 12 son configuración o red.
Las continuaciones 14 y 22 llevan el modelo y «Página 2», pero no repiten la
serie. La página 14 contiene sólo el total final de una tabla; la 22 contiene
la tabla de escaneados por destino. Se conserva la hoja completa en ambos casos.

## Reglas añadidas

- Reconocimiento explícito de E40040/X53052, por encima de la clasificación
  histórica por prefijo de serie; tolerancia acotada a confusiones OCR O/0 y Z/2.
- Lectura de la etiqueta «Número de serie del producto» de estos modelos.
- Conservación de «Página de uso» / «Usage page» y exclusión de las hojas
  identificadas como configuración o Jetdirect de estos dos modelos.
- Herencia de la serie e inventario de P1 a P2 sólo si son consecutivas en el
  PDF original, del mismo modelo y sin series contradictorias. No se atraviesan
  blancos, configuración, otros equipos o páginas faltantes.
- Una P1 se acepta sola; una pareja P1/P2 se acepta como un contador. Se siguen
  requiriendo la validación de inventario y la sede según el flujo existente.
- P2 huérfana, numeración ilegible, tipo desconocido, series contradictorias y
  duplicados quedan para revisión. No se descarta una hoja sólo por carecer de
  serie. Las hojas vacías siguen el tratamiento previo de páginas sin identificar.
- Se conserva el camino de OCR completo para estos modelos en el modo opcional
  secuencial, aunque el prefijo de su serie coincida con el de un HP anterior.

Se reutiliza el principio de emparejamiento P1/P2 de Pantum y de filtrado de
configuración de Ricoh, con reglas independientes para los nuevos modelos.

## Archivos y despliegue

Las reglas se incorporaron a `separador/separador_pdf.py` y
`separador_pdfs/separador_pdf.py`; los dos `separador_auto.py` las reutilizan.
El primero sirve al worker privado; el segundo al panel público. Una prueba
verifica que los helpers nuevos son iguales en ambos árboles. No se edita
`backend/separador/`, porque es una copia generada.

No se cambiaron las versiones, modelos ni dependencias de PaddleOCR/PaddlePaddle.
Los cambios anteriores de dependencias siguen separados de esta ampliación.
No se realizó commit, push ni despliegue. El panel público incluye el código en
su propia imagen sobre la base OCR existente. Para el worker privado, el código
está dentro de su imagen: no basta con actualizar el repositorio del servidor;
su publicación debe conservar el entorno OCR ya validado.

## Verificación

- `python -m pytest scripts/tests/test_contadores_hp.py -q`: **53 aprobadas**.
- Casos de una/dos hojas en ambos modelos y árboles; continuación sin serie;
  serie fuera de inventario; mezcla de modelos; configuración intercalada;
  blancos; P2 huérfana; duplicados; conflicto de series; PDFs reales de salida.
- Simulación de las 22 posiciones de la muestra con cabeceras transcritas:
  cinco contadores, siete hojas de uso, doce descartadas, tres para revisión.
- **32 comparaciones idénticas** frente al código anterior de `HEAD`:
  ocho escenarios por árbol, en el flujo automático y el directo. Cubren
  HP 501, HP MFP 432, Pantum, Ricoh y Kyocera; comparan la clasificación y el
  contenido de las páginas de salida.
- Los cuatro módulos compilan y `git diff --check` no detecta errores de espacios.

Las pruebas ejecutan las funciones de producción extrayéndolas mediante AST para
evitar cargar PaddleOCR en este equipo. Sustituyen únicamente la lectura OCR por
textos controlados y usan PyMuPDF para generar/verificar los PDFs de prueba.
**No equivalen a una ejecución del OCR real sobre el documento escaneado.**
Queda pendiente esa comprobación con el motor ARM64 existente en el servidor,
incluyendo su inventario real y posibles errores de lectura de las nuevas series.
