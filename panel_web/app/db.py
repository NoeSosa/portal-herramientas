"""Persistencia del panel web: usuarios, trabajos, cola y auditoría.

SQLite con WAL es suficiente para 5 usuarios y un worker. Cada petición abre su
propia conexión; el worker reclama trabajos con `BEGIN IMMEDIATE` para que no
haya dos procesos tomando el mismo job.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    _TZ_CUOTA = ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Mexico_City"))
except Exception:
    # Windows sin tzdata: el servidor sí la trae; para pruebas locales cae a UTC.
    _TZ_CUOTA = timezone.utc

ESQUEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    oid         TEXT UNIQUE NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    name        TEXT,
    role        TEXT NOT NULL DEFAULT 'user',
    created_at  TEXT NOT NULL,
    last_login  TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    owner_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    owner_email  TEXT NOT NULL,
    filename     TEXT NOT NULL,
    stored_name  TEXT NOT NULL,
    size         INTEGER NOT NULL DEFAULT 0,
    pages        INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'queued',
    pages_done   INTEGER NOT NULL DEFAULT 0,
    message      TEXT,
    error        TEXT,
    result_path  TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    client_ip    TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_estado ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_fin ON jobs(finished_at);

CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    user_id  INTEGER,
    email    TEXT,
    action   TEXT NOT NULL,
    job_id   TEXT,
    ip       TEXT,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);

-- Detalle por hoja que publica el pipeline mientras procesa.
CREATE TABLE IF NOT EXISTS job_pages (
    job_id      TEXT NOT NULL,
    pag         INTEGER NOT NULL,
    pdf         TEXT,
    pag_total   INTEGER,
    sn          TEXT,
    sede        TEXT,
    marca       TEXT,
    estado      TEXT,
    texto       TEXT,
    worker      TEXT,
    slot        INTEGER,
    pid         INTEGER,
    ms          REAL,
    ts          REAL,
    PRIMARY KEY (job_id, pag)
);
CREATE INDEX IF NOT EXISTS idx_job_pages_job ON job_pages(job_id, pag);

-- Inventario editable desde Administración. El panel exporta
-- inventario_panel.xlsx para que el pipeline lo use como fuente de verdad.
CREATE TABLE IF NOT EXISTS equipos (
    sn               TEXT PRIMARY KEY,
    marca            TEXT,
    modelo           TEXT,
    tipo             TEXT,
    sede             TEXT,
    zona             TEXT,
    domicilio        TEXT,
    ubicacion        TEXT,
    piso             TEXT,
    ip_fisica        TEXT,
    ip_hp            TEXT,
    ip_ekm           TEXT,
    perfil           TEXT,
    estatus          TEXT,
    contador_inicial TEXT,
    fecha_alta       TEXT,
    fecha_baja       TEXT,
    sustituto_sn     TEXT,
    origen_hoja      TEXT,
    actualizado_en   TEXT
);
CREATE INDEX IF NOT EXISTS idx_equipos_sede ON equipos(sede);
CREATE INDEX IF NOT EXISTS idx_equipos_marca ON equipos(marca);

CREATE TABLE IF NOT EXISTS sedes (
    nombre  TEXT PRIMARY KEY,
    correos TEXT,
    huellas TEXT,
    activa  INTEGER DEFAULT 1
);
"""

ESTADOS_ACTIVOS = ("queued", "running")
ESTADOS_FINALES = ("done", "error", "canceled")


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def conectar(ruta: Path) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _migrar_equipos(con: sqlite3.Connection) -> None:
    """Añade columnas nuevas a inventarios creados antes (idempotente)."""
    cols = {f["name"] for f in con.execute("PRAGMA table_info(equipos)")}
    for col in ("ubicacion", "piso"):
        if col not in cols:
            con.execute(f"ALTER TABLE equipos ADD COLUMN {col} TEXT")


def inicializar(ruta: Path) -> None:
    con = conectar(ruta)
    try:
        con.executescript(ESQUEMA)
        _migrar_equipos(con)
        con.commit()
    finally:
        con.close()


