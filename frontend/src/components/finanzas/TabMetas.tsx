import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "../Iconos";
import {
  type Meta,
  type ResultadoOperacion,
  borrarMeta,
  guardarMeta,
  leerMetas,
} from "../../lib/ledger";

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

type Aviso = { texto: string; ok: boolean } | null;

/** Meta vacía para el formulario de alta. */
function metaVacia() {
  return { nombre: "", objetivo: 0, ahorrado: 0, fecha_limite: "" };
}

/**
 * Pestaña de metas de ahorro.
 *
 * Cada meta muestra su avance como barra, porque la pregunta real es «¿qué tan
 * cerca estoy?», no «¿cuánto falta?». La fecha límite es opcional.
 */
export function TabMetas({ alCambiar }: { alCambiar: () => void }) {
  const [metas, setMetas] = useState<Record<string, Meta>>({});
  const [cargando, setCargando] = useState(true);
  const [aviso, setAviso] = useState<Aviso>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);

  const [editando, setEditando] = useState<string | null>(null);
  const [borrador, setBorrador] = useState(metaVacia());
  const [confirmando, setConfirmando] = useState<string | null>(null);
  const [creando, setCreando] = useState(false);
  const [nueva, setNueva] = useState(metaVacia());

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const datos = await leerMetas();
      setMetas(datos.metas);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const ejecutar = async (clave: string, accion: () => Promise<ResultadoOperacion>) => {
    setOcupado(clave);
    setError(null);
    try {
      const r = await accion();
      setAviso({ texto: r.mensaje, ok: true });
      await cargar();
      alCambiar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "La operación falló.");
    } finally {
      setOcupado(null);
    }
  };

  if (cargando) {
    return <p className="text-xs text-tinta-apagada">Leyendo metas…</p>;
  }

  const campos = <T extends { objetivo: number; ahorrado: number; fecha_limite: string }>(
    valor: T,
    set: (v: T) => void,
  ) => (
    <>
      <label>
        <span className="etiqueta block mb-1.5">Objetivo</span>
        <input
          type="number"
          step="0.01"
          min="0.01"
          required
          value={valor.objetivo}
          onChange={(e) => set({ ...valor, objetivo: Number(e.target.value) })}
          className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
        />
      </label>
      <label>
        <span className="etiqueta block mb-1.5">Ahorrado</span>
        <input
          type="number"
          step="0.01"
          min="0"
          value={valor.ahorrado}
          onChange={(e) => set({ ...valor, ahorrado: Number(e.target.value) })}
          className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
        />
      </label>
      <label>
        <span className="etiqueta block mb-1.5">Fecha límite</span>
        <input
          type="date"
          value={valor.fecha_limite}
          onChange={(e) => set({ ...valor, fecha_limite: e.target.value })}
          className="w-full rounded-control border border-linea bg-fondo px-2 py-2 font-mono text-xs text-tinta focus:border-acento"
        />
      </label>
    </>
  );

  return (
    <div>
      <div className="regla">
        <span className="etiqueta">Metas de ahorro</span>
        <span className="etiqueta ml-auto">{Object.keys(metas).length}</span>
      </div>

      {aviso && (
        <p className="mt-4 flex items-center gap-2 border border-ok/40 bg-ok/10 px-3 py-2 text-xs text-ok">
          <IconoOk tam={14} />
          {aviso.texto}
        </p>
      )}

      {error && (
        <p role="alert" className="mt-4 flex items-start gap-2 border border-error/50 bg-error/10 px-3 py-2.5 text-xs text-error">
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          {error}
        </p>
      )}

      <div className="mt-4">
        <button
          type="button"
          onClick={() => setCreando((v) => !v)}
          className="rounded-control border border-acento bg-acento px-4 py-2 text-xs uppercase tracking-wider text-fondo font-medium hover:bg-acento/90"
        >
          {creando ? "Cancelar" : "Nueva meta"}
        </button>
      </div>

      {creando && (
        <form
          className="panel mt-4 p-4 grid gap-3 sm:grid-cols-[1fr_9rem_9rem_10rem_auto] items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void ejecutar("nueva", async () => {
              const r = await guardarMeta(nueva.nombre, {
                objetivo: nueva.objetivo,
                ahorrado: nueva.ahorrado,
                fecha_limite: nueva.fecha_limite || null,
              });
              setNueva(metaVacia());
              setCreando(false);
              return r;
            });
          }}
        >
          <label>
            <span className="etiqueta block mb-1.5">Nombre</span>
            <input
              required
              value={nueva.nombre}
              onChange={(e) => setNueva({ ...nueva, nombre: e.target.value })}
              placeholder="viaje"
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 font-mono text-xs text-tinta focus:border-acento"
            />
          </label>
          {campos(nueva, setNueva)}
          <button type="submit" disabled={ocupado === "nueva"} className="rounded-control border border-ok bg-ok/15 px-4 py-2 text-xs uppercase tracking-wider text-ok font-medium hover:bg-ok/25 disabled:opacity-30">
            Guardar
          </button>
        </form>
      )}

      {Object.keys(metas).length === 0 ? (
        <p className="mt-4 text-xs text-tinta-apagada">Sin metas definidas.</p>
      ) : (
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          {Object.entries(metas).map(([nombre, meta]) => {
            const esEdicion = editando === nombre;
            const enVuelo = ocupado === nombre;
            const avance =
              meta.objetivo > 0
                ? Math.min(100, Math.round((meta.ahorrado / meta.objetivo) * 100))
                : 0;
            const completa = meta.ahorrado >= meta.objetivo;

            return (
              <div key={nombre} className={`panel p-4 ${enVuelo ? "opacity-50" : ""}`}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-xs capitalize text-tinta">
                    {nombre.replace(/_/g, " ")}
                  </span>
                  <span
                    className={`etiqueta ${completa ? "text-ok" : "text-tinta-apagada"}`}
                  >
                    {avance}%
                  </span>
                </div>

                {esEdicion ? (
                  <form
                    className="mt-3 grid gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void ejecutar(nombre, async () => {
                        const r = await guardarMeta(nombre, {
                          objetivo: borrador.objetivo,
                          ahorrado: borrador.ahorrado,
                          fecha_limite: borrador.fecha_limite || null,
                        });
                        setEditando(null);
                        return r;
                      });
                    }}
                  >
                    <div className="grid gap-2 sm:grid-cols-3">
                      {campos(borrador, setBorrador)}
                    </div>
                    <span className="flex gap-3">
                      <button type="submit" disabled={enVuelo} className="text-ok hover:underline disabled:opacity-30">
                        Guardar
                      </button>
                      <button type="button" onClick={() => setEditando(null)} className="text-tinta-apagada hover:text-tinta">
                        Cancelar
                      </button>
                    </span>
                  </form>
                ) : (
                  <>
                    <p className="mt-2 font-mono text-sm text-tinta">
                      {MONEDA.format(meta.ahorrado)}
                      <span className="text-tinta-apagada">
                        {" / "}
                        {MONEDA.format(meta.objetivo)}
                      </span>
                    </p>

                    <div className="mt-2 h-1 w-full bg-linea">
                      <div
                        className={`h-full ${completa ? "bg-ok" : "bg-acento"}`}
                        style={{ width: `${avance}%` }}
                      />
                    </div>

                    {meta.fecha_limite && (
                      <p className="mt-2 etiqueta">LÍMITE {meta.fecha_limite}</p>
                    )}

                    <div className="mt-3 flex items-center justify-end gap-4">
                      {confirmando === nombre ? (
                        <>
                          <span className="etiqueta text-error">¿BORRAR?</span>
                          <button
                            type="button"
                            onClick={() =>
                              void ejecutar(nombre, async () => {
                                const r = await borrarMeta(nombre);
                                setConfirmando(null);
                                return r;
                              })
                            }
                            className="text-xs text-error hover:underline"
                          >
                            Sí
                          </button>
                          <button type="button" onClick={() => setConfirmando(null)} className="text-xs text-tinta-apagada hover:text-tinta">
                            No
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            onClick={() => {
                              setEditando(nombre);
                              setBorrador({
                                nombre,
                                objetivo: meta.objetivo,
                                ahorrado: meta.ahorrado,
                                fecha_limite: meta.fecha_limite ?? "",
                              });
                            }}
                            className="text-xs text-tinta-tenue hover:text-acento"
                          >
                            Editar
                          </button>
                          <button type="button" onClick={() => setConfirmando(nombre)} className="text-xs text-tinta-apagada hover:text-error">
                            Borrar
                          </button>
                        </>
                      )}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
