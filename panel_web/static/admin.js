"use strict";

const A = {
  csrf: "",
  jobs: [],
  jobId: "",
  invOffset: 0,
  invLimit: 50,
  invTotal: 0,
  invAll: 0,
  editing: null,
  tick: 0,
  ocrPag: 0,
  ocrJob: "",
  ocrTotal: 0,
  ocrUltimaPag: 0,
  ocrUltimoJob: "",
  ocrUltimoEstado: "",
  ocrSignature: "",
  ocrSeq: 0,
  ocrMode: "live",
  ocrWorker: "",
  ocrUltimas: {},
  ocrViewSignature: "",
  actualPag: 0,
};

const $ = (id) => document.getElementById(id);

function set(id, valor) {
  const nodo = $(id);
  if (nodo) nodo.textContent = valor == null ? "" : String(valor);
}

function el(tag, clase, texto) {
  const nodo = document.createElement(tag);
  if (clase) nodo.className = clase;
  if (texto != null) nodo.textContent = texto;
  return nodo;
}

function estadoLabel(estado) {
  return ({
    queued: "En cola", running: "Procesando", done: "Listo",
    error: "Error", canceled: "Cancelado",
  })[estado] || estado || "—";
}

function estadoColor(estado) {
  return ({
    queued: "var(--amber)", running: "var(--accent)",
    done: "var(--green)", error: "var(--red)",
  })[estado] || "var(--muted)";
}

function fmtFecha(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-MX", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function fmtDuracion(seg) {
  const s = Number(seg || 0);
  if (!s || s <= 0) return "—";
  const m = Math.round(s / 60);
  if (m < 60) return `~${Math.max(1, m)} min`;
  return `~${Math.floor(m / 60)} h ${m % 60} min`;
}

function fmtMs(ms) {
  const n = Number(ms || 0);
  return n ? `${(n / 1000).toFixed(1)} s` : "—";
}

async function api(ruta, opciones = {}) {
  const headers = { Accept: "application/json", ...(opciones.headers || {}) };
  if (opciones.method && opciones.method !== "GET" && A.csrf) {
    headers["X-CSRF-Token"] = A.csrf;
  }
  const respuesta = await fetch(ruta, {
    credentials: "same-origin",
    cache: "no-store",
    ...opciones,
    headers,
  });
  if (respuesta.status === 401) {
    window.location.assign("/login");
    throw new Error("Sesión expirada.");
  }
  const tipo = respuesta.headers.get("content-type") || "";
  let datos = null;
  if (respuesta.status !== 204 && tipo.includes("application/json")) {
    const texto = await respuesta.text();
    if (texto.trim()) {
      try {
        datos = JSON.parse(texto);
      } catch {
        datos = null;
      }
    }
  }
  if (!respuesta.ok) {
    const detalle = datos && typeof datos.detail === "string"
      ? datos.detail : `Error del servidor (${respuesta.status}).`;
    throw new Error(detalle);
  }
  return datos;
}

async function iniciar() {
  try {
    const me = await api("/api/me");
    if (me.role !== "admin") {
      window.location.assign("/");
      return;
    }
    A.csrf = me.csrf;
    document.querySelectorAll("[data-user-email]").forEach((n) => {
      n.textContent = me.email;
    });
  } catch (error) {
    set("inv-status", error.message);
    return;
  }

  conectarEventos();
  await cargarOverview();
  await cargarJobs();
  await cargarInventario();
  await cargarSedes();
  await cargarUsuarios();
  await cargarUbicaciones("");

  A.timer = window.setInterval(async () => {
    if (document.hidden) return;
    A.tick += 1;
    const activo = document.activeElement;
    if (!(activo && activo.closest && activo.closest("#admin-job"))) {
      await cargarProgreso(true);
    }
    if (A.tick % 5 === 0) {
      await cargarOverview();
      if (!(activo && activo.closest && activo.closest("#admin-job"))) {
        await cargarJobs(true);
      }
    }
  }, 1000);
}

