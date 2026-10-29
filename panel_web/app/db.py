"""Persistencia del panel web: usuarios, trabajos, cola y auditoría.

SQLite con WAL es suficiente para 5 usuarios y un worker. Cada petición abre su
propia conexión; el worker reclama trabajos con `BEGIN IMMEDIATE` para que no
haya dos procesos tomando el mismo job.
"""

from __future__ import annotations

import os
import json
import re
import sqlite3
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .normalizacion import normalizar_serial, normalizar_ubicacion

try:
    _TZ_CUOTA = ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Mexico_City"))
except Exception:
    # Windows sin tzdata: el servidor sí la trae; para pruebas locales cae a UTC.
    _TZ_CUOTA = timezone.utc

ESQUEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    oid          TEXT UNIQUE NOT NULL,
    email        TEXT UNIQUE NOT NULL,
    name         TEXT,
    display_name TEXT,
    role         TEXT NOT NULL DEFAULT 'user',
    created_at   TEXT NOT NULL,
    last_login   TEXT
);

CREATE TABLE IF NOT EXISTS user_sedes (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sede    TEXT NOT NULL,
    PRIMARY KEY (user_id, sede)
);
CREATE INDEX IF NOT EXISTS idx_user_sedes_sede ON user_sedes(sede);

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

-- Ubicaciones canónicas: evita "2 penal", "Segundos Penal" y
-- "Juzgado Segundo en Materia Penal" como tres ubicaciones distintas.
-- Cobertura mensual por sede/serial. Persiste aunque el job se borre por
-- retención; `excluido` permite corregir un escaneo sin borrar archivos.
CREATE TABLE IF NOT EXISTS cobertura_escaneos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    periodo         TEXT NOT NULL,
    sede            TEXT NOT NULL,
    sn              TEXT NOT NULL,
    job_id          TEXT,
    owner_id        INTEGER,
    escaneado_en    TEXT NOT NULL,
    duplicado       INTEGER NOT NULL DEFAULT 0,
    duplicado_de    INTEGER,
    excluido        INTEGER NOT NULL DEFAULT 0,
    motivo_exclusion TEXT,
    UNIQUE(periodo, sede, sn, job_id)
);
CREATE INDEX IF NOT EXISTS idx_cobertura_periodo_sede
    ON cobertura_escaneos(periodo, sede, excluido);
CREATE INDEX IF NOT EXISTS idx_cobertura_sn
    ON cobertura_escaneos(sn);

CREATE TABLE IF NOT EXISTS cobertura_resets (
    periodo TEXT NOT NULL,
    sede    TEXT NOT NULL,
    reset_en TEXT NOT NULL,
    PRIMARY KEY (periodo, sede)
);

