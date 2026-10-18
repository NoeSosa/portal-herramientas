#!/home/node/.openclaw/workspace/venv/bin/python
"""
panel_config.py — Configuración y rutas del panel del separador de PDFs.
=======================================================================
Fuente única de verdad de rutas para `panel_server.py` y `panel_worker.py`
(ambos deben resolver la MISMA config; el server pasa `PANEL_CONFIG` al worker).

Config: `separador_pdfs/panel/config.json` (se crea con los valores por defecto
la primera vez). Se puede apuntar a otro archivo con la variable de entorno
`PANEL_CONFIG=/ruta/otra.json` — así las pruebas corren con su propia BD y su
propio rclone (sandbox local) sin tocar el Drive real.

Claves:
  host, puerto            → dónde escucha el servidor (por defecto 127.0.0.1)
  db                      → SQLite del panel (cola + eventos)
  rclone_remoto           → prefijo del remoto (gdrive:)
  rclone_config           → null = config real (~/.config/rclone/rclone.conf).
                            Si se define, se exporta como RCLONE_CONFIG al
                            rclone y al pipeline (pruebas con remote local).
  dry_default             → true = los jobs nuevos nacen en modo --dry
                            (sin subir a Drive ni enviar correo). Es el valor
                            seguro por defecto: hay que desmarcarlo a propósito.
  worker_idle_exit_s      → el worker se apaga solo tras N s sin jobs
  latido_s                → cada cuánto late el worker / relee el progreso
  stale_s                 → jobs "en_proceso" sin latido por >N s vuelven a pendiente
  espera_lock_s           → cuánto esperar si el lock auto/run.lock está tomado
  worker_log_retro_lineas → líneas del log del pipeline que se copian a eventos

✅ VALIDADO: 16/oct/2026 (Ches) — server y worker arrancan con esta config y
   respetan PANEL_CONFIG / RCLONE_CONFIG (prueba end-to-end con sandbox local).
"""
import json
import os
import subprocess

PANEL_DIR = os.path.dirname(os.path.abspath(__file__))
SEP_DIR = os.path.dirname(PANEL_DIR)                       # separador_pdfs/
WS = os.path.dirname(SEP_DIR)                              # workspace/

PY_VENV_PANEL = os.environ.get("PANEL_PY_PANEL") or os.path.join(WS, "venv", "bin", "python")
PY_VENV_PDF = os.environ.get("PANEL_PY_SEPARADOR") or os.path.join(WS, "venv-pdfseparador", "bin", "python")
SEPARADOR_AUTO = os.path.join(SEP_DIR, "separador_auto.py")
SEPARADOR_PDF = os.path.join(SEP_DIR, "separador_pdf.py")

AUTO_DIR = os.path.join(SEP_DIR, "auto")           # mismo dir que usa el pipeline
PROGRESS_DIR = os.path.join(AUTO_DIR, "progress")  # <job_id>.jsonl (hook de progreso)
LOCK = os.path.join(AUTO_DIR, "run.lock")          # lock del pipeline (respetado)
LOGS_DIR = os.path.join(PANEL_DIR, "logs")
STATIC_DIR = os.path.join(PANEL_DIR, "static")
TMP_DIR = os.path.join(PANEL_DIR, "tmp")
RCLONE = os.environ.get("RCLONE_BIN") or os.path.expanduser("~/.local/bin/rclone")
LD_LIBRARY = os.environ.get("PANEL_LD_LIBRARY_PATH") or os.path.expanduser("~/.local/lib")  # libgomp (PaddleOCR)

# --- Modo local (16/oct/2026): Noe se quedó sin espacio en Drive ---
ENTRADA_LOCAL = os.path.join(SEP_DIR, "entrada_local")
PROCESADOS_LOCAL = os.path.join(ENTRADA_LOCAL, "procesados")
INVENTARIOS_LOCAL = os.path.join(SEP_DIR, "inventarios_local")
SALIDA_LOCAL = os.path.join(AUTO_DIR, "salida")   # aquí quedan las corridas

DEFAULTS = {
    "host": "127.0.0.1",
    "puerto": 8791,
    "db": os.path.join(PANEL_DIR, "panel.db"),
    # modo: "local" (por defecto, sin rclone/Drive) | "drive" (histórico)
    "modo": "local",
    "rclone_remoto": "gdrive:",
    "carpeta_entrada": "Entrada_Separador",
    "carpeta_procesados": "Entrada_Separador/Procesados",
    "rclone_config": None,
    # rclone_bin: solo para pruebas/pruebas-negativas (p.ej. un stub que falla).
    # None = binario real (~/.local/bin/rclone). Se exporta como RCLONE_BIN.
    "rclone_bin": None,
    # Carpetas locales (si se dejan en null se usan las de arriba). Se exportan al
    # pipeline como SEPARADOR_ENTRADA_LOCAL / SEPARADOR_INVENTARIOS_LOCAL.
    "entrada_local": None,
    "inventarios_local": None,
    "dry_default": True,
    # Lectura SECUENCIAL por franjas (17/oct/2026): el panel muestra las líneas
    # conforme se leen y puede cortar la hoja al encontrar el SN. Opt-in: se mide
    # antes de dejarlo fijo. `ocr_solo_sn` corta solo en modelos que solo necesitan
    # el serial (HP 432/501); Ricoh/Pantum siguen leyendo la hoja completa.
    "ocr_secuencial": False,
    "ocr_solo_sn": True,
    "ocr_bandas": 4,
    # correo_activo: si es False los jobs nacen con --sin-correo (sin avisos por
    # mail). Por defecto False: en modo local no hay link de Drive y el panel basta.
    "correo_activo": False,
    "worker_idle_exit_s": 600,
    "latido_s": 3,
    "stale_s": 120,
    "espera_lock_s": 30,
    "worker_log_retro_lineas": 12,
    "timeout_rclone_s": 300,
}


