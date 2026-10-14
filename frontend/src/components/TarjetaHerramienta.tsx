import type { Herramienta } from "../lib/herramientas";

/**
 * Tarjeta de herramienta para la portada.
 */
export function TarjetaHerramienta({
  herramienta,
  alAbrir,
}: {
  herramienta: Herramienta;
  alAbrir: (id: string) => void;
}) {
  const { id, nombre, descripcion, icono, disponible } = herramienta;

  return (
    <button
      type="button"
      disabled={!disponible}
      onClick={() => alAbrir(id)}
      aria-label={
        disponible ? `Abrir ${nombre}` : `${nombre} — próximamente`
      }
      className={[
        "group text-left rounded-panel border border-borde bg-superficie p-5",
        "transition-all duration-150",
        disponible
          ? "hover:border-acento hover:bg-superficie-alta cursor-pointer"
          : "opacity-50 cursor-not-allowed",
      ].join(" ")}
    >
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="text-2xl leading-none">
          {icono}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-medium text-sm flex items-center gap-2">
            {nombre}
            {!disponible && (
              <span className="text-[0.65rem] uppercase tracking-wide text-texto-tenue border border-borde rounded px-1.5 py-0.5">
                Pronto
              </span>
            )}
          </h2>
          <p className="mt-1 text-xs text-texto-tenue leading-relaxed">
            {descripcion}
          </p>
        </div>
      </div>
    </button>
  );
}
