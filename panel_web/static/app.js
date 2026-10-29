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
  limits: {},
  cobertura: null,
  coberturaPeriodo: "",
  coberturaSede: "",
  sedePreferida: "",
  coberturaOrden: { campo: "", direccion: 1 },
  resumenJob: null,
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
  if (!respuesta.ok) {
    const error = new Error(datos?.detail?.mensaje || textoError(datos, respuesta.status));
    error.codigo = datos?.detail?.codigo;
    throw error;
  }
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

function crearStatusPill(estatus) {
  const pill = document.createElement("span");
  const clase = claseEstatus(estatus);
  pill.className = "status-pill " + (clase || "status-ok");
  pill.textContent = estatus || "Sin estatus";
  return pill;
}

function pintarChips(
  contenedor, filtros, clearId = "cov-clear", countId = "cov-filtros-count",
) {
  contenedor.textContent = "";
  const activos = filtros.filter(Boolean);
  const boton = document.getElementById(clearId);
  const contador = document.getElementById(countId);
  if (boton) boton.hidden = activos.length === 0;
  if (contador) contador.textContent = activos.length
    ? `${activos.length} filtro${activos.length === 1 ? "" : "s"}`
    : "";
  for (const filtro of activos) {
    const chip = document.createElement("span");
    chip.className = "filter-chip";
    const texto = document.createElement("span");
    texto.textContent = filtro.label;
    const quitar = document.createElement("button");
    quitar.type = "button";
    quitar.setAttribute("aria-label", `Quitar ${filtro.label}`);
    quitar.textContent = "×";
    quitar.addEventListener("click", filtro.quitar);
    chip.append(texto, quitar);
    contenedor.appendChild(chip);
  }
}

function actualizarChipsCobertura() {
  const filtros = [];
  const q = $("cov-buscar").value.trim();
  if (q) {
    filtros.push({
      label: `Buscar: ${q}`,
      quitar: () => {
        $("cov-buscar").value = "";
        renderCobertura();
        guardarFiltrosCobertura();
      },
    });
  }
  const pares = [
    ["cov-filtro-estado", "Registros"],
    ["cov-filtro-estatus", "Estatus"],
    ["cov-filtro-marca", "Marca"],
    ["cov-filtro-piso", "Piso"],
  ];
  for (const [id, etiqueta] of pares) {
    const select = $(id);
    if (!select.value) continue;
    const texto = select.options[select.selectedIndex]?.textContent || select.value;
    filtros.push({
      label: `${etiqueta}: ${texto}`,
      quitar: () => {
        select.value = "";
        renderCobertura();
        guardarFiltrosCobertura();
      },
    });
  }
  pintarChips($("cov-filtros-activos"), filtros);
}

