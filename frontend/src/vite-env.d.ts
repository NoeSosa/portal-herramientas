/// <reference types="vite/client" />

/**
 * Tipos de los módulos de Vite.
 *
 * TypeScript no sabe que `import "./index.css"` es válido: Vite lo resuelve
 * en build, pero el compilador necesita esta declaración para no marcar error.
 */
declare module "*.css" {
  const contenido: string;
  export default contenido;
}

declare module "*.svg" {
  const url: string;
  export default url;
}

declare module "*.png" {
  const url: string;
  export default url;
}
