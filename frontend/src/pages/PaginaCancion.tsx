import { useCallback, useEffect, useRef, useState } from "react";

import { IconoAviso, IconoCancion, IconoOk } from "../components/Iconos";
import { pedir, subirArchivos } from "../lib/api";

interface DatosCancion {
  titulo: string | null;
  artista: string | null;
  album: string | null;
  genero: string | null;
  portada: string | null;
  anio: string | null;
  fuente: string | null;
}

interface RespuestaCancion {
  identificacion: DatosCancion;
  bajo_copyright: boolean | null;
  detalle: Record<string, unknown> | null;
}

interface EstadoMotor {
  disponible: boolean;
  motivo: string;
  tipos: string[];
  max_upload_mb: number;
}

type Estado = "inactivo" | "identificando" | "listo" | "sin-coincidencia" | "error";

const ACEPTA = "audio/*,video/mp4,video/quicktime,video/webm";

/**
 * Herramienta «Identificar canción».
 *
 * Pensada para un móvil grabando lo que suena: se elige un audio corto, se
 * espera unos segundos y aparece la ficha. Un solo paso, sin ajustes.
 */
export function PaginaCancion() {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [estado, setEstado] = useState<Estado>("inactivo");
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<RespuestaCancion | null>(null);
  const [motor, setMotor] = useState<EstadoMotor | null>(null);

  const entrada = useRef<HTMLInputElement>(null);
  const arrastrando = useRef(false);
  const [resaltado, setResaltado] = useState(false);

  useEffect(() => {
    let vigente = true;
    pedir<EstadoMotor>("/cancion/estado")
      .then((d) => {
        if (vigente) setMotor(d);
      })
      .catch(() => {
        if (vigente) setMotor(null);
      });
    return () => {
      vigente = false;
    };
  }, []);

  const elegir = useCallback((nuevo: File | null) => {
    setArchivo(nuevo);
    setResultado(null);
    setError(null);
    setEstado("inactivo");
  }, []);

  const identificar = useCallback(async () => {
    if (!archivo) return;

    setEstado("identificando");
    setError(null);
    setResultado(null);

    try {
      const respuesta = await subirArchivos<RespuestaCancion>("/cancion", [archivo]);
      setResultado(respuesta);
      setEstado("listo");
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido.";
      setError(mensaje);
      // 422 lo emite el backend cuando el audio es válido pero no hay
      // coincidencia: no es un fallo, es un resultado que hay que explicar.
      const esSinCoincidencia =
        typeof err === "object" && err !== null && "estado" in err &&
        (err as { estado?: number }).estado === 422;
      setEstado(esSinCoincidencia ? "sin-coincidencia" : "error");
    }
  }, [archivo]);

  const ocupado = estado === "identificando";
  const cancion = resultado?.identificacion;

  return (
    <section aria-labelledby="titulo-cancion">
      <div className="regla">
        <span className="etiqueta">Identificación de audio</span>
        <span className="etiqueta ml-auto">SHAZAM · SIN CLAVE</span>
      </div>

      {motor?.disponible === false && (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          <span>
            El identificador no está disponible en este despliegue.
            {motor.motivo && <> {motor.motivo}</>}
          </span>
        </p>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_20rem]">
        <form
          className="panel p-6 space-y-6"
          onSubmit={(e) => {
            e.preventDefault();
            void identificar();
          }}
        >
          <div>
            <span className="etiqueta block mb-2">Audio o video</span>

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
              <IconoCancion tam={28} clase="mx-auto text-tinta-apagada" />
              <p className="mt-3 text-xs text-tinta-tenue">
                Arrastra un audio o
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
                MP3 · WAV · OGG · M4A · FLAC · MP4 · MÁX{" "}
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
                <span className="etiqueta text-ok">CARGADO</span>
                <span className="truncate">{archivo.name}</span>
              </div>
            )}
          </div>

          <p className="text-xs text-tinta-apagada leading-relaxed">
            Funciona mejor con 10–30 segundos del estribillo, sin voces encima
            ni ruido de fondo. Se acepta la grabación tal cual salga del móvil.
          </p>

          <div className="flex items-center gap-4">
            <button
              type="submit"
              disabled={!archivo || ocupado || motor?.disponible === false}
              className="rounded-control border border-acento bg-acento px-6 py-2.5
                         text-xs uppercase tracking-wider text-fondo font-medium
                         hover:bg-acento/90 disabled:opacity-30 disabled:cursor-not-allowed
                         transition-opacity duration-300"
            >
              {ocupado ? "Escuchando…" : "Identificar"}
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

        <aside aria-live="polite" className="space-y-4">
          <div className="regla">
            <span className="etiqueta">Resultado</span>
          </div>

          {estado === "inactivo" && (
            <p className="text-xs text-tinta-apagada leading-relaxed">
              La ficha aparecerá aquí. El archivo se borra del servidor en
              cuanto termina la identificación.
            </p>
          )}

          {estado === "identificando" && (
            <p className="text-xs text-tinta-tenue">Comparando la huella…</p>
          )}

          {estado === "sin-coincidencia" && (
            <p
              role="alert"
              className="flex items-start gap-2 border border-aviso/50 bg-aviso/10
                         px-3 py-2.5 text-xs text-aviso leading-relaxed"
            >
              <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
              {error}
            </p>
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

          {estado === "listo" && cancion && (
            <div className="panel p-4 space-y-4">
              {cancion.portada && (
                <img
                  src={cancion.portada}
                  alt={`Portada de ${cancion.album ?? cancion.titulo ?? "la canción"}`}
                  className="w-full border border-linea object-cover"
                />
              )}

              <div>
                <p className="text-sm text-tinta">
                  {cancion.titulo ?? "Sin título"}
                </p>
                <p className="mt-1 text-xs text-tinta-tenue">
                  {cancion.artista ?? "Artista desconocido"}
                </p>
              </div>

              <dl className="space-y-1.5">
                {[
                  ["ÁLBUM", cancion.album],
                  ["AÑO", cancion.anio],
                  ["GÉNERO", cancion.genero],
                  ["FUENTE", cancion.fuente],
                ]
                  .filter(([, v]) => Boolean(v))
                  .map(([etiqueta, valor]) => (
                    <div key={etiqueta} className="flex gap-3 text-xs">
                      <dt className="etiqueta w-20 shrink-0">{etiqueta}</dt>
                      <dd className="text-tinta-tenue truncate">{valor}</dd>
                    </div>
                  ))}
              </dl>

              <p className="flex items-center gap-2 text-xs">
                <IconoOk tam={14} clase="text-ok" />
                <span className="etiqueta text-ok">IDENTIFICADA</span>
              </p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
