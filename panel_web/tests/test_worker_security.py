"""Controles del worker sin cargar ni ejecutar el motor OCR."""

import os
import signal
import subprocess
import unittest
from unittest.mock import Mock, call, patch

from panel_web.app import worker
from panel_web.app.config import Settings


class SeguridadWorkerTest(unittest.TestCase):
    def test_worker_arranca_sin_secretos_web_y_web_los_exige(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env(require_auth=False)
            self.assertEqual(settings.client_secret, "")
            self.assertEqual(settings.session_secret, "")
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_subproceso_no_hereda_secretos(self):
        with patch.dict(os.environ, {
            "PATH": "runtime-path", "LD_LIBRARY_PATH": "/opt/lib",
            "SESSION_SECRET": "fake-session", "MS_CLIENT_SECRET": "fake-client",
            "GEMINI_API_KEY": "fake-api", "UNEXPECTED_TOKEN": "fake-token",
        }, clear=True):
            self.assertEqual(worker._entorno_base_ocr(),
                             {"PATH": "runtime-path", "LD_LIBRARY_PATH": "/opt/lib"})

    def test_timeout_termina_grupo_completo_y_espera(self):
        proc = Mock(pid=123)
        proc.wait.side_effect = [subprocess.TimeoutExpired("ocr", 5), 0]
        with patch.object(worker.os, "name", "posix"), \
             patch.object(worker.signal, "SIGKILL", 9, create=True), \
             patch.object(worker.os, "killpg", create=True) as killpg:
            worker._detener_proceso(proc)
            self.assertEqual(killpg.call_args_list,
                             [call(123, signal.SIGTERM), call(123, signal.SIGKILL)])
            self.assertEqual(proc.wait.call_count, 2)

    def test_hijos_se_terminan_aunque_padre_ya_haya_salido(self):
        proc = Mock(pid=123)
        proc.wait.return_value = 0
        with patch.object(worker.os, "name", "posix"), \
             patch.object(worker.signal, "SIGKILL", 9, create=True), \
             patch.object(worker.os, "killpg", create=True) as killpg:
            worker._detener_proceso(proc)
            self.assertIn(call(123, signal.SIGKILL), killpg.call_args_list)


if __name__ == "__main__":
    unittest.main()