function conectarEventos() {
  $("admin-job").addEventListener("change", (e) => {
    A.jobId = e.target.value;
    A.ocrPag = 0;
    A.ocrJob = "";
    A.ocrMode = "live";
    A.ocrWorker = "";
    A.ocrUltimaPag = 0;
    A.ocrSignature = "";
    limpiarInspector();
    cargarProgreso();
  });
  $("admin-refresh").addEventListener("click", async () => {
    await cargarJobs();
    await cargarProgreso();
  });

  $("scan-prev").addEventListener("click", () => moverOcr(-1));
  $("scan-next").addEventListener("click", () => moverOcr(1));
  $("scan-reload").addEventListener("click", () => {
    if (A.ocrPag) cargarOcrPagina(A.ocrPag, false, true);
  });
  $("scan-page-input").addEventListener("change", (e) => {
    seleccionarPaginaOcr(e.target.value);
  });
  $("scan-view").addEventListener("change", (e) => cambiarVistaOcr(e.target.value));

  $("inv-form").addEventListener("submit", guardarEquipo);
  $("inv-cancel").addEventListener("click", limpiarFormulario);
  $("inv-choose").addEventListener("click", () => $("inv-file").click());
  $("inv-file").addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) subirExcel(e.target.files[0]);
  });
  $("inv-import").addEventListener("click", () => $("inv-file").click());
  $("inv-add").addEventListener("click", () => {
    limpiarFormulario();
    $("inv-sn").focus();
    $("inv-form").scrollIntoView({behavior: "smooth", block: "center"});
  });
  $("inv-export").addEventListener("click", exportarInventario);
  let timerUbicacion = null;
  $("inv-ubicacion").addEventListener("input", (e) => {
    clearTimeout(timerUbicacion);
    timerUbicacion = setTimeout(
      () => cargarUbicaciones(e.target.value, $("inv-sede").value.trim()),
      250,
    );
  });
  $("inv-sede").addEventListener("change", () => {
    cargarUbicaciones($("inv-ubicacion").value, $("inv-sede").value.trim());
  });
  $("inv-delete-filtered").addEventListener("click", () => borrarInventario(false));
  $("inv-delete-all").addEventListener("click", () => borrarInventario(true));

  let timerBusqueda = null;
  $("inv-q").addEventListener("input", () => {
    clearTimeout(timerBusqueda);
    timerBusqueda = setTimeout(() => {
      A.invOffset = 0;
      cargarEquipos();
    }, 250);
  });
  for (const id of ["inv-sede-filter", "inv-marca-filter", "inv-estatus-filter", "inv-piso-filter"]) {
    $(id).addEventListener("change", () => {
      A.invOffset = 0;
      cargarEquipos();
    });
  }
  $("inv-prev").addEventListener("click", () => {
    A.invOffset = Math.max(0, A.invOffset - A.invLimit);
    cargarEquipos();
  });
  $("inv-next").addEventListener("click", () => {
    if (A.invOffset + A.invLimit < A.invTotal) {
      A.invOffset += A.invLimit;
      cargarEquipos();
    }
  });
}

async function cargarOverview() {
  try {
    const d = await api("/api/admin/overview");
    set("stat-active", d.active);
    set("stat-pages", d.pages_done);
    set("stat-sedes", d.sedes);
    set("stat-seriales", d.seriales);
    if (d.inventario) {
      set("inv-archivo",
        `inventario_panel.xlsx · ${Math.round(d.inventario.bytes / 1024)} KB · ${fmtFecha(new Date(d.inventario.mtime * 1000).toISOString())}`);
    } else {
      set("inv-archivo", "Sin inventario exportado.");
    }
    $("inv-warning").hidden = Number(d.seriales) > 0;
  } catch (error) {
    set("inv-status", error.message);
  }
}

async function cargarJobs(silencioso = false) {
  try {
    const d = await api("/api/admin/jobs");
    A.jobs = d.jobs || [];
    const select = $("admin-job");
    const anterior = A.jobId;
    select.textContent = "";
    if (!A.jobs.length) {
      select.appendChild(new Option("Sin documentos", ""));
      A.jobId = "";
      limpiarEnVivo();
    } else {
      for (const j of A.jobs) {
        select.appendChild(new Option(
          `${j.filename} · ${j.owner_email} · ${estadoLabel(j.status)}`,
          j.id,
        ));
      }
      if (!A.jobs.some((j) => j.id === anterior)) {
        const activo = A.jobs.find((j) => j.status === "running") || A.jobs[0];
        A.jobId = activo.id;
      }
      select.value = A.jobId;
    }
    pintarJobs();
    if (A.jobId && !silencioso) await cargarProgreso(true);
  } catch (error) {
    set("live-message", error.message);
  }
}

function pintarJobs() {
  const body = $("admin-jobs");
  body.textContent = "";
  if (!A.jobs.length) {
    const tr = el("tr");
    const td = el("td", "admin-empty", "Sin trabajos.");
    td.colSpan = 4;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const j of A.jobs.slice(0, 18)) {
    const tr = el("tr");
    if (j.id === A.jobId) tr.className = "fila-activa";
    const nombre = el("td", "", j.filename);
    nombre.title = j.filename;
    const usuario = el("td", "", j.owner_email);
    const estado = el("td", "", estadoLabel(j.status));
    estado.style.color = estadoColor(j.status);
    const avance = el("td", "num", `${j.pages_done}/${j.pages}`);
    tr.append(nombre, usuario, estado, avance);
    tr.addEventListener("click", () => {
      A.jobId = j.id;
      $("admin-job").value = j.id;
      cargarProgreso();
    });
    body.appendChild(tr);
  }
}

function limpiarEnVivo() {
  set("live-status", "SIN TRABAJO");
  set("live-name", "Esperando un documento");
  set("live-owner", "—");
  set("live-message", "La cola está vacía.");
  set("live-page", "—");
  set("live-total", "/ —");
  set("live-pages", "—");
  set("live-eta", "—");
  set("scan-page", "—");
  set("scan-total", "/ —");
  set("scan-number", "—");
  set("scan-doc", "Sin documento");
  set("scan-range", "—");
  $("admin-pages").textContent = "";
  $("admin-workers").textContent = "";
  $("admin-recent").textContent = "";
  $("live-progress").style.transform = "scaleX(0)";
  $("scan-img").hidden = true;
  $("scan-placeholder").hidden = false;
  $("scan-paper").classList.remove("scanning");
  limpiarInspector();
}

async function cargarProgreso(silencioso = false) {
  if (!A.jobId) {
    limpiarEnVivo();
    return;
  }
  try {
    const d = await api(`/api/admin/jobs/${encodeURIComponent(A.jobId)}/progress`);
    pintarProgreso(d);
  } catch (error) {
    if (!silencioso) set("live-message", error.message);
  }
}

