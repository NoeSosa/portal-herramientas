# Sistema de diseño

Este documento registra las decisiones visuales del portal y de dónde vienen.
Sigue la skill `frontend-design`, que exige **elegir una dirección estética
explícita antes de escribir código** en lugar de improvisar.

---

## Dirección: Industrial / Utilitarian

Elegida del `references/aesthetic-playbook.md` de la skill, porque el portal es
una **herramienta de trabajo**, no un producto de consumo: debe verse seria,
densa en datos y funcional.

### Tipografía

| Rol | Fuente | Uso |
|---|---|---|
| Display | **Archivo Black** | Títulos. Siempre en mayúsculas, tracking −0.02em |
| Cuerpo | **Barlow** | Texto corriente y descripciones |
| Datos | **JetBrains Mono** | Etiquetas, estados, contadores, metadatos |

Las etiquetas de datos van en monoespaciada y mayúsculas con tracking 0.1em:
es lo que da el aire de "consola técnica" sin recurrir a adornos.

**Regla:** cualquier dato técnico (estado, versión, contador, modo) va en mono.
El texto que se lee como prosa va en Barlow.

### Color

Paleta de neutros fríos con **un solo acento** (la skill pide 1–2 máximo):

```
fondo        #1a1b20   superficie base, con hatch diagonal sutil
panel        #23242a   tarjetas y bloques
panel-alto   #2b2c33   hover de panel
linea        #383941   bordes
tinta        #e9e9ec   texto principal
tinta-tenue  #b5b6bd   descripciones
tinta-apagada#a8a9b0   etiquetas de datos
acento       #e0a12e   ámbar industrial: acciones e iconos activos
ok           #6ecf9a   estados correctos
aviso        #e0b93c   advertencias
error        #e0706b   fallos
```

Todos los pares texto/fondo **cumplen WCAG AA (≥4.5:1)**, verificado por
cálculo de luminancia, no a ojo. La medición más baja es 6.26:1 (etiquetas
sobre `panel-alto`).

### Signature move

Dos elementos, aplicados con consistencia:

1. **Corner ticks**: cada panel lleva corchetes en las esquinas superior
   izquierda e inferior derecha (clase `.panel`). Se dibujan con bordes CSS,
   no con imágenes ni pseudo-elementos extra.
2. **Hatch diagonal**: el fondo lleva líneas diagonales a 45° con opacidad muy
   baja (0.22). Debe ser *sutil*: si se nota, distrae; si no se nota, no aporta.

### Movimiento

Lineal, 0.25–0.35s (el playbook lo especifica para esta dirección). Los estados
de hover cambian borde y fondo, nunca posición: en una herramienta que se usa
mucho, los elementos que se mueven cansan.

`prefers-reduced-motion` está respetado globalmente.

---

## Reglas al añadir componentes

- **Iconos: SVG inline en `components/Iconos.tsx`.** Nunca emojis: se ven
  distintos en cada sistema operativo y rompen la coherencia tipográfica.
  Trazo 1.5px, `viewBox 24×24`, esquinas sin redondear.
- **Etiquetas de estado** en mono, mayúsculas, con `·` como separador.
  Formato: `TECNOLOGÍA · ESTADO`.
- **El estado "no disponible"** se comunica con la etiqueta "Próximamente" +
  icono atenuado. No se usa opacidad sobre toda la tarjeta: reduce la
  legibilidad y el contraste por debajo de AA.
- **Un dato por etiqueta.** No frases completas: "LOCAL · PIPER", no
  "Funciona localmente con Piper".

## Honestidad de la interfaz

Una tarjeta se marca `disponible: true` **solo si el endpoint existe y
funciona**. Prometer una herramienta que devuelve 404 es peor que marcar
"próximamente": el usuario pierde tiempo y confianza.

Todas las herramientas del catálogo están activas y su endpoint responde. Si
una deja de funcionar, se marca `disponible: false` en vez de dejarla
prometiendo algo que devuelve error.

---

