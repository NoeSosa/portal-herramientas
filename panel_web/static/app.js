"use strict";

const estado = {
  csrf: "",
  jobs: [],
  queue: null,
  cargando: false,
  subiendo: false,
  timer: null,
  sedes: [],
  role: "user",
  nombre: "",
  cobertura: null,
  coberturaPeriodo: "",
  coberturaSede: "",
  jobsVistos: new Map(),
  jobsInicializado: false,
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

  const resumen = nodo.querySelector(".summary");
  if (job.status === "done") {
    resumen.hidden = false;
    resumen.addEventListener("click", () => abrirResumen(job));
  } else {
    resumen.hidden = true;
  }

  const borrar = nodo.querySelector(".delete");
  borrar.disabled = job.status === "running";
  borrar.addEventListener("click", () => borrarJob(job));

  return nodo;
}

function claseEstatus(estatus) {
  const e = String(estatus || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  if (!e) return "";
  if (e.includes("operacion")) return "";
  if (e.includes("baja") || e.includes("danad") || e.includes("dana")) return "estado-baja";
  return "estado-alerta";
}

function setEqStatus(texto) {
  const nodo = $("eq-status");
  nodo.textContent = texto || "";
  nodo.className = "form-status" + (texto ? " error" : "");
}

async function cargarUbicacionesUsuario(q = "") {
  try {
    const d = await api(`/api/ubicaciones?q=${encodeURIComponent(q)}`);
    const lista = $("eq-ubicaciones");
    lista.textContent = "";
    for (const nombre of d.ubicaciones || []) {
      const option = document.createElement("option");
      option.value = nombre;
      lista.appendChild(option);
    }
  } catch {
    // La sugerencia es auxiliar; no bloquea el formulario.
  }
}

function cargarEstatusSugeridos() {
  const lista = $("eq-estatus-lista");
  lista.textContent = "";
  const valores = new Set(["En Operación", "Dañada", "Almacenada", "Sin tóner", "Baja"]);
  for (const s of estado.cobertura?.sedes || []) {
    for (const e of s.equipos || []) if (e.estatus) valores.add(e.estatus);
  }
  for (const valor of [...valores].sort()) {
    const option = document.createElement("option");
    option.value = valor;
    lista.appendChild(option);
  }
}

function abrirEquipoForm(equipo) {
  if (!estado.sedes.length) {
    toast("No tienes una sede asignada.", "error");
    return;
  }
  $("equipo-form").reset();
  setEqStatus("");
  const original = equipo && equipo.sn ? equipo.sn : "";
  $("eq-original").value = original;
  $("eq-sn").readOnly = Boolean(original);
  const select = $("eq-sede");
  select.textContent = "";
  for (const sede of estado.sedes) select.appendChild(new Option(sede, sede));
  const sedePrevia = equipo?.sede || $("cov-sede").value || estado.sedes[0];
  select.value = estado.sedes.includes(sedePrevia) ? sedePrevia : estado.sedes[0];
  const valores = {
    "eq-sn": equipo?.sn || "",
    "eq-marca": equipo?.marca || "",
    "eq-modelo": equipo?.modelo || "",
    "eq-ubicacion": equipo?.ubicacion || "",
    "eq-piso": equipo?.piso || "",
    "eq-estatus": equipo?.estatus || "En Operación",
    "eq-domicilio": equipo?.domicilio || "",
    "eq-zona": equipo?.zona || "",
    "eq-contador": equipo?.contador_inicial || "",
  };
  for (const [id, valor] of Object.entries(valores)) $(id).value = valor;
  cargarEstatusSugeridos();
  cargarUbicacionesUsuario($("eq-ubicacion").value);
  $("equipo-dialog").showModal();
}

async function guardarEquipoUsuario(evento) {
  evento.preventDefault();
  const datos = {
    sn: $("eq-sn").value.trim(),
    marca: $("eq-marca").value.trim(),
    modelo: $("eq-modelo").value.trim(),
    sede: $("eq-sede").value,
    ubicacion: $("eq-ubicacion").value.trim(),
    piso: $("eq-piso").value.trim(),
    estatus: $("eq-estatus").value.trim(),
    domicilio: $("eq-domicilio").value.trim(),
    zona: $("eq-zona").value.trim(),
    contador_inicial: $("eq-contador").value.trim(),
  };
  if (!datos.sn) {
    setEqStatus("Falta el serial.");
    return;
  }
  try {
    const d = await api("/api/equipos", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(datos),
    });
    $("equipo-dialog").close();
    toast(`${d.creado ? "Equipo agregado" : "Equipo actualizado"}: ${d.sn}`, "ok");
    await cargarCobertura(true);
  } catch (error) {
    setEqStatus(error.message);
  }
}

