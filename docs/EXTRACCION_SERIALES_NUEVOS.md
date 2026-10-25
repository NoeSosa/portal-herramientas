# Extracción de seriales nuevos (3 letras)

**Fecha:** 2026-10-25  
**Caso base:** `Nuevas_2.pdf` (22 páginas escaneadas, HP E40040 y X53052).

## Regla

Un serial nuevo se identifica por la etiqueta de la hoja de uso:

```text
Número de serie del producto: VNG3H10262
Número de serie del producto: MX3CV6Q0DK
```

No se exige que el serial esté en el inventario. Si no está, el documento se
genera igual en `POR_VERIFICAR`, con el serial como nombre.

## Cambios implementados

1. **Etiqueta tolerante al OCR**
   - Acepta `de_serie`, `dei producto`, `producto:_`, `Número serie equipo` y
     `Product Serial Number`.
   - Normaliza `O`/`I`/`L` después de un dígito: `MX3CV6QODK` → `MX3CV6Q0DK`.
2. **Fallback genérico**
   - Serial de 3 letras + alfanumérico, con al menos 2 dígitos.
3. **Agrupación por serial**
   - Todas las hojas de uso del mismo serial forman **un solo PDF**.
   - Antes, si el serial no estaba en inventario, se generaba un PDF por hoja.
4. **Páginas de configuración**
   - Para HP E40040/X53052 se descartan las páginas de configuración/Jetdirect.
5. **Hojas en blanco o reverso**
   - Se descartan; ya no generan archivos `Sin_Identificar`.
6. **Sin dependencia del inventario**
   - El serial se extrae de la hoja, no de la base.
   - El inventario solo decide si el documento va a `VERIFICADOS` o
     `POR_VERIFICAR`, y aporta la sede.

## Resultado con `Nuevas_2.pdf`

Con el inventario vacío, el motor genera **5 documentos**:

| Serial | Páginas de uso | Estado |
|---|---|---|
| `VNG3H10262` | 1 | POR_VERIFICAR |
| `VNG3H10339` | 9 | POR_VERIFICAR |
| `MX3CV6Q0DZ` | 13–14 | POR_VERIFICAR |
| `MX3CV6Q086` | 17 | POR_VERIFICAR |
| `MX3CV6Q0DK` | 21–22 | POR_VERIFICAR |

Se descartan 12 páginas de configuración y 3 en blanco (2, 10, 18). La suma
cuadra: 7 hojas de uso + 12 configuración + 3 blancas = 22.

Si esos seriales están en el inventario, los mismos 5 documentos salen en
`VERIFICADOS` y con la sede correspondiente.

## Pruebas

- `scripts/tests/test_contadores_hp.py`
  - serial con `de_serie` y `dei producto`;
  - serial nuevo fuera de inventario agrupado en un solo documento;
  - layout de 22 hojas con 5 seriales, sin archivos de hojas blancas.
- Regresión manual con el OCR real de `Nuevas_2.pdf`: 5 grupos, 15 descartes,
  0 `Sin_Identificar`.

## Límites

- El fallback genérico exige al menos 2 dígitos para no confundir palabras.
- Si una hoja no trae etiqueta ni un serial con 3 letras/dígitos, sigue cayendo
  en `Sin_Identificar` para revisión.
- El nombre final usa el serial normalizado; si el inventario tiene el serial
  real, se usa el valor del inventario.
