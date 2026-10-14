import { useCallback, useEffect, useState } from "react";

import { IconoAviso, IconoOk } from "./components/Iconos";
import { Layout } from "./components/Layout";
import { TarjetaHerramienta } from "./components/TarjetaHerramienta";
import { pedir } from "./lib/api";
import { HERRAMIENTAS, buscarHerramienta } from "./lib/herramientas";
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
      <Layout alVolver={volver} titulo="Texto a voz">
        <PaginaTTS />
      </Layout>
    );
  }

  const activa = buscarHerramienta(vista);

  return (
    <Layout titulo={activa?.nombre}>
      <section aria-labelledby="titulo-portada">
        <div className="regla">
          <span className="etiqueta text-tinta">Herramientas</span>
          <span className="etiqueta ml-auto">
            {HERRAMIENTAS.filter((h) => h.disponible).length}/
            {HERRAMIENTAS.length} activas
          </span>
        </div>

        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-tinta-tenue">
          Herramientas internas. El acceso es exclusivamente por túnel SSH.
        </p>

        <EstadoConexionUI estado={conexion} info={info} />

        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
    return <p className="mt-6 etiqueta">Comprobando conexión…</p>;
  }

  if (estado === "error") {
    return (
      <p
        role="alert"
        className="mt-6 inline-flex items-center gap-2 border border-error/50 bg-error/10
                   px-3 py-2 text-xs text-error"
      >
        <IconoAviso tam={14} />
        No se pudo contactar al backend. Verifica que el túnel SSH siga activo.
      </p>
    );
  }

  return (
    <p className="mt-6 inline-flex items-center gap-2 text-xs text-ok">
      <IconoOk tam={14} />
      <span className="etiqueta text-ok">
        Conectado · v{info?.version} · {info?.entorno}
      </span>
    </p>
  );
}
