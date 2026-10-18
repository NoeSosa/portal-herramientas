/**
 * Prueba de MAQUETACIÓN del panel (regresión del 18/oct/2026).
 *
 * Sirve para un bug real: al animar las cajas del OCR se usó la clase `.caja`,
 * que ya era la de las TARJETAS del panel (`position:relative`). La regla nueva
 * (`position:absolute`) sacó a todas las tarjetas del flujo → se desalinearon y
 * el log quedó encima de los demás elementos. Aquí se comprueba que ninguna
 * tarjeta esté fuera de flujo y que las cajas del OCR usen su propia clase.
 *
 * ✅ VALIDADO: 18/oct/2026 — detectó el bug antes de desplegar.
 * 📘 EJEMPLO:
 *     node separador_pdfs/panel/tests/layout_jsdom.mjs
 *   (necesita el panel levantado en http://hw-frontend/panel/ o ajustar BASE;
 *    jsdom se resuelve del proyecto o de /tmp/jsdomtest/node_modules)
 *
 * Requisitos: jsdom. Si no está instalado aquí:
 *   mkdir -p /tmp/jsdomtest && cd /tmp/jsdomtest && npm i jsdom
 */
let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = await import("jsdom"));
} catch {
  ({ JSDOM, VirtualConsole } = await import("/tmp/jsdomtest/node_modules/jsdom/lib/api.js"));
}

const BASE = process.env.PANEL_BASE || "http://hw-frontend";
const vc = new VirtualConsole();
const errores = [];
vc.on("jsdomError", (e) => errores.push("jsdomError: " + (e.detail?.message || e.message)));

const html = await (await fetch(BASE + "/panel/")).text();
const fetchReal = globalThis.fetch;
const dom = new JSDOM(html, {
  url: BASE + "/panel/", runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.fetch = (u, o) => fetchReal(new URL(String(u), BASE + "/panel/").href, o);
    class ES { constructor(u) { this._h = {}; w.__es = this; } addEventListener(e, c) { this._h[e] = c; } close() {} }
    w.EventSource = ES;
  },
});
const W = dom.window, D = W.document, $ = (i) => D.getElementById(i);
await new Promise((r) => setTimeout(r, 900));
const gcs = (el) => W.getComputedStyle(el);

let fallos = 0;
const check = (ok, texto, detalle = "") => {
  if (!ok) fallos++;
  console.log(`${ok ? "✓" : "✗"} ${texto}${detalle ? " → " + detalle : ""}`);
};

/* 1 · Ninguna tarjeta puede estar fuera del flujo. */
const tarjetas = [...D.querySelectorAll(".caja")];
const fuera = tarjetas.filter((t) => ["absolute", "fixed"].includes(gcs(t).position));
check(fuera.length === 0, `${tarjetas.length} tarjetas en el flujo`,
  fuera.map((t) => (t.querySelector("h2")?.textContent || t.id || "?").slice(0, 24)).join(", "));

/* 2 · Las tarjetas conservan su caja (padding + fondo de panel). */
check(tarjetas.every((t) => gcs(t).position === "relative"), "todas las tarjetas son position:relative");

/* 3 · El panel principal NO dibuja cajas (se restauró a miniatura + texto);
   las cajas viven solo en el visor maximizado. */
check($("cajas") === null, "el visor principal no tiene capa de cajas");
check(!!$("m-cajas"), "el visor maximizado sí tiene capa de cajas");
const muestra = D.createElement("span");
muestra.className = "ocr-caja rel";
muestra.dataset.r = "serial";
$("m-cajas").appendChild(muestra);
check(gcs(muestra).position === "absolute", "recuadro del OCR es absolute");
check($("m-cajas").contains(muestra), "los recuadros viven en la capa del visor");
check($("modal").hidden === true, "el visor maximizado arranca cerrado");

/* 4 · El log no está dentro de nada fuera de flujo (causa del solape). */
const cadena = [];
for (let el = $("log"); el && el.tagName !== "BODY"; el = el.parentElement) {
  const p = gcs(el).position;
  cadena.push(`${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}=${p}`);
  if (p === "absolute" || p === "fixed") cadena.push("¡FUERA DE FLUJO!");
}
check(!cadena.some((x) => x.includes("FUERA")), "el log está en el flujo normal", cadena.join(" > "));
check(!!$("log").closest(".caja"), "el log vive dentro de su propia tarjeta");

/* 5 · Nada inesperado en posición absoluta en toda la página. */
const OK_ABS = new Set(["m-cajas", "franja", "escaner", "indicador", "mini-vacio", "m-vacio",
                        "modal", "modal-fondo"]);
const inesperados = [...D.querySelectorAll("*")].filter((el) => {
  const p = gcs(el).position;
  return (p === "absolute" || p === "fixed") && !OK_ABS.has(el.id)
    && !el.classList.contains("ocr-caja") && !el.classList.contains("escaner")
    && !el.classList.contains("indicador") && !el.classList.contains("modal-fondo");
}).map((el) => `${el.tagName.toLowerCase()}#${el.id || ""}.${[...el.classList].join(".")}`);
check(inesperados.length === 0, "sin posicionados absolutos inesperados", inesperados.slice(0, 6).join(" "));

check(errores.length === 0, "sin errores de JS", errores.slice(0, 3).join(" | "));

console.log(fallos ? `\n${fallos} comprobación(es) FALLARON` : "\ntodo OK");
process.exit(fallos ? 1 : 0);
