/**
 * Iconos SVG inline.
 *
 * Por qué inline y no una librería:
 *  - Cero dependencias nuevas en el bundle.
 *  - Heredan `currentColor`, así el color lo controla el CSS del contexto.
 *  - Se ven idénticos en cualquier sistema operativo (los emojis no).
 *
 * Convenciones de dibujo:
 *  - viewBox 24×24, trazo de 1.5px, sin relleno.
 *  - `strokeLinecap="square"` y esquinas sin redondear: coherente con la
 *    dirección Industrial/Utilitarian.
 *  - `aria-hidden` por defecto: el icono acompaña a un texto, no lo sustituye.
 */

interface PropsIcono {
  /** Tamaño en píxeles (cuadrado). Por defecto 20. */
  tam?: number;
  /** Clases adicionales para el elemento `svg`. */
  clase?: string;
}

/** Atributos comunes a todos los iconos del sistema. */
const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "square" as const,
  strokeLinejoin: "miter" as const,
  "aria-hidden": true,
  focusable: false,
};

/** Contenedor del icono: fija el viewBox y aplica los atributos base. */
function Icono({
  tam = 20,
  clase,
  children,
}: PropsIcono & { children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" width={tam} height={tam} className={clase} {...base}>
      {children}
    </svg>
  );
}

/** Caja de herramientas. */
export function IconoHerramientas(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M3 8h18v11H3z" />
      <path d="M9 8V5h6v3" />
      <path d="M3 13h18" />
      <path d="M11 13v2h2v-2" />
    </Icono>
  );
}

/** Onda de audio, para texto a voz. */
export function IconoVoz(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M4 10v4" />
      <path d="M8 7v10" />
      <path d="M12 4v16" />
      <path d="M16 8v8" />
      <path d="M20 11v2" />
    </Icono>
  );
}

/** Imagen enmarcada, para análisis de imagen. */
export function IconoImagen(props: PropsIcono) {
  return (
    <Icono {...props}>
      <rect x="3" y="4" width="18" height="16" />
      <circle cx="8.5" cy="9.5" r="1.5" />
      <path d="M3 17l5-5 4 4 3-3 6 6" />
    </Icono>
  );
}

/** Documento con líneas, para transcripción. */
export function IconoTranscripcion(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M15 3v4h4" />
      <path d="M9 12h7" />
      <path d="M9 16h7" />
    </Icono>
  );
}

/** Nota musical, para identificación de canciones. */
export function IconoCancion(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M9 18V6l10-2v12" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="16" cy="16" r="3" />
    </Icono>
  );
}

/** Recibo, para comprobantes. */
export function IconoComprobante(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M6 3v18l3-2 3 2 3-2 3 2V3z" />
      <path d="M9 8h6" />
      <path d="M9 12h6" />
      <path d="M9 16h3" />
    </Icono>
  );
}

/** Pila de hojas, para el separador de PDFs. */
export function IconoPdf(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M8 3h7l4 4v12H8z" />
      <path d="M15 3v4h4" />
      <path d="M5 7v14h10" />
    </Icono>
  );
}

/** Gráfica de barras, para el panel de finanzas. */
export function IconoFinanzas(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M4 20h16" />
      <path d="M7 20V11h3v9" />
      <path d="M12 20V6h3v14" />
      <path d="M17 20V14h3v6" />
    </Icono>
  );
}

/** Flecha de retorno. */
export function IconoVolver(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M19 12H5" />
      <path d="M11 6l-6 6 6 6" />
    </Icono>
  );
}

/** Marca de verificación, para estados correctos. */
export function IconoOk(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M4 12l5 5L20 6" />
    </Icono>
  );
}

/** Triángulo de advertencia, para errores y avisos. */
export function IconoAviso(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M12 3l9 17H3z" />
      <path d="M12 10v4" />
      <path d="M12 17h.01" />
    </Icono>
  );
}

/** Descarga: flecha hacia una bandeja. */
export function IconoDescarga(props: PropsIcono) {
  return (
    <Icono {...props}>
      <path d="M12 3v12" />
      <path d="M7 11l5 5 5-5" />
      <path d="M4 20h16" />
    </Icono>
  );
}
