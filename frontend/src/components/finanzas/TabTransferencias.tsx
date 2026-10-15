import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "../Iconos";
import {
  type Cuenta,
  type ResultadoOperacion,
  leerCuentas,
  transferir,
} from "../../lib/ledger";

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

type Aviso = { texto: string; ok: boolean } | null;

/**
 * Pestaña de transferencias entre cuentas.
 *
 * Solo salen las cuentas de débito, efectivo y prepago: desde una tarjeta de
 * crédito no se "transfiere" dinero en este modelo, se paga (y eso vive en la
 * pestaña de tarjetas).
 *
 * Mover dinero es irreversible desde el portal, así que la confirmación muestra
 * el efecto completo: de dónde sale, a dónde llega y cómo quedan ambos saldos.
 */
export function TabTransferencias({ alCambiar }: { alCambiar: () => void }) {
  const [cuentas, setCuentas] = useState<Record<string, Cuenta>>({});
  const [cargando, setCargando] = useState(true);
  const [aviso, setAviso] = useState<Aviso>(null);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const [origen, setOrigen] = useState("");
  const [destino, setDestino] = useState("");
  const [monto, setMonto] = useState<number | "">("");
  const [nota, setNota] = useState("");
  const [confirmando, setConfirmando] = useState(false);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const datos = await leerCuentas();
      setCuentas(datos.cuentas);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const mover = async () => {
    setEnviando(true);
    setError(null);

    try {
      const r: ResultadoOperacion = await transferir({
        origen,
        destino,
        monto: Number(monto),
        nota: nota.trim(),
      });
      setAviso({ texto: r.mensaje, ok: true });
      setConfirmando(false);
      setMonto("");
      setNota("");
      await cargar();
      alCambiar();
    } catch (err) {
      setError(err instanceof Error ? err.message : "La transferencia falló.");
    } finally {
      setEnviando(false);
    }
  };

  if (cargando) {
    return <p className="text-xs text-tinta-apagada">Leyendo cuentas…</p>;
  }

  const disponibles = Object.entries(cuentas).filter(
    ([, c]) => c.tipo !== "credito",
  );

  const saldoOrigen = cuentas[origen]?.saldo ?? 0;
  const saldoDestino = cuentas[destino]?.saldo ?? 0;
  const cantidad = typeof monto === "number" ? monto : 0;

  const valido =
    origen !== "" &&
    destino !== "" &&
    origen !== destino &&
    cantidad > 0 &&
    cantidad <= saldoOrigen;

  return (
    <div>
      <div className="regla">
        <span className="etiqueta">Transferir entre cuentas</span>
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

      {disponibles.length < 2 ? (
        <p className="mt-6 text-xs text-tinta-apagada">
          Hacen falta al menos dos cuentas (sin contar tarjetas de crédito) para
          poder transferir.
        </p>
      ) : !confirmando ? (
        <form
          className="panel mt-6 max-w-xl p-5 space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setConfirmando(true);
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <label>
              <span className="etiqueta block mb-1.5">Desde</span>
              <select
                required
                value={origen}
                onChange={(e) => setOrigen(e.target.value)}
                className="w-full rounded-control border border-linea bg-fondo px-3 py-2 text-xs text-tinta focus:border-acento"
              >
                <option value="">— elegir —</option>
                {disponibles.map(([nombre, c]) => (
                  <option key={nombre} value={nombre}>
                    {nombre.replace(/_/g, " ")} · {MONEDA.format(c.saldo)}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span className="etiqueta block mb-1.5">Hacia</span>
              <select
                required
                value={destino}
                onChange={(e) => setDestino(e.target.value)}
                className="w-full rounded-control border border-linea bg-fondo px-3 py-2 text-xs text-tinta focus:border-acento"
              >
                <option value="">— elegir —</option>
                {disponibles
                  .filter(([nombre]) => nombre !== origen)
                  .map(([nombre, c]) => (
                    <option key={nombre} value={nombre}>
                      {nombre.replace(/_/g, " ")} · {MONEDA.format(c.saldo)}
                    </option>
                  ))}
              </select>
            </label>
          </div>

          <label className="block">
            <span className="etiqueta block mb-1.5">Monto</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              required
              value={monto}
              onChange={(e) => setMonto(e.target.value ? Number(e.target.value) : "")}
              className="w-full rounded-control border border-linea bg-fondo px-3 py-2 font-mono text-sm text-tinta focus:border-acento"
            />
            {origen && cantidad > saldoOrigen && (
              <span className="mt-1.5 block text-xs text-error">
                La cuenta de origen solo tiene {MONEDA.format(saldoOrigen)}.
              </span>
            )}
          </label>

          <label className="block">
            <span className="etiqueta block mb-1.5">Nota</span>
            <input
              value={nota}
              onChange={(e) => setNota(e.target.value)}
              placeholder="opcional"
              className="w-full rounded-control border border-linea bg-fondo px-3 py-2 font-mono text-xs text-tinta focus:border-acento"
            />
          </label>

          <button
            type="submit"
            disabled={!valido}
            className="rounded-control border border-acento bg-acento px-6 py-2.5 text-xs uppercase tracking-wider text-fondo font-medium hover:bg-acento/90 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Revisar transferencia
          </button>
        </form>
      ) : (
        /* Confirmación con el efecto completo a la vista: es irreversible. */
        <div className="panel mt-6 max-w-xl p-5 space-y-4">
          <p className="etiqueta text-aviso">CONFIRMAR MOVIMIENTO DE DINERO</p>

          <div className="space-y-3 text-xs">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-tinta">{origen.replace(/_/g, " ")}</span>
              <span className="font-mono text-tinta-tenue">
                {MONEDA.format(saldoOrigen)}
                <span className="text-tinta-apagada"> → </span>
                <span className="text-error">{MONEDA.format(saldoOrigen - cantidad)}</span>
              </span>
            </div>

            <div className="flex items-baseline justify-between gap-3">
              <span className="text-tinta">{destino.replace(/_/g, " ")}</span>
              <span className="font-mono text-tinta-tenue">
                {MONEDA.format(saldoDestino)}
                <span className="text-tinta-apagada"> → </span>
                <span className="text-ok">{MONEDA.format(saldoDestino + cantidad)}</span>
              </span>
            </div>
          </div>

          <p className="font-mono text-lg text-tinta">{MONEDA.format(cantidad)}</p>

          {nota && <p className="text-xs text-tinta-apagada">{nota}</p>}

          <p className="text-xs text-aviso leading-relaxed">
            Los saldos cambian de inmediato y no se puede deshacer desde el portal.
          </p>

          <div className="flex items-center gap-4">
            <button
              type="button"
              disabled={enviando}
              onClick={() => void mover()}
              className="rounded-control border border-acento bg-acento px-6 py-2.5 text-xs uppercase tracking-wider text-fondo font-medium hover:bg-acento/90 disabled:opacity-30"
            >
              {enviando ? "Transfiriendo…" : "Confirmar"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmando(false)}
              className="text-xs text-tinta-apagada hover:text-tinta"
            >
              Cancelar
            </button>
          </div>
        </div>
      )}

      {/* Saldos actuales, para decidir sin cambiar de pestaña. */}
      <div className="mt-8">
        <div className="regla">
          <span className="etiqueta">Saldos</span>
        </div>
        <ul className="mt-3 divide-y divide-linea max-w-xl">
          {Object.entries(cuentas).map(([nombre, cuenta]) => (
            <li key={nombre} className="flex items-baseline justify-between gap-3 py-2">
              <span className="text-xs capitalize text-tinta">
                {nombre.replace(/_/g, " ")}
              </span>
              <span className="flex items-baseline gap-3">
                <span className="etiqueta">{cuenta.tipo}</span>
                <span className={`font-mono text-xs ${cuenta.saldo < 0 ? "text-error" : "text-tinta"}`}>
                  {MONEDA.format(cuenta.saldo)}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
