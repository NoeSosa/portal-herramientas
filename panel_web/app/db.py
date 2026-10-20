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


def inicializar(ruta: Path) -> None:
    con = conectar(ruta)
    try:
        con.executescript(ESQUEMA)
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
