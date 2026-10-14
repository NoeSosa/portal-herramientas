/**
 * Catálogo de herramientas del portal.
 *
 * Fuente única de verdad: la navegación, la portada y las rutas se generan
 * desde aquí. Añadir una herramienta es añadir una entrada.
 */

/** Iconos disponibles. Debe coincidir con `components/Iconos.tsx`. */
export type NombreIcono =
  | "voz"
  | "imagen"
  | "transcripcion"
  | "cancion"
  | "comprobante"
  | "pdf"
  | "finanzas";

export interface Herramienta {
  /** Identificador estable; se usa como clave y en la URL. */
  id: string;
  /** Nombre visible. */
  nombre: string;
  /** Descripción breve para la tarjeta de portada. */
  descripcion: string;
  /** Icono SVG asociado. */
  icono: NombreIcono;
  /** true si ya está implementada; false la muestra como "próximamente". */
  disponible: boolean;
  /** true si requiere subir archivos. */
  subeArchivos: boolean;
  /** true si tarda minutos y usa cola de trabajos. */
  esPesada: boolean;
  /** Etiqueta corta de estado para la tarjeta (industrial: dato, no frase). */
  etiqueta: string;
}

export const HERRAMIENTAS: readonly Herramienta[] = [
  {
    id: "tts",
    nombre: "Texto a voz",
    descripcion: "Convierte texto a audio con voces en español mexicano.",
    icono: "voz",
    disponible: true,
    subeArchivos: false,
    esPesada: false,
    etiqueta: "LOCAL · PIPER",
  },
  {
    id: "vision",
    nombre: "Analizar imagen",
    descripcion: "Describe o extrae información de una imagen.",
    icono: "imagen",
    disponible: false,
    subeArchivos: true,
    esPesada: false,
    etiqueta: "GEMINI · VISION",
  },
  {
    id: "transcribir",
    nombre: "Transcribir audio o video",
    descripcion: "Obtén el texto de un audio o video, descargable.",
    icono: "transcripcion",
    disponible: false,
    subeArchivos: true,
    esPesada: true,
    etiqueta: "COLA · PESADA",
  },
  {
    id: "canzone",
    nombre: "Identificar canción",
    descripcion: "Reconoce una canción por su audio y obtén su ficha.",
    icono: "cancion",
    disponible: false,
    subeArchivos: true,
    esPesada: false,
    etiqueta: "SHAZAM · SIN CLAVE",
  },
  {
    id: "comprobante",
    nombre: "Comprobante a finanzas",
    descripcion: "Sube un ticket y el movimiento se registra automáticamente.",
    icono: "comprobante",
    disponible: false,
    subeArchivos: true,
    esPesada: false,
    etiqueta: "PENDIENTE",
  },
  {
    id: "separador",
    nombre: "Separador por sede",
    descripcion: "Organiza PDFs de contadores de impresión por serial y sede.",
    icono: "pdf",
    disponible: false,
    subeArchivos: true,
    esPesada: true,
    etiqueta: "OCR · PENDIENTE",
  },
  {
    id: "finanzas",
    nombre: "Panel de finanzas",
    descripcion: "Consulta movimientos, presupuestos y tarjetas.",
    icono: "finanzas",
    disponible: false,
    subeArchivos: false,
    esPesada: false,
    etiqueta: "PENDIENTE",
  },
] as const;

/** Devuelve una herramienta por su id, o undefined si no existe. */
export function buscarHerramienta(id: string): Herramienta | undefined {
  return HERRAMIENTAS.find((h) => h.id === id);
}
