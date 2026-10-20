"use strict";

const estado = {
  csrf: "",
  jobs: [],
  queue: null,
  cargando: false,
  subiendo: false,
  timer: null,
};

const $ = (id) => document.getElementById(id);

function textoError(datos, status) {
  if (datos && typeof datos.detail === "string") return datos.detail;
  if (datos && Array.isArray(datos.detail) && datos.detail[0]?.msg) {
    return datos.detail[0].msg;
  }
  return `Error del servidor (${status}).`;
}

async function api(ruta, opciones = {}) {
  const headers = { Accept: "application/json", ...(opciones.headers || {}) };
  if (opciones.method && opciones.method !== "GET" && estado.csrf) {
    headers["X-CSRF-Token"] = estado.csrf;
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
  if (!respuesta.ok) throw new Error(textoError(datos, respuesta.status));
  return datos;
}

function formatoBytes(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${n} B`;
}

function formatoFecha(iso) {
  if (!iso) return "";
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return "";
  return fecha.toLocaleString("es-MX", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function formatoDuracion(segundos) {
  const s = Number(segundos || 0);
  if (!s || s <= 0) return "—";
  if (s < 60) return "< 1 min";
  const minutos = Math.round(s / 60);
  if (minutos < 60) return `~${minutos} min`;
  const horas = Math.floor(minutos / 60);
  const resto = minutos % 60;
  return resto ? `~${horas} h ${resto} min` : `~${horas} h`;
}

function etiquetaEstado(estadoJob) {
  return ({
    queued: "En cola",
    running: "Procesando",
    done: "Listo",
    error: "Error",
    canceled: "Cancelado",
  })[estadoJob] || estadoJob;
}

function detalleEstado(job) {
  if (job.status === "queued") {
    if (job.position > 0) {
      return `${job.position} doc. delante`;
    }
    return "Siguiente en cola";
  }
  if (job.status === "running") return "En proceso";
  if (job.status === "done") return "Expediente listo";
  if (job.status === "error") return job.error || "No se pudo procesar";
  return job.message || "";
}

function aplicarJob(nodo, job) {
  nodo.classList.add(job.status);
  nodo.querySelector(".job-name").textContent = job.filename;
  nodo.querySelector(".job-name").title = job.filename;
  nodo.querySelector(".job-meta").textContent =
    `${formatoFecha(job.created_at)} · ${formatoBytes(job.size)}`;
  nodo.querySelector(".job-pages").textContent = String(job.pages);
  const detalle = detalleEstado(job);
  nodo.querySelector(".job-status").textContent = detalle;
  nodo.querySelector(".job-status").title = detalle;

  const badge = nodo.querySelector(".status-badge");
  badge.textContent = etiquetaEstado(job.status);
  badge.classList.add(job.status);

  const track = nodo.querySelector(".progress-track");
  const barra = nodo.querySelector(".progress-bar");
  const progreso = job.status === "done"
    ? 100
    : Math.max(0, Math.min(100, Number(job.progress || 0)));
  barra.style.transform = `scaleX(${progreso / 100})`;
  track.setAttribute("aria-valuenow", String(progreso));

  nodo.querySelector(".job-progress").textContent =
    `${job.pages_done}/${job.pages}`;
  nodo.querySelector(".job-eta").textContent =
    job.status === "done" ? "Listo" :
    job.status === "error" ? "—" :
    formatoDuracion(job.eta_seconds);

  const descargar = nodo.querySelector(".download");
  if (job.download_ready) {
    descargar.href = `/api/jobs/${encodeURIComponent(job.id)}/download`;
    descargar.hidden = false;
  } else {
    descargar.hidden = true;
    descargar.removeAttribute("href");
  }

  const borrar = nodo.querySelector(".delete");
  borrar.disabled = job.status === "running";
  borrar.addEventListener("click", () => borrarJob(job));

  return nodo;
}

function renderJobs() {
  const contenedor = $("jobs");
  const vacio = $("empty-state");
  const contador = $("jobs-count");
  const plantilla = $("job-template");

  contenedor.textContent = "";
  contador.textContent =
    `${estado.jobs.length} DOCUMENTO${estado.jobs.length === 1 ? "" : "S"}`;

  if (!estado.jobs.length) {
    vacio.hidden = false;
    return;
  }
  vacio.hidden = true;
  for (const job of estado.jobs) {
    const nodo = plantilla.content.firstElementChild.cloneNode(true);
    contenedor.appendChild(aplicarJob(nodo, job));
  }
}

function renderCola() {
  const cola = estado.queue || { active: 0, pages: 0, eta_seconds: 0 };
  const enCurso = estado.jobs.filter((j) => j.status === "running").length;
  const enEspera = estado.jobs.filter((j) => j.status === "queued").length;
  const activos = Number(cola.active || 0);

  $("queue-count").textContent = String(enEspera);
  $("queue-docs").textContent = String(enCurso);
  $("queue-pages").textContent = String(cola.pages || 0);
  $("queue-eta").textContent = activos ? formatoDuracion(cola.eta_seconds) : "—";
  $("queue-note").textContent = enEspera
    ? `${enEspera} documento${enEspera === 1 ? "" : "s"} en espera.`
    : enCurso
      ? "Procesando ahora."
      : "Sin trabajos en cola.";
}

async function cargarJobs(silencioso = false) {
  if (estado.cargando) return;
  estado.cargando = true;
  if (!silencioso) $("btn-refresh").disabled = true;
  try {
    const datos = await api("/api/jobs");
    estado.jobs = datos.jobs || [];
    estado.queue = datos.queue || null;
    renderJobs();
    renderCola();
  } catch (error) {
    if (silencioso) return;
    mostrarEstado(error.message, "error");
  } finally {
    estado.cargando = false;
    $("btn-refresh").disabled = false;
  }
}

function mostrarEstado(mensaje, tipo = "") {
  const nodo = $("upload-status");
  nodo.textContent = mensaje;
  nodo.className = "form-status" + (tipo ? ` ${tipo}` : "");
}

async function subirArchivos(archivos) {
  const lista = Array.from(archivos || []).filter((f) => {
    return f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
  });
  if (!lista.length) {
    mostrarEstado("Elige uno o más archivos PDF.", "error");
    return;
  }
  if (estado.subiendo) return;

  estado.subiendo = true;
  const boton = $("btn-elegir");
  const zona = $("dropzone");
  const visual = $("upload-visual");
  boton.disabled = true;
  zona.classList.add("subiendo");
  visual.hidden = false;
  let subidos = 0;

  try {
    for (const archivo of lista) {
      mostrarEstado(`Subiendo ${archivo.name} (${subidos + 1}/${lista.length})…`);
      const datos = new FormData();
      datos.append("archivo", archivo, archivo.name);
      await api("/api/jobs", { method: "POST", body: datos });
      subidos++;
    }
    mostrarEstado(
      subidos === 1
        ? "Documento agregado a la cola."
        : `${subidos} documentos agregados a la cola.`,
      "ok",
    );
    await cargarJobs(true);
  } catch (error) {
    mostrarEstado(
      subidos
        ? `${subidos} documento(s) subido(s). ${error.message}`
        : error.message,
      "error",
    );
  } finally {
    estado.subiendo = false;
    boton.disabled = false;
    zona.classList.remove("subiendo");
    $("file-input").value = "";
    window.setTimeout(() => { visual.hidden = true; }, 400);
  }
}

async function borrarJob(job) {
  if (job.status === "running") return;
  const confirmado = window.confirm(
    `¿Borrar "${job.filename}"? Esta acción no se puede deshacer.`,
  );
  if (!confirmado) return;
  try {
    await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: "DELETE" });
    mostrarEstado("Documento borrado.", "ok");
    await cargarJobs(true);
  } catch (error) {
    mostrarEstado(error.message, "error");
  }
}

function conectarSubida() {
  const zona = $("dropzone");
  const input = $("file-input");

  const abrir = () => input.click();
  $("btn-elegir").addEventListener("click", (evento) => {
    evento.stopPropagation();
    abrir();
  });
  zona.addEventListener("click", abrir);
  zona.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter" || evento.key === " ") {
      evento.preventDefault();
      abrir();
    }
  });

  input.addEventListener("change", () => subirArchivos(input.files));

  for (const evento of ["dragenter", "dragover"]) {
    zona.addEventListener(evento, (e) => {
      e.preventDefault();
      zona.classList.add("dragover");
    });
  }
  for (const evento of ["dragleave", "drop"]) {
    zona.addEventListener(evento, (e) => {
      e.preventDefault();
      zona.classList.remove("dragover");
    });
  }
  zona.addEventListener("drop", (e) => subirArchivos(e.dataTransfer.files));
}

async function iniciar() {
  try {
    const me = await api("/api/me");
    estado.csrf = me.csrf;
    document.querySelectorAll("[data-user-email]").forEach((nodo) => {
      nodo.textContent = me.email;
    });
    const navAdmin = $("nav-admin");
    if (navAdmin) navAdmin.hidden = me.role !== "admin";
    $("upload-hint").textContent =
      `PDF · máx. ${me.limits.max_upload_mb} MB · ${me.limits.max_pages} págs.`;
  } catch (error) {
    mostrarEstado(error.message, "error");
    return;
  }

  conectarSubida();
  $("btn-refresh").addEventListener("click", () => cargarJobs(false));
  await cargarJobs(false);

  estado.timer = window.setInterval(() => {
    if (document.hidden) return;
    const activo = document.activeElement;
    // No reemplazar la tabla mientras el usuario está enfocando una acción.
    if (activo && activo.closest && activo.closest("#jobs")) return;
    cargarJobs(true);
  }, 3000);
}

document.addEventListener("DOMContentLoaded", iniciar);