def _leer(ruta):
    if os.path.isfile(ruta):
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    return {}


def cargar_config(ruta=None):
    """Devuelve la config efectiva (defaults + archivo). Crea el archivo si falta."""
    ruta = ruta or os.environ.get("PANEL_CONFIG") or os.path.join(PANEL_DIR, "config.json")
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in _leer(ruta).items() if k in DEFAULTS})
    cfg["_ruta"] = ruta
    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(PROGRESS_DIR, exist_ok=True)
    if not os.path.isfile(ruta):
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in cfg.items() if not k.startswith("_")}, f,
                          indent=2, ensure_ascii=False)
        except OSError:
            pass
    return cfg


def remoto(cfg, subruta=""):
    """'gdrive:Entrada_Separador' → 'gdrive:Entrada_Separador/...'"""
    base = f"{cfg['rclone_remoto']}{cfg['carpeta_entrada']}"
    return f"{base}/{subruta}" if subruta else base


def entorno_rclone(cfg, base=False):
    """Env para rclone / pipeline. Añade RCLONE_CONFIG si la config de pruebas
    lo define, RCLONE_BIN si se usa un binario alterno y LD_LIBRARY_PATH
    (libgomp de PaddleOCR) si es para el pipeline."""
    env = dict(os.environ)
    if cfg.get("rclone_config"):
        env["RCLONE_CONFIG"] = str(cfg["rclone_config"])
    if cfg.get("rclone_bin"):
        env["RCLONE_BIN"] = str(cfg["rclone_bin"])
    # Un solo origen de verdad para las carpetas locales: el pipeline las lee de aquí
    env["SEPARADOR_ENTRADA_LOCAL"] = entrada_local(cfg)
    env["SEPARADOR_INVENTARIOS_LOCAL"] = inventarios_local(cfg)
    # Lectura secuencial por franjas (opt-in) para el pipeline
    if cfg.get("ocr_secuencial"):
        env["PANEL_OCR_SECUENCIAL"] = "1"
        env["PANEL_OCR_BANDAS"] = str(int(cfg.get("ocr_bandas") or 4))
        env["PANEL_OCR_SOLO_SN"] = "1" if cfg.get("ocr_solo_sn") else "0"
    else:
        env.pop("PANEL_OCR_SECUENCIAL", None)
    if not base:
        previo = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = LD_LIBRARY + (os.pathsep + previo if previo else "")
    return env


def modo(cfg):
    return (cfg.get("modo") or "local").lower()


def entrada_local(cfg):
    """Carpeta de entrada local efectiva (configurable)."""
    return cfg.get("entrada_local") or ENTRADA_LOCAL


def procesados_local(cfg):
    return os.path.join(entrada_local(cfg), "procesados")


def inventarios_local(cfg):
    """Carpeta de inventarios local efectiva (configurable)."""
    return cfg.get("inventarios_local") or INVENTARIOS_LOCAL


def rclone(cfg, *args, timeout=None):
    """Ejecuta rclone con la config efectiva. Devuelve (code, stdout_limpio).

    En modo local ESTÁ PROHIBIDO: falla ruidosamente para que ningún camino de
    código toque la nube (Noe se quedó sin espacio en Drive). El código de Drive
    sigue intacto detrás de `modo: "drive"`.
    """
    if modo(cfg) == "local":
        raise RuntimeError("rclone deshabilitado (modo=local): " + " ".join(str(a) for a in args))
    timeout = timeout or cfg.get("timeout_rclone_s", 300)
    binario = cfg.get("rclone_bin") or RCLONE
    try:
        r = subprocess.run([binario] + list(args), capture_output=True, text=True,
                           timeout=timeout, env=entorno_rclone(cfg, base=True))
    except subprocess.TimeoutExpired:
        return 124, f"timeout tras {timeout}s: rclone {' '.join(args)}"
    except FileNotFoundError as e:
        return 127, f"rclone no encontrado: {e}"
    out = "\n".join(l for l in (r.stdout or "").splitlines()
                    if "retired" not in l and "NOTICE" not in l)
    err = "\n".join(l for l in (r.stderr or "").splitlines()
                    if "retired" not in l and "NOTICE" not in l)
    return r.returncode, (out + ("\n" + err if err.strip() else "")).strip()


def listar_entrada_drive(cfg):
    """PDFs en la carpeta de entrada de Drive (SIEMPRE lsjson --files-only,
    nunca lsf). Solo se usa en modo drive."""
    code, out = rclone(cfg, "lsjson", remoto(cfg), "--files-only", "--hash")
    if code != 0:
        return None, out
    try:
        archivos = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None, f"respuesta ilegible de rclone: {out[:200]}"
    archivos = [a for a in archivos if str(a.get("Name", "")).lower().endswith(".pdf")]
    archivos.sort(key=lambda a: a.get("Name", "").lower())
    return archivos, ""


def listar_entrada(cfg):
    """Compatibilidad: en modo local lista disco, en modo drive lista el Drive.
    (El panel usa panel_local.listar_entrada, que hace este despacho.)"""
    if modo(cfg) == "local":
        import panel_local
        return panel_local.listar_entrada(cfg)
    return listar_entrada_drive(cfg)


def lock_fresco(max_s=3600):
    """¿Hay una corrida del pipeline en curso? (mismo criterio que separador_auto)."""
    try:
        return os.path.exists(LOCK) and (os.path.getmtime(LOCK) and
                                         (__import__("time").time() - os.path.getmtime(LOCK)) < max_s)
    except OSError:
        return False