function pintarProgreso(d) {
  const job = d.job;
  const paginas = Number(job.pages || 0);
  const actual = Math.max(1, Math.min(paginas || 1, Number(d.current_page || job.pages_done || 1)));

  set("live-status", estadoLabel(job.status).toUpperCase());
  $("live-status").style.color = estadoColor(job.status);
  set("live-name", job.filename);
  $("live-name").title = job.filename;
  set("live-owner", `${job.owner_name} · ${job.owner_email}`);
  set("live-message", job.error || job.message || "—");
  set("live-page", actual);
  set("live-total", `/ ${paginas || "—"}`);
  set("live-pages", job.pages_done);
  set("live-eta", job.status === "done" ? "—" : fmtDuracion(job.eta_seconds));
  $("live-progress").style.transform = `scaleX(${Math.max(0, Math.min(100, job.progress)) / 100})`;

  A.actualPag = actual;
  set("scan-total", `/ ${paginas || "—"}`);
  set("scan-number", `${actual} / ${paginas || "—"}`);
  set("scan-doc", job.filename);
  $("scan-paper").classList.toggle("scanning", job.status === "running");

  pintarWorkers(d.workers || []);
  pintarPaginas(d.pages || [], paginas, actual);
  pintarRecientes(d.pages || []);
  actualizarInspector(job, actual, paginas, d.pages || [], d.workers || []);
}

function limpiarInspector() {
  A.ocrPag = 0;
  A.ocrJob = "";
  A.ocrTotal = 0;
  A.ocrUltimaPag = 0;
  A.ocrUltimoJob = "";
  A.ocrUltimoEstado = "";
  A.ocrSignature = "";
  A.ocrMode = "live";
  A.ocrWorker = "";
  A.ocrUltimas = {};
  A.ocrViewSignature = "";
  const img = $("scan-img");
  img.hidden = true;
  img.removeAttribute("src");
  img.dataset.key = "";
  const vacio = $("scan-placeholder");
  vacio.hidden = false;
  vacio.textContent = "PDF";
  $("scan-boxes").textContent = "";
  $("scan-page-input").value = "1";
  set("scan-total", "/ —");
  set("scan-number", "—");
  set("scan-doc", "Sin documento");
  set("scan-range", "—");
  set("ocr-status", "SIN LECTURA");
  set("ocr-serial", "—");
  set("ocr-worker", "—");
  set("ocr-ms", "—");
  set("ocr-source", "—");
  set("ocr-text", "Esperando la primera hoja…");
  const select = $("scan-view");
  select.textContent = "";
  select.appendChild(new Option("EN VIVO · ÚLTIMA HOJA", "live"));
}

function actualizarInspector(job, actual, total, pages, workers) {
  const n = Number(total || 0);
  A.ocrTotal = n;
  A.actualPag = Number(actual) || 0;
  $("scan-total").textContent = `/ ${n || "—"}`;
  $("scan-page-input").max = String(Math.max(1, n));

  const ultimas = {};
  for (const p of pages || []) {
    const slot = Number(p.slot);
    if (!Number.isInteger(slot) || slot < 0) continue;
    if (!p.ocr && !p.estado) continue;
    const clave = String(slot);
    if (!ultimas[clave] || Number(p.pag) > Number(ultimas[clave])) {
      ultimas[clave] = Number(p.pag);
    }
  }
  A.ocrUltimas = ultimas;

  const select = $("scan-view");
  const vistaFirma = (workers || [])
    .map((w) => `${w.slot}:${w.worker || ""}`)
    .join("|");
  if (A.ocrViewSignature !== vistaFirma || select.options.length < 2) {
    A.ocrViewSignature = vistaFirma;
    select.textContent = "";
    select.appendChild(new Option("EN VIVO · ÚLTIMA HOJA", "live"));
    for (const w of workers || []) {
      const slot = Number(w.slot);
      if (!Number.isInteger(slot) || slot < 0) continue;
      const etiqueta = `LECTOR ${slot + 1}${w.worker ? ` · ${w.worker}` : ""}`;
      select.appendChild(new Option(etiqueta, `worker:${slot}`));
    }
    select.appendChild(new Option("HOJA MANUAL", "manual"));
  }
  if (A.ocrMode === "worker" && ![...select.options].some((o) => o.value === `worker:${A.ocrWorker}`)) {
    A.ocrMode = "live";
    A.ocrWorker = "";
  }

  if (A.ocrJob !== job.id) {
    A.ocrJob = job.id;
    A.ocrMode = "live";
    A.ocrWorker = "";
    A.ocrPag = Number(actual) || 1;
    A.ocrUltimaPag = 0;
    A.ocrUltimoJob = "";
    A.ocrUltimoEstado = "";
    A.ocrSignature = "";
  }

  if (A.ocrMode === "live") {
    A.ocrPag = Number(actual) || 1;
  } else if (A.ocrMode === "worker") {
    const pagina = A.ocrUltimas[String(A.ocrWorker)];
    A.ocrPag = pagina ? Number(pagina) : (Number(actual) || 1);
  }

  if (!A.ocrPag) A.ocrPag = 1;
  if (n && A.ocrPag > n) A.ocrPag = n;
  const inputPag = $("scan-page-input");
  if (document.activeElement !== inputPag) inputPag.value = String(A.ocrPag);

  const opcion = A.ocrMode === "worker" ? `worker:${A.ocrWorker}` : A.ocrMode;
  if ([...select.options].some((o) => o.value === opcion)) select.value = opcion;
  marcarPaginaOcr();

  let cambio = A.ocrUltimoJob !== job.id || Number(A.ocrUltimaPag) !== Number(A.ocrPag);
  if (A.ocrUltimoEstado !== job.status) {
    A.ocrUltimoEstado = job.status;
    cambio = true;
  }
  const vivo = job.status === "running" || job.status === "queued";
  if (cambio || (vivo && A.ocrMode === "live" && Number(A.ocrPag) === Number(actual))) {
    cargarOcrPagina(A.ocrPag, true);
  }
}