async function abrirResumen(job) {
  $("resumen-nombre").textContent = job.filename;
  $("resumen-texto").textContent = "Cargando resultado…";
  $("resumen-aviso").hidden = true;
  $("resumen-documentos").textContent = "";
  $("resultado-dialog").showModal();
  try {
    const d = await api(`/api/jobs/${encodeURIComponent(job.id)}/resultado`);
    const r = d.resumen || {};
    const estados = r.por_estado || {};
    $("resumen-texto").textContent =
      `${job.pages} páginas · ${r.seriales || 0} seriales · ` +
      `${estados.OK || 0} OK · ${estados.POR_VERIFICAR || 0} por verificar · ` +
      `${estados.DESCARTADA || 0} descartadas`;
    const noInv = r.no_inventario || [];
    $("resumen-aviso").hidden = !noInv.length;
    $("resumen-aviso").textContent = noInv.length
      ? `${noInv.length} serial(es) no están en el inventario: ${noInv.join(", ")}`
      : "";
    const body = $("resumen-documentos");
    for (const doc of d.documentos || []) {
      const tr = document.createElement("tr");
      const tdSerial = document.createElement("td");
      tdSerial.textContent = doc.serial || "—";
      const tdEstado = document.createElement("td");
      tdEstado.textContent = doc.estado;
      const tdTam = document.createElement("td");
      tdTam.textContent = formatoBytes(doc.bytes);
      const tdAcc = document.createElement("td");
      const a = document.createElement("a");
      a.href = `/api/jobs/${encodeURIComponent(job.id)}/documento?ruta=${encodeURIComponent(doc.ruta)}`;
      a.textContent = "Abrir";
      a.className = "action";
      tdAcc.appendChild(a);
      tr.append(tdSerial, tdEstado, tdTam, tdAcc);
      body.appendChild(tr);
    }
    if (!(d.documentos || []).length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 4;
      td.className = "admin-empty";
      td.textContent = "Sin documentos en el ZIP.";
      tr.appendChild(td);
      body.appendChild(tr);
    }
  } catch (error) {
    $("resumen-texto").textContent = error.message;
  }
}

