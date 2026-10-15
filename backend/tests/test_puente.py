"""Pruebas del puente hacia los scripts de OpenClaw.

Se verifica el contrato que hace posible ejecutar scripts escritos para otro
contenedor: copia a un directorio escribible, intérprete del venv, entorno
saneado y clave inyectada por entorno (nunca por línea de comandos).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.proceso import PATH_BASE, entorno_limpio
from app.services import puente


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    """Workspace mínimo con un script y su módulo hermano."""
    carpeta = tmp_path / "workspace" / "scripts"
    carpeta.mkdir(parents=True)
    (carpeta / "principal.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "import ayudante  # noqa\n"
        "print('ok')\n",
        encoding="utf-8",
    )
    (carpeta / "ayudante.py").write_text("VALOR = 1\n", encoding="utf-8")
    (carpeta / "otro.py").write_text("import json\nprint(json)\n", encoding="utf-8")
    return carpeta


@pytest.fixture
def settings(tmp_path: Path, scripts: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        herramientas_scripts_dir=scripts,
    )


class TestCopiarScript:
    def test_copia_el_script(self, settings: Settings) -> None:
        destino = puente.copiar_script("principal.py", settings)

        assert destino.is_file()
        assert destino.parent.name == "scripts"
        assert destino.parent.parent == settings.data_dir / "runtime"

    def test_copia_los_modulos_hermanos(self, settings: Settings) -> None:
        """Regresión: `transcribir_av.py` importa `transcribe_audio`.

        Con solo el archivo principal, el import fallaba con
        `ModuleNotFoundError` y la transcripción moría antes de empezar.
        """
        puente.copiar_script("principal.py", settings)

        hermano = settings.data_dir / "runtime" / "scripts" / "ayudante.py"
        assert hermano.is_file()

    def test_no_copia_lo_que_no_importa(self, settings: Settings) -> None:
        puente.copiar_script("principal.py", settings)

        sobrante = settings.data_dir / "runtime" / "scripts" / "otro.py"
        assert not sobrante.exists()

    def test_no_copia_la_stdlib_como_si_fuera_hermano(self, settings: Settings) -> None:
        """`import json` no debe intentar copiar nada."""
        puente.copiar_script("otro.py", settings)

        carpeta = settings.data_dir / "runtime" / "scripts"
        assert [p.name for p in carpeta.iterdir()] == ["otro.py"]

    def test_script_inexistente(self, settings: Settings) -> None:
        with pytest.raises(puente.ErrorPuente, match="No se encontró"):
            puente.copiar_script("no_existe.py", settings)


class TestCopiarContexto:
    def test_copia_la_carpeta_de_datos(self, tmp_path: Path, scripts: Path) -> None:
        """`finanzas.py` resuelve `../finanzas` y escribe ahí al registrar."""
        finanzas = scripts.parent / "finanzas" / "users" / "noe"
        finanzas.mkdir(parents=True)
        (finanzas / "ledger.db").write_bytes(b"datos")

        settings = Settings(data_dir=tmp_path / "data", herramientas_scripts_dir=scripts)
        destino = puente.copiar_contexto("finanzas", settings)

        assert (destino / "users" / "noe" / "ledger.db").read_bytes() == b"datos"

    def test_refresca_la_copia_en_cada_llamada(
        self, tmp_path: Path, scripts: Path
    ) -> None:
        origen = scripts.parent / "finanzas"
        origen.mkdir(parents=True)
        (origen / "a.json").write_text("1", encoding="utf-8")

        settings = Settings(data_dir=tmp_path / "data", herramientas_scripts_dir=scripts)
        puente.copiar_contexto("finanzas", settings)

        (origen / "a.json").write_text("2", encoding="utf-8")
        destino = puente.copiar_contexto("finanzas", settings)

        assert (destino / "a.json").read_text(encoding="utf-8") == "2"

    def test_carpeta_inexistente(self, settings: Settings) -> None:
        with pytest.raises(puente.ErrorPuente, match="No se encontró la carpeta"):
            puente.copiar_contexto("no_existe", settings)


class TestEntorno:
    def test_path_incluye_el_venv(self) -> None:
        """`piper` y `ffmpeg` se invocan por nombre: deben estar en el PATH."""
        assert entorno_limpio()["PATH"] == PATH_BASE
        assert "/home/node/.openclaw/workspace/venv/bin" in PATH_BASE

    def test_no_filtra_variables_del_portal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://secreto:6379")
        monkeypatch.setenv("GEMINI_API_KEY", "no-debe-heredarse")

        entorno = entorno_limpio()

        assert "REDIS_URL" not in entorno
        assert "GEMINI_API_KEY" not in entorno

    def test_extra_sobrescribe(self) -> None:
        entorno = entorno_limpio({"GEMINI_API_KEY": "clave"})

        assert entorno["GEMINI_API_KEY"] == "clave"

    def test_extra_vacio_se_ignora(self) -> None:
        """Una clave vacía no debe pisar la herencia y dejar el proceso sin ella."""
        entorno = entorno_limpio({"GEMINI_API_KEY": ""})

        assert "GEMINI_API_KEY" not in entorno


class TestDisponibilidad:
    def test_detecta_workspace_ausente(self, tmp_path: Path) -> None:
        settings = Settings(
            data_dir=tmp_path,
            herramientas_scripts_dir=tmp_path / "no-existe",
        )
        disponible, motivo = puente.entorno_openclaw_disponible(settings)

        assert disponible is False
        assert "venv" in motivo or "scripts" in motivo
