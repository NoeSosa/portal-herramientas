# Cobertura mensual y faltantes — diseño

**Estado:** propuesta, **no implementada**.  
**Fecha:** 2026-10-25.

## Qué se quiere responder

Para una sede y un mes:

- ¿Qué equipos del inventario ya se escanearon?
- ¿Cuáles faltan?
- ¿Cuáles se escanearon dos veces?
- ¿Quién los escaneó?
- ¿Cómo corregir un escaneo equivocado sin perder el historial?

## Decisión previa: sede del usuario

Hoy `users` no tiene sede. Para mostrar “los faltantes de mi sede” hace falta
una de estas opciones:

| Opción | Ventaja | Problema |
|---|---|---|
| **A. Asignar sede(s) al usuario (recomendada)** | Cobertura estable y automática | Requiere pantalla/admin de asignación |
| B. Elegir sede al subir | No requiere catálogo de usuarios | Se puede elegir mal; ensucia la cobertura |
| C. Deducir sede del inventario | Cero configuración | Si el serial falta, no se puede deducir |

Recomendación: **A**, con una tabla `user_sedes(user_id, sede)` para permitir
usuarios que cubren más de una sede. Para 5 usuarios es suficiente.

## Qué cuenta como escaneado

Un serial cuenta si:

- aparece en un job terminado del mes;
- no está marcado `DESCARTADA`;
- está en el inventario de la sede (para poder compararlo con los esperados).

Los seriales leídos que no están en inventario se muestran aparte como
**“no reconocidos”**, no como faltantes ni como escaneados.

## Cálculo

```text
esperados  = equipos de la sede con estatus activo
escaneados = distinct(serial) en cobertura del mes/sede
faltantes  = esperados - escaneados
duplicados = escaneos del serial - 1 (por mes/sede)
```

### Estatus del inventario

Hay que definir qué estatus cuentan como “esperados”. Opciones:

- incluir solo `En Operación`;
- incluir `En Operación` y `Sin tóner`/incidencias;
- excluir `Baja`, `Almacenada`, `Dañada`.

Recomendación inicial: **incluir todos menos `Baja` y `Almacenada`**, y permitir
al administrador ajustar la lista. Sin esta decisión, los faltantes pueden ser
falsos.

### Fecha del escaneo

Usar `jobs.finished_at` en `America/Mexico_City`. Si un job cruza el cambio de
mes, se imputa al mes en que terminó. Documentarlo.

## Modelo de datos

```text
user_sedes
  user_id INTEGER
  sede TEXT
  PRIMARY KEY(user_id, sede)

cobertura_escaneos
  id
  periodo          TEXT     -- YYYY-MM
  sede             TEXT
  sn               TEXT
  job_id           TEXT
  owner_id         INTEGER
  escaneado_en     TEXT
  duplicado        INTEGER
  duplicado_de     INTEGER
  excluido         INTEGER
  motivo_exclusion TEXT
```

No se debe calcular todo desde `job_pages` en cada consulta porque:

- los jobs se borran por retención (30 días);
- hace falta conservar el historial mensual;
- hace falta recordar exclusiones.

La cobertura debe persistir aunque el job se elimine por retención. Si se borra
un job manualmente, se puede conservar la fila de cobertura marcada como
“archivo eliminado” o eliminarla; hay que decidirlo.

## Vista del usuario

### Opción recomendada: tarjeta + pestaña

**Tarjeta en el inicio** (solo si el usuario tiene sede asignada):

```text
COBERTURA · OCTUBRE
Reynosa
124 / 150 equipos escaneados (83%)
Faltan 26 · Duplicados 2
[Ver faltantes]
```

**Pestaña “Cobertura”** dentro del panel:

```text
[Mes: octubre 2026] [Sede: Reynosa]

124 de 150 escaneados    26 faltantes    2 duplicados

FALTANTES
Sede | Serial | Marca | Modelo | Ubicación | Piso | Estatus
...

ESCANEADOS
Serial | Fecha | Documento | Usuario | Estado | Duplicado
...
```