function limpiarFiltrosCobertura() {
  $("cov-buscar").value = "";
  for (const id of ["cov-filtro-estado", "cov-filtro-estatus", "cov-filtro-marca", "cov-filtro-piso"]) {
    $(id).value = "";
  }
  renderCobertura();
  guardarFiltrosCobertura();
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

function pintarSugerenciasUsuario(datos, q) {
  const contenedor = $("eq-ubicacion-sugerencias");
  const aviso = $("eq-ubicacion-aviso");
  contenedor.textContent = "";
  const existentes = datos.ubicaciones || [];
  const exacta = existentes.some((n) => n.trim().toLowerCase() === q.trim().toLowerCase());
  if (!q.trim()) {
    aviso.textContent = existentes.length
      ? "Elige una existente o escribe una nueva."
      : "Aún no hay ubicaciones en el inventario.";
    return;
  }
  if (exacta) {
    aviso.textContent = "Ya existe; se usará esa ubicación.";
    return;
  }
  const similares = datos.similares || [];
  if (!similares.length) {
    aviso.textContent = "No hay parecidas; se guardará como nueva.";
    return;
  }
  aviso.textContent = "Hay ubicaciones parecidas. Elige una o confirma la nueva.";
  for (const nombre of similares.slice(0, 5)) {
    const boton = document.createElement("button");
    boton.type = "button";
    boton.className = "location-chip";
    boton.textContent = nombre;
    boton.addEventListener("click", () => {
      $("eq-ubicacion").value = nombre;
      contenedor.textContent = "";
      aviso.textContent = "Usando una ubicación existente.";
    });
    contenedor.appendChild(boton);
  }
}

async function cargarUbicacionesUsuario(q = "", sede = "") {
  try {
    const params = new URLSearchParams({q});
    if (sede) params.set("sede", sede);
    const d = await api(`/api/ubicaciones?${params.toString()}`);
    const lista = $("eq-ubicaciones");
    lista.textContent = "";
    for (const nombre of d.ubicaciones || []) {
      const option = document.createElement("option");
      option.value = nombre;
      lista.appendChild(option);
    }
    pintarSugerenciasUsuario(d, q);
  } catch {
    // La sugerencia es auxiliar; no bloquea el formulario.
  }
}

async function confirmarUbicacionUsuario() {
  const q = $("eq-ubicacion").value.trim();
  if (!q) return {confirmar: false};
  const params = new URLSearchParams({q});
  const sede = $("eq-sede").value;
  if (sede) params.set("sede", sede);
  const d = await api(`/api/ubicaciones?${params.toString()}`);
  const exacta = (d.ubicaciones || []).some(
    (n) => n.trim().toLowerCase() === q.toLowerCase(),
  );
  const similares = d.similares || [];
  if (exacta || !similares.length) return {confirmar: false};

  let piso = $("eq-piso").value.trim();
  if (!piso) {
    piso = window.prompt(
      `La ubicación "${q}" se parece a: ${similares.slice(0, 5).join(", ")}.\n` +
      "¿Qué piso le corresponde? (Se guardará como ubicación nueva.)",
      "",
    );
    if (piso === null) return null;
    piso = piso.trim();
    if (!piso) return null;
    $("eq-piso").value = piso;
  }
  const seguro = window.confirm(
    `Ya existen ubicaciones parecidas:\n\n${similares.slice(0, 5).join("\n")}\n\n` +
    `¿Crear de todos modos "${q}" con piso "${piso}"?`,
  );
  if (!seguro) return null;
  return {confirmar: true, piso};
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
  cargarUbicacionesUsuario($("eq-ubicacion").value, $("eq-sede").value);
  $("equipo-dialog").showModal();
  window.setTimeout(() => $("eq-sn").focus(), 60);
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
    const confirmacion = await confirmarUbicacionUsuario();
    if (confirmacion === null) return;
    if (confirmacion.confirmar) {
      datos.confirmar_ubicacion = true;
      datos.piso = confirmacion.piso;
    }
    const d = await api("/api/equipos", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(datos),
    });
    $("equipo-dialog").close();
    toast(`${d.creado ? "Equipo agregado" : "Equipo actualizado"}: ${d.sn}`, "ok");
    await cargarCobertura(true);
    if (estado.resumenJob && $("resultado-dialog").open) {
      await abrirResumen(estado.resumenJob);
    }
  } catch (error) {
    setEqStatus(error.message);
  }
}

