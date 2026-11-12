# MCP pdf-separator

MCP (stdio) para consultar y mantener el inventario del panel
**pdf-separator** (`pdf-separator.duckdns.org`) sin sacar la BD del servidor.

```
Muse (VM) ──MCP stdio──> server.py ──SSH (llave muse_tailscale)──> pihole
                                                              sudo python3 db_ops.py
                                                              ├─ panel_web.db (solo lectura, salvo `aplicar`)
                                                              └─ docker exec pdfsep-web (código del propio panel)
```

La BD (`panel_web.db`, SQLite WAL, volumen `pdf-separator_panel_data:/data`)
**nunca sale del servidor**. El puente SSH usa la llave dedicada
`~/.ssh/muse_tailscale` + el proxy Tailscale de la VM; ninguna credencial
viaja como argumento ni variable del proceso MCP.

## Herramientas

| Herramienta | Tipo | Qué hace |
|---|---|---|
| `consultar_equipo(sn)` | lectura | Fila completa por serie (exacta → `sn_norm` → alterna OCR) |
| `buscar_equipos(q, sede, estatus, limit)` | lectura | Búsqueda con los mismos filtros del panel (máx. 200) |
| `resumen_sede(sede)` | lectura | Totales por estatus/ubicación + cobertura por periodo |
| `plan_actualizacion_constitucion()` | **dry-run** | Plan completo contra la BD real, sin escribir |
| `aplicar_actualizacion_constitucion(fecha_baja)` | **escritura** | Aplica Constitución (ver Seguridad) |
| `exportar_inventario()` | regenera Excel | `exportar_xlsx` del panel + copia a `exports/` en la VM |
| `verificar_normalizacion()` | lectura | Deriva de `normalizacion.py` vs BD (debe ser 0) |

## Seguridad de `aplicar`

1. Requiere autorización explícita del usuario (la herramienta no se
   auto-dispara; el dry-run `plan_*` es el paso previo recomendado).
2. Respaldo consistente con la API de backup de SQLite en
   `~/pdf-separator-mcp/backups/` **antes** de escribir.
3. El núcleo se aplica con `inventario.importar_xlsx` del propio panel
   (misma semántica de una importación: upserts con `sn_norm`/
   `ubicacion_norm` y reconciliación de cobertura del mes abierto).
4. Extensiones solo-llenado con validación (perfil ∈ A1/A2/C1/D, tipo,
   IP válida, fecha parseable); nunca blanquean valores existentes.
5. Sustituciones: la serie vieja pasa a `Retirada` con `fecha_baja` y
   `sustituto_sn` (nunca se borra la fila). Los 3 pares con lectura
   dudosa (MX3CV8N04Z, MX3CV7R0KT, VNG3H10259) quedan en pausa.
6. Regenera `inventario_panel.xlsx` (lo que lee el pipeline OCR) y deja
   fila en la tabla `audit` del panel (`email='miku-mcp'`).
7. La sede se conserva **exacta** (`Avenida Constitución Poniente 241`):
   la visibilidad por usuario del frontend (`user_sedes` /
   `listar_sedes`) no se toca.

## Despliegue y sincronización

- `server.py` (VM) sincroniza `db_ops.py`, `plan.py` y
  `apply_in_container.py` al servidor en cada operación de plan/aplicar/
  exportar. Para sincronizar a mano:
  `scp db_ops.py plan.py apply_in_container.py ubuntu@pihole:pdf-separator-mcp/`
- `app_ref/` (copia byte-exacta de `normalizacion.py` e `inventario.py`
  del contenedor) es **referencia local y no va a git**. Si el panel se
  reconstruye, recópiala con `docker cp` y corre `verificar_normalizacion`
  (debe dar 0/0) antes de aplicar nada.

## Rollback

Los respaldos están en `~/pdf-separator-mcp/backups/`. Para revertir:
detener `pdfsep-web` (Dokploy), copiar el respaldo sobre
`panel_web.db` del volumen (y borrar `panel_web.db-wal`/`-shm` si existen),
y volver a levantar el contenedor.

## Uso local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python mcp/server.py --selftest   # prueba sin servidor MCP
.venv/bin/python mcp/server.py              # servidor MCP stdio
```

El cliente CLI (`list-tools` / `call-tool`) y la documentación del conector
viven en `~/workspace/custom-connectors/custom.pdf-separator-mcp/`.

## Datos fuera de git

`seed_v2_constitucion.csv`, `pares_sustitucion.csv`, `plan.json`,
`inventario_panel.xlsx`, `exports/` y `app_ref/` están en `.gitignore`:
el repo lleva solo código y documentación; los datos del inventario no
salen del servidor/VM.
