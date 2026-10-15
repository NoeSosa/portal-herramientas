import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "../Iconos";
import {
  type Presupuesto,
  type Recordatorio,
  type ResultadoOperacion,
  borrarPresupuesto,
  borrarRecordatorio,
  guardarPresupuesto,
  guardarRecordatorio,
  leerPresupuestos,
  leerRecordatorios,
} from "../../lib/ledger";

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

type Aviso = { texto: string; ok: boolean } | null;

/**
 * Pestaña de presupuestos y recordatorios.
 *
 * Van juntos porque son el mismo tipo de dato: límites y pagos esperados, ambos
 * con una cantidad y una periodicidad. Separarlos en dos pestañas obligaría a
 * saltar entre ellas para revisar el mes.
 */
export function TabPresupuestos({ alCambiar }: { alCambiar: () => void }) {
  const [presupuestos, setPresupuestos] = useState<Record<string, Presupuesto>>({});
  const [recordatorios, setRecordatorios] = useState<Record<string, Recordatorio>>({});
  const [cargando, setCargando] = useState(true);
  const [aviso, setAviso] = useState<Aviso>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);

  // Edición de presupuesto
  const [editandoPres, setEditandoPres] = useState<string | null>(null);
  const [limite, setLimite] = useState<number | null>(null);
  const [confirmandoPres, setConfirmandoPres] = useState<string | null>(null);
  const [nuevoPres, setNuevoPres] = useState({ categoria: "", limite: 0 });
  const [creandoPres, setCreandoPres] = useState(false);

  // Edición de recordatorio
  const [editandoRec, setEditandoRec] = useState<string | null>(null);
  const [rec, setRec] = useState<Recordatorio>({ monto: 0, dia: 1, nota: "" });
  const [confirmandoRec, setConfirmandoRec] = useState<string | null>(null);
  const [nuevoRec, setNuevoRec] = useState({ nombre: "", monto: 0, dia: 1, nota: "" });
  const [creandoRec, setCreandoRec] = useState(false);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const [p, r] = await Promise.all([leerPresupuestos(), leerRecordatorios()]);
      setPresupuestos(p.presupuesto);
      setRecordatorios(r.recordatorios);
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
    return <p className="text-xs text-tinta-apagada">Leyendo configuración…</p>;
  }

  return (
    <div className="space-y-10">
      {aviso && (
        <p className="flex items-center gap-2 border border-ok/40 bg-ok/10 px-3 py-2 text-xs text-ok">
          <IconoOk tam={14} />
          {aviso.texto}
        </p>
      )}

      {error && (
        <p role="alert" className="flex items-start gap-2 border border-error/50 bg-error/10 px-3 py-2.5 text-xs text-error">
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          {error}
        </p>
      )}

      {/* --- Presupuestos --- */}
      <section>
        <div className="regla">
          <span className="etiqueta">Presupuestos por categoría</span>
          <span className="etiqueta ml-auto">{Object.keys(presupuestos).length}</span>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={() => setCreandoPres((v) => !v)}
            className="rounded-control border border-acento bg-acento px-4 py-2 text-xs uppercase tracking-wider text-fondo font-medium hover:bg-acento/90"
          >
            {creandoPres ? "Cancelar" : "Nuevo presupuesto"}
          </button>
        </div>

        {creandoPres && (
          <form
            className="panel mt-4 p-4 grid gap-3 sm:grid-cols-[1fr_10rem_auto] items-end"
            onSubmit={(e) => {
              e.preventDefault();
              void ejecutar("nuevo-pres", async () => {
                const r = await guardarPresupuesto(nuevoPres.categoria, nuevoPres.limite);
                setNuevoPres({ categoria: "", limite: 0 });
                setCreandoPres(false);
                return r;
              });
            }}
          >
            <label>
              <span className="etiqueta block mb-1.5">Categoría</span>
              <input
                required
                value={nuevoPres.categoria}
                onChange={(e) => setNuevoPres({ ...nuevoPres, categoria: e.target.value })}
                placeholder="comida"
                className="w-full rounded-control border border-linea bg-fondo px-2 py-2 font-mono text-xs text-tinta focus:border-acento"
              />
            </label>
            <label>
              <span className="etiqueta block mb-1.5">Límite mensual</span>
              <input
                type="number"
                step="0.01"
                min="0"
                required
                value={nuevoPres.limite}
                onChange={(e) => setNuevoPres({ ...nuevoPres, limite: Number(e.target.value) })}
                className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
              />
            </label>
            <button type="submit" disabled={ocupado === "nuevo-pres"} className="rounded-control border border-ok bg-ok/15 px-4 py-2 text-xs uppercase tracking-wider text-ok font-medium hover:bg-ok/25 disabled:opacity-30">
              Guardar
            </button>
          </form>
        )}

        {Object.keys(presupuestos).length === 0 ? (
          <p className="mt-4 text-xs text-tinta-apagada">Sin presupuestos definidos.</p>
        ) : (
          <ul className="mt-4 divide-y divide-linea">
            {Object.entries(presupuestos).map(([categoria, valor]) => {
              const esEdicion = editandoPres === categoria;
              const enVuelo = ocupado === categoria;

              return (
                <li key={categoria} className={`flex items-center justify-between gap-4 py-2.5 ${enVuelo ? "opacity-50" : ""}`}>
                  <span className="text-xs capitalize text-tinta">
                    {categoria.replace(/_/g, " ")}
                  </span>

                  {esEdicion ? (
                    <span className="flex items-center gap-2">
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={limite ?? ""}
                        onChange={(e) => setLimite(e.target.value ? Number(e.target.value) : null)}
                        className="w-28 rounded-control border border-linea bg-fondo px-2 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                      />
                      <button
                        type="button"
                        disabled={enVuelo || limite === null}
                        onClick={() =>
                          void ejecutar(categoria, async () => {
                            const r = await guardarPresupuesto(categoria, limite ?? 0);
                            setEditandoPres(null);
                            return r;
                          })
                        }
                        className="text-ok hover:underline disabled:opacity-30"
                      >
                        Guardar
                      </button>
                      <button type="button" onClick={() => setEditandoPres(null)} className="text-tinta-apagada hover:text-tinta">
                        Cancelar
                      </button>
                    </span>
                  ) : confirmandoPres === categoria ? (
                    <span className="flex items-center gap-3">
                      <span className="etiqueta text-error">¿BORRAR?</span>
                      <button
                        type="button"
                        onClick={() =>
                          void ejecutar(categoria, async () => {
                            const r = await borrarPresupuesto(categoria);
                            setConfirmandoPres(null);
                            return r;
                          })
                        }
                        className="text-error hover:underline"
                      >
                        Sí
                      </button>
                      <button type="button" onClick={() => setConfirmandoPres(null)} className="text-tinta-apagada hover:text-tinta">
                        No
                      </button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-4">
                      <span className="font-mono text-xs text-tinta">
                        {MONEDA.format(valor.limite)}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          setEditandoPres(categoria);
                          setLimite(valor.limite);
                        }}
                        className="text-tinta-tenue hover:text-acento"
                      >
                        Editar
                      </button>
                      <button type="button" onClick={() => setConfirmandoPres(categoria)} className="text-tinta-apagada hover:text-error">
                        Borrar
                      </button>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* --- Recordatorios --- */}
      <section>
        <div className="regla">
          <span className="etiqueta">Pagos recurrentes</span>
          <span className="etiqueta ml-auto">{Object.keys(recordatorios).length}</span>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={() => setCreandoRec((v) => !v)}
            className="rounded-control border border-acento bg-acento px-4 py-2 text-xs uppercase tracking-wider text-fondo font-medium hover:bg-acento/90"
          >
            {creandoRec ? "Cancelar" : "Nuevo recordatorio"}
          </button>
        </div>

        {creandoRec && (
          <form
            className="panel mt-4 p-4 grid gap-3 sm:grid-cols-[1fr_9rem_6rem_1fr_auto] items-end"
            onSubmit={(e) => {
              e.preventDefault();
              void ejecutar("nuevo-rec", async () => {
                const r = await guardarRecordatorio(nuevoRec.nombre, {
                  monto: nuevoRec.monto,
                  dia: nuevoRec.dia,
                  nota: nuevoRec.nota,
                });
                setNuevoRec({ nombre: "", monto: 0, dia: 1, nota: "" });
                setCreandoRec(false);
                return r;
              });
            }}
          >
            <label>
              <span className="etiqueta block mb-1.5">Nombre</span>
              <input
                required
                value={nuevoRec.nombre}
                onChange={(e) => setNuevoRec({ ...nuevoRec, nombre: e.target.value })}
                placeholder="renta"
                className="w-full rounded-control border border-linea bg-fondo px-2 py-2 font-mono text-xs text-tinta focus:border-acento"
              />
            </label>
            <label>
              <span className="etiqueta block mb-1.5">Monto</span>
              <input
                type="number"
                step="0.01"
                min="0"
                required
                value={nuevoRec.monto}
                onChange={(e) => setNuevoRec({ ...nuevoRec, monto: Number(e.target.value) })}
                className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
              />
            </label>
            <label>
              <span className="etiqueta block mb-1.5">Día</span>
              <input
                type="number"
                min="1"
                max="31"
                required
                value={nuevoRec.dia}
                onChange={(e) => setNuevoRec({ ...nuevoRec, dia: Number(e.target.value) })}
                className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
              />
            </label>
            <label>
              <span className="etiqueta block mb-1.5">Nota</span>
              <input
                value={nuevoRec.nota}
                onChange={(e) => setNuevoRec({ ...nuevoRec, nota: e.target.value })}
                placeholder="opcional"
                className="w-full rounded-control border border-linea bg-fondo px-2 py-2 font-mono text-xs text-tinta focus:border-acento"
              />
            </label>
            <button type="submit" disabled={ocupado === "nuevo-rec"} className="rounded-control border border-ok bg-ok/15 px-4 py-2 text-xs uppercase tracking-wider text-ok font-medium hover:bg-ok/25 disabled:opacity-30">
              Guardar
            </button>
          </form>
        )}

        {Object.keys(recordatorios).length === 0 ? (
          <p className="mt-4 text-xs text-tinta-apagada">Sin pagos recurrentes.</p>
        ) : (
          <ul className="mt-4 divide-y divide-linea">
            {Object.entries(recordatorios).map(([nombre, valor]) => {
              const esEdicion = editandoRec === nombre;
              const enVuelo = ocupado === nombre;

              return (
                <li key={nombre} className={`flex items-center justify-between gap-4 py-2.5 ${enVuelo ? "opacity-50" : ""}`}>
                  <span className="min-w-0 flex-1">
                    <span className="text-xs capitalize text-tinta block truncate">
                      {nombre.replace(/_/g, " ")}
                    </span>
                    {valor.nota && !esEdicion && (
                      <span className="text-xs text-tinta-apagada block truncate">{valor.nota}</span>
                    )}
                  </span>

                  {esEdicion ? (
                    <span className="flex flex-wrap items-center justify-end gap-2">
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={rec.monto}
                        onChange={(e) => setRec({ ...rec, monto: Number(e.target.value) })}
                        className="w-24 rounded-control border border-linea bg-fondo px-2 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                      />
                      <span className="etiqueta">DÍA</span>
                      <input
                        type="number"
                        min="1"
                        max="31"
                        value={rec.dia}
                        onChange={(e) => setRec({ ...rec, dia: Number(e.target.value) })}
                        className="w-16 rounded-control border border-linea bg-fondo px-2 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                      />
                      <input
                        value={rec.nota ?? ""}
                        onChange={(e) => setRec({ ...rec, nota: e.target.value })}
                        placeholder="nota"
                        className="w-40 rounded-control border border-linea bg-fondo px-2 py-1 font-mono text-xs text-tinta focus:border-acento"
                      />
                      <button
                        type="button"
                        disabled={enVuelo}
                        onClick={() =>
                          void ejecutar(nombre, async () => {
                            const r = await guardarRecordatorio(nombre, rec);
                            setEditandoRec(null);
                            return r;
                          })
                        }
                        className="text-ok hover:underline disabled:opacity-30"
                      >
                        Guardar
                      </button>
                      <button type="button" onClick={() => setEditandoRec(null)} className="text-tinta-apagada hover:text-tinta">
                        Cancelar
                      </button>
                    </span>
                  ) : confirmandoRec === nombre ? (
                    <span className="flex items-center gap-3">
                      <span className="etiqueta text-error">¿BORRAR?</span>
                      <button
                        type="button"
                        onClick={() =>
                          void ejecutar(nombre, async () => {
                            const r = await borrarRecordatorio(nombre);
                            setConfirmandoRec(null);
                            return r;
                          })
                        }
                        className="text-error hover:underline"
                      >
                        Sí
                      </button>
                      <button type="button" onClick={() => setConfirmandoRec(null)} className="text-tinta-apagada hover:text-tinta">
                        No
                      </button>
                    </span>
                  ) : (
                    <span className="flex items-center gap-4 shrink-0">
                      <span className="font-mono text-xs text-tinta">{MONEDA.format(valor.monto)}</span>
                      <span className="etiqueta">DÍA {valor.dia}</span>
                      <button
                        type="button"
                        onClick={() => {
                          setEditandoRec(nombre);
                          setRec({ monto: valor.monto, dia: valor.dia, nota: valor.nota ?? "" });
                        }}
                        className="text-tinta-tenue hover:text-acento"
                      >
                        Editar
                      </button>
                      <button type="button" onClick={() => setConfirmandoRec(nombre)} className="text-tinta-apagada hover:text-error">
                        Borrar
                      </button>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