def upsert_usuario(
    con: sqlite3.Connection, oid: str, email: str, nombre: str, rol: str
) -> sqlite3.Row:
    """Crea o actualiza el usuario de Entra. `oid` es la identidad estable."""
    email = email.strip().lower()
    ts = ahora()
    fila = con.execute(
        "SELECT id FROM users WHERE oid=? OR email=?", (oid, email)
    ).fetchone()
    if fila:
        con.execute(
            "UPDATE users SET oid=?, email=?, name=?, role=?, last_login=?"
            " WHERE id=?",
            (oid, email, nombre, rol, ts, fila["id"]),
        )
    else:
        con.execute(
            "INSERT INTO users(oid, email, name, role, created_at, last_login)"
            " VALUES(?,?,?,?,?,?)",
            (oid, email, nombre, rol, ts, ts),
        )
    con.commit()
    return con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()


def usuario_por_id(con: sqlite3.Connection, uid: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def usuario_por_email(con: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
    ).fetchone()


def contar_usuarios(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] or 0)


def auditar(
    con: sqlite3.Connection,
    *,
    user_id: int | None,
    email: str | None,
    action: str,
    job_id: str | None = None,
    ip: str | None = None,
    detail: str = "",
) -> None:
    con.execute(
        "INSERT INTO audit(ts, user_id, email, action, job_id, ip, detail)"
        " VALUES(?,?,?,?,?,?,?)",
        (ahora(), user_id, email, action, job_id, ip, detail[:500]),
    )
    con.commit()


def crear_job(
    con: sqlite3.Connection,
    *,
    job_id: str,
    owner_id: int,
    owner_email: str,
    filename: str,
    stored_name: str,
    size: int,
    pages: int,
    client_ip: str | None,
) -> sqlite3.Row:
    con.execute(
        "INSERT INTO jobs(id, owner_id, owner_email, filename, stored_name,"
        " size, pages, status, message, created_at, client_ip)"
        " VALUES(?,?,?,?,?,?,?, 'queued', 'En cola', ?, ?)",
        (job_id, owner_id, owner_email, filename, stored_name, size, pages,
         ahora(), client_ip),
    )
    con.commit()
    return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def job_por_id(
    con: sqlite3.Connection, job_id: str, owner_id: int | None = None
) -> sqlite3.Row | None:
    if owner_id is None:
        return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return con.execute(
        "SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id)
    ).fetchone()


def listar_jobs(
    con: sqlite3.Connection, owner_id: int, limite: int = 100
) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
        (owner_id, limite),
    ).fetchall()


def contar_paginas_hoy(con: sqlite3.Connection, owner_id: int) -> int:
    """Páginas subidas por el usuario en el día local (no en UTC)."""
    inicio = datetime.now(_TZ_CUOTA).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).isoformat(timespec="milliseconds")
    fila = con.execute(
        "SELECT COALESCE(SUM(pages),0) AS total FROM jobs"
        " WHERE owner_id=? AND created_at>=?",
        (owner_id, inicio),
    ).fetchone()
    return int(fila["total"] or 0)


def contar_activos(con: sqlite3.Connection, owner_id: int) -> int:
    fila = con.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE owner_id=? AND status IN ('queued','running')",
        (owner_id,),
    ).fetchone()
    return int(fila["n"] or 0)


def claim_siguiente(con: sqlite3.Connection) -> sqlite3.Row | None:
    """Reclama el job más antiguo. Un solo worker; el lock es la transacción."""
    con.execute("BEGIN IMMEDIATE")
    try:
        fila = con.execute(
            "SELECT * FROM jobs WHERE status='queued'"
            " ORDER BY created_at ASC, id ASC LIMIT 1"
        ).fetchone()
        if not fila:
            con.commit()
            return None
        con.execute(
            "UPDATE jobs SET status='running', started_at=?,"
            " message='Procesando…' WHERE id=?",
            (ahora(), fila["id"]),
        )
        con.commit()
        return con.execute("SELECT * FROM jobs WHERE id=?", (fila["id"],)).fetchone()
    except Exception:
        con.rollback()
        raise


def actualizar_progreso(
    con: sqlite3.Connection, job_id: str, paginas: int, mensaje: str
) -> None:
    con.execute(
        "UPDATE jobs SET pages_done=MAX(pages_done, ?), message=? WHERE id=?",
        (max(0, paginas), mensaje[:200], job_id),
    )
    con.commit()


def finalizar_job(
    con: sqlite3.Connection, job_id: str, result_path: str, paginas: int
) -> None:
    con.execute(
        "UPDATE jobs SET status='done', pages_done=?, result_path=?,"
        " message='Listo para descargar', finished_at=? WHERE id=?",
        (max(0, paginas), result_path, ahora(), job_id),
    )
    con.commit()


