/**
 * Cliente HTTP del portal.
 *
 * Un único punto de salida hacia el backend: centraliza el manejo de errores
 * y evita que cada componente reinvente el fetch.
 */

/** Error de API con el mensaje ya listo para mostrar al usuario. */
export class ErrorApi extends Error {
  constructor(
    mensaje: string,
    readonly estado: number,
  ) {
    super(mensaje);
    this.name = "ErrorApi";
  }
}

/** Respuesta cruda de un error del backend. */
interface CuerpoError {
  detail?: string | Array<{ msg?: string }>;
}

/** Traduce el cuerpo de error del backend a un mensaje legible. */
function extraerMensaje(cuerpo: CuerpoError, estado: number): string {
  const { detail } = cuerpo;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const mensajes = detail
      .map((e) => e?.msg)
      .filter((m): m is string => Boolean(m));
    if (mensajes.length > 0) {
      return mensajes.join("; ");
    }
  }

  return `Error del servidor (${estado}).`;
}

/** Realiza una petición JSON al backend y devuelve el resultado tipado. */
export async function pedir<T>(
  ruta: string,
  opciones: RequestInit = {},
): Promise<T> {
  let respuesta: Response;

  try {
    respuesta = await fetch(`/api${ruta}`, {
      headers: { Accept: "application/json", ...opciones.headers },
      ...opciones,
    });
  } catch {
    throw new ErrorApi(
      "No se pudo contactar al servidor. ¿Sigue activo el túnel SSH?",
      0,
    );
  }

  if (!respuesta.ok) {
    let cuerpo: CuerpoError = {};
    try {
      cuerpo = (await respuesta.json()) as CuerpoError;
    } catch {
      // Respuesta sin JSON: se usa el mensaje genérico.
    }
    throw new ErrorApi(extraerMensaje(cuerpo, respuesta.status), respuesta.status);
  }

  return (await respuesta.json()) as T;
}

/** Sube uno o más archivos a un endpoint de herramienta.
 *
 * ⚠️ El campo del archivo se llama **`archivo`** (singular) porque así lo
 * declaran los endpoints del backend (`archivo: UploadFile = File(...)`).
 * Estuvo como `archivos` y eso rompía **todas** las herramientas de subida con
 * un 422 `Field required` de FastAPI: el archivo llegaba con un nombre que
 * ningún endpoint leía. Si se renombra aquí, hay que renombrarlo allá.
 */
export async function subirArchivos<T>(
  ruta: string,
  archivos: File[],
  campos: Record<string, string> = {},
): Promise<T> {
  const datos = new FormData();
  for (const archivo of archivos) {
    datos.append("archivo", archivo, archivo.name);
  }
  for (const [clave, valor] of Object.entries(campos)) {
    datos.append(clave, valor);
  }

  return pedir<T>(ruta, { method: "POST", body: datos });
}
