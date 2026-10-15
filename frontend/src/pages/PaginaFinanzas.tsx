import { useCallback, useEffect, useState } from "react";

import { SeccionMovimientos } from "../components/SeccionMovimientos";
import { IconoAviso, IconoFinanzas, IconoOk } from "../components/Iconos";
import { pedir } from "../lib/api";

interface RespuestaPanel {
  usuario: string;
  periodo: string;
  resumen: Resumen;
  presupuesto: Record<string, { limite: number; periodo: string }>;
  cuentas: { cuentas: Record<string, { saldo: number; tipo: string }> };
  tarjetas: Record<
    string,
    {
      limite: number;
      corte: number;
      pago: number;
      compras?: Array<{ concepto: string; mensualidad: number; restantes: number }>;
    }
  >;
  metas: Record<string, unknown> | null;
}

interface Resumen {
  periodo: string;
  ingresos: number;
  gastos: number;
  balance: number;
  transacciones: number;
  top_gastos: Array<{ categoria: string; total: number }>;
  alertas_presupuesto: Array<{ categoria?: string; mensaje?: string; limite?: number; gastado?: number }>;
  pago_tarjetas_estimado: Record<string, number>;
}

interface EstadoMotor {
  disponible: boolean;
  motivo: string;
  usuario: string;
  periodos: string[];
  tipos_cuenta: string[];
}

type Estado = "cargando" | "listo" | "error";

/** Periodos que ofrece el selector, en el orden en que se leen. */
const PERIODOS: Array<{ valor: string; etiqueta: string }> = [
  { valor: "mensual", etiqueta: "ESTE MES" },
  { valor: "mes_anterior", etiqueta: "MES ANTERIOR" },
  { valor: "anual", etiqueta: "AÑO" },
];

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

/**
 * Formatea dinero. Un monto negativo se muestra en rojo: en un panel
 * financiero, el signo es el dato más importante de la línea.
 */
function dinero(valor: number | undefined | null): string {
  if (valor === undefined || valor === null || Number.isNaN(valor)) return "—";
  return MONEDA.format(valor);
}

/**
 * Herramienta «Panel de finanzas».
 *
 * Una sola carga del periodo completo: resumen, presupuesto, cuentas y
 * tarjetas. Los datos son de solo lectura; registrar movimientos se hace en
 * la herramienta de comprobantes, con confirmación.
 */
