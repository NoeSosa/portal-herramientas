import { useCallback, useState } from "react";

import { pedir } from "../lib/api";

interface RespuestaTTS {
  archivo: string;
  nombre_descarga: string;
  caracteres: number;
}

type Estado = "inactivo" | "generando" | "listo" | "error";

const MAX_CARACTERES = 5000;

/**
 * Herramienta de texto a voz.
 */
export function PaginaTTS() {
  const [texto, setTexto] = useState("");
  const [voz, setVoz] = useState("ald");
  const [velocidad, setVelocidad] = useState(1.0);
  const [estado, setEstado] = useState<Estado>("inactivo");
  const [error, setError] = useState<string | null>(null);
  const [audio, setAudio] = useState<RespuestaTTS | null>(null);

  const restantes = MAX_CARACTERES - texto.length;
  const puedeEnviar = texto.trim().length > 0 && restantes >= 0 && estado !== "generando";

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
    <section aria-labelledby="titulo-tts" className="max-w-2xl">
      <h2 id="titulo-tts" className="text-lg font-semibold tracking-tight">
        🔊 Texto a voz
      </h2>
      <p className="mt-1 text-sm text-texto-tenue">
        Voces en español mexicano con Piper. No consume cuota de API.
      </p>

      <form
        className="mt-6 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void generar();
        }}
      >
        <div>
          <label htmlFor="texto" className="block text-sm font-medium mb-1.5">
            Texto
          </label>
          <textarea
            id="texto"
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            rows={6}
            maxLength={MAX_CARACTERES}
            placeholder="Escribe aquí lo que quieras escuchar…"
            className="w-full rounded-control border border-borde bg-superficie px-3 py-2 text-sm
                       placeholder:text-texto-tenue/60 focus:border-acento resize-y"
          />
          <p
            className={`mt-1 text-xs ${restantes < 0 ? "text-error" : "text-texto-tenue"}`}
          >
            {restantes} caracteres restantes
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="voz" className="block text-sm font-medium mb-1.5">
              Voz
            </label>
            <select
              id="voz"
              value={voz}
              onChange={(e) => setVoz(e.target.value)}
              className="w-full rounded-control border border-borde bg-superficie px-3 py-2 text-sm"
            >
              <option value="ald">Ald (neutra)</option>
              <option value="claude">Claude (expresiva)</option>
            </select>
          </div>

          <div>
            <label
              htmlFor="velocidad"
              className="block text-sm font-medium mb-1.5"
            >
              Velocidad: {velocidad.toFixed(2)}×
            </label>
            <input
              id="velocidad"
              type="range"
              min={0.5}
              max={2}
              step={0.05}
              value={velocidad}
              onChange={(e) => setVelocidad(Number(e.target.value))}
              className="w-full accent-acento"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={!puedeEnviar}
          className="rounded-control bg-acento text-fondo px-4 py-2 text-sm font-medium
                     hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed
                     transition-opacity"
        >
          {estado === "generando" ? "Generando…" : "Generar audio"}
        </button>
      </form>

      {estado === "error" && error && (
        <p
          role="alert"
          className="mt-4 text-sm text-error border border-error/40 bg-error/10 rounded-control px-3 py-2"
        >
          {error}
        </p>
      )}

      {estado === "listo" && audio && (
        <div className="mt-6 rounded-panel border border-borde bg-superficie p-4">
          <p className="text-sm text-exito mb-3">
            ✓ Audio generado ({audio.caracteres} caracteres)
          </p>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <audio controls src={`/api/tts/${audio.archivo}`} className="w-full">
            Tu navegador no soporta reproducción de audio.
          </audio>
          <a
            href={`/api/tts/${audio.archivo}`}
            download={audio.nombre_descarga}
            className="mt-3 inline-block text-sm text-acento hover:underline"
          >
            ↓ Descargar WAV
          </a>
        </div>
      )}
    </section>
  );
}