function cambiarVistaOcr(valor) {
  if (!A.jobId) return;
  if (valor === "live") {
    A.ocrMode = "live";
    A.ocrWorker = "";
    A.ocrPag = Number(A.actualPag) || 1;
  } else if (valor.startsWith("worker:")) {
    A.ocrMode = "worker";
    A.ocrWorker = valor.slice(7);
    const pagina = A.ocrUltimas[A.ocrWorker];
    A.ocrPag = pagina ? Number(pagina) : (Number(A.actualPag) || 1);
  } else {
    A.ocrMode = "manual";
    A.ocrWorker = "";
  }
  A.ocrUltimaPag = 0;
  A.ocrSignature = "";
  $("scan-page-input").value = String(A.ocrPag || 1);
  marcarPaginaOcr();
  if (A.ocrPag) cargarOcrPagina(A.ocrPag, false, true);
}

function marcarPaginaOcr() {
  document.querySelectorAll("#admin-pages .page-cell").forEach((celda) => {
    celda.classList.toggle("selected", Number(celda.dataset.pag) === Number(A.ocrPag));
  });
}

function seleccionarPaginaOcr(pag) {
  if (!A.jobId || !A.ocrTotal) return;
  const destino = Math.max(1, Math.min(Number(A.ocrTotal), Number(pag) || 1));
  A.ocrMode = "manual";
  A.ocrWorker = "";
  A.ocrPag = destino;
  A.ocrUltimaPag = 0;
  A.ocrSignature = "";
  $("scan-page-input").value = String(destino);
  const select = $("scan-view");
  if ([...select.options].some((o) => o.value === "manual")) select.value = "manual";
  marcarPaginaOcr();
  cargarOcrPagina(destino, false, true);
}

function moverOcr(delta) {
  if (!A.ocrPag) return;
  seleccionarPaginaOcr(A.ocrPag + Number(delta || 0));
}

function pintarOcrImagen(pag, estado, forzar) {
  const img = $("scan-img");
  const vacio = $("scan-placeholder");
  const key = `${A.jobId}:${pag}`;
  set("scan-number", `${pag} / ${A.ocrTotal || "—"}`);
  if (img.dataset.key === key && !forzar) return;
  img.dataset.key = key;
  img.hidden = true;
  vacio.hidden = false;
  vacio.textContent = "CARGANDO HOJA…";
  img.onload = () => {
    img.hidden = false;
    vacio.hidden = true;
    const paper = $("scan-paper");
    if (img.naturalWidth && img.naturalHeight) {
      paper.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
    }
  };
  img.onerror = () => {
    img.hidden = true;
    vacio.hidden = false;
    vacio.textContent = estado === "running" ? "LEYENDO" : "SIN IMAGEN";
  };
  img.src = `/api/admin/jobs/${encodeURIComponent(A.jobId)}/page/${pag}.png?v=${Date.now()}`;
}

async function cargarOcrPagina(pag, silencioso = false, forzar = false) {
  if (!A.jobId) return;
  const jobId = A.jobId;
  const secuencia = ++A.ocrSeq;
  try {
    const d = await api(`/api/admin/jobs/${encodeURIComponent(jobId)}/ocr/${encodeURIComponent(pag)}`);
    if (secuencia !== A.ocrSeq || jobId !== A.jobId || Number(pag) !== Number(A.ocrPag)) return;
    A.ocrUltimaPag = Number(pag);
    A.ocrUltimoJob = jobId;
    pintarOcrImagen(pag, d.estado, forzar);
    pintarOcr(d, forzar);
  } catch (error) {
    if (!silencioso) {
      set("ocr-status", "ERROR");
      set("ocr-source", "—");
      set("ocr-text", error.message);
    }
  }
}

