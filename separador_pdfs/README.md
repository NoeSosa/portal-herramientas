# Panel del Separador de PDFs

Panel web local para operar el separador de PDFs: se suben los escaneos, el
pipeline los lee con OCR, separa cada equipo en su archivo y deja el resultado
listo para descargar (por sede, en ZIP, o archivo por archivo).

Todo corre en local. No requiere servicios en la nube ni credenciales de terceros.

## Que hace

1. **Entrada.** Se suben los PDFs al panel (o se copian a la carpeta de entrada).
2. **Proceso.** El OCR lee cada hoja, identifica el numero de serie, la sede y la
   marca, y separa el documento en un PDF por equipo.
3. **Resultados.** Los archivos quedan organizados por corrida y por sede, en dos
   carpetas: `VERIFICADOS` y `POR_VERIFICAR`. Se descargan desde el panel.

## Vistas

El panel tiene dos vistas conmutables y recuerda la ultima usada.

**Vista simple (por defecto)** es la de uso diario: un flujo de tres pasos, sin
pestanas ni metricas tecnicas.

1. **Documentos**: arrastrar PDFs, ver que hay y pulsar el boton principal. Si el
   panel esta en modo prueba, un aviso lo explica y ofrece procesar de verdad.
2. **Procesando**: que documento se esta leyendo, pagina actual, miniatura y datos
   detectados (serial, sede), una sola barra de avance y la tira de paginas. Al
   terminar, una tarjeta con el resumen y el acceso a resultados.
3. **Resultados**: resumen de la ultima corrida (equipos listos, por revisar y
   sedes), descarga de todo en ZIP y el historial de corridas plegado.

**Vista completa** anade la parte tecnica: pestanas, cola de trabajo, log en vivo,
metricas, historial de workers, ficha del sistema e inventario de seriales editable.

## Visor de hojas

Cada hoja puede verse en grande con el boton **maximizar**:

- La hoja con los recuadros que detecto el OCR, apareciendo en orden de lectura.
- Los renglones con dato relevante (serial, contador, modelo, fecha) resaltados.
- Texto completo de la hoja, con el serial marcado.
- Navegacion hoja a hoja y opcion de seguir en vivo la hoja en proceso.
- Historial por worker: hojas leidas, tiempo medio y rangos atendidos.

Funciona tambien con hojas de corridas ya terminadas, porque las lecturas se
guardan en la base.

## Requisitos

- Linux con Python 3.11.
- Un virtualenv con el stack de OCR (`paddleocr`, `pymupdf`, `opencv`). Ver
  `requirements.txt`.
- Opcional: Docker, para correr el panel como servicio junto al portal de
  herramientas (`docker-compose.yml` incluido).

## Instalacion y arranque

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# configuracion local (copiar y ajustar rutas)
cp panel/config.example.json panel/config.json

# arrancar servidor y worker
bash panel/run_panel.sh start
bash panel/run_panel.sh status
```

El panel escucha en `127.0.0.1:8791` por defecto. El puerto y las carpetas se
configuran en `panel/config.json`.

Para detenerlo:

```bash
bash panel/run_panel.sh stop
```

## Estructura

```
separador_pdfs/
  separador_pdf.py        OCR de las hojas y armado de los PDFs de salida
  separador_auto.py       orquestacion por documento (workers, registro, correo)
  panel/
    panel_server.py       API HTTP y envio de eventos en vivo
    panel_worker.py       ejecuta los trabajos de la cola, uno a la vez
    panel_db.py           base SQLite: cola, eventos, control, equipos
    panel_local.py        entrada, resultados, ZIP, espacio en disco, limpieza
    panel_config.py       lectura de configuracion y variables de entorno
    static/index.html     interfaz completa (un solo archivo)
    tests/                pruebas de maquetacion e integracion
    run_panel.sh          arranque y parada
entrada_local/            PDFs por procesar
inventarios_local/        inventarios (el panel exporta inventario_panel.xlsx)
auto/salida/              resultados por corrida y sede
auto/progress/            progreso y lecturas OCR de cada trabajo
```

## Modo local y modo Drive

Por defecto el panel trabaja en **modo local**: todo se queda en disco y no se
llama a `rclone` ni a Google Drive. El codigo de Drive sigue presente, detras de
la opcion `modo: "drive"` en la configuracion, pero en modo local cualquier
intento de usarlo falla de forma explicita.

## Inventario de seriales

El inventario vive en la base del panel y se edita desde la pestana **Seriales**
(vista completa). El panel exporta `inventarios_local/inventario_panel.xlsx` y el
pipeline prefiere ese archivo; si se borra, vuelve a usar el inventario original.

## Notas de operacion

- Los trabajos se ejecutan en un proceso aparte: cerrar el navegador no los
  detiene.
- **Un job a la vez.** El worker del panel saca trabajos de la cola de uno en uno.
  Dentro de cada PDF, `SEPARADOR_WORKERS` (2) reparte las paginas en rangos
  contiguos y sin solape: el worker 1 toma el primer tramo y el 2 el siguiente.
  El panel muestra ambos con su rango, paginas leidas y ultima hoja. El "worker
  del panel" es otro: es el que ejecuta el job, no el que lee las paginas.
- Si un worker de OCR termina sin leer alguna hoja, el pipeline lo detecta, lo
  registra y reintenta esas hojas antes de clasificar.
- Los correos estan desactivados por defecto (se ejecuta con `--sin-correo`).
- La base, los logs y las lecturas OCR se pueden podar; los resultados no se
  borran solos.

## Licencia

Uso interno. Sin licencia publica por ahora.
