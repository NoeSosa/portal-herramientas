import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "../components/Iconos";
import { PanelFinanzas } from "../components/PanelFinanzas";
import { pedir } from "../lib/api";

interface EstadoMotor {
  disponible: boolean;
  motivo: string;
  usuario: string;
}

/**
 * Herramienta «Finanzas».
 *
 * La página solo resuelve dos cosas: comprobar que el ledger es accesible y
 * montar el módulo. Toda la gestión vive en `PanelFinanzas` y sus pestañas.
 */
export function PaginaFinanzas() {
  const [motor, setMotor] = useState<EstadoMotor | null>(null);
  const [error, setError] = useState<string | null>(null);

  const comprobar = useCallback(async () => {
    try {
      setMotor(await pedir<EstadoMotor>("/finanzas/estado"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido.");
    }
  }, []);

  useEffect(() => {
    void comprobar();
  }, [comprobar]);

  if (error) {
    return (
      <p role="alert" className="flex items-start gap-2 border border-error/50 bg-error/10 px-3 py-2.5 text-xs text-error">
        <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
        {error}
      </p>
    );
  }

  if (!motor) {
    return <p className="text-xs text-tinta-apagada">Comprobando el ledger…</p>;
  }

  return (
    <section aria-labelledby="titulo-finanzas">
      <div className="regla">
        <span className="etiqueta">Finanzas</span>
        <span className="etiqueta ml-auto">
          {motor.usuario?.toUpperCase() ?? "NOE"}
        </span>
      </div>

      {motor.disponible === false && (
        <p
          role="alert"
          className="mt-6 flex items-start gap-2 border border-error/50 bg-error/10
                     px-3 py-2.5 text-xs text-error leading-relaxed"
        >
          <IconoAviso tam={14} clase="shrink-0 mt-0.5" />
          <span>
            El módulo no está disponible en este despliegue.
            {motor.motivo && <> {motor.motivo}</>}
          </span>
        </p>
      )}

      <div className="mt-6">
        <PanelFinanzas />
      </div>

      <p className="mt-8 flex items-center gap-2 text-xs text-tinta-apagada">
        <IconoOk tam={14} clase="text-ok" />
        <span className="etiqueta">
          Todo lo que edites aquí lo ve el agente al instante.
        </span>
      </p>
    </section>
  );
}
