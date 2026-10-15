import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "../Iconos";
import {
  type Compra,
  type ResultadoOperacion,
  type Tarjeta,
  borrarCompra,
  borrarTarjeta,
  crearCompra,
  duplicarCompra,
  editarCompra,
  guardarTarjeta,
  leerTarjetas,
  pagarTarjeta,
} from "../../lib/ledger";

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

type Aviso = { texto: string; ok: boolean } | null;

/** Formulario de compra, vacío o con los datos de una existente. */
function compraVacia(): { concepto: string; total: number; plazo: number; inicio: string } {
  return {
    concepto: "",
    total: 0,
    plazo: 1,
    inicio: new Date().toISOString().slice(0, 10),
  };
}

/**
 * Pestaña de tarjetas de crédito.
 *
 * Cada tarjeta muestra su límite, corte y pago, y despliega sus compras (MSI o
 * de contado). Todo editable: la tarjeta en sí, cada compra, y el pago del mes.
 *
 * El pago es la operación más delicada: suma un gasto, avanza un mes las compras
 * y descuenta de la cuenta. Va con confirmación.
 */
export function TabTarjetas({ alCambiar }: { alCambiar: () => void }) {
  const [tarjetas, setTarjetas] = useState<Record<string, Tarjeta>>({});
  const [cargando, setCargando] = useState(true);
  const [aviso, setAviso] = useState<Aviso>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [abierta, setAbierta] = useState<string | null>(null);

  // Edición de la tarjeta (límite, corte, pago)
  const [editando, setEditando] = useState<string | null>(null);
  const [cab, setCab] = useState({ limite: 0, corte: 1, pago: 15 });
  const [confirmandoTarjeta, setConfirmandoTarjeta] = useState<string | null>(null);

  // Alta de tarjeta
  const [creando, setCreando] = useState(false);
  const [nueva, setNueva] = useState({ nombre: "", limite: 0, corte: 1, pago: 15 });

  // Compras
  const [creandoCompra, setCreandoCompra] = useState<string | null>(null);
  const [nuevaCompra, setNuevaCompra] = useState(compraVacia());
  const [editandoCompra, setEditandoCompra] = useState<string | null>(null);
  const [borradorCompra, setBorradorCompra] = useState(compraVacia());
  const [confirmandoCompra, setConfirmandoCompra] = useState<string | null>(null);

  // Pago
  const [pagando, setPagando] = useState<string | null>(null);
  const [pago, setPago] = useState({ cuenta: "", monto: "" as string | number });

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const datos = await leerTarjetas();
      setTarjetas(datos.tarjetas);
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
    return <p className="text-xs text-tinta-apagada">Leyendo tarjetas…</p>;
  }

  return (
    <div>
      <div className="regla">
        <span className="etiqueta">Tarjetas de crédito</span>
        <span className="etiqueta ml-auto">{Object.keys(tarjetas).length}</span>
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
          {creando ? "Cancelar" : "Nueva tarjeta"}
        </button>
      </div>

      {creando && (
        <form
          className="panel mt-4 p-4 grid gap-3 sm:grid-cols-[1fr_8rem_6rem_6rem_auto] items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void ejecutar("nueva-tarjeta", async () => {
              const r = await guardarTarjeta(nueva.nombre, {
                limite: nueva.limite,
                corte: nueva.corte,
                pago: nueva.pago,
              });
              setNueva({ nombre: "", limite: 0, corte: 1, pago: 15 });
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
              placeholder="klar"
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 font-mono text-xs text-tinta focus:border-acento"
            />
          </label>
          <label>
            <span className="etiqueta block mb-1.5">Límite</span>
            <input
              type="number"
              step="0.01"
              min="0"
              value={nueva.limite}
              onChange={(e) => setNueva({ ...nueva, limite: Number(e.target.value) })}
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
            />
          </label>
          <label>
            <span className="etiqueta block mb-1.5">Corte</span>
            <input
              type="number"
              min="1"
              max="31"
              value={nueva.corte}
              onChange={(e) => setNueva({ ...nueva, corte: Number(e.target.value) })}
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
            />
          </label>
          <label>
            <span className="etiqueta block mb-1.5">Pago</span>
            <input
              type="number"
              min="1"
              max="31"
              value={nueva.pago}
              onChange={(e) => setNueva({ ...nueva, pago: Number(e.target.value) })}
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
            />
          </label>
          <button type="submit" disabled={ocupado === "nueva-tarjeta"} className="rounded-control border border-ok bg-ok/15 px-4 py-2 text-xs uppercase tracking-wider text-ok font-medium hover:bg-ok/25 disabled:opacity-30">
            Guardar
          </button>
        </form>
      )}

      {Object.keys(tarjetas).length === 0 ? (
        <p className="mt-4 text-xs text-tinta-apagada">Sin tarjetas registradas.</p>
      ) : (
        <div className="mt-5 space-y-4">
          {Object.entries(tarjetas).map(([nombre, tarjeta]) => {
            const compras = tarjeta.compras ?? [];
            const pagoMensual = compras.reduce((s, c) => s + (c.mensualidad ?? 0), 0);
            const esEdicion = editando === nombre;
            const enVuelo = ocupado === nombre;
            const desplegada = abierta === nombre;

            return (
              <div
                key={nombre}
                className={`panel p-4 ${enVuelo ? "opacity-50" : ""}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => setAbierta(desplegada ? null : nombre)}
                    className="flex items-center gap-3 text-left"
                  >
                    <span className="etiqueta">{desplegada ? "▾" : "▸"}</span>
                    <span className="text-sm uppercase tracking-wider text-tinta">
                      {nombre.replace(/_/g, " ")}
                    </span>
                    <span className="etiqueta">
                      {compras.length} COMPRA{compras.length === 1 ? "" : "S"}
                    </span>
                  </button>

                  {esEdicion ? (
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="etiqueta">LÍMITE</span>
                      <input
                        type="number"
                        step="0.01"
                        value={cab.limite}
                        onChange={(e) => setCab({ ...cab, limite: Number(e.target.value) })}
                        className="w-24 rounded-control border border-linea bg-fondo px-2 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                      />
                      <span className="etiqueta">CORTE</span>
                      <input
                        type="number"
                        min="1"
                        max="31"
                        value={cab.corte}
                        onChange={(e) => setCab({ ...cab, corte: Number(e.target.value) })}
                        className="w-14 rounded-control border border-linea bg-fondo px-2 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                      />
                      <span className="etiqueta">PAGO</span>
                      <input
                        type="number"
                        min="1"
                        max="31"
                        value={cab.pago}
                        onChange={(e) => setCab({ ...cab, pago: Number(e.target.value) })}
                        className="w-14 rounded-control border border-linea bg-fondo px-2 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                      />
                      <button
                        type="button"
                        disabled={enVuelo}
                        onClick={() =>
                          void ejecutar(nombre, async () => {
                            const r = await guardarTarjeta(nombre, cab);
                            setEditando(null);
                            return r;
                          })
                        }
                        className="text-ok hover:underline disabled:opacity-30"
                      >
                        Guardar
                      </button>
                      <button type="button" onClick={() => setEditando(null)} className="text-tinta-apagada hover:text-tinta">
                        Cancelar
                      </button>
                    </span>
                  ) : confirmandoTarjeta === nombre ? (
                    <span className="flex items-center gap-3">
                      <span className="etiqueta text-error">
                        ¿BORRAR TARJETA Y {compras.length} COMPRAS?
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          void ejecutar(nombre, async () => {
                            const r = await borrarTarjeta(nombre);
                            setConfirmandoTarjeta(null);
                            return r;
                          })
                        }
                        className="text-error hover:underline"
                      >
                        Sí
                      </button>
                      <button type="button" onClick={() => setConfirmandoTarjeta(null)} className="text-tinta-apagada hover:text-tinta">
                        No
                      </button>
                    </span>
                  ) : (
                    <span className="flex flex-wrap items-center gap-4">
                      <span className="etiqueta">
                        CORTE {tarjeta.corte} · PAGO {tarjeta.pago}
                      </span>
                      <span className="font-mono text-xs text-tinta">
                        {MONEDA.format(pagoMensual)}
                        <span className="etiqueta ml-1">/MES</span>
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          setEditando(nombre);
                          setCab({
                            limite: tarjeta.limite,
                            corte: tarjeta.corte,
                            pago: tarjeta.pago,
                          });
                        }}
                        className="text-tinta-tenue hover:text-acento"
                      >
                        Editar
                      </button>
                      <button
                        type="button"
                        onClick={() => setPagando(pagando === nombre ? null : nombre)}
                        className="text-acento hover:underline"
                      >
                        Pagar
                      </button>
                      <button type="button" onClick={() => setConfirmandoTarjeta(nombre)} className="text-tinta-apagada hover:text-error">
                        Borrar
                      </button>
                    </span>
                  )}
                </div>

                {tarjeta.limite > 0 && (
                  <p className="mt-2 etiqueta">
                    LÍMITE {MONEDA.format(tarjeta.limite)} · DISPONIBLE ESTE MES{" "}
                    {MONEDA.format(tarjeta.limite - pagoMensual)}
                  </p>
                )}

                {/* --- Pago --- */}
                {pagando === nombre && (
                  <form
                    className="mt-4 border border-acento/40 bg-acento/5 p-3 flex flex-wrap items-end gap-3"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void ejecutar(nombre, async () => {
                        const r = await pagarTarjeta(nombre, {
                          cuenta: pago.cuenta || undefined,
                          monto: pago.monto === "" ? null : Number(pago.monto),
                        });
                        setPagando(null);
                        setPago({ cuenta: "", monto: "" });
                        return r;
                      });
                    }}
                  >
                    <p className="w-full text-xs text-aviso leading-relaxed">
                      Registrar el pago suma un gasto, avanza un mes las compras MSI
                      y descuenta el monto de la cuenta. No se puede deshacer desde
                      el portal.
                    </p>
                    <label>
                      <span className="etiqueta block mb-1.5">Cuenta (opcional)</span>
                      <input
                        value={pago.cuenta}
                        onChange={(e) => setPago({ ...pago, cuenta: e.target.value })}
                        placeholder="openbank"
                        className="w-40 rounded-control border border-linea bg-fondo px-2 py-1.5 font-mono text-xs text-tinta focus:border-acento"
                      />
                    </label>
                    <label>
                      <span className="etiqueta block mb-1.5">
                        Monto (vacío = {MONEDA.format(pagoMensual)})
                      </span>
                      <input
                        type="number"
                        step="0.01"
                        value={pago.monto}
                        onChange={(e) => setPago({ ...pago, monto: e.target.value })}
                        placeholder={pagoMensual.toFixed(2)}
                        className="w-32 rounded-control border border-linea bg-fondo px-2 py-1.5 text-right font-mono text-xs text-tinta focus:border-acento"
                      />
                    </label>
                    <button
                      type="submit"
                      disabled={enVuelo}
                      className="rounded-control border border-acento bg-acento px-4 py-2 text-xs uppercase tracking-wider text-fondo font-medium hover:bg-acento/90 disabled:opacity-30"
                    >
                      {enVuelo ? "Pagando…" : "Confirmar pago"}
                    </button>
                    <button type="button" onClick={() => setPagando(null)} className="text-xs text-tinta-apagada hover:text-tinta">
                      Cancelar
                    </button>
                  </form>
                )}

                {/* --- Compras --- */}
                {desplegada && (
                  <div className="mt-4 border-t border-linea pt-3">
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => {
                          setCreandoCompra(creandoCompra === nombre ? null : nombre);
                          setNuevaCompra(compraVacia());
                        }}
                        className="text-xs text-tinta-tenue hover:text-acento"
                      >
                        {creandoCompra === nombre ? "Cancelar" : "+ Añadir compra"}
                      </button>
                    </div>

                    {creandoCompra === nombre && (
                      <form
                        className="mt-3 grid gap-2 sm:grid-cols-[1fr_8rem_6rem_8rem_auto] items-end"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void ejecutar(nombre, async () => {
                            const r = await crearCompra(nombre, nuevaCompra);
                            setNuevaCompra(compraVacia());
                            setCreandoCompra(null);
                            return r;
                          });
                        }}
                      >
                        <label>
                          <span className="etiqueta block mb-1.5">Concepto</span>
                          <input
                            required
                            value={nuevaCompra.concepto}
                            onChange={(e) => setNuevaCompra({ ...nuevaCompra, concepto: e.target.value })}
                            placeholder="Laptop MSI"
                            className="w-full rounded-control border border-linea bg-fondo px-2 py-1.5 font-mono text-xs text-tinta focus:border-acento"
                          />
                        </label>
                        <label>
                          <span className="etiqueta block mb-1.5">Total</span>
                          <input
                            type="number"
                            step="0.01"
                            min="0.01"
                            required
                            value={nuevaCompra.total}
                            onChange={(e) => setNuevaCompra({ ...nuevaCompra, total: Number(e.target.value) })}
                            className="w-full rounded-control border border-linea bg-fondo px-2 py-1.5 text-right font-mono text-xs text-tinta focus:border-acento"
                          />
                        </label>
                        <label>
                          <span className="etiqueta block mb-1.5">Meses</span>
                          <input
                            type="number"
                            min="1"
                            max="120"
                            value={nuevaCompra.plazo}
                            onChange={(e) => setNuevaCompra({ ...nuevaCompra, plazo: Number(e.target.value) })}
                            className="w-full rounded-control border border-linea bg-fondo px-2 py-1.5 text-right font-mono text-xs text-tinta focus:border-acento"
                          />
                        </label>
                        <label>
                          <span className="etiqueta block mb-1.5">Inicio</span>
                          <input
                            type="date"
                            value={nuevaCompra.inicio}
                            onChange={(e) => setNuevaCompra({ ...nuevaCompra, inicio: e.target.value })}
                            className="w-full rounded-control border border-linea bg-fondo px-2 py-1.5 font-mono text-xs text-tinta focus:border-acento"
                          />
                        </label>
                        <button type="submit" disabled={enVuelo} className="rounded-control border border-ok bg-ok/15 px-3 py-1.5 text-xs uppercase tracking-wider text-ok hover:bg-ok/25 disabled:opacity-30">
                          Añadir
                        </button>
                      </form>
                    )}

                    {compras.length === 0 ? (
                      <p className="mt-3 text-xs text-tinta-apagada">Sin compras registradas.</p>
                    ) : (
                      <table className="mt-3 w-full border-collapse text-xs">
                        <thead>
                          <tr className="border-b border-linea">
                            <th className="etiqueta py-1.5 pr-2 text-left">Concepto</th>
                            <th className="etiqueta py-1.5 pr-2 text-right">Total</th>
                            <th className="etiqueta py-1.5 pr-2 text-right">Mensualidad</th>
                            <th className="etiqueta py-1.5 pr-2 text-right">Restantes</th>
                            <th className="etiqueta py-1.5 text-right">Acciones</th>
                          </tr>
                        </thead>
                        <tbody>
                          {compras.map((compra: Compra) => {
                            const esEdicionCompra = editandoCompra === compra.id;

                            return (
                              <tr key={compra.id} className="border-b border-linea">
                                <td className="py-1.5 pr-2">
                                  {esEdicionCompra ? (
                                    <input
                                      value={borradorCompra.concepto}
                                      onChange={(e) => setBorradorCompra({ ...borradorCompra, concepto: e.target.value })}
                                      className="w-full rounded-control border border-linea bg-fondo px-1.5 py-1 font-mono text-xs text-tinta focus:border-acento"
                                    />
                                  ) : (
                                    <span className="text-tinta">{compra.concepto}</span>
                                  )}
                                </td>
                                <td className="py-1.5 pr-2 text-right">
                                  {esEdicionCompra ? (
                                    <input
                                      type="number"
                                      step="0.01"
                                      value={borradorCompra.total}
                                      onChange={(e) => setBorradorCompra({ ...borradorCompra, total: Number(e.target.value) })}
                                      className="w-24 rounded-control border border-linea bg-fondo px-1.5 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                                    />
                                  ) : (
                                    <span className="font-mono text-tinta-tenue">{MONEDA.format(compra.total)}</span>
                                  )}
                                </td>
                                <td className="py-1.5 pr-2 text-right font-mono text-tinta">
                                  {MONEDA.format(compra.mensualidad)}
                                </td>
                                <td className="py-1.5 pr-2 text-right">
                                  {esEdicionCompra ? (
                                    <input
                                      type="number"
                                      min="1"
                                      value={borradorCompra.plazo}
                                      onChange={(e) => setBorradorCompra({ ...borradorCompra, plazo: Number(e.target.value) })}
                                      className="w-16 rounded-control border border-linea bg-fondo px-1.5 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                                    />
                                  ) : (
                                    <span className={`font-mono ${compra.restantes === 0 ? "text-ok" : "text-aviso"}`}>
                                      {compra.restantes}/{compra.plazo}
                                    </span>
                                  )}
                                </td>
                                <td className="py-1.5 text-right whitespace-nowrap">
                                  {esEdicionCompra ? (
                                    <span className="flex justify-end gap-2">
                                      <button
                                        type="button"
                                        onClick={() =>
                                          void ejecutar(nombre, async () => {
                                            const r = await editarCompra(nombre, compra.id, borradorCompra);
                                            setEditandoCompra(null);
                                            return r;
                                          })
                                        }
                                        className="text-ok hover:underline"
                                      >
                                        Guardar
                                      </button>
                                      <button type="button" onClick={() => setEditandoCompra(null)} className="text-tinta-apagada hover:text-tinta">
                                        Cancelar
                                      </button>
                                    </span>
                                  ) : confirmandoCompra === compra.id ? (
                                    <span className="flex items-center justify-end gap-2">
                                      <span className="etiqueta text-error">¿BORRAR?</span>
                                      <button
                                        type="button"
                                        onClick={() =>
                                          void ejecutar(nombre, async () => {
                                            const r = await borrarCompra(nombre, compra.id);
                                            setConfirmandoCompra(null);
                                            return r;
                                          })
                                        }
                                        className="text-error hover:underline"
                                      >
                                        Sí
                                      </button>
                                      <button type="button" onClick={() => setConfirmandoCompra(null)} className="text-tinta-apagada hover:text-tinta">
                                        No
                                      </button>
                                    </span>
                                  ) : (
                                    <span className="flex justify-end gap-2">
                                      <button
                                        type="button"
                                        onClick={() => {
                                          setEditandoCompra(compra.id);
                                          setBorradorCompra({
                                            concepto: compra.concepto,
                                            total: compra.total,
                                            plazo: compra.plazo,
                                            inicio: compra.inicio ?? "",
                                          });
                                        }}
                                        className="text-tinta-tenue hover:text-acento"
                                      >
                                        Editar
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() =>
                                          void ejecutar(nombre, () => duplicarCompra(nombre, compra.id))
                                        }
                                        className="text-tinta-tenue hover:text-acento"
                                      >
                                        Duplicar
                                      </button>
                                      <button type="button" onClick={() => setConfirmandoCompra(compra.id)} className="text-tinta-apagada hover:text-error">
                                        Borrar
                                      </button>
                                    </span>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}

                    <p className="mt-2 text-xs text-tinta-apagada">
                      Editar una compra recalcula la mensualidad pero no cambia los
                      pagos restantes: eso lo hace «Pagar».
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