function renderJobs() {
  const contenedor = $("jobs");
  const vacio = $("empty-state");
  const contador = $("jobs-count");
  const plantilla = $("job-template");
  const q = ($("job-search")?.value || "").trim().toLowerCase();
  const lista = q
    ? estado.jobs.filter((job) =>
        `${job.filename} ${etiquetaEstado(job.status)}`.toLowerCase().includes(q))
    : estado.jobs;

  contenedor.textContent = "";
  contador.textContent = q
    ? `${lista.length} DE ${estado.jobs.length}`
    : `${estado.jobs.length} DOCUMENTO${estado.jobs.length === 1 ? "" : "S"}`;

  if (!estado.jobs.length) {
    vacio.hidden = false;
    vacio.querySelector("h3").textContent = "Sin documentos en esta sesión.";
    return;
  }
  if (!lista.length) {
    vacio.hidden = false;
    vacio.querySelector("h3").textContent = "Sin coincidencias.";
    return;
  }
  vacio.hidden = true;
  for (const job of lista) {
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
    notificarCambiosJobs();
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

function periodoLocal() {
  const hoy = new Date();
  return `${hoy.getFullYear()}-${String(hoy.getMonth() + 1).padStart(2, "0")}`;
}

function toast(mensaje, tipo = "info") {
  const cont = $("toasts");
  if (!cont) return;
  const nodo = document.createElement("div");
  nodo.className = `toast ${tipo}`;
  const texto = document.createElement("span");
  texto.textContent = mensaje;
  const cerrar = document.createElement("button");
  cerrar.type = "button";
  cerrar.className = "toast-close";
  cerrar.setAttribute("aria-label", "Cerrar aviso");
  cerrar.textContent = "×";
  cerrar.addEventListener("click", () => nodo.remove());
  nodo.append(texto, cerrar);
  cont.appendChild(nodo);
  window.setTimeout(() => nodo.remove(), tipo === "error" ? 9000 : 5000);
}

function notificarCambiosJobs() {
  if (!estado.jobsInicializado) {
    for (const job of estado.jobs) estado.jobsVistos.set(job.id, job.status);
    estado.jobsInicializado = true;
    return;
  }
  const vistos = new Set();
  for (const job of estado.jobs) {
    vistos.add(job.id);
    const anterior = estado.jobsVistos.get(job.id);
    if (anterior !== job.status) {
      if (job.status === "done") {
        toast(`Documento listo: ${job.filename}`, "ok");
      } else if (job.status === "error") {
        toast(`Error en ${job.filename}: ${job.error || "revisa el documento"}`, "error");
      }
    }
    estado.jobsVistos.set(job.id, job.status);
  }
  for (const id of [...estado.jobsVistos.keys()]) {
    if (!vistos.has(id)) estado.jobsVistos.delete(id);
  }
}

async function excluirCobertura(id) {
  const motivo = window.prompt("Motivo para excluir este escaneo del conteo:", "Error de escaneo");
  if (motivo === null) return;
  try {
    await api("/api/admin/cobertura/excluir", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id, motivo}),
    });
    toast("Escaneo excluido del conteo.", "ok");
    await cargarCobertura(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function reiniciarCobertura() {
  const periodo = $("cov-periodo").value;
  const sede = $("cov-sede").value;
  if (!sede) {
    toast("Elige una sede concreta para reiniciar la cobertura.", "error");
    return;
  }
  const tecleado = window.prompt(
    `Vas a reiniciar la cobertura de ${sede} en ${periodo}. Escribe REINICIAR:`,
    "",
  );
  if ((tecleado || "").trim().toUpperCase() !== "REINICIAR") return;
  try {
    await api("/api/admin/cobertura/reiniciar", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({periodo, sede, confirm: "REINICIAR"}),
    });
    toast("Cobertura reiniciada; los jobs viejos no se vuelven a contar.", "ok");
    await cargarCobertura(true);
  } catch (error) {
    toast(error.message, "error");
  }
}

function abrirPerfil() {
  $("perfil-name").value = estado.nombre || "";
  setEstadoPerfil("");
  $("perfil-dialog").showModal();
}

function setEstadoPerfil(texto) {
  const nodo = $("perfil-status");
  if (!nodo) return;
  nodo.textContent = texto;
  nodo.className = "form-status" + (texto ? " error" : "");
}

async function guardarPerfil(evento) {
  evento.preventDefault();
  const nombre = $("perfil-name").value.trim();
  if (!nombre) return;
  try {
    const d = await api("/api/me", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: nombre }),
    });
    estado.nombre = d.name;
    document.querySelectorAll("[data-user-name]").forEach((n) => {
      n.textContent = d.name;
    });
    $("perfil-dialog").close();
    toast("Nombre actualizado.", "ok");
  } catch (error) {
    setEstadoPerfil(error.message);
  }
}