def fallar_job(con: sqlite3.Connection, job_id: str, error: str) -> None:
    con.execute(
        "UPDATE jobs SET status='error', error=?, message='Terminó con error',"
        " finished_at=? WHERE id=?",
        (error[:1000], ahora(), job_id),
    )
    con.commit()


def borrar_job(con: sqlite3.Connection, job_id: str, owner_id: int) -> bool:
    cur = con.execute(
        "DELETE FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id)
    )
    con.commit()
    return cur.rowcount > 0


def promedio_segundos_por_pagina(con: sqlite3.Connection) -> float:
    filas = con.execute(
        "SELECT pages, started_at, finished_at FROM jobs"
        " WHERE status='done' AND pages>0 AND started_at IS NOT NULL"
        " AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 20"
    ).fetchall()
    valores = []
    for f in filas:
        try:
            ini = datetime.fromisoformat(f["started_at"])
            fin = datetime.fromisoformat(f["finished_at"])
            segundos = (fin - ini).total_seconds()
        except (TypeError, ValueError):
            continue
        if segundos > 0 and f["pages"]:
            valores.append(segundos / f["pages"])
    if not valores:
        return 12.0
    return max(2.0, sum(valores) / len(valores))


def estadisticas_cola(
    con: sqlite3.Connection, job: sqlite3.Row, promedio_s: float
) -> dict[str, int | float]:
    """Posición y ETA aproximada de un job dentro de la cola global.

    `pages_ahead` cuenta las páginas de los jobs anteriores más lo que le queda
    al que está corriendo. No expone datos de otros usuarios: solo números.
    """
    estado = job["status"]
    if estado in ESTADOS_FINALES:
        return {"posicion": 0, "pages_ahead": 0, "eta_seconds": 0}

    corriendo = con.execute(
        "SELECT pages, pages_done FROM jobs WHERE status='running' LIMIT 1"
    ).fetchone()
    antes = con.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(pages),0) AS paginas FROM jobs"
        " WHERE status='queued' AND (created_at < ? OR (created_at = ? AND id < ?))",
        (job["created_at"], job["created_at"], job["id"]),
    ).fetchone()

    if estado == "running":
        restantes = max(0, int(job["pages"]) - int(job["pages_done"]))
        return {
            "posicion": 0,
            "pages_ahead": 0,
            "eta_seconds": round(restantes * promedio_s),
        }

    paginas_antes = int(antes["paginas"] or 0)
    if corriendo:
        paginas_antes += max(
            0, int(corriendo["pages"] or 0) - int(corriendo["pages_done"] or 0)
        )
    posicion = int(antes["n"] or 0) + (1 if corriendo else 0)
    restantes = max(0, int(job["pages"] or 0) - int(job["pages_done"] or 0))
    return {
        "posicion": posicion,
        "pages_ahead": paginas_antes,
        "eta_seconds": round((paginas_antes + restantes) * promedio_s),
    }


def jobs_vencidos(con: sqlite3.Connection, dias: int) -> list[sqlite3.Row]:
    corte = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(
        timespec="seconds"
    )
    return con.execute(
        "SELECT * FROM jobs WHERE status IN ('done','error','canceled')"
        " AND COALESCE(finished_at, created_at) < ?",
        (corte,),
    ).fetchall()


def borrar_job_por_id(con: sqlite3.Connection, job_id: str) -> None:
    con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    con.commit()


# ---------------------------------------------------------------- administración
def listar_jobs_admin(con: sqlite3.Connection, limite: int = 60) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT j.*, u.name AS owner_name FROM jobs j"
        " LEFT JOIN users u ON u.id=j.owner_id"
        " ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,"
        " j.created_at DESC LIMIT ?",
        (int(limite),),
    ).fetchall()


