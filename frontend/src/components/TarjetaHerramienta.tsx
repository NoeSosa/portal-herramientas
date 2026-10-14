import type { NombreIcono, Herramienta } from "../lib/herramientas";
import {
  IconoAviso,
  IconoCancion,
  IconoComprobante,
  IconoFinanzas,
  IconoImagen,
  IconoPdf,
  IconoTranscripcion,
  IconoVoz,
  IconoHerramientas,
} from "./Iconos";

/** Mapa de nombre lógico → componente de icono. */
const ICONOS: Record<NombreIcono, (p: { tam?: number; clase?: string }) => React.ReactElement> = {
  voz: IconoVoz,
  imagen: IconoImagen,
  transcripcion: IconoTranscripcion,
  cancion: IconoCancion,
  comprobante: IconoComprobante,
  pdf: IconoPdf,
  finanzas: IconoFinanzas,
};

/**
 * Tarjeta de herramienta.
 *
 * Estructura de panel industrial: icono, título, descripción y una etiqueta
 * de datos en monoespaciada. El estado "no disponible" se marca con una
 * etiqueta, no con opacidad, para que siga siendo legible.
 */
export function TarjetaHerramienta({
  herramienta,
  alAbrir,
}: {
  herramienta: Herramienta;
  alAbrir: (id: string) => void;
}) {
  const { id, nombre, descripcion, icono, disponible, etiqueta } = herramienta;
  const Icono = ICONOS[icono] ?? IconoHerramientas;

  return (
    <button
      type="button"
      disabled={!disponible}
      onClick={() => alAbrir(id)}
      aria-label={disponible ? `Abrir ${nombre}` : `${nombre}, próximamente`}
      className={[
        "panel panel-interactivo text-left p-5 h-full",
        "flex flex-col gap-4",
        disponible ? "cursor-pointer" : "cursor-not-allowed",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        {/* Contenedor de tamaño fijo: alinea las líneas de icono entre
            tarjetas aunque los trazos tengan alturas ópticas distintas. */}
        <span className="inline-flex items-center justify-center w-7 h-7 shrink-0">
          <Icono
            tam={26}
            /* En pendientes el icono también se atenúa: el estado debe
               leerse de un vistazo, sin depender solo de la etiqueta. */
            clase={disponible ? "text-acento" : "text-tinta-apagada/50"}
          />
        </span>
        <span className="etiqueta text-right shrink-0 pl-2">{etiqueta}</span>
      </div>

      <div className="flex-1">
        <h2 className="text-sm font-normal text-tinta">{nombre}</h2>
        {/* Altura mínima: alinea las etiquetas de estado entre tarjetas,
            aunque una descripción ocupe una línea y otra ocupe dos. */}
        <p className="mt-2 text-xs leading-relaxed text-tinta-tenue min-h-8">
          {descripcion}
        </p>
      </div>

      {!disponible && (
        <span className="inline-flex items-center gap-1.5 etiqueta">
          <IconoAviso tam={12} />
          Próximamente
        </span>
      )}
    </button>
  );
}
