import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "./Iconos";
import { pedir } from "../lib/api";

interface Movimiento {
  id: string;
  fecha: string | null;
  tipo: string | null;
  categoria: string | null;
  monto: number | null;
  nota: string | null;
  cuenta: string | null;
}

interface ListaMovimientos {
  total_transacciones: number;
  total_ingresos: number;
  total_gastos: number;
  balance: number;
  transacciones: Movimiento[];
}

interface ResultadoOperacion {
  ok: boolean;
  mensaje: string;
}

interface EstadoFinanzas {
  disponible: boolean;
  motivo: string;
  usuario: string;
  tipos_movimiento: string[];
}

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

const TIPOS = ["ingreso", "gasto"] as const;

/** Formulario vacío para crear un movimiento. */
function movimientoVacio(): Omit<Movimiento, "id"> {
  return {
    fecha: new Date().toISOString().slice(0, 10),
    tipo: "gasto",
    categoria: "",
    monto: null,
    nota: "",
    cuenta: null,
  };
}

/**
 * Editor del ledger.
 *
 * Tabla densa con edición en línea: se pulsa una fila, se corrige el campo y se
 * guarda. Todo lo que escribe el usuario pasa por el mismo camino, así que la
 * fila se marca mientras está en vuelo y el resultado se anuncia en el aviso
 * superior.
 *
 * El borrado es la única acción irreversible: pide confirmación en la propia
 * fila, con el concepto a la vista, en vez de un diálogo que tape el contexto.
 */