def upsert_pagina(
    con: sqlite3.Connection, job_id: str, pag: int, **campos: Any
) -> None:
    """Guarda/actualiza el estado de una hoja sin perder datos de la otra fase."""
    permitidos = ("pdf", "pag_total", "sn", "sede", "marca", "estado", "texto",
                  "worker", "slot", "pid", "ms", "ts")
    datos = {c: campos.get(c) for c in permitidos}
    con.execute(
        "INSERT INTO job_pages(job_id, pag, pdf, pag_total, sn, sede, marca,"
        " estado, texto, worker, slot, pid, ms, ts)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(job_id,pag) DO UPDATE SET"
        " pdf=COALESCE(NULLIF(excluded.pdf,''), job_pages.pdf),"
        " pag_total=COALESCE(excluded.pag_total, job_pages.pag_total),"
        " sn=CASE WHEN excluded.sn IS NOT NULL AND excluded.sn<>''"
        "          THEN excluded.sn ELSE job_pages.sn END,"
        " sede=CASE WHEN excluded.sede IS NOT NULL AND excluded.sede<>''"
        "            THEN excluded.sede ELSE job_pages.sede END,"
        " marca=CASE WHEN excluded.marca IS NOT NULL AND excluded.marca<>''"
        "             THEN excluded.marca ELSE job_pages.marca END,"
        " estado=CASE WHEN excluded.estado IS NOT NULL AND excluded.estado<>''"
        "              THEN excluded.estado ELSE job_pages.estado END,"
        " texto=CASE WHEN excluded.texto IS NOT NULL AND excluded.texto<>''"
        "             THEN excluded.texto ELSE job_pages.texto END,"
        " worker=CASE WHEN excluded.worker IS NOT NULL AND excluded.worker<>''"
        "              THEN excluded.worker ELSE job_pages.worker END,"
        " slot=CASE WHEN excluded.slot IS NOT NULL AND excluded.slot>=0"
        "            THEN excluded.slot ELSE job_pages.slot END,"
        " pid=CASE WHEN excluded.pid IS NOT NULL AND excluded.pid>0"
        "           THEN excluded.pid ELSE job_pages.pid END,"
        " ms=COALESCE(excluded.ms, job_pages.ms),"
        " ts=MAX(COALESCE(excluded.ts,0), COALESCE(job_pages.ts,0))",
        (job_id, int(pag), datos["pdf"], datos["pag_total"], datos["sn"],
         datos["sede"], datos["marca"], datos["estado"], datos["texto"],
         datos["worker"], datos["slot"], datos["pid"], datos["ms"], datos["ts"]),
    )


