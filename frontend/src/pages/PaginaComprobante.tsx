import { useCallback, useEffect, useRef, useState } from "react";

import { IconoAviso, IconoComprobante, IconoOk } from "../components/Iconos";
import { pedir, subirArchivos } from "../lib/api";

interface FilaComprobante {
  estado: "listo" | "registrado" | "falta-dato" | "descartado";
  datos: {
    monto?: number | null;
    tipo?: string | null;
    concepto?: string | null;
    categoria?: string | null;
    cuenta?: string | null;
    fecha?: string | null;
  } | null;
  mensaje: string | null;
}

interface RespuestaAnalisis {
  analizado: boolean;
  registrado: boolean;
  filas: FilaComprobante[];
  resumen: string;
}

interface EstadoMotor {
  disponible: boolean;
  motivo: string;
  tipos: string[];
  max_upload_mb: number;
}

type Fase = "inactivo" | "analizando" | "revisando" | "registrando" | "registrado" | "error";

const ACEPTA = "image/jpeg,image/png,image/webp";

const MONEDA = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

/**
 * Herramienta «Comprobante a finanzas».
 *
 * **Dos pasos a propósito.** Primero se analiza el ticket y se muestra lo que
 * la visión entendió; solo si el usuario confirma se registra en el ledger.
 * Registrar dinero sin que nadie lo mire es exactamente el tipo de acción que
 * este portal no debe automatizar.
 */
