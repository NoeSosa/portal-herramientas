#!/home/node/.openclaw/workspace/venv/bin/python
"""
panel_db.py — Estado compartido del panel (SQLite).
====================================================
Única fuente de verdad entre el worker (escribe) y el servidor (lee y sirve SSE).
El pipeline NUNCA escribe aquí: solo escribe su jsonl de progreso, y el worker lo
traduce a filas de `eventos` + columnas de progreso de `cola`.

Tablas:
  cola     → 1 fila por PDF a procesar (estado, prioridad, timestamps, hash, progreso)
  eventos  → log estructurado (1 fila por evento/página) → lo consume el SSE
  control  → claves simples (pausa global, pid del worker, …)
  equipos / equipos_historial / sedes
           → RESERVADAS para el futuro inventario en BD (propuesta §3).
             Se crean vacías a propósito: NO hay código que las use todavía.

✅ VALIDADO: 16/oct/2026 (Ches) — esquema creado idempotente; cola/eventos
   alimentan la UI y el SSE (probado con job real --dry).
"""
import json
import os
import re
import sqlite3
import time
from datetime import datetime

ESTADOS_ACTIVOS = ("pendiente", "en_proceso")
ESTADOS_FINALES = ("ok", "error", "cancelado")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS cola (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT,
    nombre         TEXT NOT NULL,              -- nombre del PDF en la entrada
    remoto         TEXT,                       -- ruta remota (gdrive:Entrada_Separador/x.pdf)
    hash           TEXT,                       -- hash/quickxor del PDF (dedupe)
    tam            INTEGER,
    prioridad      INTEGER NOT NULL DEFAULT 100,
    dry            INTEGER NOT NULL DEFAULT 1, -- 1 = --dry (simulación pura)
    sin_correo     INTEGER NOT NULL DEFAULT 1, -- 1 = --sin-correo (sin avisos por mail)
    estado         TEXT NOT NULL DEFAULT 'pendiente',
    fase           TEXT DEFAULT '',            -- preparando|esperando_lock|ocr|fin
    intentos       INTEGER NOT NULL DEFAULT 0,
    pag_total      INTEGER DEFAULT 0,
    pag_hechas     INTEGER DEFAULT 0,
    vel_pag_min    REAL DEFAULT 0,
    eta_s          REAL DEFAULT 0,
    mensaje        TEXT DEFAULT '',
    error          TEXT DEFAULT '',
    pid            INTEGER,
    creado_en      TEXT,
    encolado_en    TEXT,
    iniciado_en    TEXT,
    terminado_en   TEXT,
    latido_en      TEXT
);
CREATE INDEX IF NOT EXISTS idx_cola_estado ON cola(estado, prioridad, id);

CREATE TABLE IF NOT EXISTS eventos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL,
    job_id       TEXT,
    cola_id      INTEGER,
    tipo         TEXT,        -- job_inicio|doc_inicio|pagina|doc_fin|job_fin|log|estado
    nivel        TEXT,        -- info|ok|aviso|error
    pdf          TEXT,
    pag          INTEGER,
    pag_total    INTEGER,
    sn           TEXT,
    sede         TEXT,
    marca        TEXT,
    estado       TEXT,
    ms_pagina    REAL,
    vel_pag_min  REAL,
    eta_doc_s    REAL,
    eta_global_s REAL,
    detalle      TEXT,
    texto        TEXT,        -- 16/oct/2026: lectura cruda del OCR de esa hoja (en vivo)
    banda        INTEGER,     -- 17/oct/2026: franja horizontal (lectura secuencial)
    bandas       INTEGER,
    y0           REAL,
    y1           REAL,
    parcial      INTEGER,     -- 1 = se cortó la lectura al encontrar el SN
    cajas        TEXT,        -- 18/oct/2026: cajas detectadas por el OCR (JSON, solo visor)
    pid_worker   INTEGER,     -- 18/oct/2026: proceso OCR que leyó la hoja
    slot_worker  INTEGER      -- 18/oct/2026: ranura del pool (0,1,…) — estable
);
CREATE INDEX IF NOT EXISTS idx_eventos_job ON eventos(job_id, id);