function renderResumenSeriales(r) {
  const contenedor = $("resumen-seriales");
  contenedor.textContent = "";
  const desconocidos = r.desconocidos || (r.no_inventario || []).map((sn) => ({
    sn, sede_sugerida: "",
  }));
  const otras = r.otra_sede || [];
  const sinSede = r.sin_sede || [];
  const ambiguos = r.ambiguos || [];
  contenedor.hidden = !desconocidos.length && !otras.length && !sinSede.length && !ambiguos.length;

  if (desconocidos.length) {
    const bloque = document.createElement("div");
    bloque.className = "resumen-serial-group";
    const titulo = document.createElement("h3");
    titulo.textContent = "Seriales no inventariados";
    const ayuda = document.createElement("p");
    ayuda.textContent = r.puede_agregar
      ? "Puedes agregarlos a una de tus sedes asignadas. Revisa ubicación y piso antes de guardar."
      : "No tienes una sede asignada; pide a administración que asocie tu cuenta.";
    bloque.append(titulo, ayuda);
    for (const item of desconocidos) {
      const fila = document.createElement("div");
      fila.className = "resumen-serial-row";
      const serial = document.createElement("code");
      serial.textContent = item.sn;
      fila.appendChild(serial);
      if (r.puede_agregar) {
        const boton = document.createElement("button");
        boton.type = "button";
        boton.className = "btn-text";
        boton.textContent = "Agregar al inventario";
        boton.addEventListener("click", () => {
          abrirEquipoForm({ sn: item.sn, sede: item.sede_sugerida || "" });
        });
        fila.appendChild(boton);
      }
      bloque.appendChild(fila);
    }
    contenedor.appendChild(bloque);
  }

  if (otras.length || sinSede.length) {
    const bloque = document.createElement("div");
    bloque.className = "resumen-serial-group resumen-serial-alert";
    const titulo = document.createElement("h3");
    titulo.textContent = "Seriales fuera de tu alcance";
    const texto = document.createElement("p");
    texto.textContent = [
      r.mensaje_otra_sede || "",
      sinSede.length ? "Hay seriales sin sede en el inventario; avisa a administración." : "",
      ambiguos.length ? "Hay seriales que coinciden con más de un equipo; revisa la serie exacta." : "",
    ].filter(Boolean).join(" ");
    bloque.append(titulo, texto);
    for (const item of otras) {
      const fila = document.createElement("div");
      fila.className = "resumen-serial-row";
      const serial = document.createElement("code");
      serial.textContent = item.sn;
      const sede = document.createElement("span");
      sede.textContent = `Sede ${item.sede}`;
      fila.append(serial, sede);
      bloque.appendChild(fila);
    }
    for (const sn of sinSede) {
      const fila = document.createElement("div");
      fila.className = "resumen-serial-row";
      const serial = document.createElement("code");
      serial.textContent = sn;
      const nota = document.createElement("span");
      nota.textContent = "Sin sede asignada";
      fila.append(serial, nota);
      bloque.appendChild(fila);
    }
    for (const sn of ambiguos) {
      const fila = document.createElement("div");
      fila.className = "resumen-serial-row";
      const serial = document.createElement("code");
      serial.textContent = sn;
      const nota = document.createElement("span");
      nota.textContent = "Coincidencia ambigua";
      fila.append(serial, nota);
      bloque.appendChild(fila);
    }
    contenedor.appendChild(bloque);
  }
}

