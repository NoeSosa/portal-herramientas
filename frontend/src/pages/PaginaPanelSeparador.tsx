/**
 * Herramienta «Panel del separador».
 *
 * El panel en vivo (cola, progreso por página, entrada local, resultados y ZIPs)
 * es una aplicación propia con su API. Se sirve por el MISMO origen que el portal
 * (nginx lo proxya a `http://panel:8791` en `/panel/` y `/api/panel/`), así que
 * basta un iframe: nada de puertos extra ni de exponer el panel fuera del túnel.
 *
 * Por qué un iframe y no reimplementarlo en React: el panel emite SSE y descarga
 * archivos con nombres que contienen sedes/acentos; moverlo aquí duplicaría el
 * código del servidor. Además así el panel sigue funcionando igual en local
 * (`venv/bin/python separador_pdfs/panel/panel_server.py` → 127.0.0.1:8791).
 */
export function PaginaPanelSeparador() {
  return (
    <section aria-labelledby="titulo-panel-separador" className="flex flex-col gap-3">
      <div className="regla">
        <span className="etiqueta text-tinta">Panel del separador</span>
        <span className="etiqueta">MODO LOCAL · SIN DRIVE</span>
        <a
          className="etiqueta ml-auto underline"
          href="/panel/"
          target="_blank"
          rel="noreferrer"
        >
          abrir en pestaña nueva ↗
        </a>
      </div>

      <p className="max-w-3xl text-xs leading-relaxed text-tinta-tenue">
        Sube PDFs de contadores, dispara el procesamiento y descarga los resultados
        por sede. El OCR corre en el servidor: puedes cerrar el navegador.
      </p>

      <iframe
        src="/panel/"
        title="Panel del separador de PDFs"
        className="h-[78vh] w-full border border-linea bg-fondo"
      />
    </section>
  );
}