export function SeccionMovimientos() {
  const [lista, setLista] = useState<ListaMovimientos | null>(null);
  const [motor, setMotor] = useState<EstadoFinanzas | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);

  // Filtros
  const [buscar, setBuscar] = useState("");
  const [categoria, setCategoria] = useState("");
  const [tipo, setTipo] = useState("");
  const [limite, setLimite] = useState(100);

  // Estado de edición: una sola fila a la vez.
  const [editando, setEditando] = useState<string | null>(null);
  const [borrador, setBorrador] = useState<Movimiento | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [confirmandoBorrado, setConfirmandoBorrado] = useState<string | null>(null);

  // Alta de movimiento
  const [creando, setCreando] = useState(false);
  const [nuevo, setNuevo] = useState(movimientoVacio());

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);

    const parametros = new URLSearchParams();
    if (buscar.trim()) parametros.set("buscar", buscar.trim());
    if (categoria.trim()) parametros.set("categoria", categoria.trim());
    if (tipo) parametros.set("tipo", tipo);
    parametros.set("limite", String(limite));

    try {
      const datos = await pedir<ListaMovimientos>(
        `/finanzas/movimientos?${parametros.toString()}`,
      );
      setLista(datos);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
    } finally {
      setCargando(false);
    }
  }, [buscar, categoria, tipo, limite]);

  useEffect(() => {
    let vigente = true;
    pedir<EstadoFinanzas>("/finanzas/estado")
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

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // El aviso es efímero: no tiene sentido dejarlo pegado mientras se trabaja.
  useEffect(() => {
    if (!aviso) return;
    const t = window.setTimeout(() => setAviso(null), 6000);
    return () => window.clearTimeout(t);
  }, [aviso]);

  const abrirEdicion = (movimiento: Movimiento) => {
    setEditando(movimiento.id);
    setBorrador({ ...movimiento });
    setConfirmandoBorrado(null);
    setError(null);
  };

  const cerrarEdicion = () => {
    setEditando(null);
    setBorrador(null);
  };

  const guardar = useCallback(async () => {
    if (!borrador) return;

    setOcupado(borrador.id);
    setError(null);

    try {
      const resultado = await pedir<ResultadoOperacion>(
        `/finanzas/movimientos/${borrador.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            monto: borrador.monto,
            categoria: borrador.categoria,
            nota: borrador.nota ?? "",
            fecha: borrador.fecha,
          }),
        },
      );
      setAviso(resultado.mensaje);
      cerrarEdicion();
      await cargar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setOcupado(null);
    }
  }, [borrador, cargar]);

  const crear = useCallback(async () => {
    setOcupado("nuevo");
    setError(null);

    try {
      const resultado = await pedir<ResultadoOperacion>("/finanzas/movimientos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          monto: nuevo.monto,
          tipo: nuevo.tipo,
          categoria: nuevo.categoria,
          nota: nuevo.nota ?? "",
          fecha: nuevo.fecha,
        }),
      });
      setAviso(resultado.mensaje);
      setNuevo(movimientoVacio());
      setCreando(false);
      await cargar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear.");
    } finally {
      setOcupado(null);
    }
  }, [nuevo, cargar]);

  const eliminar = useCallback(
    async (id: string) => {
      setOcupado(id);
      setError(null);

      try {
        const resultado = await pedir<ResultadoOperacion>(
          `/finanzas/movimientos/${id}?confirmar=true`,
          { method: "DELETE" },
        );
        setAviso(resultado.mensaje);
        setConfirmandoBorrado(null);
        await cargar();
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo eliminar.");
      } finally {
        setOcupado(null);
      }
    },
    [cargar],
  );

  return (
    <div className="mt-8">
      <div className="regla">
        <span className="etiqueta">Movimientos</span>
        <span className="etiqueta ml-auto">
          {lista ? `${lista.total_transacciones} registros` : "…"}
        </span>
      </div>

      {aviso && (
        <p className="mt-4 flex items-center gap-2 border border-ok/40 bg-ok/10 px-3 py-2 text-xs text-ok">
          <IconoOk tam={14} />
          {aviso}
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="mt-4 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          {error}
        </p>
      )}

      {/* --- Filtros y alta --- */}
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="flex-1 min-w-40">
          <span className="etiqueta block mb-1.5">Buscar</span>
          <input
            value={buscar}
            onChange={(e) => setBuscar(e.target.value)}
            placeholder="concepto o categoría"
            className="w-full rounded-control border border-linea bg-fondo px-3 py-2
                       font-mono text-xs text-tinta placeholder:text-tinta-apagada
                       focus:border-acento"
          />
        </label>

        <label className="w-32">
          <span className="etiqueta block mb-1.5">Categoría</span>
          <input
            value={categoria}
            onChange={(e) => setCategoria(e.target.value)}
            placeholder="todas"
            className="w-full rounded-control border border-linea bg-fondo px-3 py-2
                       font-mono text-xs text-tinta placeholder:text-tinta-apagada
                       focus:border-acento"
          />
        </label>

        <label className="w-28">
          <span className="etiqueta block mb-1.5">Tipo</span>
          <select
            value={tipo}
            onChange={(e) => setTipo(e.target.value)}
            className="w-full rounded-control border border-linea bg-fondo px-3 py-2
                       text-xs text-tinta focus:border-acento"
          >
            <option value="">Todos</option>
            {TIPOS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        <label className="w-24">
          <span className="etiqueta block mb-1.5">Máximo</span>
          <input
            type="number"
            min={1}
            max={500}
            value={limite}
            onChange={(e) => setLimite(Number(e.target.value) || 100)}
            className="w-full rounded-control border border-linea bg-fondo px-3 py-2
                       font-mono text-xs text-tinta focus:border-acento"
          />
        </label>

        <button
          type="button"
          onClick={() => setCreando((v) => !v)}
          disabled={motor?.disponible === false}
          className="rounded-control border border-acento bg-acento px-4 py-2
                     text-xs uppercase tracking-wider text-fondo font-medium
                     hover:bg-acento/90 disabled:opacity-30 transition-opacity duration-300"
        >
          {creando ? "Cancelar" : "Nuevo movimiento"}
        </button>
      </div>

      {/* --- Alta --- */}
      {creando && (
        <form
          className="panel mt-4 p-4 grid gap-3 sm:grid-cols-[7rem_7rem_1fr_7rem_auto] items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void crear();
          }}
        >
          <label>
            <span className="etiqueta block mb-1.5">Fecha</span>
            <input
              type="date"
              required
              value={nuevo.fecha ?? ""}
              onChange={(e) => setNuevo({ ...nuevo, fecha: e.target.value })}
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2
                         font-mono text-xs text-tinta focus:border-acento"
            />
          </label>

          <label>
            <span className="etiqueta block mb-1.5">Tipo</span>
            <select
              value={nuevo.tipo ?? "gasto"}
              onChange={(e) => setNuevo({ ...nuevo, tipo: e.target.value })}
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2
                         text-xs text-tinta focus:border-acento"
            >
              {TIPOS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>

          <label>
            <span className="etiqueta block mb-1.5">Categoría y concepto</span>
            <input
              required
              value={nuevo.categoria ?? ""}
              onChange={(e) => setNuevo({ ...nuevo, categoria: e.target.value })}
              placeholder="comida"
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2
                         font-mono text-xs text-tinta placeholder:text-tinta-apagada
                         focus:border-acento"
            />
          </label>

          <label>
            <span className="etiqueta block mb-1.5">Monto</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              required
              value={nuevo.monto ?? ""}
              onChange={(e) =>
                setNuevo({ ...nuevo, monto: e.target.value ? Number(e.target.value) : null })
              }
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2
                         font-mono text-xs text-tinta focus:border-acento"
            />
          </label>

          <button
            type="submit"
            disabled={ocupado === "nuevo"}
            className="rounded-control border border-ok bg-ok/15 px-4 py-2
                       text-xs uppercase tracking-wider text-ok font-medium
                       hover:bg-ok/25 disabled:opacity-30 transition-colors duration-300"
          >
            {ocupado === "nuevo" ? "Guardando…" : "Guardar"}
          </button>

          <label className="sm:col-span-5">
            <span className="etiqueta block mb-1.5">Nota</span>
            <input
              value={nuevo.nota ?? ""}
              onChange={(e) => setNuevo({ ...nuevo, nota: e.target.value })}
              placeholder="opcional"
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2
                         font-mono text-xs text-tinta placeholder:text-tinta-apagada
                         focus:border-acento"
            />
          </label>
        </form>
      )}

      {/* --- Tabla --- */}
      <div className="mt-4 overflow-x-auto">
        {cargando && !lista ? (
          <p className="text-xs text-tinta-apagada">Leyendo el ledger…</p>
        ) : !lista || lista.transacciones.length === 0 ? (
          <p className="text-xs text-tinta-apagada">
            No hay movimientos que cumplan el filtro.
          </p>
        ) : (
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-linea-fuerte">
                <th className="etiqueta py-2 pr-3 text-left">Fecha</th>
                <th className="etiqueta py-2 pr-3 text-left">Tipo</th>
                <th className="etiqueta py-2 pr-3 text-left">Categoría</th>
                <th className="etiqueta py-2 pr-3 text-right">Monto</th>
                <th className="etiqueta py-2 pr-3 text-left">Nota</th>
                <th className="etiqueta py-2 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {lista.transacciones.map((movimiento) => {
                const esEdicion = editando === movimiento.id;
                const enVuelo = ocupado === movimiento.id;
                const esGasto = movimiento.tipo === "gasto";

                return (
                  <tr
                    key={movimiento.id}
                    className={[
                      "border-b border-linea transition-colors duration-300",
                      esEdicion ? "bg-panel-alto" : "hover:bg-panel-alto/50",
                      enVuelo ? "opacity-50" : "",
                    ].join(" ")}
                  >
                    <td className="py-2 pr-3">
                      {esEdicion ? (
                        <input
                          type="date"
                          value={borrador?.fecha ?? ""}
                          onChange={(e) =>
                            setBorrador((b) => (b ? { ...b, fecha: e.target.value } : b))
                          }
                          className="w-full rounded-control border border-linea bg-fondo px-2 py-1
                                     font-mono text-xs text-tinta focus:border-acento"
                        />
                      ) : (
                        <span className="font-mono text-tinta-tenue">
                          {movimiento.fecha ?? "—"}
                        </span>
                      )}
                    </td>

                    <td className="py-2 pr-3">
                      <span
                        className={`etiqueta ${esGasto ? "text-error" : "text-ok"}`}
                      >
                        {movimiento.tipo}
                      </span>
                    </td>

                    <td className="py-2 pr-3">
                      {esEdicion ? (
                        <input
                          value={borrador?.categoria ?? ""}
                          onChange={(e) =>
                            setBorrador((b) =>
                              b ? { ...b, categoria: e.target.value } : b,
                            )
                          }
                          className="w-full rounded-control border border-linea bg-fondo px-2 py-1
                                     font-mono text-xs text-tinta focus:border-acento"
                        />
                      ) : (
                        <span className="capitalize text-tinta">
                          {movimiento.categoria ?? "—"}
                        </span>
                      )}
                    </td>

                    <td className="py-2 pr-3 text-right">
                      {esEdicion ? (
                        <input
                          type="number"
                          step="0.01"
                          value={borrador?.monto ?? ""}
                          onChange={(e) =>
                            setBorrador((b) =>
                              b
                                ? {
                                    ...b,
                                    monto: e.target.value
                                      ? Number(e.target.value)
                                      : null,
                                  }
                                : b,
                            )
                          }
                          className="w-28 rounded-control border border-linea bg-fondo px-2 py-1
                                     text-right font-mono text-xs text-tinta focus:border-acento"
                        />
                      ) : (
                        <span
                          className={`font-mono ${esGasto ? "text-error" : "text-ok"}`}
                        >
                          {esGasto ? "−" : "+"}
                          {MONEDA.format(movimiento.monto ?? 0)}
                        </span>
                      )}
                    </td>

                    <td className="py-2 pr-3 max-w-64">
                      {esEdicion ? (
                        <input
                          value={borrador?.nota ?? ""}
                          onChange={(e) =>
                            setBorrador((b) => (b ? { ...b, nota: e.target.value } : b))
                          }
                          className="w-full rounded-control border border-linea bg-fondo px-2 py-1
                                     font-mono text-xs text-tinta focus:border-acento"
                        />
                      ) : (
                        <span className="text-tinta-tenue line-clamp-1">
                          {movimiento.nota || "—"}
                        </span>
                      )}
                    </td>

                    <td className="py-2 text-right whitespace-nowrap">
                      {esEdicion ? (
                        <span className="flex justify-end gap-3">
                          <button
                            type="button"
                            onClick={() => void guardar()}
                            disabled={enVuelo}
                            className="text-ok hover:underline disabled:opacity-30"
                          >
                            {enVuelo ? "Guardando…" : "Guardar"}
                          </button>
                          <button
                            type="button"
                            onClick={cerrarEdicion}
                            className="text-tinta-apagada hover:text-tinta"
                          >
                            Cancelar
                          </button>
                        </span>
                      ) : confirmandoBorrado === movimiento.id ? (
                        /* Confirmación dentro de la fila: el concepto queda a la
                           vista, así no se borra a ciegas. */
                        <span className="flex items-center justify-end gap-3">
                          <span className="etiqueta text-error">¿BORRAR?</span>
                          <button
                            type="button"
                            onClick={() => void eliminar(movimiento.id)}
                            disabled={enVuelo}
                            className="text-error hover:underline disabled:opacity-30"
                          >
                            Sí
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmandoBorrado(null)}
                            className="text-tinta-apagada hover:text-tinta"
                          >
                            No
                          </button>
                        </span>
                      ) : (
                        <span className="flex justify-end gap-3">
                          <button
                            type="button"
                            onClick={() => abrirEdicion(movimiento)}
                            className="text-tinta-tenue hover:text-acento"
                          >
                            Editar
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmandoBorrado(movimiento.id)}
                            className="text-tinta-apagada hover:text-error"
                          >
                            Borrar
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-linea-fuerte">
                <td colSpan={3} className="py-2 etiqueta">
                  Totales del filtro
                </td>
                <td className="py-2 pr-3 text-right font-mono text-xs">
                  <span className="text-ok">+{MONEDA.format(lista.total_ingresos)}</span>
                  <span className="text-tinta-apagada"> · </span>
                  <span className="text-error">−{MONEDA.format(lista.total_gastos)}</span>
                </td>
                <td colSpan={2} className="py-2 text-right">
                  <span className="etiqueta mr-2">BALANCE</span>
                  <span
                    className={`font-mono text-xs ${
                      lista.balance < 0 ? "text-error" : "text-ok"
                    }`}
                  >
                    {MONEDA.format(lista.balance)}
                  </span>
                </td>
              </tr>
            </tfoot>
          </table>
        )}
      </div>

      {lista && lista.transacciones.length > 0 && (
        <p className="mt-3 text-xs text-tinta-apagada">
          Los cambios se guardan en el ledger y quedan visibles para el agente.
        </p>
      )}
    </div>
  );
}
