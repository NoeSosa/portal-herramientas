import { useCallback, useEffect, useRef, useState } from "react";

import { IconoAviso, IconoDescarga, IconoOk, IconoPdf } from "../components/Iconos";
import { pedir, subirArchivos } from "../lib/api";

interface RespuestaEncolado {
  job_id: string;
  mensaje: string;
}

interface EstadoJob {
  job_id: string;
  estado: "queued" | "started" | "finished" | "failed";
  resultado: {
    ok?: number;
    verificar?: number;
    zip?: string;
    //: Ruta relativa ya lista para descargar, generada por el backend.
    link_descarga?: string;
    total?: number;
    [clave: string]: unknown;
  } | null;
  error: string | null;
}

type Fase = "inactivo" | "subiendo" | "procesando" | "listo" | "error";

/** El OCR tarda minutos: se consulta cada 10 s, no cada 4 como la transcripción. */
const INTERVALO_SONDEO_MS = 10000;

/**
 * Herramienta «Separador por sede».
 *
 * El motor hace OCR página a página (10–20 s por página), así que un PDF de
 * 100 páginas puede tardar 20 minutos. Todo el trabajo va al worker OCR y aquí
 * solo se sigue el avance.
 */
export function PaginaSeparador() {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [fase, setFase] = useState<Fase>("inactivo");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [trabajo, setTrabajo] = useState<EstadoJob | null>(null);

  const entrada = useRef<HTMLInputElement>(null);
  const arrastrando = useRef(false);
  const [resaltado, setResaltado] = useState(false);
  const temporizador = useRef<number | null>(null);

  useEffect(() => {
    if (!jobId || fase === "listo" || fase === "error") return;

    let vigente = true;

    const consultar = async () => {
      try {
        const estado = await pedir<EstadoJob>(`/separador/${jobId}`);
        if (!vigente) return;

        setTrabajo(estado);

        if (estado.estado === "finished") {
          setFase("listo");
        } else if (estado.estado === "failed") {
          setError(estado.error ?? "El procesamiento falló.");
          setFase("error");
        }
      } catch (err) {
        if (!vigente) return;
        setError(err instanceof Error ? err.message : "Error consultando el trabajo.");
        setFase("error");
      }
    };

    void consultar();
    temporizador.current = window.setInterval(() => void consultar(), INTERVALO_SONDEO_MS);

    return () => {
      vigente = false;
      if (temporizador.current !== null) {
        window.clearInterval(temporizador.current);
        temporizador.current = null;
      }
    };
  }, [jobId, fase]);

  const elegir = useCallback((nuevo: File | null) => {
    setArchivo(nuevo);
    setJobId(null);
    setTrabajo(null);
    setError(null);
    setFase("inactivo");
  }, []);

  const enviar = useCallback(async () => {
    if (!archivo) return;

    setFase("subiendo");
    setError(null);
    setTrabajo(null);

    try {
      const respuesta = await subirArchivos<RespuestaEncolado>("/separador", [archivo]);
      setJobId(respuesta.job_id);
      setFase("procesando");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
      setFase("error");
    }
  }, [archivo]);

  const ocupado = fase === "subiendo" || fase === "procesando";
  const resultado = trabajo?.resultado;

  return (
    <section aria-labelledby="titulo-separador">
      <div className="regla">
        <span className="etiqueta">Separador por sede</span>
        <span className="etiqueta ml-auto">OCR · COLA PESADA</span>
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_20rem]">
        <form
          className="panel p-6 space-y-6"
          onSubmit={(e) => {
            e.preventDefault();
            void enviar();
          }}
        >
          <div>
            <span className="etiqueta block mb-2">PDF de contadores</span>

            <div
              onDragOver={(e) => {
                e.preventDefault();
                if (!arrastrando.current) {
                  arrastrando.current = true;
                  setResaltado(true);
                }
              }}
              onDragLeave={() => {
                arrastrando.current = false;
                setResaltado(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                arrastrando.current = false;
                setResaltado(false);
                elegir(e.dataTransfer.files?.[0] ?? null);
              }}
              className={[
                "border border-dashed px-4 py-8 text-center transition-colors duration-300",
                resaltado ? "border-acento bg-acento/5" : "border-linea",
              ].join(" ")}
            >
              <IconoPdf tam={28} clase="mx-auto text-tinta-apagada" />
              <p className="mt-3 text-xs text-tinta-tenue">
                Arrastra el PDF o
                <button
                  type="button"
                  onClick={() => entrada.current?.click()}
                  className="ml-1 underline decoration-linea underline-offset-2
                             hover:text-acento hover:decoration-acento transition-colors duration-300"
                >
                  elige un archivo
                </button>
              </p>
              <p className="mt-2 etiqueta">PDF · ESCANEADO O DIGITAL</p>
            </div>

            <input
              ref={entrada}
              type="file"
              accept="application/pdf"
              className="hidden"
              onChange={(e) => elegir(e.target.files?.[0] ?? null)}
            />
          </div>

          <p className="text-xs text-tinta-apagada leading-relaxed">
            El motor lee el serial de cada equipo y lo agrupa por sede. Si no se
            proporciona un inventario de referencia, los equipos quedan en la
            carpeta <span className="font-mono">Verificar/</span> para revisión
            manual: es el comportamiento esperado, no un fallo.
          </p>

          <div className="flex items-center gap-4">
            <button
              type="submit"
              disabled={!archivo || ocupado}
              className="rounded-control border border-acento bg-acento px-6 py-2.5
                         text-xs uppercase tracking-wider text-fondo font-medium
                         hover:bg-acento/90 disabled:opacity-30 disabled:cursor-not-allowed
                         transition-opacity duration-300"
            >
              {fase === "subiendo" ? "Subiendo…" : ocupado ? "Procesando…" : "Separar"}
            </button>

            {archivo && !ocupado && (
              <button
                type="button"
                onClick={() => elegir(null)}
                className="text-xs text-tinta-apagada hover:text-tinta transition-colors duration-300"
              >
                Quitar
              </button>
            )}
          </div>

          {ocupado && (
            <p className="text-xs text-tinta-apagada leading-relaxed">
              El OCR tarda 10–20 segundos por página. Puedes cerrar esta pestaña:
              el trabajo continúa en el servidor.
            </p>
          )}
        </form>

        <aside aria-live="polite" className="space-y-4">
          <div className="regla">
            <span className="etiqueta">Resultado</span>
            {trabajo && (
              <span className="etiqueta ml-auto">{trabajo.estado.toUpperCase()}</span>
            )}
          </div>

          {fase === "inactivo" && (
            <p className="text-xs text-tinta-apagada leading-relaxed">
              El ZIP con los PDFs organizados por sede aparecerá aquí.
            </p>
          )}

          {ocupado && (
            <div className="panel p-4">
              <p className="etiqueta">
                {trabajo ? trabajo.estado.toUpperCase() : "SUBIENDO"}
              </p>
              <div className="mt-3 h-1 w-full bg-linea overflow-hidden">
                <div className="h-full w-1/3 bg-acento animate-pulso" />
              </div>
            </div>
          )}

          {error && (
            <p
              role="alert"
              className="flex items-start gap-2 border border-error/50 bg-error/10
                         px-3 py-2.5 text-xs text-error leading-relaxed"
            >
              <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
              {error}
            </p>
          )}

          {fase === "listo" && resultado && (
            <div className="panel p-4 space-y-4">
              <p className="flex items-center gap-2 text-xs text-ok">
                <IconoOk tam={14} />
                <span className="etiqueta text-ok">PROCESADO</span>
              </p>

              <dl className="space-y-1.5">
                {typeof resultado.ok === "number" && (
                  <div className="flex justify-between text-xs">
                    <dt className="etiqueta">CLASIFICADOS</dt>
                    <dd className="font-mono text-ok">{resultado.ok}</dd>
                  </div>
                )}
                {typeof resultado.verificar === "number" && (
                  <div className="flex justify-between text-xs">
                    <dt className="etiqueta">A VERIFICAR</dt>
                    <dd className="font-mono text-aviso">{resultado.verificar}</dd>
                  </div>
                )}
              </dl>

              {/* La ruta la genera el backend (`link_descarga`): el frontend no
                  compone rutas de archivos por su cuenta. */}
              {resultado.link_descarga && (
                <a
                  href={resultado.link_descarga}
                  download={`separador-${jobId}.zip`}
                  className="inline-flex items-center gap-2 border border-linea px-3 py-2
                             text-xs uppercase tracking-wider text-tinta-tenue
                             hover:border-acento hover:text-tinta transition-colors duration-300"
                >
                  <IconoDescarga tam={14} />
                  Descargar ZIP
                </a>
              )}
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
