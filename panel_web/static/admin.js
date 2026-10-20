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
  const datos = tipo.includes("application/json") ? await respuesta.json() : null;
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
    cargarProgreso();
  });
  $("admin-refresh").addEventListener("click", async () => {
    await cargarJobs();
    await cargarProgreso();
  });

  $("inv-form").addEventListener("submit", guardarEquipo);
  $("inv-cancel").addEventListener("click", limpiarFormulario);
  $("inv-choose").addEventListener("click", () => $("inv-file").click());
  $("inv-file").addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) subirExcel(e.target.files[0]);
  });
  $("inv-import").addEventListener("click", () => $("inv-file").click());
  $("inv-export").addEventListener("click", exportarInventario);
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
  for (const id of ["inv-sede-filter", "inv-marca-filter", "inv-estatus-filter"]) {
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
  set("scan-total", "—");
  set("scan-number", "—");
  set("scan-doc", "Sin documento");
  set("scan-range", "—");
  set("admin-ocr", "Esperando la primera hoja…");
  $("admin-pages").textContent = "";
  $("admin-workers").textContent = "";
  $("admin-recent").textContent = "";
  $("live-progress").style.transform = "scaleX(0)";
  $("scan-img").hidden = true;
  $("scan-placeholder").hidden = false;
  $("scan-paper").classList.remove("scanning");
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

  set("scan-page", actual);
  set("scan-total", paginas);
  set("scan-number", `${actual} / ${paginas}`);
  set("scan-doc", job.filename);
  set("scan-range", `${job.pages_done} / ${paginas} hojas leídas`);
  $("scan-paper").classList.toggle("scanning", job.status === "running");

  pintarHoja(job.id, actual, job.status);
  pintarWorkers(d.workers || []);
  set("admin-ocr", d.ultima_lectura || "Esperando la primera hoja…");
  pintarPaginas(d.pages || [], paginas, actual);
  pintarRecientes(d.pages || []);
}

function pintarHoja(jobId, pag, estado) {
  const img = $("scan-img");
  const placeholder = $("scan-placeholder");
  const url = `/api/admin/jobs/${encodeURIComponent(jobId)}/page/${pag}.png`;
  if (img.dataset.pag !== String(pag) || img.dataset.job !== jobId) {
    img.dataset.pag = String(pag);
    img.dataset.job = jobId;
    img.hidden = true;
    placeholder.hidden = false;
    img.onload = () => {
      img.hidden = false;
      placeholder.hidden = true;
    };
    img.onerror = () => {
      img.hidden = true;
      placeholder.hidden = false;
      placeholder.textContent = estado === "running" ? "LEYENDO" : "SIN IMAGEN";
    };
    img.src = `${url}?v=${Date.now()}`;
  }
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

function pintarPaginas(pages, total, actual) {
  const cont = $("admin-pages");
  cont.textContent = "";
  const porPag = new Map(pages.map((p) => [Number(p.pag), p]));
  for (let i = 1; i <= total; i += 1) {
    const p = porPag.get(i);
    const celda = el("div", "page-cell");
    if (p) {
      const estado = (p.estado || "").toUpperCase();
      if (estado === "OK") celda.classList.add("ok");
      else if (estado === "A_REVISAR") celda.classList.add("aviso");
      else if (estado === "DESCARTADA") celda.classList.add("descartada");
      else if (p.ocr) celda.classList.add("ocr");
    } else {
      celda.classList.add("empty");
    }
    if (i === actual) celda.classList.add("active");
    if (p) {
      celda.title = `Hoja ${i} · ${p.estado || (p.ocr ? "leída" : "pendiente")}` +
        ` · ${p.sn || "sin serial"} · ${p.sede || "sin sede"}`;
    } else {
      celda.title = `Hoja ${i} · pendiente`;
    }
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
      el("td", "", p.estado || (p.ocr ? "LEÍDA" : "—")),
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
    zona: "",
  };
  const hayFiltros = !!(filtros.q || filtros.sede || filtros.marca || filtros.estatus);
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

document.addEventListener("DOMContentLoaded", iniciar);