async function cargarCobertura(silencioso = false) {
  if (!estado.sedes.length) {
    $("cov-empty").hidden = false;
    $("cov-content").hidden = true;
    return;
  }
  $("cov-empty").hidden = true;
  $("cov-content").hidden = false;
  $("cov-reset").hidden = estado.role !== "admin";
  const periodo = $("cov-periodo").value || periodoLocal();
  const select = $("cov-sede");
  const seleccion = select.value || estado.sedes[0];
  const previas = [...select.options].map((o) => o.value).filter(Boolean);
  if (previas.join("|") !== estado.sedes.join("|")) {
    select.textContent = "";
    for (const sede of estado.sedes) select.appendChild(new Option(sede, sede));
    if (estado.role === "admin") select.appendChild(new Option("Todas las sedes", ""));
    select.value = estado.sedes.includes(seleccion) ? seleccion : estado.sedes[0];
  }
  estado.coberturaPeriodo = periodo;
  estado.coberturaSede = select.value;
  try {
    const q = new URLSearchParams({ periodo });
    if (select.value) q.set("sede", select.value);
    const d = await api(`/api/cobertura?${q.toString()}`);
    estado.cobertura = d;
    renderCobertura();
    if (!silencioso) toast("Cobertura actualizada.", "info");
  } catch (error) {
    if (!silencioso) toast(error.message, "error");
  }
}

function renderCobertura() {
  const datos = estado.cobertura;
  if (!datos) return;
  const sedes = datos.sedes || [];
  const equipos = sedes.flatMap((s) =>
    (s.equipos || s.faltantes || []).map((e) => ({ ...e, sede: s.sede })));
  const escaneados = sedes.flatMap((s) =>
    (s.detalle_escaneos || []).map((e) => ({ ...e, sede: s.sede })));
  const q = ($("cov-buscar")?.value || "").trim().toLowerCase();
  const filtro = (fila) => !q || [
    fila.sn, fila.marca, fila.modelo, fila.ubicacion, fila.piso, fila.estatus,
    fila.usuario, fila.documento, fila.sede,
  ].filter(Boolean).join(" ").toLowerCase().includes(q);

  $("cov-esperados").textContent = datos.esperados ?? 0;
  $("cov-escaneados").textContent = datos.escaneados ?? 0;
  $("cov-faltantes").textContent = datos.faltantes ?? 0;
  $("cov-duplicados").textContent = datos.duplicados ?? 0;
  $("cov-sede-nombre").textContent = sedes.map((s) => s.sede).join(" · ") || "—";
  $("cov-faltantes-count").textContent = String(equipos.filter(filtro).length);

  const cuerpoF = $("cov-faltantes-body");
  cuerpoF.textContent = "";
  const visiblesF = equipos.filter(filtro);
  if (!visiblesF.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.className = "admin-empty";
    td.textContent = q ? "Sin coincidencias." : "No hay equipos inventariados en esta sede.";
    tr.appendChild(td);
    cuerpoF.appendChild(tr);
  } else {
    for (const e of visiblesF) {
      const tr = document.createElement("tr");
      const clase = claseEstatus(e.estatus);
      if (clase) tr.classList.add(clase);
      if (!e.escaneado) tr.classList.add("faltante");
      for (const v of [e.sede, e.sn, e.marca, e.modelo, e.ubicacion, e.piso, e.estatus]) {
        const td = document.createElement("td");
        td.textContent = v || "—";
        tr.appendChild(td);
      }
      const tdAcc = document.createElement("td");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-text";
      btn.textContent = "Editar";
      btn.addEventListener("click", () => abrirEquipoForm(e));
      tdAcc.appendChild(btn);
      tr.appendChild(tdAcc);
      cuerpoF.appendChild(tr);
    }
  }

  document.querySelectorAll(".cov-admin-col").forEach((n) => {
    n.hidden = estado.role !== "admin";
  });
  const cuerpoE = $("cov-escaneados-body");
  cuerpoE.textContent = "";
  const visiblesE = escaneados
    .filter(filtro)
    .sort((a, b) => Number(b.duplicado) - Number(a.duplicado));
  if (!visiblesE.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = estado.role === "admin" ? 6 : 5;
    td.className = "admin-empty";
    td.textContent = "Sin escaneos registrados.";
    tr.appendChild(td);
    cuerpoE.appendChild(tr);
  } else {
    for (const e of visiblesE) {
      const tr = document.createElement("tr");
      const estadoTexto = e.duplicado ? "DUPLICADO" : "OK";
      for (const v of [
        e.sn, e.escaneado_en ? e.escaneado_en.replace("T", " ").slice(0, 16) : "—",
        e.usuario || "—", e.documento || "—", estadoTexto,
      ]) {
        const td = document.createElement("td");
        td.textContent = v;
        tr.appendChild(td);
      }
      if (estado.role === "admin") {
        const td = document.createElement("td");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-text danger";
        btn.textContent = "Excluir";
        btn.addEventListener("click", () => excluirCobertura(e.id));
        td.appendChild(btn);
        tr.appendChild(td);
      }
      cuerpoE.appendChild(tr);
    }
  }
}

