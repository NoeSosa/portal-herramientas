import type { ReactNode } from "react";

/**
 * Marco de la aplicación: cabecera fija + contenido.
 */
export function Layout({
  children,
  alVolver,
}: {
  children: ReactNode;
  alVolver?: () => void;
}) {
  return (
    <div className="min-h-dvh flex flex-col">
      <header className="border-b border-borde bg-superficie/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="mx-auto max-w-5xl px-4 sm:px-6 h-14 flex items-center gap-3">
          <span aria-hidden="true" className="text-lg">
            🧰
          </span>
          <h1 className="text-sm font-semibold tracking-tight">
            Portal de Herramientas
          </h1>
          {alVolver && (
            <button
              type="button"
              onClick={alVolver}
              className="ml-auto text-sm text-texto-tenue hover:text-texto transition-colors rounded-control px-2 py-1"
            >
              ← Volver
            </button>
          )}
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-5xl px-4 sm:px-6 py-8">
        {children}
      </main>

      <footer className="border-t border-borde py-4">
        <p className="mx-auto max-w-5xl px-4 sm:px-6 text-xs text-texto-tenue">
          Acceso restringido · servido solo en loopback
        </p>
      </footer>
    </div>
  );
}