def paginas_job(con: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
    filas = con.execute(
        "SELECT pag, pdf, pag_total, sn, sede, marca, estado, worker, slot, pid,"
        " ms, ts, CASE WHEN texto IS NOT NULL AND texto<>'' THEN 1 ELSE 0 END AS ocr"
        " FROM job_pages WHERE job_id=? ORDER BY pag",
        (job_id,),
    ).fetchall()
    return [dict(f) for f in filas]


def resumen_paginas(con: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    filas = con.execute(
        "SELECT pag, sn, sede, marca, estado, texto, ts FROM job_pages"
        " WHERE job_id=? ORDER BY pag",
        (job_id,),
    ).fetchall()
    por_estado: dict[str, int] = {}
    por_sede: dict[str, int] = {}
    seriales: dict[str, dict[str, str]] = {}
    for f in filas:
        estado = (f["estado"] or "").strip().upper()
        if estado:
            por_estado[estado] = por_estado.get(estado, 0) + 1
        sede = (f["sede"] or "").strip() or "(sin sede)"
        por_sede[sede] = por_sede.get(sede, 0) + 1
        sn = (f["sn"] or "").strip()
        if sn:
            seriales[sn] = {"sn": sn, "sede": f["sede"] or "",
                            "marca": f["marca"] or ""}
    return {"por_estado": por_estado, "por_sede": por_sede,
            "seriales": list(seriales.values())[:80]}


def trabajadores_job(con: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
    filas = con.execute(
        "SELECT slot, worker, COUNT(*) AS paginas, MAX(pag) AS ultima,"
        " MAX(ts) AS ultimo_ts, AVG(ms) AS ms, MAX(pid) AS pid"
        " FROM job_pages WHERE job_id=? AND (slot IS NOT NULL OR worker IS NOT NULL)"
        " GROUP BY slot, worker ORDER BY slot, worker",
        (job_id,),
    ).fetchall()
    return [dict(f) for f in filas]


# ----------------------------------------------------------------- inventario
CAMPOS_EQUIPO = ("marca", "modelo", "tipo", "sede", "zona", "domicilio",
                 "ubicacion", "piso", "ip_fisica", "ip_hp", "ip_ekm",
                 "perfil", "estatus", "contador_inicial", "sustituto_sn",
                 "origen_hoja")


def upsert_equipo(con: sqlite3.Connection, sn: str, **campos: Any) -> tuple[bool, str]:
    sn = (sn or "").strip().upper()
    if not sn:
        raise ValueError("serial vacío")
    existia = con.execute("SELECT 1 FROM equipos WHERE sn=?", (sn,)).fetchone() is not None
    cols = ["sn"] + [c for c in CAMPOS_EQUIPO if c in campos]
    vals = [sn] + [("" if campos.get(c) is None else str(campos.get(c))) for c in cols[1:]]
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
    sufijo = (f" ON CONFLICT(sn) DO UPDATE SET {sets},"
              " actualizado_en=datetime('now')" if sets else "")
    con.execute(
        f"INSERT INTO equipos({','.join(cols)}, actualizado_en)"
        f" VALUES({','.join('?' * len(cols))}, datetime('now'))" + sufijo,
        vals,
    )
    con.commit()
    return (not existia), sn


def borrar_equipo(con: sqlite3.Connection, sn: str) -> bool:
    cur = con.execute("DELETE FROM equipos WHERE sn=?", ((sn or "").strip().upper(),))
    con.commit()
    return cur.rowcount > 0


def borrar_equipos(con: sqlite3.Connection, **filtros) -> int:
    """Borra todo lo que coincida. Sin filtros equivale a vaciar el inventario."""
    where, params = _filtros_equipos(**filtros)
    cur = con.execute(f"DELETE FROM equipos{where}", params)
    con.commit()
    return int(cur.rowcount or 0)


def _filtros_equipos(q=None, sede=None, marca=None, estatus=None, zona=None):
    where, params = [], []
    if q:
        where.append(
            "(sn LIKE ? OR marca LIKE ? OR modelo LIKE ? OR sede LIKE ?"
            " OR ubicacion LIKE ? OR piso LIKE ?)"
        )
        params += [f"%{q}%"] * 6
    for campo, valor in (("sede", sede), ("marca", marca),
                         ("estatus", estatus), ("zona", zona)):
        if valor:
            where.append(f"{campo}=?")
            params.append(valor)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def contar_equipos(con: sqlite3.Connection, **filtros) -> int:
    where, params = _filtros_equipos(**filtros)
    return int(con.execute(
        f"SELECT COUNT(*) AS n FROM equipos{where}", params
    ).fetchone()["n"] or 0)


def listar_equipos(
    con: sqlite3.Connection, limite: int = 100, offset: int = 0,
    orden: str = "sn", **filtros,
) -> list[dict[str, Any]]:
    where, params = _filtros_equipos(**filtros)
    orden = orden if orden in ("sn", "marca", "modelo", "sede", "zona", "estatus") else "sn"
    filas = con.execute(
        f"SELECT sn, marca, modelo, tipo, sede, zona, domicilio, ubicacion,"
        f" piso, estatus, contador_inicial, origen_hoja, actualizado_en"
        f" FROM equipos{where}"
        f" ORDER BY {orden} LIMIT ? OFFSET ?",
        params + [max(1, min(500, int(limite))), max(0, int(offset))],
    ).fetchall()
    return [dict(f) for f in filas]


def valores_equipos(con: sqlite3.Connection, columna: str) -> list[str]:
    columna = columna if columna in ("sede", "marca", "estatus", "zona") else "sede"
    filas = con.execute(
        f"SELECT DISTINCT {columna} AS v FROM equipos"
        f" WHERE {columna} IS NOT NULL AND {columna}<>'' ORDER BY {columna}"
    ).fetchall()
    return [f["v"] for f in filas]


def resumen_sedes(con: sqlite3.Connection) -> list[dict[str, Any]]:
    filas = con.execute(
        "SELECT COALESCE(NULLIF(sede,''),'(sin sede)') AS sede,"
        " COUNT(*) AS equipos, COUNT(DISTINCT marca) AS marcas,"
        " COUNT(DISTINCT zona) AS zonas, MAX(actualizado_en) AS actualizado"
        " FROM equipos GROUP BY COALESCE(NULLIF(sede,''),'(sin sede)')"
        " ORDER BY equipos DESC, sede"
    ).fetchall()
    return [dict(f) for f in filas]