export function PaginaFinanzas() {
  const [periodo, setPeriodo] = useState("mensual");
  const [estado, setEstado] = useState<Estado>("cargando");
  const [error, setError] = useState<string | null>(null);
  const [datos, setDatos] = useState<RespuestaPanel | null>(null);
  const [motor, setMotor] = useState<EstadoMotor | null>(null);

  // Edición de saldos: una cuenta a la vez.
  const [cuentaEditando, setCuentaEditando] = useState<string | null>(null);
  const [saldoBorrador, setSaldoBorrador] = useState<number | null>(null);
  const [guardandoSaldo, setGuardandoSaldo] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);

  useEffect(() => {
    let vigente = true;
    pedir<EstadoMotor>("/finanzas/estado")
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

  const cargar = useCallback(async () => {
    setEstado("cargando");
    setError(null);

    try {
      const respuesta = await pedir<RespuestaPanel>(`/finanzas/panel?periodo=${periodo}`);
      setDatos(respuesta);
      setEstado("listo");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
      setEstado("error");
    }
  }, [periodo]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // El aviso de guardado es efímero: no debe quedarse pegado mientras se trabaja.
  useEffect(() => {
    if (!aviso) return;
    const t = window.setTimeout(() => setAviso(null), 6000);
    return () => window.clearTimeout(t);
  }, [aviso]);

  /** Guarda el saldo de una cuenta. Corrige el dato, no registra un movimiento. */
  const guardarSaldo = useCallback(
    async (cuenta: string, saldo: number, tipo: string) => {
      setGuardandoSaldo(true);
      setError(null);

      try {
        const resultado = await pedir<{ ok: boolean; mensaje: string }>(
          "/finanzas/cuentas",
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cuenta, saldo, tipo }),
          },
        );
        setAviso(resultado.mensaje);
        setCuentaEditando(null);
        setSaldoBorrador(null);
        await cargar();
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo guardar el saldo.");
      } finally {
        setGuardandoSaldo(false);
      }
    },
    [cargar],
  );

  const resumen = datos?.resumen;
  const cuentas = datos?.cuentas?.cuentas ?? {};
  const tarjetas = datos?.tarjetas ?? {};
  const tarjetasLista = Object.entries(tarjetas).filter(
    ([clave, t]) => clave !== "status" && typeof t === "object" && "corte" in t,
  );

  return (
    <section aria-labelledby="titulo-finanzas">
      <div className="regla">
        <span className="etiqueta">Finanzas</span>
        <span className="etiqueta ml-auto">
          LEDGER · {motor?.usuario?.toUpperCase() ?? "NOE"}
        </span>
      </div>

      {motor?.disponible === false && (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          <span>
            El panel no está disponible en este despliegue.
            {motor.motivo && <> {motor.motivo}</>}
          </span>
        </p>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-2">
        {PERIODOS.map((p) => (
          <button
            key={p.valor}
            type="button"
            onClick={() => setPeriodo(p.valor)}
            className={[
              "border px-3 py-1.5 text-xs uppercase tracking-wider transition-colors duration-300",
              periodo === p.valor
                ? "border-acento text-acento"
                : "border-linea text-tinta-apagada hover:border-acento-tenue hover:text-tinta",
            ].join(" ")}
          >
            {p.etiqueta}
          </button>
        ))}

        <button
          type="button"
          onClick={() => void cargar()}
          className="ml-auto text-xs text-tinta-apagada hover:text-tinta transition-colors duration-300"
        >
          Actualizar
        </button>
      </div>

      {estado === "cargando" && (
        <p className="mt-8 text-xs text-tinta-apagada">Leyendo el ledger…</p>
      )}

      {aviso && (
        <p className="mt-4 flex items-center gap-2 border border-ok/40 bg-ok/10 px-3 py-2 text-xs text-ok">
          <IconoOk tam={14} />
          {aviso}
        </p>
      )}

      {estado === "error" && error && (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          {error}
        </p>
      )}

      {estado === "listo" && resumen && (
        <>
          {/* --- Cifras del periodo --- */}
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Cifra etiqueta="INGRESOS" valor={dinero(resumen.ingresos)} clase="text-ok" />
            <Cifra etiqueta="GASTOS" valor={dinero(resumen.gastos)} clase="text-error" />
            <Cifra
              etiqueta="BALANCE"
              valor={dinero(resumen.balance)}
              clase={resumen.balance < 0 ? "text-error" : "text-tinta"}
            />
            <Cifra
              etiqueta="MOVIMIENTOS"
              valor={String(resumen.transacciones)}
              clase="text-tinta"
            />
          </div>

          {/* --- Alertas de presupuesto --- */}
          {resumen.alertas_presupuesto.length > 0 && (
            <div className="mt-6 panel p-4">
              <p className="etiqueta">Alertas de presupuesto</p>
              <ul className="mt-3 space-y-2">
                {resumen.alertas_presupuesto.map((alerta, indice) => (
                  <li key={indice} className="flex items-start gap-2 text-xs text-aviso">
                    <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
                    <span>
                      {alerta.mensaje ??
                        `${alerta.categoria ?? "Categoría"}: ${dinero(
                          alerta.gastado,
                        )} de ${dinero(alerta.limite)}`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-8 grid gap-6 lg:grid-cols-2">
            {/* --- Presupuesto --- */}
            <div className="panel p-5">
              <div className="regla">
                <span className="etiqueta">Presupuesto del mes</span>
              </div>

              {Object.keys(datos?.presupuesto ?? {}).length === 0 ? (
                <p className="mt-4 text-xs text-tinta-apagada">
                  Sin presupuestos configurados.
                </p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {Object.entries(datos?.presupuesto ?? {}).map(([categoria, valor]) => {
                    const gastado =
                      resumen.top_gastos.find(
                        (g) => g.categoria.toLowerCase() === categoria.toLowerCase(),
                      )?.total ?? 0;
                    const proporcion = valor.limite > 0
                      ? Math.min(100, Math.round((gastado / valor.limite) * 100))
                      : 0;
                    const excedido = gastado > valor.limite;

                    return (
                      <li key={categoria}>
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="text-xs capitalize text-tinta">{categoria}</span>
                          <span className={`etiqueta ${excedido ? "text-error" : "text-tinta-apagada"}`}>
                            {dinero(gastado)} / {dinero(valor.limite)}
                          </span>
                        </div>
                        <div className="mt-2 h-1 w-full bg-linea">
                          <div
                            className={`h-full ${excedido ? "bg-error" : "bg-acento"}`}
                            style={{ width: `${proporcion}%` }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {/* --- Cuentas --- */}
            <div className="panel p-5">
              <div className="regla">
                <span className="etiqueta">Cuentas</span>
              </div>

              {Object.keys(cuentas).length === 0 ? (
                <p className="mt-4 text-xs text-tinta-apagada">Sin cuentas cargadas.</p>
              ) : (
                <ul className="mt-4 divide-y divide-linea">
                  {Object.entries(cuentas).map(([nombre, cuenta]) => {
                    const esEdicion = cuentaEditando === nombre;

                    return (
                      <li
                        key={nombre}
                        className="flex items-center justify-between gap-3 py-2"
                      >
                        <span className="text-xs text-tinta capitalize">
                          {nombre.replace(/_/g, " ")}
                        </span>

                        {esEdicion ? (
                          <span className="flex items-center gap-2">
                            <input
                              type="number"
                              step="0.01"
                              autoFocus
                              value={saldoBorrador ?? ""}
                              onChange={(e) =>
                                setSaldoBorrador(
                                  e.target.value ? Number(e.target.value) : null,
                                )
                              }
                              className="w-28 rounded-control border border-linea bg-fondo px-2 py-1
                                         text-right font-mono text-xs text-tinta focus:border-acento"
                            />
                            <button
                              type="button"
                              disabled={guardandoSaldo || saldoBorrador === null}
                              onClick={() =>
                                void guardarSaldo(nombre, saldoBorrador ?? 0, cuenta.tipo)
                              }
                              className="text-ok hover:underline disabled:opacity-30"
                            >
                              {guardandoSaldo ? "…" : "Guardar"}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setCuentaEditando(null);
                                setSaldoBorrador(null);
                              }}
                              className="text-tinta-apagada hover:text-tinta"
                            >
                              Cancelar
                            </button>
                          </span>
                        ) : (
                          <span className="flex items-baseline gap-3">
                            <span className="etiqueta">{cuenta.tipo}</span>
                            <button
                              type="button"
                              onClick={() => {
                                setCuentaEditando(nombre);
                                setSaldoBorrador(cuenta.saldo);
                              }}
                              title="Corregir el saldo"
                              className={`font-mono text-xs hover:underline ${
                                cuenta.saldo < 0 ? "text-error" : "text-tinta"
                              }`}
                            >
                              {dinero(cuenta.saldo)}
                            </button>
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          {/* --- Tarjetas --- */}
          {tarjetasLista.length > 0 && (
            <div className="mt-6 panel p-5">
              <div className="regla">
                <span className="etiqueta">Tarjetas de crédito</span>
                <span className="etiqueta ml-auto">
                  PAGO ESTIMADO{" "}
                  {dinero(
                    Object.values(resumen.pago_tarjetas_estimado ?? {}).reduce(
                      (suma, monto) => suma + monto,
                      0,
                    ),
                  )}
                </span>
              </div>

              <div className="mt-4 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                {tarjetasLista.map(([nombre, tarjeta]) => (
                  <div key={nombre} className="border border-linea p-4">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-xs uppercase tracking-wider text-tinta">
                        {nombre.replace(/_/g, " ")}
                      </span>
                      <span className="etiqueta">
                        PAGO {tarjeta.pago} · CORTE {tarjeta.corte}
                      </span>
                    </div>

                    <p className="mt-3 font-mono text-sm text-tinta">
                      {dinero(resumen.pago_tarjetas_estimado?.[nombre] ?? 0)}
                    </p>
                    <p className="mt-1 etiqueta">ESTE MES</p>

                    {(tarjeta.compras ?? []).length > 0 && (
                      <ul className="mt-3 space-y-1 border-t border-linea pt-3">
                        {(tarjeta.compras ?? []).slice(0, 4).map((compra) => (
                          <li key={compra.concepto} className="flex justify-between gap-2 text-xs">
                            <span className="truncate text-tinta-tenue">{compra.concepto}</span>
                            <span className="etiqueta shrink-0">
                              {dinero(compra.mensualidad)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* --- Top gastos --- */}
          {resumen.top_gastos.length > 0 && (
            <div className="mt-6 panel p-5">
              <div className="regla">
                <span className="etiqueta">Mayores gastos · {resumen.periodo}</span>
              </div>
              <ul className="mt-4 space-y-2">
                {resumen.top_gastos
                  .slice()
                  .sort((a, b) => b.total - a.total)
                  .map((gasto) => (
                    <li key={gasto.categoria} className="flex items-baseline justify-between gap-3">
                      <span className="text-xs capitalize text-tinta">{gasto.categoria}</span>
                      <span className="font-mono text-xs text-error">{dinero(gasto.total)}</span>
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </>
      )}

      <p className="mt-6 flex items-center gap-2 text-xs text-tinta-apagada">
        <IconoOk tam={14} clase="text-ok" />
        <span className="etiqueta">
          <IconoFinanzas tam={12} clase="mr-1 inline" />
          El portal mantiene el registro. Las transferencias y los pagos siguen
          siendo conversación con el agente.
        </span>
      </p>

      {/* --- Movimientos: el editor del ledger --- */}
      <SeccionMovimientos />
    </section>
  );
}

/** Bloque de una cifra del periodo: etiqueta mono + número grande. */
function Cifra({
  etiqueta,
  valor,
  clase,
}: {
  etiqueta: string;
  valor: string;
  clase: string;
}) {
  return (
    <div className="panel p-4">
      <p className="etiqueta">{etiqueta}</p>
      <p className={`mt-2 font-mono text-lg ${clase}`}>{valor}</p>
    </div>
  );
}