- El usuario solo ve las sedes que tiene asignadas.
- Si tiene varias sedes, selector de sede.
- Si no tiene sede: “Solicita a administración que asigne tu sede”.
- Admin ve todas las sedes y puede exportar.
- La tabla de faltantes debe permitir buscar por serial, marca, modelo,
  ubicación o piso.
- El botón “Descargar CSV/Excel” es opcional; primero validar la vista.

### Opción compacta

Un panel lateral fijo con el mismo resumen y un modal para faltantes. Menos
descubrible; no la recomiendo como primera versión.

### Opción solo reporte

Generar un Excel/CSV mensual y enviarlo por correo. No permite consultar en
tiempo real ni corregir; sirve como complemento, no como reemplazo.

## Duplicados y limpieza

- Un serial escaneado dos veces se muestra con badge **DUPLICADO**.
- El conteo de cobertura usa `distinct(serial)`: el duplicado no reduce los
  faltantes.
- El usuario puede ver “quién y cuándo” escaneó cada serial.
- **Excluir un escaneo del conteo**: acción administrativa, soft delete, con
  motivo y auditoría. No borra el PDF.
- **Borrar un job**: acción existente; elimina archivos y su cobertura.
- **Reiniciar cobertura de una sede/mes**: solo admin; borra las filas de
  cobertura de ese periodo, no los jobs. Sirve para volver a procesar, pero
  debe pedir confirmación y quedar auditado.
- No conviene un “borrar todo el historial” global: destruye la trazabilidad.

## Notificaciones en pantalla

- Contenedor fijo arriba a la derecha.
- Tipos: éxito, aviso, error.
- Se dispara al detectar cambio de estado en `/api/jobs`:
  - documento listo;
  - documento con error;
  - seriales duplicados;
  - cobertura actualizada/faltantes.
- Auto-cierre a los 5–8 s; los errores quedan hasta que el usuario los cierre.
- `aria-live="polite"` para accesibilidad.
- No usar notificaciones del navegador en la primera versión; los toasts son
  suficientes y no requieren permisos.

## Búsqueda

- En “Mis documentos”: por nombre, estado y fecha.
- En cobertura: por serial, marca, modelo, ubicación o piso.
- En faltantes: mismos filtros, más estatus.
- Búsqueda en servidor si crece el histórico; para 5 usuarios y retención de 30
  días, se puede empezar en cliente.

## Cuotas

La regla ya existe: `MAX_QUEUED_JOBS=3` limita a 3 documentos activos
(`queued`/`running`) por usuario. No se implementan cuotas mensuales de páginas
ni de documentos.

## Fases sugeridas

1. **Base de datos y asignación de sede** (`user_sedes`, `cobertura_escaneos`).
2. **Backfill** desde `job_pages` de jobs terminados del mes actual.
3. **Vista de cobertura de solo lectura** + faltantes con las columnas pedidas.
4. **Duplicados** y detalle de escaneos.
5. **Exclusiones/reinicio** con auditoría.
6. **Notificaciones/alertas** y exportación.

## Riesgos y decisiones pendientes

- Inventario desactualizado: falsos faltantes.
- Estatus que deben contar como activos.
- Usuarios con varias sedes.
- Jobs que cruzan el mes.
- Seriales no reconocidos/no inventariados.
- Retención de 30 días vs historial mensual.
- ¿Quién puede excluir/reiniciar: solo admin o también usuario?
- ¿Un escaneo duplicado debe contar para cobertura? Recomendación: sí, una vez.

## Recomendación

Implementar primero **cobertura de solo lectura** con backfill y asignación de
sede. No incluir todavía reinicio/exclusiones hasta validar que los números
cuadran con el inventario real. Después agregar duplicados y limpieza.
