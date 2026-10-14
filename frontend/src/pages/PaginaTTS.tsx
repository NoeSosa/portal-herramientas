import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoDescarga, IconoOk } from "../components/Iconos";
import { pedir } from "../lib/api";

interface RespuestaTTS {
  archivo: string;
  nombre_descarga: string;
  caracteres: number;
}

interface EstadoMotor {
  disponible: boolean;
  motivo: string;
  voces: string[];
}

type Estado = "inactivo" | "generando" | "listo" | "error";

const MAX_CARACTERES = 5000;

/**
 * Herramienta de texto a voz.
 *
 * Layout en dos columnas en pantallas anchas: formulario a la izquierda,
 * resultado a la derecha. Los controles siguen el patrón de panel etiquetado
 * de la dirección Industrial/Utilitarian.
 */
export function PaginaTTS() {
  const [texto, setTexto] = useState("");
  const [voz, setVoz] = useState("ald");
  const [velocidad, setVelocidad] = useState(1.0);
  const [estado, setEstado] = useState<Estado>("inactivo");
  const [error, setError] = useState<string | null>(null);
  const [audio, setAudio] = useState<RespuestaTTS | null>(null);
  const [motor, setMotor] = useState<EstadoMotor | null>(null);

  // Comprueba el motor al entrar: si falta el entorno montado, es mejor
  // avisar antes de que el usuario escriba un texto y pierda el tiempo.
  useEffect(() => {
    let vigente = true;
    pedir<EstadoMotor>("/tts/estado")
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

  const restantes = MAX_CARACTERES - texto.length;
  const puedeEnviar =
    texto.trim().length > 0 && restantes >= 0 && estado !== "generando";

  const generar = useCallback(async () => {
    setEstado("generando");
    setError(null);
    setAudio(null);

    try {
      const resultado = await pedir<RespuestaTTS>("/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto, voz, velocidad }),
      });
      setAudio(resultado);
      setEstado("listo");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
      setEstado("error");
    }
  }, [texto, voz, velocidad]);

  return (
    <section aria-labelledby="titulo-tts">
      <div className="regla">
        <span className="etiqueta">Síntesis de voz</span>
        <span className="etiqueta ml-auto">
          {motor?.disponible === false
            ? "MOTOR NO DISPONIBLE"
            : "PIPER · LOCAL · SIN CUOTA"}
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
            El motor de voz no está disponible en este despliegue.
            {motor.motivo && <> {motor.motivo}</>}
          </span>
        </p>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_20rem]">
        {/* --- Formulario --- */}
        <form
          className="panel p-6 space-y-6"
          onSubmit={(e) => {
            e.preventDefault();
            void generar();
          }}
        >
          <div>
            <label
              htmlFor="texto"
              className="etiqueta block mb-2"
            >
              Texto de entrada
            </label>
            <textarea
              id="texto"
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              rows={10}
              maxLength={MAX_CARACTERES}
              placeholder="Escribe aquí lo que quieras escuchar…"
              className="w-full rounded-control border border-linea bg-fondo px-3 py-2.5
                         font-mono text-sm leading-relaxed text-tinta
                         placeholder:text-tinta-apagada focus:border-acento resize-y"
            />
            <p
              className={`mt-2 etiqueta ${restantes < 0 ? "text-error" : ""}`}
            >
              {restantes} restantes
            </p>
          </div>

          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <label htmlFor="voz" className="etiqueta block mb-2">
                Voz
              </label>
              <select
                id="voz"
                value={voz}
                onChange={(e) => setVoz(e.target.value)}
                className="w-full rounded-control border border-linea bg-fondo px-3 py-2
                           text-sm text-tinta focus:border-acento"
              >
                <option value="ald">ALD — neutra</option>
                <option value="claude">CLAUDE — expresiva</option>
              </select>
            </div>

            <div>
              <label htmlFor="velocidad" className="etiqueta block mb-2">
                Velocidad · {velocidad.toFixed(2)}×
              </label>
              <input
                id="velocidad"
                type="range"
                min={0.5}
                max={2}
                step={0.05}
                value={velocidad}
                onChange={(e) => setVelocidad(Number(e.target.value))}
                className="w-full h-8 accent-acento"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={!puedeEnviar}
            className="w-full sm:w-auto rounded-control border border-acento bg-acento
                       px-6 py-2.5 text-xs uppercase tracking-wider text-fondo font-medium
                       hover:bg-acento/90 disabled:opacity-30 disabled:cursor-not-allowed
                       transition-opacity duration-300"
          >
            {estado === "generando" ? "Generando…" : "Generar audio"}
          </button>
        </form>

        {/* --- Resultado --- */}
        <aside aria-live="polite" className="space-y-4">
          <div className="regla">
            <span className="etiqueta">Resultado</span>
          </div>

          {estado === "inactivo" && (
            <p className="text-xs text-tinta-apagada leading-relaxed">
              El audio generado aparecerá aquí.
            </p>
          )}

          {estado === "generando" && (
            <p className="text-xs text-tinta-tenue">Procesando…</p>
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

          {estado === "listo" && audio && (
            <div className="panel p-4 space-y-4">
              <p className="flex items-center gap-2 text-xs text-ok">
                <IconoOk tam={14} />
                <span className="etiqueta text-ok">
                  {audio.caracteres} caracteres procesados
                </span>
              </p>

              {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
              <audio controls src={`/api/tts/descargar/${audio.archivo}`} className="w-full">
                Tu navegador no soporta reproducción de audio.
              </audio>

              <a
                href={`/api/tts/descargar/${audio.archivo}`}
                download={audio.nombre_descarga}
                className="inline-flex items-center gap-2 border border-linea px-3 py-2
                           text-xs uppercase tracking-wider text-tinta-tenue
                           hover:border-acento hover:text-tinta transition-colors duration-300"
              >
                <IconoDescarga tam={14} />
                Descargar WAV
              </a>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