function pintarOcr(d, forzar = false) {
  const firma = JSON.stringify([
    d.pag, d.texto, d.sn, d.estado, d.worker, d.ms, d.parcial,
    Array.isArray(d.cajas) ? d.cajas.length : 0,
  ]);
  if (!forzar && firma === A.ocrSignature) return;
  A.ocrSignature = firma;

  set("ocr-status", `${etiquetaPagina(d.estado, d.texto)}${d.parcial ? " · PARCIAL" : ""}`);
  set("ocr-serial", d.sn ? `SERIAL ${d.sn}` : "SIN SERIAL");
  set("ocr-worker", d.worker ? `LECTOR ${d.worker}` : "—");
  set("ocr-ms", d.ms ? fmtMs(d.ms) : "—");
  set("ocr-source", d.fuente === "db" ? "TEXTO GUARDADO" : (d.parcial ? "OCR PARCIAL" : "OCR COMPLETO"));
  set("ocr-text", d.texto || "Sin texto OCR para esta hoja.");
  set("scan-number", `${d.pag} / ${A.ocrTotal || "—"}`);
  const vistaSel = $("scan-view").selectedOptions[0];
  set("scan-range", `${vistaSel ? vistaSel.textContent : "HOJA"} · ${d.pag} / ${A.ocrTotal || "—"}`);

  const cont = $("scan-boxes");
  cont.textContent = "";
  const tipos = new Set(["serial", "modelo", "contador", "fecha"]);
  const cajas = Array.isArray(d.cajas) ? d.cajas : [];
  cajas.forEach((c, i) => {
    const x = Number(c.x);
    const y = Number(c.y);
    const w = Number(c.w);
    const h = Number(c.h);
    if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return;
    const tipo = tipos.has(c.r) ? c.r : "";
    const box = el("div", `ocr-box${tipo ? ` ${tipo}` : ""}`);
    box.style.left = `${Math.max(0, Math.min(100, x * 100))}%`;
    box.style.top = `${Math.max(0, Math.min(100, y * 100))}%`;
    box.style.width = `${Math.max(0.2, Math.min(100, w * 100))}%`;
    box.style.height = `${Math.max(0.2, Math.min(100, h * 100))}%`;
    box.style.setProperty("--i", String(Math.min(i, 60)));
    if (c.t) {
      box.title = c.t;
      if (tipo === "serial" || tipo === "modelo") {
        box.appendChild(el("span", "", c.t));
      }
    }
    cont.appendChild(box);
  });
}

function pintarWorkers(workers) {
  const cont = $("admin-workers");
  cont.textContent = "";
  if (!workers.length) {
    cont.appendChild(el("p", "admin-empty", "Sin lectores en curso."));
    return;
  }
  const ahora = Date.now() / 1000;
  for (const w of workers) {
    const card = el("div", "worker-card");
    if (w.ultimo_ts && ahora - w.ultimo_ts < 8) card.classList.add("activo");
    const slotWorker = Number.isInteger(w.slot) && w.slot >= 0 ? w.slot : -1;
    if (slotWorker >= 0) {
      if (A.ocrMode === "worker" && String(slotWorker) === A.ocrWorker) {
        card.classList.add("worker-selected");
      }
      card.dataset.slot = String(slotWorker);
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.title = "Ver la última hoja de este lector";
      const elegir = () => {
        const valor = `worker:${slotWorker}`;
        $("scan-view").value = valor;
        cambiarVistaOcr(valor);
      };
      card.addEventListener("click", elegir);
      card.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          elegir();
        }
      });
    }
    const cab = el("div", "worker-cab");
    const slot = Number.isInteger(w.slot) && w.slot >= 0 ? w.slot + 1 : "?";
    cab.append(
      el("b", "", `LECTOR ${slot}`),
      el("span", "", w.worker || "rango ?"),
    );
    const linea = el("div", "worker-line");
    linea.append(
      el("span", "", `${w.paginas || 0} hojas`),
      el("span", "", `última ${w.ultima || "—"}`),
      el("span", "", fmtMs(w.ms)),
    );
    card.append(cab, linea);
    cont.appendChild(card);
  }
}

function etiquetaPagina(estado, ocr) {
  const e = (estado || "").toUpperCase();
  if (e === "OK") return "VERIFICADO";
  if (e === "A_REVISAR" || e === "POR_VERIFICAR") return "POR VERIFICAR";
  if (e === "DESCARTADA") return "DESCARTADA";
  if (ocr) return "LEÍDA";
  return "—";
}

function pintarPaginas(pages, total, actual) {
  const cont = $("admin-pages");
  cont.textContent = "";
  const porPag = new Map(pages.map((p) => [Number(p.pag), p]));
  for (let i = 1; i <= total; i += 1) {
    const p = porPag.get(i);
    const celda = el("div", "page-cell");
    celda.dataset.pag = String(i);
    if (p) {
      const estado = (p.estado || "").toUpperCase();
      if (estado === "OK") celda.classList.add("ok");
      else if (estado === "A_REVISAR" || estado === "POR_VERIFICAR") celda.classList.add("aviso");
      else if (estado === "DESCARTADA") celda.classList.add("descartada");
      else if (p.ocr) celda.classList.add("ocr");
    } else {
      celda.classList.add("empty");
    }
    if (i === actual) celda.classList.add("active");
    if (i === Number(A.ocrPag)) celda.classList.add("selected");
    if (p) {
      celda.title = `Hoja ${i} · ${etiquetaPagina(p.estado, p.ocr).toLowerCase()}` +
        ` · ${p.sn || "sin serial"} · ${p.sede || "sin sede"}`;
    } else {
      celda.title = `Hoja ${i} · pendiente`;
    }
    celda.addEventListener("click", () => seleccionarPaginaOcr(i));
    cont.appendChild(celda);
  }
}

function pintarRecientes(pages) {
  const body = $("admin-recent");
  body.textContent = "";
  const lista = pages
    .filter((p) => p.estado || p.ocr)
    .sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0))
    .slice(0, 15);
  if (!lista.length) {
    const tr = el("tr");
    const td = el("td", "admin-empty", "Sin datos todavía.");
    td.colSpan = 5;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const p of lista) {
    const tr = el("tr");
    tr.append(
      el("td", "num", p.pag),
      el("td", "", p.sn || "—"),
      el("td", "", p.sede || "—"),
      el("td", "", etiquetaPagina(p.estado, p.ocr)),
      el("td", "", p.worker || (Number.isInteger(p.slot) && p.slot >= 0 ? `slot ${p.slot + 1}` : "—")),
    );
    body.appendChild(tr);
  }
}