## Verificación visual

`scripts/capturar.py` toma una captura del portal con Chrome headless. Requiere:

```bash
export LD_LIBRARY_PATH=/home/node/armlibs/extracted/usr/lib/aarch64-linux-gnu:/home/node/armlibs/extracted/lib/aarch64-linux-gnu
export FONTCONFIG_FILE=/home/node/armlibs/fc/fonts.conf
python scripts/capturar.py "http://<ip-frontend>/" /tmp/portal.png
```

Sirve para revisar el resultado sin abrir el navegador. Para el contraste,
el cálculo WCAG está en el historial de `docs/DISENO.md` (script de luminancia
sobre los tokens OKLCH).

---

## Patrones de interacción (decididos al implementar las herramientas)

Estas reglas se fijaron al añadir cada herramienta y aplican a las siguientes.

### Trabajo síncrono vs. cola

| Duración | Patrón | Ejemplos |
|---|---|---|
| Segundos | Petición HTTP normal, script en un hilo | TTS, analizar imagen, identificar canción |
| Minutos | Encolar en RQ + consultar estado + descargar | Separador por OCR, transcribir audio/video |

Regla: si puede tardar más que la paciencia de una petición HTTP (≈30 s), va a
la cola. El usuario debe poder cerrar la pestaña sin perder el trabajo.

### Progreso indeterminado

Cuando la duración no se conoce (el OCR no reporta porcentaje), la barra
**barre de ida y vuelta** (`animate-pulso`) en vez de avanzar. Una barra que
avanza sola mentiría sobre el avance real: es un dato falso en una interfaz
que presume de datos.

### Acciones con consecuencias: dos pasos

Registrar un movimiento en finanzas **nunca** ocurre en la misma petición que
el análisis. Primero se muestra lo detectado (monto, categoría, cuenta) y solo
tras la confirmación explícita del usuario se escribe en el ledger. El portal
no mueve dinero: eso sigue siendo conversación con el agente.

### Errores: qué se muestra y qué no

- **Al usuario:** el mensaje del dominio (`"cuota agotada"`,
  `"no se reconoció la canción"`), en el panel de resultado.
- **Nunca:** trazas, rutas internas del servidor ni `stderr` completo. Eso va
  al log del contenedor.
- Un resultado negativo no es un error de servidor: "no hay coincidencia" es
  **422**, no 500. La interfaz lo pinta distinto (ámbar vs. rojo).

### Panel del separador: flujo de 3 pasos

La vista simple del panel no usa pestañas: usa un flujo **Documentos →
Procesando → Resultados**. Es la misma lógica de "qué hago ahora" que un
formulario por pasos, aplicada a un trabajo que dura minutos.

Reglas que se aplicaron al rediseñarlo:

- **Un solo paso activo.** La barra de pasos marca dónde está el usuario y
  resume el estado de cada paso en una línea ("2 PDFs listos", "Página 8 de 17",
  "32 listos · 2 por revisar"). No hace falta abrir nada para saber cómo va.
- **Una acción principal por pantalla.** Documentos tiene "Procesar"; el cierre
  de la corrida tiene "Ver resultados" y "Descargar ZIP". Todo lo demás es
  secundario o vive en la vista completa.
- **El modo prueba se dice, no se esconde.** Si `dry_default` está activo, un
  aviso lo explica y ofrece "Procesar de verdad"; el botón principal no finge
  que está procesando de verdad.
- **Los datos importantes no se ocultan.** Miniatura + documento, página,
  serial y sede quedan visibles; el texto OCR crudo, las métricas y la cola son
  de la vista completa.
- **Resumen antes que listado.** Resultados muestra primero los contadores de la
  última corrida y la descarga completa; el historial de archivos queda plegado.
- **Nada de nombres internos en la vista simple.** `job`, `worker`, `OCR`,
  `dry`, `pipeline` y las rutas absolutas se sustituyen por "proceso", "lectura
  de páginas", "modo prueba" y fechas legibles.