async function iniciar() {
  try {
    const me = await api("/api/me");
    estado.csrf = me.csrf;
    estado.sedes = me.sedes || [];
    estado.role = me.role || "user";
    estado.nombre = me.name || me.email;
    document.querySelectorAll("[data-user-email]").forEach((nodo) => {
      nodo.textContent = me.email;
    });
    document.querySelectorAll("[data-user-name]").forEach((nodo) => {
      nodo.textContent = estado.nombre;
    });
    const navAdmin = $("nav-admin");
    if (navAdmin) navAdmin.hidden = me.role !== "admin";
    $("upload-hint").textContent =
      `PDF · máx. ${me.limits.max_upload_mb} MB · ${me.limits.max_pages} págs.` +
      ` · ${me.limits.max_queued_jobs} documentos a la vez`;
  } catch (error) {
    mostrarEstado(error.message, "error");
    return;
  }

  conectarSubida();
  $("btn-refresh").addEventListener("click", () => cargarJobs(false));
  $("job-search").addEventListener("input", renderJobs);
  $("btn-perfil").addEventListener("click", abrirPerfil);
  $("perfil-cancel").addEventListener("click", () => $("perfil-dialog").close());
  $("resumen-cerrar").addEventListener("click", () => $("resultado-dialog").close());
  $("perfil-form").addEventListener("submit", guardarPerfil);
  $("cov-refresh").addEventListener("click", () => cargarCobertura(true));
  $("cov-periodo").addEventListener("change", () => cargarCobertura(true));
  $("cov-sede").addEventListener("change", () => cargarCobertura(true));
  $("cov-buscar").addEventListener("input", renderCobertura);
  $("cov-reset").addEventListener("click", reiniciarCobertura);
  $("cov-add").addEventListener("click", () => abrirEquipoForm(null));
  $("eq-cancel").addEventListener("click", () => $("equipo-dialog").close());
  $("equipo-form").addEventListener("submit", guardarEquipoUsuario);
  let timerEqUbicacion = null;
  $("eq-ubicacion").addEventListener("input", (e) => {
    clearTimeout(timerEqUbicacion);
    timerEqUbicacion = setTimeout(() => cargarUbicacionesUsuario(e.target.value), 250);
  });
  const periodo = $("cov-periodo");
  if (!periodo.value) periodo.value = periodoLocal();
  await cargarJobs(false);
  await cargarCobertura(true);

  estado.timer = window.setInterval(() => {
    if (document.hidden) return;
    const activo = document.activeElement;
    // No reemplazar la tabla mientras el usuario está enfocando una acción.
    if (activo && activo.closest && activo.closest("#jobs")) return;
    cargarJobs(true);
    if (new Date().getSeconds() % 6 === 0) cargarCobertura(true);
  }, 3000);
}

document.addEventListener("DOMContentLoaded", iniciar);
