/**
 * Catálogo de herramientas del portal.
 *
 * Fuente única de verdad: la navegación, la portada y las rutas se generan
 * desde aquí. Añadir una herramienta es añadir una entrada.
 */

export interface Herramienta {
  /** Identificador estable; se usa como clave y en la URL. */
  id: string;
  /** Nombre visible. */
  nombre: string;
  /** Descripción breve para la tarjeta de portada. */
  descripcion: string;
  /** Emoji representativo. */
  icono: string;
  /** true si ya está implementada; false la muestra como "próximamente". */
  disponible: boolean;
  /** true si requiere subir archivos. */
  subeArchivos: boolean;
  /** true si tarda minutos y usa cola de trabajos. */
  esPesada: boolean;
  /** true si necesita clave de Gemini configurada. */
  requiereGemini: boolean;
}

export const HERRAMIENTAS: readonly Herramienta[] = [
  {
    id: "tts",
    nombre: "Texto a voz",
    descripcion:
      "Convierte texto a audio con voces en español mexicano (Piper).",
    icono: "🔊",
    disponible: true,
    subeArchivos: false,
    esPesada: false,
    requiereGemini: false,
  },
  {
    id: "vision",
    nombre: "Analizar imagen",
    descripcion:
      "Describe o extrae información de una imagen usando Gemini Vision.",
    icono: "🖼️",
    disponible: true,
    subeArchivos: true,
    esPesada: false,
    requiereGemini: true,
  },
  {
    id: "transcribir",
    nombre: "Transcribir audio o video",
    descripcion:
      "Obtén el texto de un audio o video. Devuelve transcripción descargable.",
    icono: "📝",
    disponible: true,
    subeArchivos: true,
    esPesada: true,
    requiereGemini: true,
  },
  {
    id: "canzone",
    nombre: "Identificar canción",
    descripcion: "Reconoce una canción por su audio y obtén su ficha.",
    icono: "🎵",
    disponible: true,
    subeArchivos: true,
    esPesada: false,
    requiereGemini: false,
  },
  {
    id: "comprobante",
    nombre: "Comprobante a finanzas",
    descripcion:
      "Sube un ticket o captura y se registra el movimiento automáticamente.",
    icono: "🧾",
    disponible: false,
    subeArchivos: true,
    esPesada: false,
    requiereGemini: true,
  },
  {
    id: "separador",
    nombre: "Separador por sede",
    descripcion:
      "Organiza PDFs por serial y sede. Para operaciones de PDF genéricas, usa Stirling-PDF.",
    icono: "📄",
    disponible: false,
    subeArchivos: true,
    esPesada: true,
    requiereGemini: false,
  },
  {
    id: "finanzas",
    nombre: "Panel de finanzas",
    descripcion: "Consulta movimientos, presupuestos y tarjetas.",
    icono: "💰",
    disponible: false,
    subeArchivos: false,
    esPesada: false,
    requiereGemini: false,
  },
] as const;

/** Devuelve una herramienta por su id, o undefined si no existe. */
export function buscarHerramienta(id: string): Herramienta | undefined {
  return HERRAMIENTAS.find((h) => h.id === id);
}
