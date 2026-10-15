import { useCallback, useEffect, useState } from "react";

import { IconoAviso } from "./Iconos";
import { TabCuentas } from "./finanzas/TabCuentas";
import { TabMetas } from "./finanzas/TabMetas";
import { TabPresupuestos } from "./finanzas/TabPresupuestos";
import { TabTarjetas } from "./finanzas/TabTarjetas";
import { TabTransferencias } from "./finanzas/TabTransferencias";
import { SeccionMovimientos } from "./SeccionMovimientos";
import { pedir } from "../lib/api";

interface Panel {
  resumen: {
    periodo: string;
    ingresos: number;
    gastos: number;
    balance: number;
    transacciones: number;
    pago_tarjetas_estimado: Record<string, number> | null;
  };
  cuentas: { cuentas: Record<string, { saldo: number; tipo: string }>; total?: number };
  tarjetas: Record<string, unknown>;
  presupuesto: Record<string, unknown>;
}

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

/** Las áreas del módulo. El orden va de lo más usado a lo más esporádico. */
const PESTANAS = [
  { id: "movimientos", etiqueta: "Movimientos", descripcion: "El día a día" },
  { id: "tarjetas", etiqueta: "Tarjetas", descripcion: "Crédito y MSI" },
  { id: "cuentas", etiqueta: "Cuentas", descripcion: "Saldos" },
  { id: "presupuestos", etiqueta: "Presupuestos", descripcion: "Límites y pagos fijos" },
  { id: "metas", etiqueta: "Metas", descripcion: "Ahorro" },
  { id: "transferir", etiqueta: "Transferir", descripcion: "Mover dinero" },
] as const;

type IdPestana = (typeof PESTANAS)[number]["id"];

/** Cuenta cuántas entradas tiene cada área, para el contador de la pestaña. */
function contar(id: IdPestana, panel: Panel | null): number | null {
  if (!panel) return null;
  switch (id) {
    case "cuentas":
      return Object.keys(panel.cuentas?.cuentas ?? {}).length;
    case "tarjetas":
      return Object.keys(panel.tarjetas ?? {}).filter(
        (k) => !["status", "total"].includes(k),
      ).length;
    case "presupuestos":
      return Object.keys(panel.presupuesto ?? {}).length;
    default:
      return null;
  }
}

/**
 * Módulo de finanzas.
 *
 * Antes era una página de consulta: un resumen y unas tarjetas de solo lectura.
 * Ahora es una herramienta de gestión, así que el trabajo se reparte en
 * pestañas por área en vez de apilar todo en un scroll. El resumen del periodo
 * queda arriba y siempre visible: es el dato que se mira de reojo mientras se
 * edita otra cosa.
 *
 * `version` sube con cada cambio y fuerza a recargar el resumen: sin eso, los
 * totales se quedarían viejos mientras el usuario edita.
 */
export function PanelFinanzas() {
  const [pestana, setPestana] = useState<IdPestana>("movimientos");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const recargar = useCallback(async () => {
    try {
      const datos = await pedir<Panel>("/finanzas/panel?periodo=mensual");
      setPanel(datos);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
    }
  }, []);

  useEffect(() => {
    void recargar();
  }, [recargar, version]);

  const alCambiar = useCallback(() => setVersion((v) => v + 1), []);

  const resumen = panel?.resumen;
  const totalCuentas =
    panel?.cuentas?.total ??
    Object.values(panel?.cuentas?.cuentas ?? {}).reduce((s, c) => s + c.saldo, 0);

  const pagoTarjetas = Object.values(resumen?.pago_tarjetas_estimado ?? {}).reduce(
    (s, v) => s + v,
    0,
  );

  return (
    <div>
      {/* --- Resumen del periodo: siempre visible --- */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Cifra etiqueta="INGRESOS MES" valor={MONEDA.format(resumen?.ingresos ?? 0)} clase="text-ok" />
        <Cifra etiqueta="GASTOS MES" valor={MONEDA.format(resumen?.gastos ?? 0)} clase="text-error" />
        <Cifra
          etiqueta="BALANCE"
          valor={MONEDA.format(resumen?.balance ?? 0)}
          clase={(resumen?.balance ?? 0) < 0 ? "text-error" : "text-tinta"}
        />
        <Cifra etiqueta="EN CUENTAS" valor={MONEDA.format(totalCuentas)} clase="text-tinta" />
        <Cifra etiqueta="TARJETAS DEL MES" valor={MONEDA.format(pagoTarjetas)} clase="text-aviso" />
      </div>

      {error && (
        <p role="alert" className="mt-4 flex items-start gap-2 border border-error/50 bg-error/10 px-3 py-2.5 text-xs text-error">
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          {error}
        </p>
      )}

      {/* --- Pestañas --- */}
      <nav className="mt-8 flex flex-wrap gap-1 border-b border-linea-fuerte">
        {PESTANAS.map((p) => {
          const activa = pestana === p.id;
          const cuenta = contar(p.id, panel);

          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setPestana(p.id)}
              aria-current={activa ? "page" : undefined}
              className={[
                "border border-b-0 px-4 py-2.5 text-left transition-colors duration-300",
                activa
                  ? "border-linea-fuerte bg-panel text-tinta"
                  : "border-transparent text-tinta-apagada hover:text-tinta",
              ].join(" ")}
            >
              <span className="flex items-baseline gap-2">
                <span className="text-xs uppercase tracking-wider">{p.etiqueta}</span>
                {cuenta !== null && <span className="etiqueta">{cuenta}</span>}
              </span>
              <span className="mt-0.5 block text-xs text-tinta-apagada">
                {p.descripcion}
              </span>
            </button>
          );
        })}
      </nav>

      <div className="mt-6" role="region" aria-label={pestana}>
        {pestana === "movimientos" && <SeccionMovimientos />}
        {pestana === "tarjetas" && <TabTarjetas alCambiar={alCambiar} />}
        {pestana === "cuentas" && <TabCuentas alCambiar={alCambiar} />}
        {pestana === "presupuestos" && <TabPresupuestos alCambiar={alCambiar} />}
        {pestana === "metas" && <TabMetas alCambiar={alCambiar} />}
        {pestana === "transferir" && <TabTransferencias alCambiar={alCambiar} />}
      </div>
    </div>
  );
}

/** Bloque de una cifra del resumen. */
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
      <p className={`mt-2 font-mono text-base ${clase}`}>{valor}</p>
    </div>
  );
}
