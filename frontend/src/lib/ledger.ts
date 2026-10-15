/**
 * Cliente del ledger editable.
 *
 * Centraliza las operaciones de escritura de finanzas para que las cinco
 * pestañas no repitan `fetch`. Aquí vive el contrato con el backend: los
 * nombres de campo que espera cada endpoint.
 */

import { pedir } from "./api";

/** Resultado estándar de una escritura. */
export interface ResultadoOperacion {
  ok: boolean;
  mensaje: string;
  detalle?: Record<string, unknown> | null;
}

/** Cuenta con su saldo y tipo. */
export interface Cuenta {
  saldo: number;
  tipo: string;
}

/** Compra dentro de una tarjeta (MSI o de contado). */
export interface Compra {
  id: string;
  concepto: string;
  total: number;
  mensualidad: number;
  restantes: number;
  plazo: number;
  inicio?: string;
  proximo_pago?: string;
}

/** Tarjeta de crédito con sus compras. */
export interface Tarjeta {
  limite: number;
  corte: number;
  pago: number;
  compras?: Compra[];
}

export interface Presupuesto {
  limite: number;
  periodo?: string;
}

export interface Meta {
  objetivo: number;
  ahorrado: number;
  fecha_limite?: string | null | undefined;
}

export interface Recordatorio {
  monto: number;
  dia: number;
  nota?: string;
}

export interface ListaCuentas {
  cuentas: Record<string, Cuenta>;
  total: number;
}

/* --- Cuentas ------------------------------------------------------------ */

export async function leerCuentas(): Promise<ListaCuentas> {
  return pedir<ListaCuentas>("/finanzas/cuentas");
}

export async function guardarCuenta(
  nombre: string,
  saldo: number,
  tipo: string,
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(`/finanzas/cuentas/${encodeURIComponent(nombre)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nombre, saldo, tipo }),
  });
}

export async function borrarCuenta(nombre: string): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(`/finanzas/cuentas/${encodeURIComponent(nombre)}`, {
    method: "DELETE",
  });
}

export async function duplicarCuenta(
  nombre: string,
  nuevo: string,
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/cuentas/${encodeURIComponent(nombre)}/duplicar?nuevo=${encodeURIComponent(nuevo)}`,
    { method: "POST" },
  );
}

/* --- Presupuestos ------------------------------------------------------- */

export async function leerPresupuestos(): Promise<{ presupuesto: Record<string, Presupuesto> }> {
  return pedir("/finanzas/presupuestos");
}

export async function guardarPresupuesto(
  categoria: string,
  limite: number,
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/presupuestos/${encodeURIComponent(categoria)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ categoria, limite, periodo: "mensual" }),
    },
  );
}

export async function borrarPresupuesto(categoria: string): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/presupuestos/${encodeURIComponent(categoria)}`,
    { method: "DELETE" },
  );
}

/* --- Metas -------------------------------------------------------------- */

export async function leerMetas(): Promise<{ metas: Record<string, Meta> }> {
  return pedir("/finanzas/metas");
}

export async function guardarMeta(
  nombre: string,
  meta: Meta,
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(`/finanzas/metas/${encodeURIComponent(nombre)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nombre, ...meta }),
  });
}

export async function borrarMeta(nombre: string): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(`/finanzas/metas/${encodeURIComponent(nombre)}`, {
    method: "DELETE",
  });
}

/* --- Recordatorios ------------------------------------------------------ */

export async function leerRecordatorios(): Promise<{
  recordatorios: Record<string, Recordatorio>;
}> {
  return pedir("/finanzas/recordatorios");
}

export async function guardarRecordatorio(
  nombre: string,
  datos: Recordatorio,
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/recordatorios/${encodeURIComponent(nombre)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nombre, ...datos }),
    },
  );
}

export async function borrarRecordatorio(nombre: string): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/recordatorios/${encodeURIComponent(nombre)}`,
    { method: "DELETE" },
  );
}

/* --- Tarjetas y compras ------------------------------------------------- */

export async function leerTarjetas(): Promise<{ tarjetas: Record<string, Tarjeta> }> {
  return pedir("/finanzas/tarjetas");
}

export async function guardarTarjeta(
  nombre: string,
  tarjeta: { limite: number; corte: number; pago: number },
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(`/finanzas/tarjetas/${encodeURIComponent(nombre)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nombre, ...tarjeta }),
  });
}