async function abrirResumen(job) {
  estado.resumenJob = job;
  $("resumen-nombre").textContent = job.filename;
  $("resumen-texto").textContent = "Cargando resultado…";
  $("resumen-aviso").hidden = true;
  $("resumen-seriales").hidden = true;
  $("resumen-documentos").textContent = "";
  const dialogo = $("resultado-dialog");
  if (!dialogo.open) dialogo.showModal();
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
      ? `${noInv.length} serial(es) no están en el inventario de esta sede.`
      : "";
    renderResumenSeriales(r);
    if ((r.otra_sede || []).length) {
      toast("Se detectaron seriales de otra sede en este documento.", "error");
    }
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

function reiniciarProgresoSubida() {
  const caja = $("upload-progress");
  caja.hidden = false;
  caja.classList.remove("is-error");
  $("upload-progress-bar").style.transform = "scaleX(0)";
  $("upload-progress-track").setAttribute("aria-valuenow", "0");
  $("upload-progress-percent").textContent = "0%";
  $("upload-progress-detail").textContent = "";
}

function actualizarProgresoSubida(archivo, indice, total, cargado, bytes) {
  const porcentaje = bytes > 0
    ? Math.min(100, Math.round((cargado / bytes) * 100))
    : 0;
  $("upload-progress").hidden = false;
  $("upload-progress").classList.remove("is-error");
  $("upload-progress-file").textContent =
    `${archivo.name} · ${indice} de ${total}`;
  $("upload-progress-percent").textContent = `${porcentaje}%`;
  $("upload-progress-bar").style.transform = `scaleX(${porcentaje / 100})`;
  $("upload-progress-track").setAttribute("aria-valuenow", String(porcentaje));
  $("upload-progress-detail").textContent = bytes
    ? `${formatoBytes(cargado)} de ${formatoBytes(bytes)}`
    : "Preparando archivo…";
}

function marcarErrorProgreso(mensaje) {
  $("upload-progress").classList.add("is-error");
  $("upload-progress-detail").textContent = mensaje;
}

function ocultarProgresoSubida(demora = 3000) {
  window.setTimeout(() => {
    $("upload-progress").hidden = true;
  }, demora);
}

function validarArchivoLocal(archivo) {
  const nombre = archivo.name.toLowerCase();
  const maxMb = Number(estado.limits.max_upload_mb || 50);
  if (!nombre.endsWith(".pdf") && archivo.type !== "application/pdf") {
    return "No es un PDF. Elige un archivo con extensión .pdf.";
  }
  if (!archivo.size) {
    return "El archivo está vacío.";
  }
  if (archivo.size > maxMb * 1024 * 1024) {
    return `El archivo pesa ${formatoBytes(archivo.size)} y el máximo es ${maxMb} MB.`;
  }
  return "";
}

function mensajeErrorSubida(error) {
  const porCodigo = {
    pdf_demasiado_grande: "El PDF supera el tamaño máximo permitido.",
    pdf_invalido: "El archivo está dañado, truncado o no es un PDF real.",
    pdf_cifrado: "El PDF está protegido con contraseña. Quita la protección y vuelve a intentarlo.",
    pdf_ilegible: "No se pudo leer el PDF; puede estar dañado o usar una estructura no soportada.",
    pdf_contenido_activo: "El PDF contiene JavaScript, archivos adjuntos o acciones activas de alto riesgo. Por seguridad no se acepta.",
    pdf_sin_paginas: "El PDF no contiene páginas.",
    pdf_demasiadas_paginas: "El PDF supera el máximo de páginas permitido.",
    subida_interrumpida: "La subida se interrumpió antes de completarse. Revisa tu conexión y vuelve a intentarlo.",
    limite_subidas: "Demasiadas subidas en poco tiempo. Espera unos minutos.",
    demasiados_activos: "Ya tienes el máximo de documentos activos. Espera a que terminen.",
    limite_paginas: "Superarías el límite diario de páginas.",
    conexion: "Se perdió la conexión durante la subida. El archivo no se agregó.",
    tiempo_agotado: "La subida tardó demasiado y se canceló. Intenta con una conexión más estable.",
    sesion: "Tu sesión expiró. Vuelve a iniciar sesión.",
  };
  if (error && error.codigo && porCodigo[error.codigo]) {
    return porCodigo[error.codigo];
  }
  if (error && error.status === 413) return "El PDF supera el tamaño máximo permitido.";
  if (error && error.status === 429) return error.message || "Demasiadas solicitudes. Espera un momento.";
  if (error && error.status === 400) return error.message || "El archivo no pasó la validación.";
  return (error && error.message) || "No se pudo subir el archivo.";
}

function subirArchivoXHR(archivo, indice, total, reprocesar = false) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    if (estado.csrf) xhr.setRequestHeader("X-CSRF-Token", estado.csrf);
    xhr.timeout = 10 * 60 * 1000;
    xhr.upload.onprogress = (evento) => {
      if (evento.lengthComputable) {
        actualizarProgresoSubida(
          archivo, indice, total, evento.loaded, evento.total,
        );
      }
    };
    xhr.upload.onload = () => {
      actualizarProgresoSubida(archivo, indice, total, archivo.size, archivo.size);
      $("upload-progress-detail").textContent = "Validando PDF y agregando a la cola…";
    };
    xhr.onload = () => {
      let datos = null;
      try {
        datos = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        datos = null;
      }
      if (xhr.status === 401) {
        window.location.assign("/login");
        reject(Object.assign(new Error("Sesión expirada."), {codigo: "sesion", status: 401}));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(datos || {});
        return;
      }
      const error = new Error(
        datos?.detail?.mensaje || textoError(datos, xhr.status),
      );
      error.codigo = datos?.detail?.codigo;
      error.status = xhr.status;
      reject(error);
    };
    xhr.onerror = () => {
      reject(Object.assign(
        new Error("Se perdió la conexión durante la subida."),
        {codigo: "conexion", status: 0},
      ));
    };
    xhr.ontimeout = () => {
      reject(Object.assign(
        new Error("La subida tardó demasiado."),
        {codigo: "tiempo_agotado", status: 0},
      ));
    };
    xhr.onabort = () => {
      reject(Object.assign(
        new Error("La subida se canceló."),
        {codigo: "subida_interrumpida", status: 0},
      ));
    };
    const datos = new FormData();
    datos.append("archivo", archivo, archivo.name);
    if (reprocesar) datos.append("reprocesar", "true");
    xhr.send(datos);
  });
}

