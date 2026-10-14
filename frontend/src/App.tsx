import { useCallback, useEffect, useState } from "react";

import { Layout } from "./components/Layout";
import { TarjetaHerramienta } from "./components/TarjetaHerramienta";
import { pedir } from "./lib/api";
import { HERRAMIENTAS } from "./lib/herramientas";
import { PaginaTTS } from "./pages/PaginaTTS";

/** Estado de conexión con el backend. */
type EstadoConexion = "cargando" | "ok" | "error";

interface InfoBackend {
  app: string;
  version: string;
  entorno: string;
  capacidades: {
    vision: boolean;
    transcripcion: boolean;
    correo: boolean;
  };
}

export default function App() {
  const [vista, setVista] = useState<string>("inicio");
  const [conexion, setConexion] = useState<EstadoConexion>("cargando");
  const [info, setInfo] = useState<InfoBackend | null>(null);

  // Comprueba al arrancar que el backend responde. Si el túnel SSH se cayó,
  // el usuario lo ve de inmediato en vez de al usar una herramienta.
  useEffect(() => {
    let vigente = true;

    pedir<InfoBackend>("/info")
      .then((datos) => {
        if (!vigente) return;
        setInfo(datos);
        setConexion("ok");
      })
      .catch(() => {
        if (vigente) setConexion("error");
      });

    return () => {
      vigente = false;
    };
  }, []);

  const volver = useCallback(() => setVista("inicio"), []);

  if (vista === "tts") {
    return (
      <Layout alVolver={volver}>
        <PaginaTTS />
      </Layout>
    );
  }

  return (
    <Layout>
      <section aria-labelledby="titulo-portada">
        <h2 id="titulo-portada" className="text-lg font-semibold tracking-tight">
          Herramientas disponibles
        </h2>
        <p className="mt-1 text-sm text-texto-tenue">
          Herramientas internas. El acceso es exclusivamente por túnel SSH.
        </p>

        <EstadoConexionUI estado={conexion} info={info} />

        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {HERRAMIENTAS.map((herramienta) => (
            <TarjetaHerramienta
              key={herramienta.id}
              herramienta={herramienta}
              alAbrir={setVista}
            />
          ))}
        </div>
      </section>
    </Layout>
  );
}

/** Aviso de estado del backend, en una línea. */
function EstadoConexionUI({
  estado,
  info,
}: {
  estado: EstadoConexion;
  info: InfoBackend | null;
}) {
  if (estado === "cargando") {
    return (
      <p className="mt-4 text-xs text-texto-tenue">Comprobando conexión…</p>
    );
  }

  if (estado === "error") {
    return (
      <p
        role="alert"
        className="mt-4 text-xs text-error border border-error/40 bg-error/10 rounded-control px-3 py-2"
      >
        No se pudo contactar al backend. Verifica que el túnel SSH siga activo.
      </p>
    );
  }

  return (
    <p className="mt-4 text-xs text-exito">
      Conectado · v{info?.version} · {info?.entorno}
    </p>
  );
}