export async function borrarTarjeta(nombre: string): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(`/finanzas/tarjetas/${encodeURIComponent(nombre)}`, {
    method: "DELETE",
  });
}

export async function crearCompra(
  tarjeta: string,
  compra: { concepto: string; total: number; plazo: number; inicio?: string | null | undefined },
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/tarjetas/${encodeURIComponent(tarjeta)}/compras`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(compra),
    },
  );
}

export async function editarCompra(
  tarjeta: string,
  compraId: string,
  compra: { concepto: string; total: number; plazo: number; inicio?: string | null | undefined },
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/tarjetas/${encodeURIComponent(tarjeta)}/compras/${encodeURIComponent(compraId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(compra),
    },
  );
}

export async function duplicarCompra(
  tarjeta: string,
  compraId: string,
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/tarjetas/${encodeURIComponent(tarjeta)}/compras/${encodeURIComponent(compraId)}/duplicar`,
    { method: "POST" },
  );
}

export async function borrarCompra(
  tarjeta: string,
  compraId: string,
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/tarjetas/${encodeURIComponent(tarjeta)}/compras/${encodeURIComponent(compraId)}`,
    { method: "DELETE" },
  );
}

/* --- Operaciones que mueven dinero -------------------------------------- */

export async function transferir(datos: {
  origen: string;
  destino: string;
  monto: number;
  nota?: string;
}): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>("/finanzas/transferir?confirmar=true", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(datos),
  });
}

export async function pagarTarjeta(
  tarjeta: string,
  datos: { cuenta?: string | undefined; monto?: number | null | undefined },
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/tarjetas/${encodeURIComponent(tarjeta)}/pagar?confirmar=true`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tarjeta, ...datos }),
    },
  );
}

/* --- Movimientos (ya existían) ------------------------------------------ */

export interface Movimiento {
  id: string;
  fecha: string | null;
  tipo: string | null;
  categoria: string | null;
  monto: number | null;
  nota: string | null;
  cuenta: string | null;
}

export interface ListaMovimientos {
  total_transacciones: number;
  total_ingresos: number;
  total_gastos: number;
  balance: number;
  transacciones: Movimiento[];
}

export async function leerMovimientos(filtros: {
  buscar?: string;
  categoria?: string;
  tipo?: string;
  desde?: string;
  hasta?: string;
  limite?: number;
}): Promise<ListaMovimientos> {
  const p = new URLSearchParams();
  if (filtros.buscar?.trim()) p.set("buscar", filtros.buscar.trim());
  if (filtros.categoria?.trim()) p.set("categoria", filtros.categoria.trim());
  if (filtros.tipo) p.set("tipo", filtros.tipo);
  if (filtros.desde) p.set("desde", filtros.desde);
  if (filtros.hasta) p.set("hasta", filtros.hasta);
  p.set("limite", String(filtros.limite ?? 200));

  return pedir<ListaMovimientos>(`/finanzas/movimientos?${p.toString()}`);
}

export async function crearMovimiento(datos: {
  monto: number;
  tipo: string;
  categoria: string;
  nota?: string | undefined;
  fecha?: string | null | undefined;
  cuenta?: string | null | undefined;
}): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>("/finanzas/movimientos", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(datos),
  });
}

export async function editarMovimiento(
  id: string,
  datos: {
    monto?: number | null | undefined;
    categoria?: string | null | undefined;
    nota?: string | null | undefined;
    fecha?: string | null | undefined;
    cuenta?: string | null | undefined;
  },
): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(`/finanzas/movimientos/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(datos),
  });
}

export async function borrarMovimiento(id: string): Promise<ResultadoOperacion> {
  return pedir<ResultadoOperacion>(
    `/finanzas/movimientos/${encodeURIComponent(id)}?confirmar=true`,
    { method: "DELETE" },
  );
}

export async function duplicarMovimiento(movimiento: Movimiento): Promise<ResultadoOperacion> {
  return crearMovimiento({
    monto: movimiento.monto ?? 0,
    tipo: movimiento.tipo ?? "gasto",
    categoria: movimiento.categoria ?? "otro",
    nota: `${movimiento.nota ?? ""} (copia)`.trim(),
    fecha: movimiento.fecha,
    cuenta: movimiento.cuenta,
  });
}
