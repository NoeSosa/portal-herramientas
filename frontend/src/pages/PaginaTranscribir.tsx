import { useCallback, useEffect, useRef, useState } from "react";

import { IconoAviso, IconoDescarga, IconoOk, IconoTranscripcion } from "../components/Iconos";
import { pedir, subirArchivos } from "../lib/api";

interface RespuestaEncolado {
  job_id: string;
  mensaje: string;
}

interface EstadoJob {
  job_id: string;
  estado: "queued" | "started" | "finished" | "failed";
  texto: string | null;
  caracteres: number | null;
  archivo: string | null;
  error: string | null;
}

interface EstadoMotor {
  disponible: boolean;
  motivo: string;
  tipos: string[];
  max_upload_mb: number;
  motores: string[];
  idiomas: string[];
}

type Fase = "inactivo" | "subiendo" | "en-cola" | "listo" | "error";

/** Cada cuánto se pregunta al backend por el avance del trabajo. */
const INTERVALO_SONDEO_MS = 4000;

const ACEPTA = "audio/*,video/mp4,video/quicktime,video/webm";

/** Etiqueta legible del estado del trabajo en el servidor. */
function etiquetaEstado(estado: EstadoJob["estado"]): string {
  switch (estado) {
    case "queued":
      return "EN COLA";
    case "started":
      return "TRANSCRIBIENDO";
    case "finished":
      return "COMPLETADO";
    default:
      return "FALLÓ";
  }
}

/**
 * Herramienta «Transcribir audio o video».
 *
 * Es un trabajo de minutos, así que el flujo es asíncrono: se sube, se encola
 * y se consulta el estado cada pocos segundos. Si el usuario cierra la
 * pestaña, el trabajo sigue en el servidor.
 */