/* --------------------------------------------------------------- inventario */
async function cargarInventario() {
  try {
    const d = await api("/api/admin/inventario");
    set("inv-count", `${d.total} SERIAL${d.total === 1 ? "" : "ES"}`);
    set("stat-seriales", d.total);
    $("inv-warning").hidden = Number(d.total) > 0;
    rellenarFiltros("inv-sede-filter", d.sedes.map((s) => s.sede), "todas las sedes");
    rellenarFiltros("inv-marca-filter", d.marcas || [], "todas las marcas");
    rellenarFiltros("inv-estatus-filter", d.estatus || [], "todos los estatus");
    rellenarFiltros("inv-piso-filter", d.pisos || [], "todos los pisos");
    A.invOffset = 0;
    await cargarEquipos();
  } catch (error) {
    set("inv-status", error.message);
  }
}

function rellenarFiltros(id, valores, vacio) {
  const select = $(id);
  const previo = select.value;
  select.textContent = "";
  select.appendChild(new Option(vacio, ""));
  for (const v of valores) select.appendChild(new Option(v, v));
  if ([...select.options].some((o) => o.value === previo)) select.value = previo;
}

async function cargarEquipos() {
  const params = new URLSearchParams({
    q: $("inv-q").value || "",
    sede: $("inv-sede-filter").value || "",
    marca: $("inv-marca-filter").value || "",
    estatus: $("inv-estatus-filter").value || "",
    piso: $("inv-piso-filter").value || "",
    limite: A.invLimit,
    offset: A.invOffset,
  });
  try {
    const d = await api(`/api/admin/equipos?${params}`);
    A.invTotal = d.total;
    A.invAll = d.todos;
    set("inv-count", `${d.total} SERIAL${d.total === 1 ? "" : "ES"}`);
    set("inv-range", `${d.total ? A.invOffset + 1 : 0}–${A.invOffset + d.equipos.length} de ${d.total}`);
    $("inv-prev").disabled = A.invOffset <= 0;
    $("inv-next").disabled = A.invOffset + A.invLimit >= d.total;
    pintarEquipos(d.equipos || []);
  } catch (error) {
    set("inv-status", error.message);
  }
}

function pintarEquipos(equipos) {
  const body = $("inv-table-body");
  body.textContent = "";
  if (!equipos.length) {
    const tr = el("tr");
    const td = el("td", "admin-empty", "Sin seriales todavía. Importa el Excel o da de alta uno.");
    td.colSpan = 10;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const e of equipos) {
    const tr = el("tr");
    const clase = claseEstatusAdmin(e.estatus);
    if (clase) tr.classList.add(clase);
    const acciones = el("td");
    const editar = el("button", "row-action", "Editar");
    editar.type = "button";
    editar.addEventListener("click", () => editarEquipo(e));
    const borrar = el("button", "row-action", "Borrar");
    borrar.type = "button";
    borrar.addEventListener("click", () => borrarEquipo(e.sn));
    acciones.append(editar, document.createTextNode(" "), borrar);
    tr.append(
      el("td", "", e.sn), el("td", "", e.marca || "—"),
      el("td", "", e.modelo || "—"), el("td", "", e.sede || "—"),
      el("td", "", e.zona || "—"), el("td", "", e.ubicacion || "—"),
      el("td", "", e.piso || "—"), el("td", "", e.estatus || "—"),
      el("td", "", (e.origen_hoja || "—").slice(0, 36)), acciones,
    );
    body.appendChild(tr);
  }
}

function claseEstatusAdmin(estatus) {
  const e = String(estatus || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  if (!e || e.includes("operacion")) return "";
  if (e.includes("baja") || e.includes("danad") || e.includes("dana")) return "estado-baja";
  return "estado-alerta";
}

function editarEquipo(e) {
  A.editing = e.sn;
  for (const campo of ["sn", "marca", "modelo", "sede", "zona", "domicilio", "ubicacion", "piso", "estatus", "contador_inicial"]) {
    const input = $(`inv-${campo === "contador_inicial" ? "contador" : campo}`);
    if (input) input.value = e[campo] || "";
  }
  $("inv-sn").value = e.sn;
  $("inv-save").textContent = "Guardar cambios";
  $("inv-cancel").hidden = false;
  set("inv-form-status", `editando ${e.sn}`);
  $("inv-form").scrollIntoView({behavior: "smooth", block: "center"});
}

function limpiarFormulario() {
  A.editing = null;
  $("inv-form").reset();
  $("inv-save").textContent = "Guardar serial";
  $("inv-cancel").hidden = true;
  set("inv-form-status", "");
}

async function guardarEquipo(evento) {
  evento.preventDefault();
  const datos = {
    sn: $("inv-sn").value.trim(),
    marca: $("inv-marca").value.trim(),
    modelo: $("inv-modelo").value.trim(),
    sede: $("inv-sede").value.trim(),
    zona: $("inv-zona").value.trim(),
    domicilio: $("inv-domicilio").value.trim(),
    ubicacion: $("inv-ubicacion").value.trim(),
    piso: $("inv-piso").value.trim(),
    estatus: $("inv-estatus").value.trim(),
    contador_inicial: $("inv-contador").value.trim(),
  };
  if (!datos.sn) {
    set("inv-form-status", "Falta el serial.");
    return;
  }
  try {
    const confirmacion = await confirmarUbicacionNueva();
    if (confirmacion === null) return;
    if (confirmacion.confirmar) {
      datos.confirmar_ubicacion = true;
      datos.piso = confirmacion.piso;
    }
    const d = await api("/api/admin/equipos", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(datos),
    });
    set("inv-form-status", d.ok
      ? `${d.creado ? "Alta" : "Actualizado"} ${d.sn} · ${d.total} seriales`
      : d.detalle || "No se pudo exportar el Excel.");
    limpiarFormulario();
    await cargarInventario();
    await cargarOverview();
    await cargarSedes();
  } catch (error) {
    set("inv-form-status", error.message);
  }
}

