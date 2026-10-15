import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "../Iconos";
import {
  type Cuenta,
  type ResultadoOperacion,
  borrarCuenta,
  duplicarCuenta,
  guardarCuenta,
  leerCuentas,
} from "../../lib/ledger";

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

const TIPOS = ["debito", "credito", "fisico", "prepago"] as const;

type Aviso = { texto: string; ok: boolean } | null;

/**
 * Pestaña de cuentas.
 *
 * Cada fila se edita en su lugar (saldo y tipo) y se puede duplicar o borrar.
 * Borrar una cuenta no toca los movimientos: el ledger guarda el nombre como
 * texto, así que el historial queda intacto.
 */
export function TabCuentas({ alCambiar }: { alCambiar: () => void }) {
  const [cuentas, setCuentas] = useState<Record<string, Cuenta>>({});
  const [total, setTotal] = useState(0);
  const [cargando, setCargando] = useState(true);
  const [aviso, setAviso] = useState<Aviso>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);

  const [editando, setEditando] = useState<string | null>(null);
  const [saldo, setSaldo] = useState<number | null>(null);
  const [tipo, setTipo] = useState<string>("debito");
  const [confirmando, setConfirmando] = useState<string | null>(null);
  const [duplicando, setDuplicando] = useState<string | null>(null);
  const [nombreCopia, setNombreCopia] = useState("");

  const [creando, setCreando] = useState(false);
  const [nueva, setNueva] = useState({ nombre: "", saldo: 0, tipo: "debito" });

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const datos = await leerCuentas();
      setCuentas(datos.cuentas);
      setTotal(datos.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const anunciar = (r: ResultadoOperacion) => {
    setAviso({ texto: r.mensaje, ok: true });
    setError(null);
    alCambiar();
  };

  const ejecutar = async (clave: string, accion: () => Promise<ResultadoOperacion>) => {
    setOcupado(clave);
    setError(null);
    try {
      anunciar(await accion());
      await cargar();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "La operación falló.");
      return false;
    } finally {
      setOcupado(null);
    }
  };

  return (
    <div>
      <div className="regla">
        <span className="etiqueta">Cuentas</span>
        <span className="etiqueta ml-auto">
          {Object.keys(cuentas).length} · TOTAL {MONEDA.format(total)}
        </span>
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

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={() => setCreando((v) => !v)}
          className="rounded-control border border-acento bg-acento px-4 py-2 text-xs uppercase tracking-wider text-fondo font-medium hover:bg-acento/90"
        >
          {creando ? "Cancelar" : "Nueva cuenta"}
        </button>
      </div>

      {creando && (
        <form
          className="panel mt-4 p-4 grid gap-3 sm:grid-cols-[1fr_10rem_10rem_auto] items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void ejecutar("nueva", async () => {
              const r = await guardarCuenta(nueva.nombre, nueva.saldo, nueva.tipo);
              setNueva({ nombre: "", saldo: 0, tipo: "debito" });
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
              placeholder="openbank"
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 font-mono text-xs text-tinta focus:border-acento"
            />
          </label>
          <label>
            <span className="etiqueta block mb-1.5">Saldo</span>
            <input
              type="number"
              step="0.01"
              required
              value={nueva.saldo}
              onChange={(e) => setNueva({ ...nueva, saldo: Number(e.target.value) })}
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-right font-mono text-xs text-tinta focus:border-acento"
            />
          </label>
          <label>
            <span className="etiqueta block mb-1.5">Tipo</span>
            <select
              value={nueva.tipo}
              onChange={(e) => setNueva({ ...nueva, tipo: e.target.value })}
              className="w-full rounded-control border border-linea bg-fondo px-2 py-2 text-xs text-tinta focus:border-acento"
            >
              {TIPOS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={ocupado === "nueva"}
            className="rounded-control border border-ok bg-ok/15 px-4 py-2 text-xs uppercase tracking-wider text-ok font-medium hover:bg-ok/25 disabled:opacity-30"
          >
            Guardar
          </button>
        </form>
      )}

      <div className="mt-4 overflow-x-auto">
        {cargando ? (
          <p className="text-xs text-tinta-apagada">Leyendo cuentas…</p>
        ) : Object.keys(cuentas).length === 0 ? (
          <p className="text-xs text-tinta-apagada">Sin cuentas cargadas.</p>
        ) : (
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-linea-fuerte">
                <th className="etiqueta py-2 pr-3 text-left">Cuenta</th>
                <th className="etiqueta py-2 pr-3 text-left">Tipo</th>
                <th className="etiqueta py-2 pr-3 text-right">Saldo</th>
                <th className="etiqueta py-2 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(cuentas).map(([nombre, cuenta]) => {
                const esEdicion = editando === nombre;
                const enVuelo = ocupado === nombre;

                return (
                  <tr
                    key={nombre}
                    className={[
                      "border-b border-linea",
                      esEdicion ? "bg-panel-alto" : "hover:bg-panel-alto/50",
                      enVuelo ? "opacity-50" : "",
                    ].join(" ")}
                  >
                    <td className="py-2 pr-3 capitalize text-tinta">
                      {nombre.replace(/_/g, " ")}
                    </td>
                    <td className="py-2 pr-3">
                      {esEdicion ? (
                        <select
                          value={tipo}
                          onChange={(e) => setTipo(e.target.value)}
                          className="rounded-control border border-linea bg-fondo px-2 py-1 text-xs text-tinta focus:border-acento"
                        >
                          {TIPOS.map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      ) : (
                        <span className="etiqueta">{cuenta.tipo}</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-right">
                      {esEdicion ? (
                        <input
                          type="number"
                          step="0.01"
                          value={saldo ?? ""}
                          onChange={(e) => setSaldo(e.target.value ? Number(e.target.value) : null)}
                          className="w-28 rounded-control border border-linea bg-fondo px-2 py-1 text-right font-mono text-xs text-tinta focus:border-acento"
                        />
                      ) : (
                        <span className={`font-mono ${cuenta.saldo < 0 ? "text-error" : "text-tinta"}`}>
                          {MONEDA.format(cuenta.saldo)}
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right whitespace-nowrap">
                      {esEdicion ? (
                        <span className="flex justify-end gap-3">
                          <button
                            type="button"
                            disabled={enVuelo || saldo === null}
                            onClick={() =>
                              void ejecutar(nombre, async () => {
                                const r = await guardarCuenta(nombre, saldo ?? 0, tipo);
                                setEditando(null);
                                return r;
                              })
                            }
                            className="text-ok hover:underline disabled:opacity-30"
                          >
                            {enVuelo ? "Guardando…" : "Guardar"}
                          </button>
                          <button type="button" onClick={() => setEditando(null)} className="text-tinta-apagada hover:text-tinta">
                            Cancelar
                          </button>
                        </span>
                      ) : confirmando === nombre ? (
                        <span className="flex items-center justify-end gap-3">
                          <span className="etiqueta text-error">¿BORRAR?</span>
                          <button
                            type="button"
                            disabled={enVuelo}
                            onClick={() =>
                              void ejecutar(nombre, async () => {
                                const r = await borrarCuenta(nombre);
                                setConfirmando(null);
                                return r;
                              })
                            }
                            className="text-error hover:underline disabled:opacity-30"
                          >
                            Sí
                          </button>
                          <button type="button" onClick={() => setConfirmando(null)} className="text-tinta-apagada hover:text-tinta">
                            No
                          </button>
                        </span>
                      ) : duplicando === nombre ? (
                        <form
                          className="flex items-center justify-end gap-2"
                          onSubmit={(e) => {
                            e.preventDefault();
                            void ejecutar(nombre, async () => {
                              const r = await duplicarCuenta(nombre, nombreCopia);
                              setDuplicando(null);
                              setNombreCopia("");
                              return r;
                            });
                          }}
                        >
                          <input
                            autoFocus
                            required
                            value={nombreCopia}
                            onChange={(e) => setNombreCopia(e.target.value)}
                            placeholder="nombre nuevo"
                            className="w-32 rounded-control border border-linea bg-fondo px-2 py-1 font-mono text-xs text-tinta focus:border-acento"
                          />
                          <button type="submit" className="text-ok hover:underline">OK</button>
                          <button type="button" onClick={() => setDuplicando(null)} className="text-tinta-apagada hover:text-tinta">
                            No
                          </button>
                        </form>
                      ) : (
                        <span className="flex justify-end gap-3">
                          <button
                            type="button"
                            onClick={() => {
                              setEditando(nombre);
                              setSaldo(cuenta.saldo);
                              setTipo(cuenta.tipo);
                            }}
                            className="text-tinta-tenue hover:text-acento"
                          >
                            Editar
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setDuplicando(nombre);
                              setNombreCopia(`${nombre}_copia`);
                            }}
                            className="text-tinta-tenue hover:text-acento"
                          >
                            Duplicar
                          </button>
                          <button type="button" onClick={() => setConfirmando(nombre)} className="text-tinta-apagada hover:text-error">
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
      </div>

      <p className="mt-3 text-xs text-tinta-apagada">
        Borrar una cuenta no borra sus movimientos: quedan en el ledger sin cuenta
        asignada.
      </p>
    </div>
  );
}
