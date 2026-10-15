import { useCallback, useEffect, useRef, useState } from "react";

import { IconoAviso, IconoImagen, IconoOk } from "../components/Iconos";
import { pedir, subirArchivos } from "../lib/api";

interface RespuestaVision {
  texto: string;
  caracteres: number;
}

interface EstadoMotor {
  disponible: boolean;
  motivo: string;
  modelo: string;
  tipos: string[];
  max_upload_mb: number;
  prompt_por_defecto: string;
}

type Estado = "inactivo" | "analizando" | "listo" | "error";

/** Extensiones que el selector de archivos ofrece. El backend revalida igual. */
const ACEPTA = "image/jpeg,image/png,image/webp,image/gif";

/**
 * Formatea bytes a una etiqueta corta para la franja de metadatos.
 * Se usa en mono con mayúsculas: es un dato, no una frase.
 */
function bytesLegibles(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Herramienta «Analizar imagen».
 *
 * Una sola pantalla: se elige la imagen, se ajusta el prompt y se lee el
 * resultado. Sin pasos intermedios porque la operación tarda segundos.
 */
export function PaginaVision() {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [vista, setVista] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [estado, setEstado] = useState<Estado>("inactivo");
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<RespuestaVision | null>(null);
  const [motor, setMotor] = useState<EstadoMotor | null>(null);

  const entrada = useRef<HTMLInputElement>(null);
  const arrastrando = useRef(false);
  const [resaltado, setResaltado] = useState(false);

  useEffect(() => {
    let vigente = true;
    pedir<EstadoMotor>("/vision/estado")
      .then((d) => {
        if (!vigente) return;
        setMotor(d);
        setPrompt(d.prompt_por_defecto);
      })
      .catch(() => {
        if (vigente) setMotor(null);
      });
    return () => {
      vigente = false;
    };
  }, []);

  // La vista previa se construye desde el archivo local: nunca se sube una
  // imagen solo para mostrarla.
  useEffect(() => {
    if (!archivo) {
      setVista(null);
      return;
    }
    const url = URL.createObjectURL(archivo);
    setVista(url);
    return () => URL.revokeObjectURL(url);
  }, [archivo]);

  const elegir = useCallback((nuevo: File | null) => {
    setResultado(null);
    setError(null);
    setEstado("inactivo");
    setArchivo(nuevo);
  }, []);

  const analizar = useCallback(async () => {
    if (!archivo) return;

    setEstado("analizando");
    setError(null);
    setResultado(null);

    try {
      const respuesta = await subirArchivos<RespuestaVision>("/vision", [archivo], {
        prompt: prompt.trim(),
      });
      setResultado(respuesta);
      setEstado("listo");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
      setEstado("error");
    }
  }, [archivo, prompt]);

  const ocupado = estado === "analizando";

  return (
    <section aria-labelledby="titulo-vision">
      <div className="regla">
        <span className="etiqueta">Análisis de imagen</span>
        <span className="etiqueta ml-auto">
          {motor ? `GEMINI · ${motor.modelo}` : "GEMINI · VISION"}
        </span>
      </div>

      {motor?.disponible === false && (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          <span>
            El análisis de imagen no está disponible en este despliegue.
            {motor.motivo && <> {motor.motivo}</>}
          </span>
        </p>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_20rem]">
        {/* --- Entrada --- */}
        <form
          className="panel p-6 space-y-6"
          onSubmit={(e) => {
            e.preventDefault();
            void analizar();
          }}
        >
          <div>
            <span className="etiqueta block mb-2">Imagen</span>

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
              <IconoImagen tam={28} clase="mx-auto text-tinta-apagada" />
              <p className="mt-3 text-xs text-tinta-tenue">
                Arrastra la imagen aquí o
                <button
                  type="button"
                  onClick={() => entrada.current?.click()}
                  className="ml-1 underline decoration-linea underline-offset-2
                             hover:text-acento hover:decoration-acento transition-colors duration-300"
                >
                  elige un archivo
                </button>
              </p>
              <p className="mt-2 etiqueta">
                JPG · PNG · WEBP · GIF · MÁX{" "}
                {motor?.max_upload_mb ?? 50} MB
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
                <span className="etiqueta text-ok">CARGADA</span>
                <span className="truncate">{archivo.name}</span>
                <span className="etiqueta ml-auto shrink-0">
                  {bytesLegibles(archivo.size)}
                </span>
              </div>
            )}
          </div>

          <div>
            <label htmlFor="prompt" className="etiqueta block mb-2">
              Instrucción para el modelo
            </label>
            <textarea
              id="prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              maxLength={2000}
              className="w-full rounded-control border border-linea bg-fondo px-3 py-2.5
                         font-mono text-xs leading-relaxed text-tinta
                         placeholder:text-tinta-apagada focus:border-acento resize-y"
            />
            <p className="mt-2 etiqueta">{2000 - prompt.length} restantes</p>
          </div>

          <div className="flex items-center gap-4">
            <button
              type="submit"
              disabled={!archivo || ocupado || motor?.disponible === false}
              className="rounded-control border border-acento bg-acento px-6 py-2.5
                         text-xs uppercase tracking-wider text-fondo font-medium
                         hover:bg-acento/90 disabled:opacity-30 disabled:cursor-not-allowed
                         transition-opacity duration-300"
            >
              {ocupado ? "Analizando…" : "Analizar"}
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
        </form>

        {/* --- Resultado --- */}
        <aside aria-live="polite" className="space-y-4">
          <div className="regla">
            <span className="etiqueta">Resultado</span>
          </div>

          {vista && (
            <img
              src={vista}
              alt="Vista previa de la imagen cargada"
              className="w-full border border-linea object-contain max-h-56"
            />
          )}

          {estado === "inactivo" && (
            <p className="text-xs text-tinta-apagada leading-relaxed">
              La descripción aparecerá aquí. La imagen se borra del servidor en
              cuanto termina el análisis.
            </p>
          )}

          {estado === "analizando" && (
            <p className="text-xs text-tinta-tenue">Consultando al modelo…</p>
          )}

          {estado === "error" && error && (
            <p
              role="alert"
              className="flex items-start gap-2 border border-error/50 bg-error/10
                         px-3 py-2.5 text-xs text-error leading-relaxed"
            >
              <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
              {error}
            </p>
          )}

          {estado === "listo" && resultado && (
            <div className="panel p-4 space-y-3">
              <p className="flex items-center gap-2 text-xs text-ok">
                <IconoOk tam={14} />
                <span className="etiqueta text-ok">
                  {resultado.caracteres} CARACTERES
                </span>
              </p>

              <p className="whitespace-pre-wrap text-sm leading-relaxed text-tinta">
                {resultado.texto}
              </p>

              <button
                type="button"
                onClick={() => void navigator.clipboard.writeText(resultado.texto)}
                className="inline-flex items-center gap-2 border border-linea px-3 py-2
                           text-xs uppercase tracking-wider text-tinta-tenue
                           hover:border-acento hover:text-tinta transition-colors duration-300"
              >
                Copiar texto
              </button>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