export function PaginaComprobante() {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [cuenta, setCuenta] = useState("");
  const [fase, setFase] = useState<Fase>("inactivo");
  const [error, setError] = useState<string | null>(null);
  const [analisis, setAnalisis] = useState<RespuestaAnalisis | null>(null);
  const [registro, setRegistro] = useState<RespuestaAnalisis | null>(null);
  const [motor, setMotor] = useState<EstadoMotor | null>(null);

  const entrada = useRef<HTMLInputElement>(null);
  const arrastrando = useRef(false);
  const [resaltado, setResaltado] = useState(false);

  useEffect(() => {
    let vigente = true;
    pedir<EstadoMotor>("/finanzas/comprobante/estado")
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

  const elegir = useCallback((nuevo: File | null) => {
    setArchivo(nuevo);
    setAnalisis(null);
    setRegistro(null);
    setError(null);
    setFase("inactivo");
  }, []);

  const analizar = useCallback(async () => {
    if (!archivo) return;

    setFase("analizando");
    setError(null);
    setRegistro(null);

    try {
      const respuesta = await subirArchivos<RespuestaAnalisis>(
        "/finanzas/comprobante",
        [archivo],
        { cuenta },
      );
      setAnalisis(respuesta);
      setFase("revisando");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
      setFase("error");
    }
  }, [archivo, cuenta]);

  const registrar = useCallback(async () => {
    if (!archivo) return;

    setFase("registrando");
    setError(null);

    try {
      const respuesta = await subirArchivos<RespuestaAnalisis>(
        "/finanzas/comprobante/registrar",
        [archivo],
        { cuenta },
      );
      setRegistro(respuesta);
      setFase("registrado");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
      setFase("error");
    }
  }, [archivo, cuenta]);

  const ocupado = fase === "analizando" || fase === "registrando";
  const reconocidos = (analisis?.filas ?? []).filter((f) => f.estado === "listo");
  const puedeRegistrar = fase === "revisando" && reconocidos.length > 0;

  return (
    <section aria-labelledby="titulo-comprobante">
      <div className="regla">
        <span className="etiqueta">Comprobante a finanzas</span>
        <span className="etiqueta ml-auto">2 PASOS · CONFIRMACIÓN MANUAL</span>
      </div>

      {motor?.disponible === false && (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          <span>
            No se pueden procesar comprobantes en este despliegue.
            {motor.motivo && <> {motor.motivo}</>}
          </span>
        </p>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_22rem]">
        <form
          className="panel p-6 space-y-6"
          onSubmit={(e) => {
            e.preventDefault();
            void analizar();
          }}
        >
          <div>
            <span className="etiqueta block mb-2">Foto del ticket</span>

            <div
              onDragOver={(e) => {
                e.preventDefault();
                if (!arrastrando.current) {
                  arrastrando.current = true;
                  setResaltado(true);
                }
              }}
              onDragLeave={() => {
                arrastrando.current = false;
                setResaltado(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                arrastrando.current = false;
                setResaltado(false);
                elegir(e.dataTransfer.files?.[0] ?? null);
              }}
              className={[
                "border border-dashed px-4 py-8 text-center transition-colors duration-300",
                resaltado ? "border-acento bg-acento/5" : "border-linea",
              ].join(" ")}
            >
              <IconoComprobante tam={28} clase="mx-auto text-tinta-apagada" />
              <p className="mt-3 text-xs text-tinta-tenue">
                Arrastra la foto o
                <button
                  type="button"
                  onClick={() => entrada.current?.click()}
                  className="ml-1 underline decoration-linea underline-offset-2
                             hover:text-acento hover:decoration-acento transition-colors duration-300"
                >
                  elige un archivo
                </button>
              </p>
              <p className="mt-2 etiqueta">
                JPG · PNG · WEBP · MÁX {motor?.max_upload_mb ?? 50} MB
              </p>
            </div>

            <input
              ref={entrada}
              type="file"
              accept={ACEPTA}
              className="hidden"
              onChange={(e) => elegir(e.target.files?.[0] ?? null)}
            />
          </div>

          <div>
            <label htmlFor="cuenta" className="etiqueta block mb-2">
              Cuenta o tarjeta (opcional)
            </label>
            <input
              id="cuenta"
              value={cuenta}
              onChange={(e) => setCuenta(e.target.value)}
              placeholder="la infiere la visión si se deja vacío"
              className="w-full rounded-control border border-linea bg-fondo px-3 py-2.5
                         font-mono text-xs text-tinta placeholder:text-tinta-apagada
                         focus:border-acento"
            />
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <button
              type="submit"
              disabled={!archivo || ocupado || motor?.disponible === false}
              className="rounded-control border border-acento bg-acento px-6 py-2.5
                         text-xs uppercase tracking-wider text-fondo font-medium
                         hover:bg-acento/90 disabled:opacity-30 disabled:cursor-not-allowed
                         transition-opacity duration-300"
            >
              {fase === "analizando" ? "Analizando…" : "Analizar ticket"}
            </button>

            {puedeRegistrar && (
              <button
                type="button"
                onClick={() => void registrar()}
                className="rounded-control border border-ok bg-ok/15 px-6 py-2.5
                           text-xs uppercase tracking-wider text-ok font-medium
                           hover:bg-ok/25 transition-colors duration-300"
              >
                Registrar en finanzas
              </button>
            )}

            {archivo && !ocupado && (
              <button
                type="button"
                onClick={() => elegir(null)}
                className="text-xs text-tinta-apagada hover:text-tinta transition-colors duration-300"
              >
                Quitar
              </button>
            )}
          </div>

          <p className="text-xs text-tinta-apagada leading-relaxed">
            Analizar no registra nada: primero verás el monto, la categoría y la
            cuenta que se detectaron, y tú decides si se guardan.
          </p>
        </form>

        <aside aria-live="polite" className="space-y-4">
          <div className="regla">
            <span className="etiqueta">
              {fase === "registrado" ? "Registrado" : "Detección"}
            </span>
          </div>

          {fase === "inactivo" && (
            <p className="text-xs text-tinta-apagada leading-relaxed">
              Aquí verás lo que se detectó en el ticket antes de guardar nada.
            </p>
          )}

          {ocupado && <p className="text-xs text-tinta-tenue">Leyendo el ticket…</p>}

          {error && (
            <p
              role="alert"
              className="flex items-start gap-2 border border-error/50 bg-error/10
                         px-3 py-2.5 text-xs text-error leading-relaxed"
            >
              <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
              {error}
            </p>
          )}

          {(analisis || registro) && (
            <div className="panel p-4 space-y-3">
              <p className="flex items-center gap-2 text-xs">
                {fase === "registrado" ? (
                  <>
                    <IconoOk tam={14} clase="text-ok" />
                    <span className="etiqueta text-ok">GUARDADO</span>
                  </>
                ) : (
                  <span className="etiqueta text-aviso">SIN GUARDAR</span>
                )}
              </p>

              <p className="text-xs text-tinta-tenue leading-relaxed">
                {(registro ?? analisis)?.resumen}
              </p>

              {(registro ?? analisis)?.filas.map((fila, indice) => (
                <div key={indice} className="border-t border-linea pt-3">
                  <p className="etiqueta mb-2">{fila.estado.toUpperCase()}</p>

                  {fila.datos && (
                    <dl className="space-y-1">
                      {[
                        ["MONTO", fila.datos.monto != null ? MONEDA.format(fila.datos.monto) : null],
                        ["TIPO", fila.datos.tipo],
                        ["CATEGORÍA", fila.datos.categoria],
                        ["CUENTA", fila.datos.cuenta],
                        ["CONCEPTO", fila.datos.concepto],
                        ["FECHA", fila.datos.fecha],
                      ]
                        .filter(([, valor]) => Boolean(valor))
                        .map(([clave, valor]) => (
                          <div key={clave} className="flex gap-3 text-xs">
                            <dt className="etiqueta w-20 shrink-0">{clave}</dt>
                            <dd className="text-tinta-tenue">{valor}</dd>
                          </div>
                        ))}
                    </dl>
                  )}

                  {fila.mensaje && (
                    <p className="text-xs text-aviso leading-relaxed">{fila.mensaje}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
