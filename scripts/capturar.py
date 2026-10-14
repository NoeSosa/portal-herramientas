"""Captura el portal por HTTP y guarda una captura de pantalla.

Sirve para verificar visualmente el resultado del rediseño sin depender de que
alguien abra el navegador.

El portal escucha en 127.0.0.1:8080 del host. Como este script corre en el
contenedor openclaw (que comparte red con el proxy), accede por la IP del
contenedor hw-frontend en la red del portal.
"""

from __future__ import annotations

import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.wait import WebDriverWait

CHROME = "/home/node/armlibs/extracted/opt/google/chrome/chrome"
CHROMEDRIVER = "/home/node/.local/bin/chromedriver"
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/"
SALIDA = sys.argv[2] if len(sys.argv) > 2 else "/tmp/portal.png"


def main() -> None:
    opciones = Options()
    opciones.binary_location = CHROME
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--hide-scrollbars")
    opciones.add_argument("--window-size=1280,900")
    opciones.add_argument("--force-device-scale-factor=1")

    servicio = Service(executable_path=CHROMEDRIVER)
    navegador = webdriver.Chrome(service=servicio, options=opciones)

    try:
        navegador.get(URL)
        # Espera a que el SPA monte y resuelva la llamada a /api/info.
        WebDriverWait(navegador, 15).until(
            lambda d: d.find_elements("css selector", "main")
        )
        time.sleep(3)  # deja cargar las tipografías externas
        ok = navegador.save_screenshot(SALIDA)
        print(f"captura={'ok' if ok else 'fallo'} ruta={SALIDA}")
        print(f"titulo={navegador.title!r}")
    finally:
        navegador.quit()


if __name__ == "__main__":
    main()