CREATE TABLE IF NOT EXISTS control (
    clave TEXT PRIMARY KEY,
    valor TEXT,
    ts    TEXT
);

-- ---------------------------------------------------------------------------
-- RESERVADO (futuro inventario en BD, propuesta §3). Sin uso por ahora.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equipos (
    sn TEXT PRIMARY KEY, marca TEXT, modelo TEXT, tipo TEXT, sede TEXT, zona TEXT,
    domicilio TEXT, ip_fisica TEXT, ip_hp TEXT, ip_ekm TEXT, perfil TEXT,
    estatus TEXT, contador_inicial INTEGER, fecha_alta TEXT, fecha_baja TEXT,
    sustituto_sn TEXT, origen_hoja TEXT, actualizado_en TEXT
);
CREATE TABLE IF NOT EXISTS equipos_historial (
    id INTEGER PRIMARY KEY AUTOINCREMENT, sn TEXT, campo TEXT, valor_antes TEXT,
    valor_despues TEXT, usuario TEXT, fecha TEXT
);
CREATE TABLE IF NOT EXISTS sedes (
    nombre TEXT PRIMARY KEY, correos TEXT, huellas TEXT, activa INTEGER DEFAULT 1
);
"""


# ---------------------------------------------------------------- conexión
def conectar(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=8000")
    return con


def inicializar(db_path):
    con = conectar(db_path)
    con.executescript(ESQUEMA)
    _migrar(con)
    con.commit()
    return con


def _migrar(con):
    """Añade columnas nuevas a bases creadas antes (idempotente)."""
    cols = {f["name"] for f in con.execute("PRAGMA table_info(cola)")}
    if "sin_correo" not in cols:      # 16/oct/2026: jobs sin aviso por correo
        con.execute("ALTER TABLE cola ADD COLUMN sin_correo INTEGER DEFAULT 1")
    cols_ev = {f["name"] for f in con.execute("PRAGMA table_info(eventos)")}
    if "texto" not in cols_ev:        # 16/oct/2026: lectura del OCR por hoja (en vivo)
        con.execute("ALTER TABLE eventos ADD COLUMN texto TEXT")
    # 17/oct/2026: lectura secuencial por franjas (banda, posición y si se cortó)
    for col, tipo in (("banda", "INTEGER"), ("bandas", "INTEGER"),
                      ("y0", "REAL"), ("y1", "REAL"), ("parcial", "INTEGER")):
        if col not in cols_ev:
            con.execute(f"ALTER TABLE eventos ADD COLUMN {col} {tipo}")
    # 18/oct/2026: cajas que detectó el OCR por hoja (JSON; solo para animar el visor)
    if "cajas" not in cols_ev:
        con.execute("ALTER TABLE eventos ADD COLUMN cajas TEXT")
    # 18/oct/2026: proceso que leyó la hoja (identifica a cada worker del pool)
    if "pid_worker" not in cols_ev:
        con.execute("ALTER TABLE eventos ADD COLUMN pid_worker INTEGER")
    # 18/oct/2026: ranura del worker en el pool (0, 1, …): identidad estable
    if "slot_worker" not in cols_ev:
        con.execute("ALTER TABLE eventos ADD COLUMN slot_worker INTEGER")


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- control
def control_set(con, clave, valor):
    con.execute("INSERT INTO control(clave, valor, ts) VALUES(?,?,?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, ts=excluded.ts",
                (clave, str(valor), ahora()))
    con.commit()


def control_get(con, clave, default=None):
    r = con.execute("SELECT valor FROM control WHERE clave=?", (clave,)).fetchone()
    return r["valor"] if r else default


def pausa_global(con):
    return control_get(con, "pausa_global", "0") == "1"


# ---------------------------------------------------------------- cola
def encolar(con, nombre, remoto="", tam=None, md5=None, prioridad=100,
            dry=1, mensaje="Encolado desde el panel", sin_correo=1):
    """Encola un PDF. No duplica si ya hay un job activo con el mismo nombre."""
    dup = con.execute("SELECT id, estado FROM cola WHERE nombre=? AND estado IN "
                      "('pendiente','en_proceso') ORDER BY id DESC LIMIT 1",
                      (nombre,)).fetchone()
    if dup:
        return dup["id"], False
    cur = con.execute(
        "INSERT INTO cola(nombre, remoto, tam, hash, prioridad, dry, sin_correo,"
        " estado, fase, creado_en, encolado_en, mensaje)"
        " VALUES(?,?,?,?,?,?,?, 'pendiente','',?,?,?)",
        (nombre, remoto, tam, md5, prioridad, dry, int(sin_correo), ahora(), ahora(), mensaje))
    con.commit()
    return cur.lastrowid, True


def cola(con, limite=200):
    filas = con.execute(
        "SELECT * FROM cola ORDER BY CASE estado WHEN 'en_proceso' THEN 0 "
        "WHEN 'pendiente' THEN 1 WHEN 'pausado' THEN 2 ELSE 3 END, "
        "prioridad ASC, id ASC LIMIT ?", (limite,)).fetchall()
    return [dict(f) for f in filas]


def job(con, cola_id):
    f = con.execute("SELECT * FROM cola WHERE id=?", (cola_id,)).fetchone()
    return dict(f) if f else None


def siguiente_pendiente(con):
    f = con.execute("SELECT * FROM cola WHERE estado='pendiente' "
                    "ORDER BY prioridad ASC, id ASC LIMIT 1").fetchone()
    return dict(f) if f else None


def job_en_proceso(con):
    f = con.execute("SELECT * FROM cola WHERE estado='en_proceso' "
                    "ORDER BY id DESC LIMIT 1").fetchone()
    return dict(f) if f else None


def marcar(con, cola_id, estado=None, **campos):
    sets, vals = [], []
    if estado:
        sets.append("estado=?")
        vals.append(estado)
    for k, v in campos.items():
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    vals.append(cola_id)
    con.execute(f"UPDATE cola SET {', '.join(sets)} WHERE id=?", vals)
    con.commit()


def latido(con, cola_id, pid=None):
    campos = {"latido_en": ahora()}
    if pid:
        campos["pid"] = pid
    marcar(con, cola_id, **campos)


def reap_stale(con, stale_s=120):
    """Jobs 'en_proceso' sin latido por > stale_s → 'pendiente' (worker muerto)."""
    limite = time.time() - stale_s
    recuperados = []
    for f in con.execute("SELECT id, job_id, latido_en, estado FROM cola "
                         "WHERE estado='en_proceso'").fetchall():
        t = None
        try:
            t = datetime.strptime(f["latido_en"], "%Y-%m-%d %H:%M:%S").timestamp()
        except (TypeError, ValueError):
            t = 0
        if t < limite:
            marcar(con, f["id"], "pendiente", fase="",
                   mensaje="retomado tras reinicio del worker (sin latido)")
            recuperados.append(f["id"])
    return recuperados


# ---------------------------------------------------------------- eventos
_CAMPOS_EVENTO = ("pdf", "pag", "pag_total", "sn", "sede", "marca", "estado",
                  "ms_pagina", "vel_pag_min", "eta_doc_s", "eta_global_s")


def evento(con, job_id=None, cola_id=None, tipo="log", nivel="info",
           detalle="", **campos):
    cols = ["ts", "job_id", "cola_id", "tipo", "nivel", "detalle"] + list(_CAMPOS_EVENTO)
    vals = [time.time(), job_id, cola_id, tipo, nivel, detalle]
    vals += [campos.get(c) for c in _CAMPOS_EVENTO]
    con.execute(f"INSERT INTO eventos({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
    con.commit()


def eventos_desde(con, ultimo_id=0, limite=400, job_id=None):
    if job_id:
        filas = con.execute("SELECT * FROM eventos WHERE id>? AND job_id=? "
                            "ORDER BY id ASC LIMIT ?", (ultimo_id, job_id, limite)).fetchall()
    else:
        filas = con.execute("SELECT * FROM eventos WHERE id>? "
                            "ORDER BY id ASC LIMIT ?", (ultimo_id, limite)).fetchall()
    return [dict(f) for f in filas]


def ultimo_evento_id(con):
    r = con.execute("SELECT COALESCE(MAX(id),0) AS m FROM eventos").fetchone()
    return r["m"]


def eventos_recientes(con, n=60, job_id=None):
    if job_id:
        filas = con.execute("SELECT * FROM eventos WHERE job_id=? ORDER BY id DESC LIMIT ?",
                            (job_id, n)).fetchall()
    else:
        filas = con.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [dict(f) for f in reversed(filas)]


def limpiar_eventos(con, dias=7):
    """Poda eventos viejos (los jsonl de progreso se conservan en disco)."""
    con.execute("DELETE FROM eventos WHERE ts < ?", (time.time() - dias * 86400,))
    con.commit()


# ---------------------------------------------------------------- jsonl de progreso
def ruta_progreso(progress_dir, job_id):
    return os.path.join(progress_dir, f"{job_id}.jsonl")


def leer_jsonl_nuevas(ruta, offset=0):
    """Lee líneas nuevas (append-only). Devuelve (nuevo_offset, [dicts]).

    Si la última línea está incompleta se devuelve el offset ANTES de ella para
    releerla después (escritura concurrente del pipeline).
    """
    if not os.path.isfile(ruta):
        return offset, []
    eventos, nuevo = [], offset
    with open(ruta, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        while True:
            linea = f.readline()
            if not linea:
                break
            if not linea.endswith("\n"):
                break  # línea a medias: no avanzar el offset
            nuevo = f.tell()
            linea = linea.strip()
            if not linea:
                continue
            try:
                eventos.append(json.loads(linea))
            except json.JSONDecodeError:
                continue
    return nuevo, eventos


def ingerir_progreso(con, progress_dir, job_id, cola_id, offset=0):
    """Traduce el jsonl del pipeline a filas de `eventos` y actualiza `cola`.

    Devuelve (nuevo_offset, n_eventos_nuevos).
    """
    ruta = ruta_progreso(progress_dir, job_id)
    nuevo, eventos = leer_jsonl_nuevas(ruta, offset)
    for ev in eventos:
        tipo = ev.get("tipo") or "pagina"
        campos = {c: ev.get(c) for c in _CAMPOS_EVENTO}
        if ev.get("estado") in ("OK",) or tipo in ("doc_fin", "job_fin"):
            nivel = "ok"
        elif ev.get("estado") in ("POR_VERIFICAR", "A_REVISAR", "DESCARTADA"):
            nivel = "aviso"
        elif tipo == "error":
            nivel = "error"
        else:
            nivel = "info"
        con.execute(
            "INSERT INTO eventos(ts, job_id, cola_id, tipo, nivel, detalle, " +
            ",".join(_CAMPOS_EVENTO) + ") VALUES(" +
            ",".join("?" * (6 + len(_CAMPOS_EVENTO))) + ")",
            [ev.get("ts") or time.time(), job_id, cola_id, tipo, nivel,
             json.dumps({k: v for k, v in ev.items() if k not in _CAMPOS_EVENTO},
                        ensure_ascii=False)[:800]]
            + [campos[c] for c in _CAMPOS_EVENTO])
        if tipo == "pagina":
            # Progreso del JOB (no del documento): nº de páginas clasificadas y total
            # del job, tomado del job_inicio. Antes se usaba ev['pag'] y un job con
            # varios PDFs acababa mostrando "100/21".
            hechas = con.execute(
                "SELECT COUNT(*) FROM eventos WHERE job_id=? AND tipo='pagina'",
                (job_id,)).fetchone()[0]
            total_job = con.execute(
                "SELECT MAX(pag_total) FROM eventos WHERE job_id=? AND tipo='job_inicio'",
                (job_id,)).fetchone()[0]
            con.execute("UPDATE cola SET pag_hechas=?, pag_total=COALESCE(?, pag_total), "
                        "vel_pag_min=?, eta_s=?, latido_en=? WHERE id=?",
                        (hechas, total_job or None, ev.get("vel_pag_min") or 0,
                         ev.get("eta_global_s") or 0, ahora(), cola_id))
        elif tipo == "doc_inicio":
            con.execute("UPDATE cola SET fase='ocr', mensaje=? WHERE id=?",
                        (f"OCR {ev.get('pdf')}", cola_id))
    if eventos:
        con.commit()
    return nuevo, len(eventos)


def resumen_workers(con, job_id, limite=14):
    """Agregado por worker OCR del ÚLTIMO PDF del job + últimas hojas leídas.

    Un job puede llevar varios PDFs. Mezclar sus páginas en un mismo contador
    confunde (las páginas se repiten: la 1 de cada documento), así que el panel
    muestra el documento que se está leyendo: se filtra por el último `pdf` con
    eventos OCR. El identificador estable del worker es el slot (0,1,…).
    """
    vacio = {"workers": [], "recientes": [], "pdf": None}
    if not job_id:
        return vacio
    fila_pdf = con.execute(
        "SELECT pdf FROM eventos WHERE job_id=? AND tipo='ocr_pagina'"
        " AND COALESCE(pdf,'')<>'' ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    pdf = fila_pdf["pdf"] if fila_pdf else None
    filtro_pdf = " AND pdf=?" if pdf else ""
    params_pdf = (pdf,) if pdf else ()

    # 1º los workers reales del pool (slot 0,1,… = los que corren en paralelo)
    filas = con.execute(
        "SELECT slot_worker AS pid, GROUP_CONCAT(DISTINCT detalle) AS rangos,"
        " COUNT(DISTINCT pag) AS n, AVG(COALESCE(ms_pagina,0)) AS ms_medio,"
        " SUM(COALESCE(ms_pagina,0)) AS ms_total, MAX(ts) AS ultimo_ts,"
        " MAX(pag) AS ultima_pag, COUNT(DISTINCT pag) AS pags"
        " FROM eventos WHERE job_id=? AND tipo='ocr_pagina' AND slot_worker IS NOT NULL"
        + filtro_pdf +
        " GROUP BY slot_worker ORDER BY slot_worker",
        (job_id, *params_pdf)).fetchall()
    hay_slots = bool(filas)
    if not filas:
        # Corridas anteriores al 18/oct/2026 (sin slot): se agrupa por proceso.
        filas = con.execute(
            "SELECT COALESCE(pid_worker, 0) AS pid, GROUP_CONCAT(DISTINCT detalle) AS rangos,"
            " COUNT(DISTINCT pag) AS n, AVG(COALESCE(ms_pagina,0)) AS ms_medio,"
            " SUM(COALESCE(ms_pagina,0)) AS ms_total, MAX(ts) AS ultimo_ts,"
            " MAX(pag) AS ultima_pag, COUNT(DISTINCT pag) AS pags"
            " FROM eventos WHERE job_id=? AND tipo='ocr_pagina'"
            + filtro_pdf +
            " GROUP BY COALESCE(pid_worker, 0) ORDER BY MIN(id)",
            (job_id, *params_pdf)).fetchall()
    workers = []
    for f in filas:
        rangos = [x.replace("worker ", "").strip() for x in (f["rangos"] or "").split(",") if x]
        rango = 0
        for r in rangos:
            m = re.match(r"(\d+)-(\d+)", r)
            if m:
                rango += max(0, int(m.group(2)) - int(m.group(1)))
        slot = f["pid"] if hay_slots else None
        workers.append({"worker": rangos[0] if rangos else "?", "rangos": rangos,
                        "slot": slot, "pdf": pdf,
                        "pid": f["pid"] or None, "rango": rango,
                        "paginas": f["pags"] or 0, "eventos": f["n"] or 0,
                        "ms_medio": round(f["ms_medio"] or 0, 1),
                        "ms_total": round(f["ms_total"] or 0, 1),
                        "ultima_pag": f["ultima_pag"], "ultimo_ts": f["ultimo_ts"]})
    recientes = [{"worker": (r["detalle"] or "").replace("worker ", "").strip() or "?",
                  "pid": r["pid_worker"], "pdf": r["pdf"], "pag": r["pag"], "ms": r["ms_pagina"],
                  "sn": r["sn"] or "", "ts": r["ts"]}
                 for r in con.execute(
                     "SELECT detalle, pid_worker, pdf, pag, ms_pagina, sn, ts FROM eventos"
                     " WHERE job_id=? AND tipo='ocr_pagina'" + filtro_pdf +
                     " ORDER BY id DESC LIMIT ?",
                     (job_id, *params_pdf, limite)).fetchall()]
    return {"workers": workers, "recientes": recientes, "pdf": pdf}


def hoja_ocr(con, pdf, pag, job_id=None):
    """Última lectura OCR guardada de una hoja (texto, cajas, ms, worker).

    Sirve para ver/animar hojas ya procesadas desde el visor maximizado.
    """
    sql = ("SELECT e.*, p.estado AS estado_pag, p.sede, p.marca FROM eventos e"
           " LEFT JOIN eventos p ON p.job_id=e.job_id AND p.tipo='pagina'"
           "  AND p.pdf=e.pdf AND p.pag=e.pag"
           " WHERE e.tipo='ocr_pagina' AND e.pdf=? AND e.pag=?")
    args = [pdf, int(pag)]
    if job_id:
        sql += " AND e.job_id=?"
        args.append(job_id)
    f = con.execute(sql + " ORDER BY e.id DESC LIMIT 1", args).fetchone()
    if not f:
        return None
    fila = dict(f)
    if not (fila.get("sede") and fila.get("marca") and fila.get("estado_pag")):
        # La clasificación pudo registrarse en otro intento del mismo PDF: se
        # completa lo que falte (sede, marca, estado) para los detalles.
        p = con.execute(
            "SELECT estado, sede, marca FROM eventos WHERE tipo='pagina' AND pdf=? AND pag=?"
            " AND COALESCE(estado,'')!='' ORDER BY id DESC LIMIT 1",
            (pdf, int(pag))).fetchone()
        if p:
            fila["estado_pag"] = fila.get("estado_pag") or p["estado"]
            fila["sede"] = fila.get("sede") or p["sede"]
            fila["marca"] = fila.get("marca") or p["marca"]
    return fila


def resumen_job(con, job_id):
    """Contadores para la UI: por estado de página y por sede/serial."""
    paginas = con.execute(
        "SELECT estado, sn, sede, marca FROM eventos WHERE job_id=? AND tipo='pagina'",
        (job_id,)).fetchall()
    por_estado, por_sede, seriales = {}, {}, {}
    for p in paginas:
        e = (p["estado"] or "?").upper()
        por_estado[e] = por_estado.get(e, 0) + 1
        s = p["sede"] or "(sin sede)"
        por_sede[s] = por_sede.get(s, 0) + 1
        sn = p["sn"]
        if sn:
            seriales[sn] = {"sn": sn, "sede": p["sede"], "marca": p["marca"],
                            "estado": e}
    return {"por_estado": por_estado, "por_sede": por_sede,
            "seriales": list(seriales.values())[:60]}


def nuevo_job_id(cola_id):
    """Identificador único por intento: job<cola>_<fecha>."""
    return f"job{cola_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


# ---------------------------------------------------------------- equipos (inventario)
# La tabla `equipos` era "reservada" para el futuro; desde el 16/oct/2026 el panel
# es su dueño: aquí se editan los seriales y el panel exporta un Excel que el
# pipeline usa de forma preferente (ver panel/inventario.py).
CAMPOS_EQUIPO = ("marca", "modelo", "tipo", "sede", "zona", "domicilio",
                 "ip_fisica", "ip_hp", "ip_ekm", "perfil", "estatus",
                 "contador_inicial", "sustituto_sn", "origen_hoja")


def upsert_equipo(con, sn, **campos):
    """Inserta o actualiza un equipo por serial. Devuelve (creado, sn)."""
    sn = (sn or "").strip().upper()
    if not sn:
        raise ValueError("serial vacío")
    existia = con.execute("SELECT 1 FROM equipos WHERE sn=?", (sn,)).fetchone() is not None
    cols = ["sn"] + [c for c in CAMPOS_EQUIPO if c in campos]
    vals = [sn] + [campos.get(c, "") for c in cols[1:]]
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
    sufijo = (f" ON CONFLICT(sn) DO UPDATE SET {sets}, actualizado_en=datetime('now')"
              if sets else "")
    sql = (f"INSERT INTO equipos({','.join(cols)}, actualizado_en)"
           f" VALUES({','.join('?' * len(cols))}, datetime('now'))" + sufijo)
    con.execute(sql, vals)
    con.commit()
    return (not existia), sn


def borrar_equipo(con, sn):
    sn = (sn or "").strip().upper()
    cur = con.execute("DELETE FROM equipos WHERE sn=?", (sn,))
    con.commit()
    return cur.rowcount > 0


def _filtros_equipos(q=None, sede=None, marca=None, estatus=None, zona=None):
    where, params = [], []
    if q:
        where.append("(sn LIKE ? OR marca LIKE ? OR modelo LIKE ? OR sede LIKE ?)")
        params += [f"%{q}%"] * 4
    for campo, valor in (("sede", sede), ("marca", marca), ("estatus", estatus), ("zona", zona)):
        if valor:
            where.append(f"{campo}=?")
            params.append(valor)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def contar_equipos(con, **filtros):
    where, params = _filtros_equipos(**filtros)
    return con.execute(f"SELECT COUNT(*) AS n FROM equipos{where}", params).fetchone()["n"]


def listar_equipos(con, limite=100, offset=0, orden="sn", **filtros):
    where, params = _filtros_equipos(**filtros)
    orden = orden if orden in ("sn", "marca", "modelo", "sede", "zona", "estatus") else "sn"
    filas = con.execute(
        f"SELECT sn, marca, modelo, sede, zona, domicilio, estatus, contador_inicial,"
        f" origen_hoja, actualizado_en FROM equipos{where} ORDER BY {orden}"
        f" LIMIT ? OFFSET ?", params + [int(limite), int(offset)]).fetchall()
    return [dict(f) for f in filas]


def valores_equipos(con, columna):
    columna = columna if columna in ("sede", "marca", "estatus", "zona") else "sede"
    filas = con.execute(
        f"SELECT DISTINCT {columna} AS v FROM equipos WHERE {columna}<>'' ORDER BY {columna}").fetchall()
    return [f["v"] for f in filas]


def job_ultimo(con):
    """Último job con job_id (en curso o terminado): la UI conserva así los
    contadores de la última corrida cuando no hay nada corriendo."""
    f = con.execute("SELECT * FROM cola WHERE job_id IS NOT NULL AND job_id != '' "
                    "ORDER BY id DESC LIMIT 1").fetchone()
    return dict(f) if f else None


def limpiar_nombre(n):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(n))[:80]
