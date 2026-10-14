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

Al cierre de esta sesión solo TTS está activa; el resto está marcado pendiente
aunque algunas tengan backend escrito pero sin router conectado.

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
