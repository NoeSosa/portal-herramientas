"""Ejecuta el despliegue con un Docker ficticio; nunca toca contenedores reales."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DespliegueSinCambiarOCRTest(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            candidato = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
            self.bash = str(candidato) if candidato.is_file() else None
        else:
            self.bash = shutil.which("bash")
        if not self.bash:
            self.skipTest("Se requiere Bash para la prueba de despliegue.")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "scripts").mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.script = self.base / "scripts/desplegar.sh"
        shutil.copyfile(ROOT / "scripts/desplegar.sh", self.script)
        (self.base / ".env").write_text("# Entorno ficticio, sin secretos\n")
        (self.base / "scripts/preparar_separador.sh").write_text(
            '#!/usr/bin/env bash\nprintf "preparar-ocr\\n" >> "$DOCKER_SMOKE_LOG"\n',
            encoding="utf-8",
        )
        docker = self.bin / "docker"
        docker.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$*" >> "$DOCKER_SMOKE_LOG"\n'
            'if [[ "$1 $2" == "image inspect" && "${MISSING_OCR:-0}" == "1" ]]; then exit 1; fi\n'
            'if [[ "$1" == "version" ]]; then echo "27.0"; fi\n'
            'exit 0\n', encoding="utf-8",
        )
        docker.chmod(0o700)
        self.log = self.base / "docker.log"

    def ejecutar(self, *args, imagen_ausente=False):
        env = dict(os.environ, SMOKE_BIN=str(self.bin), SMOKE_SCRIPT=str(self.script),
                   DOCKER_SMOKE_LOG=str(self.log), MISSING_OCR="1" if imagen_ausente else "0")
        comando = ('export PATH="$(cygpath -u "$SMOKE_BIN"):$PATH"; '
                   'exec bash "$SMOKE_SCRIPT" "$@"') if os.name == "nt" else (
                   'export PATH="$SMOKE_BIN:$PATH"; exec bash "$SMOKE_SCRIPT" "$@"')
        result = subprocess.run([self.bash, "-c", comando, "smoke", *args],
                                env=env, text=True, capture_output=True, timeout=30)
        acciones = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        return result, acciones

    def test_por_defecto_no_reconstruye_ni_recrea_worker_ocr(self):
        result, acciones = self.ejecutar()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build -t herramientas-web-backend:local", acciones)
        self.assertIn("build -t herramientas-web-frontend:local", acciones)
        self.assertNotIn("Dockerfile.ocr", acciones)
        self.assertNotIn("--name hw-worker-ocr", acciones)
        self.assertNotIn("preparar-ocr", acciones)

    def test_sin_ocr_conserva_la_opcion_existente(self):
        result, acciones = self.ejecutar("--sin-ocr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Dockerfile.ocr", acciones)

    def test_reconstruccion_exige_opcion_explicita(self):
        result, acciones = self.ejecutar("--reconstruir-ocr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dockerfile.ocr", acciones)
        self.assertIn("--name hw-worker-ocr", acciones)
        self.assertIn("preparar-ocr", acciones)

    def test_sin_imagen_validada_se_detiene_antes_de_cambiar_servicios(self):
        result, acciones = self.ejecutar(imagen_ausente=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No existe la imagen OCR validada", result.stderr)
        self.assertNotIn("build ", acciones)
        self.assertNotIn("run ", acciones)


if __name__ == "__main__":
    unittest.main()