async function borrarEquipo(sn) {
  if (!window.confirm(`¿Borrar el serial ${sn} del inventario?`)) return;
  try {
    const d = await api(`/api/admin/equipos/${encodeURIComponent(sn)}`, {method: "DELETE"});
    set("inv-status", d.ok ? `Baja ${d.sn} · ${d.total} seriales` : "No se encontró.");
    await cargarInventario();
    await cargarOverview();
    await cargarSedes();
  } catch (error) {
    set("inv-status", error.message);
  }
}

async function subirExcel(file) {
  if (!file) return;
  const datos = new FormData();
  datos.append("archivo", file, file.name);
  set("inv-status", `Importando ${file.name}…`);
  try {
    const d = await api("/api/admin/inventario/importar", {
      method: "POST", body: datos,
    });
    set("inv-status",
      `Importado: ${d.leidos || 0} filas, ${d.insertados || 0} altas, ` +
      `${d.actualizados || 0} actualizaciones · ${d.total} seriales`);
    $("inv-file").value = "";
    await cargarInventario();
    await cargarOverview();
    await cargarSedes();
  } catch (error) {
    set("inv-status", error.message);
    $("inv-file").value = "";
  }
}

async function borrarInventario(todo) {
  const filtros = {
    q: $("inv-q").value.trim(),
    sede: $("inv-sede-filter").value,
    marca: $("inv-marca-filter").value,
    estatus: $("inv-estatus-filter").value,
    piso: $("inv-piso-filter").value,
    zona: "",
  };
  const hayFiltros = !!(filtros.q || filtros.sede || filtros.marca || filtros.estatus || filtros.piso);
  if (!todo && !hayFiltros) {
    set("inv-delete-status", "Elige al menos un filtro o usa Borrar todo.");
    return;
  }
  const resumen = todo
    ? `TODO el inventario (${A.invAll} seriales)`
    : `los seriales filtrados (${A.invTotal} coincidencias)`;
  const tecleado = window.prompt(
    `Vas a borrar ${resumen}. Escribe BORRAR para confirmar:`,
    "",
  );
  if ((tecleado || "").trim().toUpperCase() !== "BORRAR") return;
  const cuerpo = todo
    ? {q: "", sede: "", marca: "", estatus: "", zona: "", confirm: "BORRAR"}
    : {...filtros, confirm: "BORRAR"};
  try {
    const d = await api("/api/admin/equipos/borrar", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(cuerpo),
    });
    set("inv-delete-status",
      `${d.borrados} borrados · quedan ${d.total} · ${d.ok ? "Excel actualizado" : (d.detalle || "error al exportar")}`);
    A.invOffset = 0;
    await cargarInventario();
    await cargarOverview();
    await cargarSedes();
  } catch (error) {
    set("inv-delete-status", error.message);
  }
}

async function exportarInventario() {
  try {
    const d = await api("/api/admin/inventario/exportar", {method: "POST"});
    set("inv-status", d.ok ? `Exportado: ${d.filas} seriales → ${d.destino}` : (d.detalle || "Error"));
    await cargarOverview();
  } catch (error) {
    set("inv-status", error.message);
  }
}

