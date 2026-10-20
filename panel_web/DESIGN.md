# Dirección de diseño — panel público

## Concepto

**Archivo corporativo / editorial suizo.** La interfaz se trata como un
expediente de operación, no como un dashboard genérico: papel cálido, azul
marino profundo, reglas de 1 px, tipografía con contraste y una sola tabla
densa. El objetivo es que se sienta diseñada para Mainbit y para el trabajo de
separar documentos, no una plantilla de “AI dashboard”.

## Sistema

- **Azul marino** `#0A1B2E`: navegación, cabeceras, acciones primarias.
- **Papel** `#F2EEE6`: fondo; evita el blanco clínico.
- **Bermellón** `#C94B2D`: acento único para foco, progreso activo y enlaces.
- **Verde** `#2E6B4F`, **ámbar** `#A8641A`, **rojo** `#A32A1E` para estados.
- **IBM Plex Serif** para títulos; **IBM Plex Sans** para interfaz; **IBM Plex
  Mono** para etiquetas, metadatos y cifras. Las tres familias tienen carácter
  y pertenecen al mismo sistema.
- Sin sombras, sin gradientes decorativos, sin tarjetas redondeadas. La
  jerarquía se construye con reglas, espacio y tamaño tipográfico.
- La retícula azul del sidebar y el `PDF` en outline del login son textura
  contextual, no decoración genérica.

## Layout

- Sidebar fijo de 248 px con marca, navegación numerada y estado de operación.
- Cabecera editorial asimétrica: título serif grande a la izquierda, sesión a la
  derecha separada por una regla vertical.
- Ingreso en dos columnas: explicación + especificaciones a la izquierda,
  dropzone rectangular a la derecha.
- Resumen de cola en una franja de cuatro celdas, no en tarjetas.
- Tabla de documentos con columnas: documento, páginas, estado, avance, ETA y
  acciones. En móvil las filas se apilan con etiquetas mono.
- Login a dos paneles: manifiesto navy a la izquierda, acceso a la derecha.

## Referencias

- [Anthropic · frontend-design skill](https://github.com/anthropics/skills/blob/98669c11ca63e9c81c11501e1437e5c47b556621/skills/frontend-design/SKILL.md):
  dirección estética deliberada, tipografía con carácter, evitar “AI slop”.
- [GitHub Primer · Layout](https://primer.style/product/getting-started/foundations/layout/):
  jerarquía, densidad y patrones de tabla/empty state.
- [IBM Carbon Design System](https://carbondesignsystem.com/): retícula,
  tipografía IBM Plex y uso funcional del color.

No se instaló una skill de UI en el harness: se usaron estas referencias públicas
como guía y se implementó el diseño directamente.

## Fuentes

Las fuentes se cargan desde Google Fonts con `display=swap` y la CSP permite
solo `fonts.googleapis.com` (CSS) y `fonts.gstatic.com` (archivos). Si se
prefiere evitar la dependencia externa, descargar los WOFF2 de IBM Plex y
reemplazar el `<link>` por `@font-face` con `font-display: swap`; el resto del
CSS no cambia.

## Accesibilidad

- Contraste alto papel/navy y foco visible bermellón.
- `prefers-reduced-motion` desactiva las animaciones.
- Tabla con roles ARIA, `aria-valuenow` en progreso y `aria-live` en el estado
  de subida.
- Navegación por teclado en dropzone, botones y enlaces.
- En móvil, etiquetas visibles por celda; no se depende solo del color.
