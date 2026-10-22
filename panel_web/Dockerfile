# =============================================================================
# Panel web público — imagen autocontenida
# =============================================================================
# La imagen base (herramientas-web-ocr:local) trae Python, FastAPI, uvicorn,
# PyMuPDF, PaddleOCR y las dependencias del pipeline. Aquí se agrega la app web,
# Authlib y el código del separador. El resultado NO monta el workspace de
# OpenClaw ni el repo del host: es inmutable en runtime.
#
# Contexto de build: la raíz del repositorio (ver docker-compose.yml).
# =============================================================================
FROM herramientas-web-ocr:local

USER root

COPY panel_web/requirements.txt /tmp/panel_web_requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir \
      -r /tmp/panel_web_requirements.txt \
 && rm -f /tmp/panel_web_requirements.txt

WORKDIR /panel_web
COPY --chown=1000:1000 panel_web/app ./app
COPY --chown=1000:1000 panel_web/static ./static

# Código del motor que ejecuta el worker (solo lectura en runtime).
COPY --chown=1000:1000 separador_pdfs /separador_pdfs

ENV PYTHONPATH=/panel_web \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data \
    DATA_DIR=/data \
    SEPARATOR_CODE_DIR=/separador_pdfs

USER portal

EXPOSE 8000

# FORWARDED_ALLOW_IPS define proxies concretos; nunca confiar en cualquier origen.
CMD ["python3", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-server-header"]