export function PaginaTranscribir() {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [idioma, setIdioma] = useState("");
  const [motor, setMotor] = useState("auto");
  const [fase, setFase] = useState<Fase>("inactivo");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [trabajo, setTrabajo] = useState<EstadoJob | null>(null);
  const [motorInfo, setMotorInfo] = useState<EstadoMotor | null>(null);

  const entrada = useRef<HTMLInputElement>(null);
  const arrastrando = useRef(false);
  const [resaltado, setResaltado] = useState(false);
  const temporizador = useRef<number | null>(null);

  useEffect(() => {
    let vigente = true;
    pedir<EstadoMotor>("/transcribir/estado")
      .then((d) => {
        if (vigente) setMotorInfo(d);
      })
      .catch(() => {
        if (vigente) setMotorInfo(null);
      });
    return () => {
      vigente = false;
    };
  }, []);

  // Sondeo del trabajo. Se detiene solo cuando llega a un estado terminal.
  useEffect(() => {
    if (!jobId || fase === "listo" || fase === "error") return;

    let vigente = true;

    const consultar = async () => {
      try {
        const estado = await pedir<EstadoJob>(`/transcribir/${jobId}`);
        if (!vigente) return;

        setTrabajo(estado);

        if (estado.estado === "finished") {
          setFase("listo");
        } else if (estado.estado === "failed") {
          setError(estado.error ?? "La transcripción falló.");
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
      const respuesta = await subirArchivos<RespuestaEncolado>("/transcribir", [archivo], {
        idioma,
        motor,
      });
      setJobId(respuesta.job_id);
      setFase("en-cola");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
      setFase("error");
    }
  }, [archivo, idioma, motor]);

  const ocupado = fase === "subiendo" || fase === "en-cola";
  const texto = trabajo?.texto ?? null;

  return (
    <section aria-labelledby="titulo-transcribir">
      <div className="regla">
        <span className="etiqueta">Transcripción</span>
        <span className="etiqueta ml-auto">COLA · GEMINI → WHISPER</span>
      </div>

      {motorInfo?.disponible === false && (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          <span>
            El transcriptor no está disponible en este despliegue.
            {motorInfo.motivo && <> {motorInfo.motivo}</>}
          </span>
        </p>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_20rem]">
        <form
          className="panel p-6 space-y-6"
          onSubmit={(e) => {
            e.preventDefault();
            void enviar();
          }}
        >
          <div>
            <span className="etiqueta block mb-2">Archivo de audio o video</span>

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
              <IconoTranscripcion tam={28} clase="mx-auto text-tinta-apagada" />
              <p className="mt-3 text-xs text-tinta-tenue">
                Arrastra el archivo o
                <button
                  type="button"
                  onClick={() => entrada.current?.click()}
                  className="ml-1 underline decoration-linea underline-offset-2
                             hover:text-acento hover:decoration-acento transition-colors duration-300"
                >
                  elige uno
                </button>
              </p>
              <p className="mt-2 etiqueta">
                AUDIO Y VIDEO · MÁX {motorInfo?.max_upload_mb ?? 50} MB
              </p>
            </div>

            <input
              ref={entrada}
              type="file"
              accept={ACEPTA}
              className="hidden"
              onChange={(e) => elegir(e.target.files?.[0] ?? null)}
            />

            {archivo && (
              <div className="mt-3 flex items-center gap-3 text-xs text-tinta-tenue">
                <span className="etiqueta text-ok">CARGADO</span>
                <span className="truncate">{archivo.name}</span>
              </div>
            )}
          </div>

          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <label htmlFor="idioma" className="etiqueta block mb-2">
                Idioma
              </label>
              <select
                id="idioma"
                value={idioma}
                onChange={(e) => setIdioma(e.target.value)}
                className="w-full rounded-control border border-linea bg-fondo px-3 py-2
                           text-sm text-tinta focus:border-acento"
              >
                <option value="">Autodetectar</option>
                <option value="es">Español</option>
                <option value="en">Inglés</option>
                <option value="pt">Portugués</option>
              </select>
            </div>

            <div>
              <label htmlFor="motor" className="etiqueta block mb-2">
                Motor
              </label>
              <select
                id="motor"
                value={motor}
                onChange={(e) => setMotor(e.target.value)}
                className="w-full rounded-control border border-linea bg-fondo px-3 py-2
                           text-sm text-tinta focus:border-acento"
              >
                <option value="auto">Automático</option>
                <option value="whisper">Local (Whisper)</option>
                <option value="gemini">Gemini</option>
              </select>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <button
              type="submit"
              disabled={!archivo || ocupado || motorInfo?.disponible === false}
              className="rounded-control border border-acento bg-acento px-6 py-2.5
                         text-xs uppercase tracking-wider text-fondo font-medium
                         hover:bg-acento/90 disabled:opacity-30 disabled:cursor-not-allowed
                         transition-opacity duration-300"
            >
              {fase === "subiendo" ? "Subiendo…" : ocupado ? "En proceso…" : "Transcribir"}
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
              El trabajo continúa en el servidor aunque cierres esta pestaña.
            </p>
          )}
        </form>

        <aside aria-live="polite" className="space-y-4">
          <div className="regla">
            <span className="etiqueta">Resultado</span>
            {trabajo && (
              <span className="etiqueta ml-auto">{etiquetaEstado(trabajo.estado)}</span>
            )}
          </div>

          {fase === "inactivo" && (
            <p className="text-xs text-tinta-apagada leading-relaxed">
              El texto aparecerá aquí. El archivo se borra del servidor al
              terminar la transcripción.
            </p>
          )}

          {ocupado && (
            <div className="panel p-4">
              <p className="etiqueta">{trabajo ? etiquetaEstado(trabajo.estado) : "SUBIENDO"}</p>
              <div className="mt-3 h-1 w-full bg-linea overflow-hidden">
                <div className="h-full w-1/3 bg-acento animate-pulso" />
              </div>
              <p className="mt-3 text-xs text-tinta-apagada leading-relaxed">
                El motor local puede tardar uno o dos minutos por cada diez
                minutos de audio.
              </p>
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

          {fase === "listo" && texto && (
            <div className="panel p-4 space-y-3">
              <p className="flex items-center gap-2 text-xs text-ok">
                <IconoOk tam={14} />
                <span className="etiqueta text-ok">
                  {trabajo?.caracteres ?? texto.length} CARACTERES
                </span>
              </p>

              <pre className="max-h-96 overflow-auto whitespace-pre-wrap font-mono
                              text-xs leading-relaxed text-tinta">
                {texto}
              </pre>

              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => void navigator.clipboard.writeText(texto)}
                  className="inline-flex items-center gap-2 border border-linea px-3 py-2
                             text-xs uppercase tracking-wider text-tinta-tenue
                             hover:border-acento hover:text-tinta transition-colors duration-300"
                >
                  Copiar
                </button>

                {jobId && (
                  <a
                    href={`/api/transcribir/descargar/${jobId}`}
                    download={`transcripcion-${jobId}.txt`}
                    className="inline-flex items-center gap-2 border border-linea px-3 py-2
                               text-xs uppercase tracking-wider text-tinta-tenue
                               hover:border-acento hover:text-tinta transition-colors duration-300"
                  >
                    <IconoDescarga tam={14} />
                    Descargar TXT
                  </a>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
