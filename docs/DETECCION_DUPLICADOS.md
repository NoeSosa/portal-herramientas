# Detección de duplicados — diseño

**Estado:** documentado, **no implementado**.  
**Fecha:** 2026-10-25.

## Objetivo

Avisar cuando un mismo equipo se escanea más de una vez en el mismo mes y sede,
sin bloquear el procesamiento. El conteo mensual debe considerar cada serial
una sola vez.

## Casos que cubre

1. **Mismo serial en dos jobs distintos** del mismo mes/sede.
2. **Mismo PDF subido dos veces**, aunque el nombre cambie.
3. **Mismo serial repetido dentro del mismo PDF** (hojas duplicadas).
4. **Serial escaneado en una sede distinta** a la del inventario.

## Fuente de datos actual

- `job_pages.sn`: serial leído por hoja.
- `job_pages.estado`: `OK`, `POR_VERIFICAR`, `DESCARTADA`.
- `jobs`: dueño, fecha, nombre, estado.
- `equipos`: sede y estatus del serial.
- No se guarda hoy un hash del PDF ni la confianza OCR.

## Regla propuesta

- Periodo: mes calendario en `America/Mexico_City`.
- Sede: la del inventario para ese serial; si no está en inventario, la sede
  asignada al usuario que subió el documento.
- Se considera escaneado un serial no descartado.
- Para cobertura, el conteo es **distinct(serial)** por periodo/sede.
- Si el serial aparece más de una vez en el periodo/sede, se marca
  **duplicado**, pero no se borra ni se bloquea.
- El aviso es informativo: “Este serial ya se escaneó el 12/oct por Juan”.

## Modelo de datos sugerido

```text
cobertura_escaneos
  id
  periodo          TEXT     -- YYYY-MM
  sede             TEXT
  sn               TEXT
  job_id           TEXT
  owner_id         INTEGER
  pagina_uso       INTEGER
  escaneado_en     TEXT
  duplicado        INTEGER  -- 0/1
  duplicado_de     INTEGER  -- id del primer escaneo
  excluido         INTEGER  -- 0/1, para corregir un error
  motivo_exclusion TEXT
```

Clave de consulta: `(periodo, sede, sn)`. No usar una restricción única que
impida guardar el duplicado: hay que poder mostrarlo y excluirlo.

Opcional futuro: `jobs.file_hash` SHA-256 para detectar el mismo archivo antes
de OCR. Hoy no existe.

## Dónde se detecta

Después de que el job termina y ya están los `job_pages`:

1. Agrupar seriales distintos del job.
2. Buscar el serial en `equipos` para obtener sede.
3. Insertar/actualizar la cobertura del mes.
4. Si ya existe otro escaneo del mismo serial/periodo/sede, marcar duplicado.
5. Crear una notificación al usuario y una alerta en Administración.

No se puede avisar antes de OCR salvo que se compare el hash del archivo; el
serial todavía no se conoce.

## UI propuesta

- **Toast superior derecha** después del job:
  - “2 seriales ya estaban escaneados este mes.”
  - “Ver duplicados”.
- **Resultado del job**: badge `DUPLICADO` por serial.
- **Cobertura mensual**: pestaña “Duplicados” con:
  - serial, sede, fecha, usuario, job;
  - primer escaneo;
  - acción “Excluir este escaneo” (admin).
- **Administración**: filtro de duplicados por sede/mes.

## Falsos positivos y decisiones

- El mismo serial puede escanearse dos veces legítimamente si se corrigió una
  lectura o se rehizo el contador. Por eso **no se bloquea**.
- Un serial puede estar mal leído y parecer distinto; no se puede detectar como
  duplicado sin confianza/fuzzy matching.
- Un equipo trasladado de sede puede aparecer en otra sede; se debe revisar el
  inventario, no asumir duplicado.
- Un serial duplicado dentro del mismo PDF no necesariamente es un error de
  escaneo; puede ser una hoja repetida.

## Exclusiones y limpieza

- “Excluir del conteo” es **soft delete**: conserva el job, el PDF y la
  auditoría; solo deja de contar para cobertura.
- “Limpiar historial del mes” no debería borrar jobs ni archivos: debe reiniciar
  las filas de cobertura de una sede/mes, con confirmación y auditoría.
- Borrar un job (acción existente) sí elimina su cobertura y sus archivos.

## Relación con “faltantes”

La cobertura mensual es la base de los faltantes:

```text
faltantes = equipos activos de la sede
          - seriales distintos escaneados en el mes/sede
```

Los duplicados no aumentan el conteo; solo se muestran para control.
