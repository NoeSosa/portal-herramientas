import type { ReactNode } from "react";

import { IconoHerramientas, IconoVolver } from "./Iconos";

/**
 * Marco de la aplicación: cabecera fija + contenido.
 *
 * La cabecera usa la tipografía display y una regla inferior marcada, en
 * lugar de sombras: la profundidad se expresa con bordes, no con blur.
 */
export function Layout({
  children,
  alVolver,
  titulo,
}: {
  children: ReactNode;
  alVolver?: (() => void) | undefined;
  titulo?: string | undefined;
}) {
  return (
    <div className="min-h-dvh flex flex-col">
      <header className="border-b border-linea bg-panel sticky top-0 z-10">
        <div className="mx-auto max-w-6xl px-5 sm:px-8 h-16 flex items-center gap-4">
          <IconoHerramientas tam={22} clase="text-acento shrink-0" />

          <div className="flex items-baseline gap-3 min-w-0">
            <h1 className="text-sm font-normal tracking-tight truncate">
              Portal de Herramientas
            </h1>
            {titulo && (
              <>
                <span aria-hidden="true" className="text-linea-fuerte">
                  /
                </span>
                <span className="etiqueta truncate">{titulo}</span>
              </>
            )}
          </div>

          {alVolver && (
            <button
              type="button"
              onClick={alVolver}
              className="ml-auto inline-flex items-center gap-2 rounded-control border border-linea
                         px-3 py-1.5 text-xs uppercase tracking-wider text-tinta-tenue
                         hover:border-acento hover:text-tinta transition-colors duration-300"
            >
              <IconoVolver tam={14} />
              Volver
            </button>
          )}
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-6xl px-5 sm:px-8 py-10">
        {children}
      </main>

      <footer className="border-t border-linea bg-panel">
        <div className="mx-auto max-w-6xl px-5 sm:px-8 h-12 flex items-center gap-3">
          <span className="etiqueta">
            Acceso restringido · loopback únicamente
          </span>
          <span aria-hidden="true" className="ml-auto etiqueta">
            v0.1.0
          </span>
        </div>
      </footer>
    </div>
  );
}