async function cargarSedes() {
  try {
    const d = await api("/api/admin/sedes");
    set("sedes-count", `${d.sedes.length} SEDE${d.sedes.length === 1 ? "" : "S"}`);
    const body = $("sedes-body");
    body.textContent = "";
    if (!d.sedes.length) {
      const tr = el("tr");
      const td = el("td", "admin-empty", "Sin inventario. Importa un Excel para empezar.");
      td.colSpan = 5;
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    for (const s of d.sedes) {
      const tr = el("tr");
      tr.append(
        el("td", "", s.sede),
        el("td", "num", s.equipos),
        el("td", "num", s.marcas),
        el("td", "num", s.zonas),
        el("td", "", s.actualizado || "—"),
      );
      body.appendChild(tr);
    }
  } catch (error) {
    set("inv-status", error.message);
  }
}

function pintarSugerenciasUbicacion(contenedor, aviso, input, datos, q) {
  contenedor.textContent = "";
  const existentes = datos.ubicaciones || [];
  const exacta = existentes.some((n) => n.trim().toLowerCase() === q.trim().toLowerCase());
  if (!q.trim()) {
    aviso.textContent = existentes.length
      ? "Elige una ubicación existente o escribe una nueva."
      : "Todavía no hay ubicaciones; esta será la primera.";
    return;
  }
  if (exacta) {
    aviso.textContent = "Esta ubicación ya existe; se usará tal cual.";
    return;
  }
  const similares = datos.similares || [];
  if (!similares.length) {
    aviso.textContent = "No hay ubicaciones parecidas; se guardará como nueva.";
    return;
  }
  aviso.textContent = "Ya existen ubicaciones parecidas. Elige una o confirma la nueva.";
  for (const nombre of similares.slice(0, 5)) {
    const boton = document.createElement("button");
    boton.type = "button";
    boton.className = "location-chip";
    boton.textContent = nombre;
    boton.title = `Usar ${nombre}`;
    boton.addEventListener("click", () => {
      input.value = nombre;
      contenedor.textContent = "";
      aviso.textContent = "Usando una ubicación existente.";
    });
    contenedor.appendChild(boton);
  }
}

async function cargarUbicaciones(q, sede = "") {
  try {
    const params = new URLSearchParams({q: q || ""});
    if (sede) params.set("sede", sede);
    const d = await api(`/api/admin/ubicaciones?${params.toString()}`);
    const lista = $("inv-ubicaciones");
    lista.textContent = "";
    for (const nombre of d.ubicaciones || []) {
      const option = document.createElement("option");
      option.value = nombre;
      lista.appendChild(option);
    }
    pintarSugerenciasUbicacion(
      $("inv-ubicacion-sugerencias"),
      $("inv-ubicacion-aviso"),
      $("inv-ubicacion"),
      d,
      q || "",
    );
  } catch (error) {
    set("inv-ubicacion-aviso", error.message);
  }
}

async function confirmarUbicacionNueva() {
  const input = $("inv-ubicacion");
  const q = input.value.trim();
  if (!q) return {confirmar: false};
  const params = new URLSearchParams({q});
  const sede = $("inv-sede").value.trim();
  if (sede) params.set("sede", sede);
  const d = await api(`/api/admin/ubicaciones?${params.toString()}`);
  const exacta = (d.ubicaciones || []).some(
    (n) => n.trim().toLowerCase() === q.toLowerCase(),
  );
  const similares = d.similares || [];
  if (exacta || !similares.length) return {confirmar: false};

  let piso = $("inv-piso").value.trim();
  if (!piso) {
    piso = window.prompt(
      `La ubicación "${q}" se parece a: ${similares.slice(0, 5).join(", ")}.\n` +
      "¿Qué piso le corresponde? (Se guardará como ubicación nueva.)",
      "",
    );
    if (piso === null) return null;
    piso = piso.trim();
    if (!piso) return null;
    $("inv-piso").value = piso;
  }
  const seguro = window.confirm(
    `Ya existen ubicaciones parecidas:\n\n${similares.slice(0, 5).join("\n")}\n\n` +
    `¿Crear de todos modos "${q}" con piso "${piso}"?`,
  );
  if (!seguro) return null;
  return {confirmar: true, piso};
}

async function cargarUsuarios() {
  try {
    const d = await api("/api/admin/usuarios");
    const cont = $("usuarios-list");
    cont.textContent = "";
    const sedes = d.sedes || [];
    set("usuarios-status", `${(d.usuarios || []).length} USUARIOS`);
    if (!d.usuarios || !d.usuarios.length) {
      cont.appendChild(el("p", "admin-empty", "Sin usuarios."));
      return;
    }
    for (const u of d.usuarios) {
      const card = el("div", "user-card");
      const cabeza = el("div", "user-card-head");
      cabeza.append(
        el("b", "", u.email),
        el("span", "mono-label", u.role === "admin" ? "ADMIN" : "USUARIO"),
      );
      card.appendChild(cabeza);
      const sub = el("p", "user-card-sub", u.display_name || u.name || u.email);
      card.appendChild(sub);

      const grupo = el("div", "user-sedes");
      const asignadas = new Set(u.sedes || []);
      if (!sedes.length) {
        grupo.appendChild(el("span", "mono-label", "Sin sedes en inventario"));
      } else {
        for (const sede of sedes) {
          const label = document.createElement("label");
          const check = document.createElement("input");
          check.type = "checkbox";
          check.value = sede;
          check.checked = asignadas.has(sede);
          label.appendChild(check);
          label.appendChild(document.createTextNode(sede));
          grupo.appendChild(label);
        }
      }
      card.appendChild(grupo);

      const acciones = el("div", "user-card-actions");
      const guardar = el("button", "btn-text", "Guardar sedes");
      guardar.type = "button";
      guardar.addEventListener("click", () => guardarUsuario(u, card));
      const estado = el("span", "mono-label", "");
      acciones.append(guardar, estado);
      card.appendChild(acciones);
      cont.appendChild(card);
    }
  } catch (error) {
    set("usuarios-status", error.message);
  }
}

async function guardarUsuario(usuario, card) {
  const checks = [...card.querySelectorAll(".user-sedes input[type=checkbox]")];
  const sedes = checks.filter((c) => c.checked).map((c) => c.value);
  const estado = card.querySelector(".user-card-actions .mono-label");
  try {
    const d = await api(`/api/admin/usuarios/${encodeURIComponent(usuario.id)}`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({sedes}),
    });
    estado.textContent = `Guardado · ${d.sedes.length} sede(s)`;
  } catch (error) {
    estado.textContent = error.message;
  }
}

document.addEventListener("DOMContentLoaded", iniciar);