CREATE TABLE IF NOT EXISTS cobertura_meta (
    periodo TEXT PRIMARY KEY,
    backfill_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cobertura_periodos (
    periodo TEXT PRIMARY KEY,
    capturado_en TEXT NOT NULL,
    origen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cobertura_inventario (
    periodo TEXT NOT NULL,
    sn TEXT NOT NULL,
    sede TEXT NOT NULL,
    datos TEXT NOT NULL,
    PRIMARY KEY(periodo, sn)
);
CREATE TABLE IF NOT EXISTS cobertura_lecturas (
    job_id TEXT NOT NULL,
    sn TEXT NOT NULL,
    periodo TEXT NOT NULL,
    owner_id INTEGER,
    escaneado_en TEXT NOT NULL,
    documento TEXT NOT NULL,
    PRIMARY KEY(job_id, sn)
);
CREATE INDEX IF NOT EXISTS idx_lecturas_periodo ON cobertura_lecturas(periodo);
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
    for col in ("ubicacion", "piso", "sn_norm", "ubicacion_norm"):
        if col not in cols:
            con.execute(f"ALTER TABLE equipos ADD COLUMN {col} TEXT")
    # Completar registros anteriores sin alterar su serie real ni fusionarlos.
    for fila in con.execute("SELECT sn, ubicacion FROM equipos").fetchall():
        con.execute("UPDATE equipos SET sn_norm=?, ubicacion_norm=? WHERE sn=?",
                    (normalizar_serial(fila["sn"]), normalizar_ubicacion(fila["ubicacion"]), fila["sn"]))
    con.execute("CREATE INDEX IF NOT EXISTS idx_equipos_sn_norm ON equipos(sn_norm)")


def _migrar_usuarios(con: sqlite3.Connection) -> None:
    cols = {f["name"] for f in con.execute("PRAGMA table_info(users)")}
    if "display_name" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN display_name TEXT")


def inicializar(ruta: Path) -> None:
    con = conectar(ruta)
    try:
        con.executescript(ESQUEMA)
        con.execute("BEGIN IMMEDIATE")
        _migrar_equipos(con)
        _migrar_usuarios(con)
        if "file_hash" not in {f["name"] for f in con.execute("PRAGMA table_info(jobs)")}:
            con.execute("ALTER TABLE jobs ADD COLUMN file_hash TEXT")
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_hash ON jobs(owner_id, file_hash)")
        if "documento" not in {f["name"] for f in con.execute("PRAGMA table_info(cobertura_escaneos)")}:
            con.execute("ALTER TABLE cobertura_escaneos ADD COLUMN documento TEXT")
        con.execute("UPDATE cobertura_escaneos SET documento=(SELECT filename FROM jobs WHERE id=job_id) WHERE documento IS NULL")
        asegurar_inventario_periodo(con, periodo_de())
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
            "UPDATE users SET oid=?, email=?, name=?, role=?, last_login=?,"
            " display_name=COALESCE(NULLIF(display_name,''), ?)"
            " WHERE id=?",
            (oid, email, nombre, rol, ts, nombre, fila["id"]),
        )
    else:
        con.execute(
            "INSERT INTO users(oid, email, name, display_name, role, created_at, last_login)"
            " VALUES(?,?,?,?,?,?,?)",
            (oid, email, nombre, nombre, rol, ts, ts),
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


def sedes_usuario(con: sqlite3.Connection, uid: int) -> list[str]:
    return [
        f["sede"] for f in con.execute(
            "SELECT sede FROM user_sedes WHERE user_id=? ORDER BY sede", (uid,)
        )
    ]


def asignar_sedes(con: sqlite3.Connection, uid: int, sedes: list[str]) -> None:
    limpias = sorted({(s or "").strip() for s in sedes if (s or "").strip()})
    con.execute("DELETE FROM user_sedes WHERE user_id=?", (uid,))
    con.executemany(
        "INSERT OR IGNORE INTO user_sedes(user_id, sede) VALUES(?,?)",
        [(uid, s) for s in limpias],
    )
    con.commit()


def actualizar_display_name(con: sqlite3.Connection, uid: int, nombre: str) -> None:
    con.execute(
        "UPDATE users SET display_name=? WHERE id=?",
        ((nombre or "").strip()[:120], uid),
    )
    con.commit()


def listar_usuarios(con: sqlite3.Connection) -> list[dict[str, Any]]:
    filas = con.execute(
        "SELECT id, email, name, display_name, role, created_at, last_login"
        " FROM users ORDER BY email"
    ).fetchall()
    return [
        dict(f, sedes=sedes_usuario(con, int(f["id"]))) for f in filas
    ]


def listar_sedes(con: sqlite3.Connection) -> list[str]:
    filas = con.execute(
        "SELECT DISTINCT sede FROM equipos"
        " WHERE sede IS NOT NULL AND sede<>'' ORDER BY sede"
    ).fetchall()
    return [f["sede"] for f in filas]


def listar_sedes_historicas(con: sqlite3.Connection) -> list[str]:
    return [f[0] for f in con.execute(
        "SELECT sede FROM equipos WHERE COALESCE(sede,'')<>''"
        " UNION SELECT sede FROM cobertura_inventario ORDER BY sede")]


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
    file_hash: str | None = None,
) -> sqlite3.Row:
    con.execute(
        "INSERT INTO jobs(id, owner_id, owner_email, filename, stored_name,"
        " size, pages, status, message, created_at, client_ip, file_hash)"
        " VALUES(?,?,?,?,?,?,?, 'queued', 'En cola', ?, ?, ?)",
        (job_id, owner_id, owner_email, filename, stored_name, size, pages,
         ahora(), client_ip, file_hash),
    )
    con.commit()
    return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def job_por_hash(con: sqlite3.Connection, owner_id: int, file_hash: str):
    return con.execute(
        "SELECT id, filename, created_at, status FROM jobs WHERE owner_id=? AND file_hash=?"
        " ORDER BY created_at DESC LIMIT 1", (owner_id, file_hash),
    ).fetchone()


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
    if cur.rowcount:
        con.execute("DELETE FROM cobertura_escaneos WHERE job_id=?", (job_id,))
        con.execute("DELETE FROM cobertura_lecturas WHERE job_id=?", (job_id,))
        con.execute("DELETE FROM job_pages WHERE job_id=?", (job_id,))
        recalcular_duplicados(con)
    con.commit()
    return cur.rowcount > 0


def borrar_job_por_id(con: sqlite3.Connection, job_id: str) -> None:
    """Retención: elimina archivos/metadatos del job, conserva la cobertura."""
    registrar_cobertura_job(con, job_id)
    con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    con.execute("DELETE FROM job_pages WHERE job_id=?", (job_id,))
    con.commit()


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


def periodo_de(fecha_iso: str | None = None) -> str:
    """Periodo YYYY-MM en hora local; las fechas en BD están en UTC."""
    if fecha_iso:
        try:
            fecha = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
        except ValueError:
            fecha = datetime.now(timezone.utc)
    else:
        fecha = datetime.now(timezone.utc)
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return fecha.astimezone(_TZ_CUOTA).strftime("%Y-%m")


def asegurar_inventario_periodo(con: sqlite3.Connection, periodo: str, *, refrescar: bool = False) -> None:
    """El mes abierto refleja el inventario; los meses anteriores quedan fijos."""
    existe = con.execute("SELECT 1 FROM cobertura_periodos WHERE periodo=?", (periodo,)).fetchone()
    if existe and (periodo != periodo_de() or not refrescar):
        return
    if not existe:
        con.execute("INSERT INTO cobertura_periodos VALUES(?,?,?)",
                    (periodo, ahora(), "registrado" if periodo == periodo_de() else "reconstruido"))
    con.execute("DELETE FROM cobertura_inventario WHERE periodo=?", (periodo,))
    for fila in con.execute("SELECT sn,sede,marca,modelo,zona,domicilio,ubicacion,piso,estatus,contador_inicial"
                            " FROM equipos WHERE COALESCE(sede,'')<>''").fetchall():
        con.execute("INSERT INTO cobertura_inventario VALUES(?,?,?,?)",
                    (periodo, fila["sn"], fila["sede"], json.dumps(dict(fila), ensure_ascii=False)))


def recalcular_duplicados(con: sqlite3.Connection) -> None:
    con.execute("UPDATE cobertura_escaneos SET duplicado=0, duplicado_de=NULL")
    primeros = {}
    for f in con.execute("SELECT id, periodo, sede, sn FROM cobertura_escaneos WHERE excluido=0"
                         " ORDER BY escaneado_en, id").fetchall():
        clave = (f["periodo"], f["sede"], normalizar_serial(f["sn"]))
        if clave in primeros:
            con.execute("UPDATE cobertura_escaneos SET duplicado=1, duplicado_de=? WHERE id=?",
                        (primeros[clave], f["id"]))
        else:
            primeros[clave] = f["id"]


def reconciliar_cobertura(con: sqlite3.Connection, periodo: str) -> None:
    asegurar_inventario_periodo(con, periodo, refrescar=True)
    indice = {}
    for f in con.execute("SELECT sn, sede FROM cobertura_inventario WHERE periodo=?", (periodo,)):
        indice.setdefault(normalizar_serial(f["sn"]), []).append(f)
    resets = {f["sede"]: f["reset_en"] for f in con.execute(
        "SELECT sede, reset_en FROM cobertura_resets WHERE periodo=?", (periodo,))}
    roles = {
        int(f["id"]): f["role"]
        for f in con.execute("SELECT id, role FROM users")
    }
    sedes_por_usuario: dict[int, set[str]] = {}
    for f in con.execute("SELECT user_id, sede FROM user_sedes"):
        sedes_por_usuario.setdefault(int(f["user_id"]), set()).add(f["sede"])

    def puede_cubrir(owner_id, sede: str) -> bool:
        if owner_id is None:
            return False
        if roles.get(int(owner_id)) == "admin":
            return True
        return bool(sede) and sede in sedes_por_usuario.get(int(owner_id), set())

    existentes = {}
    for f in con.execute("SELECT * FROM cobertura_escaneos WHERE periodo=?", (periodo,)):
        existentes.setdefault((f["job_id"], normalizar_serial(f["sn"])), f)
    for f in con.execute("SELECT * FROM cobertura_lecturas WHERE periodo=? ORDER BY escaneado_en", (periodo,)).fetchall():
        candidatos = indice.get(normalizar_serial(f["sn"]), [])
        # Una clave OCR ambigua nunca decide qué equipo se ha escaneado.
        if len(candidatos) != 1:
            continue
        equipo = candidatos[0]
        # Un escaneo nunca debe acreditar cobertura de una sede que el usuario
        # no tiene asignada. El resumen del job le avisa de esa lectura.
        if not puede_cubrir(f["owner_id"], equipo["sede"]):
            continue
        if f["escaneado_en"] <= resets.get(equipo["sede"], ""):
            continue
        clave = (f["job_id"], normalizar_serial(f["sn"]))
        previo = existentes.get(clave)
        if previo:
            # Conserva exclusión, motivo e identidad al corregir la sede del mes abierto.
            if periodo == periodo_de():
                con.execute("UPDATE cobertura_escaneos SET sede=?, sn=? WHERE id=?",
                            (equipo["sede"], equipo["sn"], previo["id"]))
            continue
        con.execute(
            "INSERT OR IGNORE INTO cobertura_escaneos"
            "(periodo,sede,sn,job_id,owner_id,escaneado_en,documento) VALUES(?,?,?,?,?,?,?)",
            (periodo, equipo["sede"], equipo["sn"], f["job_id"], f["owner_id"], f["escaneado_en"], f["documento"]),
        )
        existentes[clave] = con.execute(
            "SELECT * FROM cobertura_escaneos WHERE periodo=? AND sede=? AND sn=? AND job_id=?",
            (periodo, equipo["sede"], equipo["sn"], f["job_id"]),
        ).fetchone()
    recalcular_duplicados(con)


def _guardar_lecturas_job(con: sqlite3.Connection, job) -> None:
    if job["status"] != "done":
        return
    periodo = periodo_de(job["finished_at"] or job["created_at"])
    for f in con.execute("SELECT DISTINCT sn FROM job_pages WHERE job_id=? AND COALESCE(sn,'')<>''"
                         " AND COALESCE(estado,'')<>'DESCARTADA'", (job["id"],)).fetchall():
        con.execute("INSERT OR IGNORE INTO cobertura_lecturas VALUES(?,?,?,?,?,?)",
                    (job["id"], f["sn"], periodo, job["owner_id"],
                     job["finished_at"] or job["created_at"], job["filename"]))


def registrar_cobertura_job(con: sqlite3.Connection, job_id: str) -> int:
    job = job_por_id(con, job_id)
    if not job or job["status"] != "done":
        return 0
    antes = con.execute("SELECT COUNT(*) FROM cobertura_escaneos").fetchone()[0]
    _guardar_lecturas_job(con, job)
    reconciliar_cobertura(con, periodo_de(job["finished_at"] or job["created_at"]))
    con.commit()
    return con.execute("SELECT COUNT(*) FROM cobertura_escaneos").fetchone()[0] - antes


def backfill_cobertura(con: sqlite3.Connection, periodo: str) -> None:
    # Idempotente. Permite recuperar registros tras fallos y altas de inventario,
    # incluso si una consulta anterior ya marcó el mes como rellenado.
    for job in con.execute("SELECT * FROM jobs WHERE status='done' ORDER BY finished_at").fetchall():
        if periodo_de(job["finished_at"] or job["created_at"]) == periodo:
            _guardar_lecturas_job(con, job)
    reconciliar_cobertura(con, periodo)
    con.execute("INSERT OR REPLACE INTO cobertura_meta VALUES(?,?)", (periodo, ahora()))
    con.commit()


def actualizar_cobertura_inventario(con: sqlite3.Connection) -> None:
    # Sólo se reconcilia el mes abierto. Las sedes y equipos de meses cerrados
    # proceden de su copia histórica, nunca del inventario actual.
    backfill_cobertura(con, periodo_de())


def cobertura_sede(con: sqlite3.Connection, sede: str, periodo: str) -> dict[str, Any]:
    asegurar_inventario_periodo(con, periodo)
    meta = con.execute("SELECT * FROM cobertura_periodos WHERE periodo=?", (periodo,)).fetchone()
    esperados = [json.loads(f["datos"]) for f in con.execute(
        "SELECT datos FROM cobertura_inventario WHERE periodo=? AND sede=? ORDER BY sn", (periodo, sede))]
    escaneos = con.execute(
        "SELECT c.*, COALESCE(u.display_name,u.name,u.email) AS usuario,"
        " COALESCE(j.filename,c.documento) AS documento, (j.id IS NULL) AS archivo_eliminado"
        " FROM cobertura_escaneos c"
        " LEFT JOIN users u ON u.id=c.owner_id"
        " LEFT JOIN jobs j ON j.id=c.job_id"
        " WHERE c.periodo=? AND c.sede=? AND c.excluido=0"
        " ORDER BY c.escaneado_en DESC",
        (periodo, sede),
    ).fetchall()
    escaneados = {f["sn"] for f in escaneos} & {f["sn"] for f in esperados}
    equipos = []
    for f in esperados:
        fila = dict(f)
        fila["escaneado"] = fila["sn"] in escaneados
        equipos.append(fila)
    faltantes = [f for f in equipos if not f["escaneado"]]
    duplicados = [dict(f) for f in escaneos if f["duplicado"]]
    return {
        "sede": sede,
        "periodo": periodo,
        "cerrado": periodo < periodo_de(),
        "inventario_origen": meta["origen"],
        "inventario_capturado_en": meta["capturado_en"],
        "esperados": len(esperados),
        "escaneados": len(escaneados),
        "faltantes": faltantes,
        "equipos": equipos,
        "duplicados": duplicados,
        "detalle_escaneos": [dict(f) for f in escaneos],
    }


def excluir_cobertura(con: sqlite3.Connection, cid: int, motivo: str) -> bool:
    cur = con.execute(
        "UPDATE cobertura_escaneos SET excluido=1, motivo_exclusion=?"
        " WHERE id=?",
        ((motivo or "Corrección administrativa")[:300], cid),
    )
    recalcular_duplicados(con)
    con.commit()
    return cur.rowcount > 0


def reiniciar_cobertura(con: sqlite3.Connection, periodo: str, sede: str) -> int:
    cur = con.execute(
        "DELETE FROM cobertura_escaneos WHERE periodo=? AND sede=?",
        (periodo, sede),
    )
    con.execute(
        "INSERT OR REPLACE INTO cobertura_resets(periodo, sede, reset_en)"
        " VALUES(?,?,?)", (periodo, sede, ahora()),
    )
    con.commit()
    return int(cur.rowcount or 0)


def cobertura_para_usuario(
    con: sqlite3.Connection, uid: int, periodo: str, sede: str | None = None
) -> dict[str, Any]:
    sedes = sedes_usuario(con, uid)
    if sede:
        if sede not in sedes:
            raise PermissionError("Sede no asignada al usuario.")
        sedes = [sede]
    if not sedes:
        return {"periodo": periodo, "sedes": [], "faltantes": 0}
    backfill_cobertura(con, periodo)
    datos = [cobertura_sede(con, s, periodo) for s in sedes]
    return {
        "periodo": periodo,
        "sedes": datos,
        "esperados": sum(d["esperados"] for d in datos),
        "escaneados": sum(d["escaneados"] for d in datos),
        "faltantes": sum(len(d["faltantes"]) for d in datos),
        "duplicados": sum(len(d["duplicados"]) for d in datos),
    }
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


def _ubicaciones_existentes(
    con: sqlite3.Connection, sede: str = ""
) -> list[str]:
    filas = con.execute(
        "SELECT DISTINCT ubicacion FROM equipos"
        " WHERE ubicacion IS NOT NULL AND ubicacion<>''"
        " AND (?='' OR sede=?)"
        " ORDER BY ubicacion COLLATE NOCASE",
        (sede or "", sede or ""),
    ).fetchall()
    return [f["ubicacion"] for f in filas]


def _parecido_ubicacion(texto: str, existente: str) -> float:
    a = normalizar_ubicacion(texto)
    b = normalizar_ubicacion(existente)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if tokens_a and tokens_b:
        ratio = max(ratio, len(tokens_a & tokens_b) / len(tokens_a | tokens_b))
    return ratio


def ubicaciones_similares(
    con: sqlite3.Connection, ubicacion: str, sede: str = "",
    umbral: float = 0.60,
) -> list[str]:
    """Sugerencias cercanas; no se usan para fusionar ni guardar nada."""
    nombre = re.sub(r"\s+", " ", str(ubicacion or "")).strip()
    if not nombre:
        return []
    out = []
    for existente in _ubicaciones_existentes(con, sede):
        if existente.strip().casefold() == nombre.casefold():
            continue
        if _parecido_ubicacion(nombre, existente) >= umbral:
            out.append(existente)
    return out[:8]


def ubicacion_exacta(
    con: sqlite3.Connection, ubicacion: str, sede: str = ""
) -> bool:
    nombre = re.sub(r"\s+", " ", str(ubicacion or "")).strip().casefold()
    if not nombre:
        return False
    return any(
        n.strip().casefold() == nombre
        for n in _ubicaciones_existentes(con, sede)
    )


def sugerencias_ubicacion(
    con: sqlite3.Connection, q: str = "", sede: str = ""
) -> list[str]:
    q = (q or "").strip()
    existentes = _ubicaciones_existentes(con, sede)
    if not q:
        return existentes[:30]
    qlow = q.casefold()
    exactas = [n for n in existentes if n.strip().casefold() == qlow]
    cercanas = sorted(
        (n for n in existentes if n.strip().casefold() != qlow),
        key=lambda n: _parecido_ubicacion(q, n),
        reverse=True,
    )
    cercanas = [n for n in cercanas if _parecido_ubicacion(q, n) >= 0.45]
    return (exactas + cercanas)[:30]


class SerialAmbiguo(ValueError):
    pass


class SerialInvalido(ValueError):
    pass


def buscar_equipo(con: sqlite3.Connection, sn: str):
    original = (sn or "").strip().upper()
    exacto = con.execute("SELECT * FROM equipos WHERE sn=?", (original,)).fetchone()
    if exacto:
        return exacto
    filas = con.execute("SELECT * FROM equipos WHERE sn_norm=?", (normalizar_serial(sn),)).fetchall()
    if len(filas) > 1:
        raise SerialAmbiguo("Hay varias series parecidas. Usa la serie exacta del inventario.")
    return filas[0] if filas else None


def upsert_equipo(con: sqlite3.Connection, sn: str, *, reconciliar: bool = True, **campos: Any) -> tuple[bool, str]:
    sn = (sn or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9 _-]{0,255}", sn):
        raise SerialInvalido("La serie debe contener letras, números, espacios o guiones (máximo 256 caracteres).")
    # Evita duplicados lógicos Mx3.../MX3.../MX3...O...: reutiliza el serial
    # existente en lugar de insertar una segunda fila.
    existente = buscar_equipo(con, sn)
    if existente:
        sn = existente["sn"]
    existia = con.execute("SELECT 1 FROM equipos WHERE sn=?", (sn,)).fetchone() is not None
    if "ubicacion" in campos:
        campos["ubicacion"] = re.sub(r"\s+", " ", str(campos["ubicacion"] or "")).strip()
    cols = ["sn", "sn_norm", "ubicacion_norm"] + [c for c in CAMPOS_EQUIPO if c in campos]
    ubicacion = campos.get("ubicacion", existente["ubicacion"] if existente else "")
    vals = [sn, normalizar_serial(sn), normalizar_ubicacion(ubicacion)]
    vals += [("" if campos.get(c) is None else str(campos.get(c))) for c in cols[3:]]
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
    sufijo = (f" ON CONFLICT(sn) DO UPDATE SET {sets},"
              " actualizado_en=datetime('now')" if sets else "")
    con.execute(
        f"INSERT INTO equipos({','.join(cols)}, actualizado_en)"
        f" VALUES({','.join('?' * len(cols))}, datetime('now'))" + sufijo,
        vals,
    )
    if reconciliar:
        actualizar_cobertura_inventario(con)
    con.commit()
    return (not existia), sn


def borrar_equipo(con: sqlite3.Connection, sn: str) -> bool:
    cur = con.execute("DELETE FROM equipos WHERE sn=?", ((sn or "").strip().upper(),))
    actualizar_cobertura_inventario(con)
    con.commit()
    return cur.rowcount > 0


def borrar_equipos(con: sqlite3.Connection, **filtros) -> int:
    """Borra todo lo que coincida. Sin filtros equivale a vaciar el inventario."""
    where, params = _filtros_equipos(**filtros)
    cur = con.execute(f"DELETE FROM equipos{where}", params)
    actualizar_cobertura_inventario(con)
    con.commit()
    return int(cur.rowcount or 0)


def _filtros_equipos(q=None, sede=None, marca=None, estatus=None, zona=None, piso=None):
    where, params = [], []
    if q:
        where.append(
            "(sn LIKE ? OR marca LIKE ? OR modelo LIKE ? OR sede LIKE ?"
            " OR ubicacion LIKE ? OR piso LIKE ?)"
        )
        params += [f"%{q}%"] * 6
    for campo, valor in (("sede", sede), ("marca", marca),
                         ("estatus", estatus), ("zona", zona), ("piso", piso)):
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
    descendente = str(orden).startswith("-")
    campo = str(orden).lstrip("-")
    campo = campo if campo in ("sn", "marca", "modelo", "sede", "zona",
                               "ubicacion", "piso", "estatus") else "sn"
    direccion = "DESC" if descendente else "ASC"
    filas = con.execute(
        f"SELECT sn, marca, modelo, tipo, sede, zona, domicilio, ubicacion,"
        f" piso, estatus, contador_inicial, origen_hoja, actualizado_en"
        f" FROM equipos{where}"
        f" ORDER BY {campo} {direccion} LIMIT ? OFFSET ?",
        params + [max(1, min(500, int(limite))), max(0, int(offset))],
    ).fetchall()
    return [dict(f) for f in filas]


def valores_equipos(con: sqlite3.Connection, columna: str) -> list[str]:
    columna = columna if columna in ("sede", "marca", "estatus", "zona", "piso") else "sede"
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