async function subirArchivos(archivos) {
  const lista = Array.from(archivos || []);
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
  reiniciarProgresoSubida();
  let subidos = 0;
  let omitidos = 0;
  const errores = [];
  const advertencias = [];

  try {
    for (let indice = 0; indice < lista.length; indice++) {
      const archivo = lista[indice];
      const errorLocal = validarArchivoLocal(archivo);
      if (errorLocal) {
        errores.push(`${archivo.name}: ${errorLocal}`);
        marcarErrorProgreso(`${archivo.name}: ${errorLocal}`);
        continue;
      }
      actualizarProgresoSubida(archivo, indice + 1, lista.length, 0, archivo.size);
      mostrarEstado(`Subiendo ${archivo.name} (${indice + 1}/${lista.length})…`);
      try {
        let respuesta;
        try {
          respuesta = await subirArchivoXHR(archivo, indice + 1, lista.length);
        } catch (error) {
          if (error.codigo !== "pdf_repetido") throw error;
          const confirmar = window.confirm(`${archivo.name}: ${error.message}`);
          if (!confirmar) {
            omitidos++;
            continue;
          }
          respuesta = await subirArchivoXHR(
            archivo, indice + 1, lista.length, true,
          );
        }
        subidos++;
        for (const aviso of respuesta.advertencias || []) {
          advertencias.push(`${archivo.name}: ${aviso}`);
        }
        actualizarProgresoSubida(archivo, indice + 1, lista.length, archivo.size, archivo.size);
        $("upload-progress-detail").textContent = "Agregado a la cola.";
      } catch (error) {
        const mensaje = mensajeErrorSubida(error);
        errores.push(`${archivo.name}: ${mensaje}`);
        marcarErrorProgreso(`${archivo.name}: ${mensaje}`);
      }
    }

    if (advertencias.length) {
      const activos = advertencias.filter((a) => !a.toLowerCase().includes("sin %%eof"));
      toast(
        activos.length
          ? `Contenido activo detectado en el PDF (${activos.slice(0, 3).join(", ")}). Se procesa como documento y no se ejecuta.`
          : "El PDF tiene una estructura incompleta (sin cierre %%EOF). Se procesará para revisión.",
        "info",
      );
    }
    if (errores.length) {
      const detalle = errores.slice(0, 2).join(" ");
      mostrarEstado(
        `${subidos} documento(s) agregado(s). ${errores.length} con error: ${detalle}`,
        "error",
      );
      ocultarProgresoSubida(8000);
    } else if (omitidos) {
      mostrarEstado(
        `${subidos} documento(s) agregado(s); ${omitidos} repetido(s) omitido(s).`,
        "ok",
      );
      ocultarProgresoSubida(3000);
    } else {
      mostrarEstado(
        subidos === 1
          ? "Documento agregado a la cola."
          : `${subidos} documentos agregados a la cola.`,
        "ok",
      );
      ocultarProgresoSubida(3000);
    }
    await cargarJobs(true);
  } catch (error) {
    const mensaje = mensajeErrorSubida(error);
    mostrarEstado(mensaje, "error");
    marcarErrorProgreso(mensaje);
    ocultarProgresoSubida(8000);
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

function restaurarFiltrosCobertura() {
  const params = new URLSearchParams(window.location.search);
  const set = (id, clave) => {
    const valor = params.get(clave);
    if (valor !== null) $(id).value = valor;
  };
  set("cov-periodo", "periodo");
  set("cov-buscar", "q");
  set("cov-filtro-estado", "estado");
  set("cov-filtro-estatus", "estatus");
  set("cov-filtro-marca", "marca");
  set("cov-filtro-piso", "piso");
  estado.sedePreferida = params.get("sede") || "";
}

function guardarFiltrosCobertura() {
  const params = new URLSearchParams(window.location.search);
  const guardar = (clave, valor) => {
    if (valor) params.set(clave, valor);
    else params.delete(clave);
  };
  guardar("periodo", $("cov-periodo").value);
  guardar("sede", $("cov-sede").value);
  guardar("q", $("cov-buscar").value.trim());
  guardar("estado", $("cov-filtro-estado").value);
  guardar("estatus", $("cov-filtro-estatus").value);
  guardar("marca", $("cov-filtro-marca").value);
  guardar("piso", $("cov-filtro-piso").value);
  const query = params.toString();
  const url = window.location.pathname + (query ? `?${query}` : "") + window.location.hash;
  window.history.replaceState(null, "", url);
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
        api(`/api/jobs/${encodeURIComponent(job.id)}/resultado`)
          .then((datos) => {
            const otras = datos?.resumen?.otra_sede || [];
            const nuevos = datos?.resumen?.desconocidos || [];
            if (otras.length) {
              toast(
                `Atención: ${job.filename} contiene seriales de otra sede.`,
                "error",
              );
            }
            if (nuevos.length) {
              toast(
                `${nuevos.length} serial(es) nuevo(s) en ${job.filename}. Abre Resumen para agregarlos.`,
                "info",
              );
            }
          })
          .catch(() => {});
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

let solicitudCobertura = 0;
function mostrarEsqueletoCobertura() {
  const cuerpo = $("cov-faltantes-body");
  cuerpo.textContent = "";
  for (let i = 0; i < 4; i++) {
    const tr = document.createElement("tr");
    tr.className = "skeleton-row";
    const td = document.createElement("td");
    td.colSpan = 8;
    const linea = document.createElement("span");
    linea.className = "skeleton-line";
    td.appendChild(linea);
    tr.appendChild(td);
    cuerpo.appendChild(tr);
  }
}

async function cargarCobertura(silencioso = false) {
  const solicitud = ++solicitudCobertura;
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
  if (estado.sedePreferida
      && [...select.options].some((o) => o.value === estado.sedePreferida)) {
    select.value = estado.sedePreferida;
    estado.sedePreferida = "";
  }
  estado.coberturaPeriodo = periodo;
  estado.coberturaSede = select.value;
  guardarFiltrosCobertura();
  $("cov-content").setAttribute("aria-busy", "true");
  if (!estado.cobertura) mostrarEsqueletoCobertura();
  try {
    const q = new URLSearchParams({ periodo });
    if (select.value) q.set("sede", select.value);
    const d = await api(`/api/cobertura?${q.toString()}`);
    if (solicitud !== solicitudCobertura) return;
    estado.cobertura = d;
    renderCobertura();
    if (!silencioso) toast("Cobertura actualizada.", "info");
  } catch (error) {
    if (!silencioso) toast(error.message, "error");
  } finally {
    if (solicitud === solicitudCobertura) $("cov-content").removeAttribute("aria-busy");
  }
}

function renderCobertura() {
  const datos = estado.cobertura;
  if (!datos) return;
  const sedes = datos.sedes || [];
  const historico = sedes.some((s) => s.cerrado);
  $("cov-add").hidden = historico;
  $("cov-add-inline").hidden = historico;
  $("cov-historia").textContent = sedes.some((s) => s.inventario_origen === "reconstruido")
    ? "Mes anterior reconstruido con el inventario disponible; no existía una copia histórica."
    : historico ? "Mes cerrado: los equipos y sus sedes se conservan según el inventario de ese mes."
    : "Mes abierto: la cobertura incorpora las actualizaciones del inventario.";
  const equipos = sedes.flatMap((s) =>
    (s.equipos || s.faltantes || []).map((e) => ({ ...e, sede: s.sede })));
  const orden = estado.coberturaOrden;
  if (orden.campo) {
    equipos.sort((a, b) => {
      const va = String(a[orden.campo] ?? "");
      const vb = String(b[orden.campo] ?? "");
      return va.localeCompare(vb, "es", { numeric: true, sensitivity: "base" })
        * orden.direccion;
    });
  }
  document.querySelectorAll("#cov-equipos-table th[data-sort]").forEach((th) => {
    const activo = th.dataset.sort === orden.campo;
    th.setAttribute("aria-sort", activo
      ? (orden.direccion === 1 ? "ascending" : "descending")
      : "none");
    th.classList.toggle("is-sorted", activo);
  });
  const escaneados = sedes.flatMap((s) =>
    (s.detalle_escaneos || []).map((e) => ({ ...e, sede: s.sede })));
  const q = ($("cov-buscar")?.value || "").trim().toLowerCase();
  const fEstado = $("cov-filtro-estado").value;
  const fEstatus = $("cov-filtro-estatus").value;
  const fMarca = $("cov-filtro-marca").value;
  const fPiso = $("cov-filtro-piso").value;
  const filtro = (fila) => {
    if (q && ![
      fila.sn, fila.marca, fila.modelo, fila.ubicacion, fila.piso, fila.estatus,
      fila.usuario, fila.documento, fila.sede,
    ].filter(Boolean).join(" ").toLowerCase().includes(q)) return false;
    if (fEstatus && fila.estatus !== fEstatus) return false;
    if (fMarca && fila.marca !== fMarca) return false;
    if (fPiso && fila.piso !== fPiso) return false;
    if (fEstado === "faltante" && fila.escaneado) return false;
    if (fEstado === "escaneado" && !fila.escaneado) return false;
    return true;
  };

  const unicos = (campo) => [...new Set(equipos.map((e) => e[campo]).filter(Boolean))].sort();
  const opciones = (id, valores, etiqueta) => {
    const select = $(id);
    const previo = select.value;
    select.textContent = "";
    select.appendChild(new Option(etiqueta, ""));
    for (const valor of valores) select.appendChild(new Option(valor, valor));
    if ([...select.options].some((o) => o.value === previo)) select.value = previo;
  };
  opciones("cov-filtro-estatus", unicos("estatus"), "todos los estatus");
  opciones("cov-filtro-marca", unicos("marca"), "todas las marcas");
  opciones("cov-filtro-piso", unicos("piso"), "todos los pisos");
  actualizarChipsCobertura();

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
    td.className = "admin-empty table-empty";
    const texto = document.createElement("span");
    texto.textContent = q || fEstado || fEstatus || fMarca || fPiso
      ? "No hay equipos con estos filtros."
      : "No hay equipos inventariados en esta sede.";
    td.appendChild(texto);
    const accion = document.createElement("button");
    accion.type = "button";
    accion.className = "btn-text";
    if (q || fEstado || fEstatus || fMarca || fPiso) {
      accion.textContent = "Limpiar filtros";
      accion.addEventListener("click", limpiarFiltrosCobertura);
    } else {
      accion.textContent = "Agregar equipo";
      accion.hidden = historico;
      accion.addEventListener("click", () => abrirEquipoForm(null));
    }
    td.appendChild(accion);
    tr.appendChild(td);
    cuerpoF.appendChild(tr);
  } else {
    for (const e of visiblesF) {
      const tr = document.createElement("tr");
      const clase = claseEstatus(e.estatus);
      if (clase) tr.classList.add(clase);
      if (!e.escaneado) tr.classList.add("faltante");
      [e.sede, e.sn, e.marca, e.modelo, e.ubicacion, e.piso, e.estatus]
        .forEach((v, indice) => {
          const td = document.createElement("td");
          if (indice === 6) {
            td.className = "status-cell";
            td.appendChild(crearStatusPill(v));
          } else {
            td.textContent = v || "—";
          }
          tr.appendChild(td);
        });
      const tdAcc = document.createElement("td");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-text";
      btn.textContent = "Editar";
      btn.hidden = historico;
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
    .filter((fila) => !q || [
      fila.sn, fila.usuario, fila.documento, fila.sede,
    ].filter(Boolean).join(" ").toLowerCase().includes(q))
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
        e.usuario || "—", `${e.documento || "—"}${e.archivo_eliminado ? " (archivo eliminado)" : ""}`, estadoTexto,
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
    estado.limits = me.limits || {};
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
  $("resumen-cerrar").addEventListener("click", () => {
    estado.resumenJob = null;
    $("resultado-dialog").close();
  });
  $("resultado-dialog").addEventListener("close", () => {
    estado.resumenJob = null;
  });
  $("perfil-form").addEventListener("submit", guardarPerfil);
  $("cov-refresh").addEventListener("click", () => cargarCobertura(true));
  $("cov-periodo").addEventListener("change", () => cargarCobertura(true));
  $("cov-sede").addEventListener("change", () => cargarCobertura(true));
  $("cov-buscar").addEventListener("input", () => {
    renderCobertura();
    guardarFiltrosCobertura();
  });
  for (const id of ["cov-filtro-estado", "cov-filtro-estatus", "cov-filtro-marca", "cov-filtro-piso"]) {
    $(id).addEventListener("change", () => {
      renderCobertura();
      guardarFiltrosCobertura();
    });
  }
  document.querySelectorAll("#cov-equipos-table th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const campo = th.dataset.sort;
      estado.coberturaOrden = {
        campo,
        direccion: estado.coberturaOrden.campo === campo
          && estado.coberturaOrden.direccion === 1 ? -1 : 1,
      };
      renderCobertura();
    });
  });
  $("cov-reset").addEventListener("click", reiniciarCobertura);
  $("cov-add").addEventListener("click", () => abrirEquipoForm(null));
  $("cov-add-inline").addEventListener("click", () => abrirEquipoForm(null));
  $("cov-clear").addEventListener("click", limpiarFiltrosCobertura);
  $("eq-cancel").addEventListener("click", () => $("equipo-dialog").close());
  $("equipo-form").addEventListener("submit", guardarEquipoUsuario);
  let timerEqUbicacion = null;
  $("eq-ubicacion").addEventListener("input", (e) => {
    clearTimeout(timerEqUbicacion);
    timerEqUbicacion = setTimeout(
      () => cargarUbicacionesUsuario(e.target.value, $("eq-sede").value),
      250,
    );
  });
  $("eq-sede").addEventListener("change", () => {
    cargarUbicacionesUsuario($("eq-ubicacion").value, $("eq-sede").value);
  });
  const periodo = $("cov-periodo");
  if (!periodo.value) periodo.value = periodoLocal();
  restaurarFiltrosCobertura();
  await cargarJobs(false);
  await cargarCobertura(true);

  let ultimaCobertura = Date.now();
  estado.timer = window.setInterval(() => {
    if (document.hidden) return;
    const activo = document.activeElement;
    // No reemplazar la tabla mientras el usuario está enfocando una acción.
    if (activo && activo.closest && activo.closest("#jobs")) return;
    cargarJobs(true);
    if (Date.now() - ultimaCobertura >= 6000) {
      ultimaCobertura = Date.now();
      cargarCobertura(true);
    }
  }, 3000);
}

document.addEventListener("DOMContentLoaded", iniciar);
