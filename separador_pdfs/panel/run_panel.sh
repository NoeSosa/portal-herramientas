#!/usr/bin/env bash
# run_panel.sh — Arranque/parada del panel del separador de PDFs (16/oct/2026)
# ---------------------------------------------------------------------------
# Uso:
#   bash separador_pdfs/panel/run_panel.sh start     # arranca el servidor (127.0.0.1:8791)
#   bash separador_pdfs/panel/run_panel.sh stop      # lo detiene (SIGTERM)
#   bash separador_pdfs/panel/run_panel.sh restart
#   bash separador_pdfs/panel/run_panel.sh status     # estado servidor + worker + cola
#   bash separador_pdfs/panel/run_panel.sh worker-stop# corta el worker (termina el job en curso)
#   PANEL_CONFIG=...tests/config.test.json bash ... start   # modo pruebas (sandbox)
#
# ⚠️ Sin supervisor: si se reinicia el gateway/contenedor, el servidor y el worker
#    NO se relanzan solos (ver docs/panel-separador.md §Limitaciones).
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$(cd "$DIR/../.." && pwd)"
PY="$WS/venv/bin/python"
SRV="$DIR/panel_server.py"
WRK="$DIR/panel_worker.py"
CONFIG="${PANEL_CONFIG:-$DIR/config.json}"
PIDFILE="$DIR/logs/panel.pid"
WPIDFILE="$DIR/logs/worker.pid"
LOG="$DIR/logs/server.log"

puerto() { "$PY" - "$CONFIG" <<'PY'
import json, os, sys
d = {}
p = sys.argv[1]
if os.path.isfile(p):
    try:
        d = json.load(open(p))
    except Exception:
        d = {}
print(d.get("puerto", 8791))
PY
}

vivo() { [ -f "$1" ] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }

case "${1:-status}" in
  start)
    if vivo "$PIDFILE"; then echo "ya está corriendo (pid $(cat "$PIDFILE"))"; exit 0; fi
    mkdir -p "$DIR/logs"
    cd "$WS"
    PANEL_CONFIG="$CONFIG" nohup "$PY" "$SRV" >>"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 3
    if vivo "$PIDFILE"; then
      echo "panel arriba → http://127.0.0.1:$(puerto)  (pid $(cat "$PIDFILE"), log $LOG)"
    else
      echo "ERROR: no arrancó; revisa $LOG"; tail -20 "$LOG"; exit 1
    fi
    ;;
  stop)
    if vivo "$PIDFILE"; then kill "$(cat "$PIDFILE")" && echo "servidor detenido (pid $(cat "$PIDFILE"))"; rm -f "$PIDFILE"; else echo "no estaba corriendo"; fi
    ;;
  restart)
    "$0" stop; sleep 1; "$0" start
    ;;
  worker-stop)
    if vivo "$WPIDFILE"; then kill "$(cat "$WPIDFILE")" && echo "SIGTERM al worker (termina el job en curso)"; else echo "sin worker vivo"; fi
    ;;
  status)
    if vivo "$PIDFILE"; then echo "servidor: VIVO pid $(cat "$PIDFILE") · puerto $(puerto)"; else echo "servidor: detenido"; fi
    if vivo "$WPIDFILE"; then echo "worker:   VIVO pid $(cat "$WPIDFILE")"; else echo "worker:   detenido"; fi
    "$PY" - "$CONFIG" <<'PY'
import json, os, sqlite3, sys
cfg = {}
p = sys.argv[1]
if os.path.isfile(p):
    try:
        cfg = json.load(open(p))
    except Exception:
        cfg = {}
db = cfg.get("db", os.path.join(os.path.dirname(p), "panel.db"))
if not os.path.isfile(db):
    print("cola:     (sin BD todavía)")
    sys.exit(0)
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
for f in con.execute("SELECT estado, COUNT(*) n FROM cola GROUP BY estado"):
    print(f"cola:     {f['estado']}: {f['n']}")
PY
    ;;
  *)
    echo "uso: $0 {start|stop|restart|status|worker-stop}"; exit 2;;
esac
