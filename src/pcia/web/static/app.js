"use strict";

const elSetup = document.getElementById("setup");
const elChat = document.getElementById("chat");
const elMensajes = document.getElementById("mensajes");
const elEstado = document.getElementById("estado");
const elFormInput = document.getElementById("form-input");
const elInputTexto = document.getElementById("input-texto");
const elDescargar = document.getElementById("descargar");
const elDescargarTranscript = document.getElementById("descargar-transcript");
const elErrorSetup = document.getElementById("error-setup");
const elCorregirSesion = document.getElementById("corregir-sesion");

let sesionId = null;
let fuenteEventos = null;
// Último estado conocido del ciclo (foto de cada evento SSE) y config del
// proveedor de la sesión activa: lo que permite editar una respuesta ya
// enviada sin perder el resto de la conversación (ver
// ``historialTurnos``/``iniciarEdicion`` más abajo y docs/UX.md U3/U4/U5).
let ultimoEstado = null;
let payloadProveedorActual = null;

// Preferencias del formulario: viven solo en el navegador de quien las
// carga (localStorage), nunca en el servidor — mismo nivel de confianza que
// un archivo de config local, pero por sesión de navegador en vez de por
// archivo. Así no hay que retipear la key en cada recarga de esta máquina,
// sin reintroducir un secreto compartido del lado del servidor.
const CLAVE_STORAGE = "pcia-preferencias-ia";

function guardarPreferencias() {
  const datos = {
    provider: document.querySelector('input[name="provider"]:checked').value,
    anthropicKey: document.getElementById("anthropic-key").value,
    anthropicModel: document.getElementById("anthropic-model").value,
    anthropicModelCustom: document.getElementById("anthropic-model-custom").value,
    openaiUrl: document.getElementById("openai-url").value,
    openaiModel: document.getElementById("openai-model").value,
    openaiModelCustom: document.getElementById("openai-model-custom").value,
    openaiKey: document.getElementById("openai-key").value,
  };
  localStorage.setItem(CLAVE_STORAGE, JSON.stringify(datos));
}

function restaurarSelect(select, custom, valorGuardado) {
  if (!valorGuardado) return;
  const opciones = Array.from(select.options).map((o) => o.value);
  if (opciones.includes(valorGuardado)) {
    select.value = valorGuardado;
  } else {
    select.value = "otro";
    custom.value = valorGuardado;
    custom.classList.remove("oculto");
  }
}

function cargarPreferencias() {
  const crudo = localStorage.getItem(CLAVE_STORAGE);
  if (!crudo) return;
  let datos;
  try {
    datos = JSON.parse(crudo);
  } catch {
    return;
  }

  if (datos.provider) {
    const radio = document.querySelector(`input[name="provider"][value="${datos.provider}"]`);
    if (radio) {
      radio.checked = true;
      radio.dispatchEvent(new Event("change"));
    }
  }
  document.getElementById("anthropic-key").value = datos.anthropicKey || "";
  document.getElementById("openai-url").value = datos.openaiUrl || document.getElementById("openai-url").value;
  document.getElementById("openai-key").value = datos.openaiKey || "";
  restaurarSelect(
    document.getElementById("anthropic-model"),
    document.getElementById("anthropic-model-custom"),
    datos.anthropicModel === "otro" ? datos.anthropicModelCustom : datos.anthropicModel
  );
  restaurarSelect(
    document.getElementById("openai-model"),
    document.getElementById("openai-model-custom"),
    datos.openaiModel === "otro" ? datos.openaiModelCustom : datos.openaiModel
  );
}

document.querySelectorAll('input[name="provider"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const esAnthropic = radio.value === "anthropic_api" && radio.checked;
    if (!radio.checked) return;
    const proveedor = document.querySelector('input[name="provider"]:checked').value;
    const esSuscripcion = proveedor === "claude_subscription";
    document.getElementById("campos-anthropic").classList.toggle("oculto", !esAnthropic || esSuscripcion);
    document.getElementById("campos-openai").classList.toggle("oculto", esAnthropic || esSuscripcion);
  });
});

function configurarSelectorModelo(idSelect, idCustom) {
  const select = document.getElementById(idSelect);
  const custom = document.getElementById(idCustom);
  select.addEventListener("change", () => {
    custom.classList.toggle("oculto", select.value !== "otro");
    if (select.value === "otro") custom.focus();
  });
}

function valorModelo(idSelect, idCustom) {
  const select = document.getElementById(idSelect);
  if (select.value === "otro") return document.getElementById(idCustom).value.trim();
  return select.value;
}

configurarSelectorModelo("anthropic-model", "anthropic-model-custom");
configurarSelectorModelo("openai-model", "openai-model-custom");

document.getElementById("usar-openrouter").addEventListener("click", () => {
  document.getElementById("openai-url").value = "https://openrouter.ai/api/v1";
  document.getElementById("openai-model").value = "nvidia/nemotron-3-ultra-550b-a55b:free";
  document.getElementById("openai-key").focus();
});

document.getElementById("usar-ollama").addEventListener("click", () => {
  document.getElementById("openai-url").value = "http://localhost:11434/v1";
  document.getElementById("openai-key").value = "";
  document.getElementById("cargar-modelos").click();
});

function poblarSelectorModelos(nombres) {
  const select = document.getElementById("openai-model");
  const seleccionPrevia = select.value;
  select.innerHTML = "";
  for (const nombre of nombres) {
    const opcion = document.createElement("option");
    opcion.value = nombre;
    opcion.textContent = nombre;
    select.appendChild(opcion);
  }
  const otro = document.createElement("option");
  otro.value = "otro";
  otro.textContent = "Otro (especificar)…";
  select.appendChild(otro);
  select.value = nombres.includes(seleccionPrevia) ? seleccionPrevia : nombres[0];
  document.getElementById("openai-model-custom").classList.add("oculto");
}

// Los servidores "compatibles con OpenAI" no siempre lo son del todo en
// /v1/models: OpenAI devuelve {data:[{id}]} y Ollama (y algunos llama.cpp)
// {models:[{name}]}. Se aceptan ambas formas.
function nombresDeModelos(datos) {
  const nombres = new Set();
  for (const m of datos?.data || []) if (typeof m?.id === "string") nombres.add(m.id);
  for (const m of datos?.models || []) {
    if (typeof m?.name === "string") nombres.add(m.name);
    else if (typeof m?.model === "string") nombres.add(m.model);
  }
  return [...nombres].sort();
}

async function descubrirModelosDesdeElNavegador(baseUrl, apiKey) {
  const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {};
  const resp = await fetch(`${baseUrl}/models`, { headers });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  const nombres = nombresDeModelos(await resp.json());
  if (nombres.length === 0) throw new Error("la respuesta no trae modelos");
  return nombres;
}

async function descubrirModelosViaServidor(baseUrl, apiKey) {
  const resp = await fetch("/api/discover-models", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
  });
  if (!resp.ok) {
    const detalle = await resp.json().catch(() => ({}));
    throw new Error(detalle.detail || `${resp.status} ${resp.statusText}`);
  }
  const datos = await resp.json();
  return datos.modelos;
}

document.getElementById("cargar-modelos").addEventListener("click", async () => {
  const boton = document.getElementById("cargar-modelos");
  const elEstadoModelos = document.getElementById("estado-modelos");
  const baseUrl = document.getElementById("openai-url").value.trim().replace(/\/+$/, "");
  const apiKey = document.getElementById("openai-key").value.trim();

  elEstadoModelos.classList.remove("oculto");
  elEstadoModelos.textContent = "Consultando modelos disponibles…";
  boton.disabled = true;
  try {
    let nombres;
    let via = "tu navegador";
    try {
      nombres = await descubrirModelosDesdeElNavegador(baseUrl, apiKey);
    } catch {
      // Típicamente CORS (ej.: Ollama Cloud no lo habilita): el servidor sí
      // puede alcanzar la API sin ese problema, porque CORS es una
      // restricción del navegador, no del backend.
      via = "el servidor (el navegador no pudo por CORS)";
      nombres = await descubrirModelosViaServidor(baseUrl, apiKey);
    }
    poblarSelectorModelos(nombres);
    elEstadoModelos.textContent = `${nombres.length} modelo(s) encontrados en ${baseUrl} (vía ${via}).`;
  } catch (err) {
    elEstadoModelos.textContent =
      `No se pudo consultar ${baseUrl}/models (${err.message}). ` +
      "Podés seguir eligiendo de la lista fija o escribir el modelo a mano.";
  } finally {
    boton.disabled = false;
  }
});

function agregarMensaje(texto, esUsuario = false) {
  const burbuja = document.createElement("div");
  burbuja.className = `burbuja ${esUsuario ? "burbuja-usuario" : "burbuja-sistema"}`;
  burbuja.textContent = texto;
  elMensajes.appendChild(burbuja);
  elMensajes.scrollTop = elMensajes.scrollHeight;
  return burbuja;
}

// Burbuja de una respuesta del usuario que todavía se puede corregir (ver
// ``historialTurnos`` e ``iniciarEdicion`` más abajo): igual que
// ``agregarMensaje``, pero con un botón de lápiz que abre la edición inline.
function agregarBurbujaEditable(texto, indiceTurno) {
  const burbuja = document.createElement("div");
  burbuja.className = "burbuja burbuja-usuario burbuja-editable";
  const span = document.createElement("span");
  span.className = "burbuja-texto";
  span.textContent = texto;
  const boton = document.createElement("button");
  boton.type = "button";
  boton.className = "btn-editar";
  boton.title = "Editar esta respuesta";
  boton.setAttribute("aria-label", "Editar esta respuesta");
  boton.textContent = "✏️";
  boton.addEventListener("click", () => iniciarEdicion(indiceTurno));
  burbuja.append(span, boton);
  elMensajes.appendChild(burbuja);
  elMensajes.scrollTop = elMensajes.scrollHeight;
  return burbuja;
}

function autogrow() {
  elInputTexto.style.height = "auto";
  elInputTexto.style.height = `${elInputTexto.scrollHeight}px`;
}

function setEstado(texto, vivo) {
  elEstado.textContent = texto;
  elEstado.dataset.vivo = vivo ? "1" : "0";
}

function habilitarInput(habilitado) {
  elInputTexto.disabled = !habilitado;
  elFormInput.querySelector("button").disabled = !habilitado;
  if (habilitado) elInputTexto.focus();
}

// --- indicador de actividad -------------------------------------------------------
// Sin esto no había ninguna señal de que el agente estaba trabajando: el
// campo de entrada se deshabilitaba y ahí quedaba, indistinguible de una
// sesión colgada — sobre todo con un modelo local (10-40s por turno) o
// mientras corre un build de Docker en la verificación profunda.

const TEXTO_ESPERA_LARGA = "Seguimos esperando al agente (puede tardar más con un "
  + "modelo local, o si está corriendo un build de Docker)…";
const UMBRAL_ESPERA_LARGA_MS = 15000;
let temporizadorEsperaLarga = null;

function mostrarIndicadorProcesando() {
  ocultarIndicadorProcesando();
  const burbuja = document.createElement("div");
  burbuja.className = "burbuja burbuja-sistema burbuja-procesando";
  burbuja.id = "indicador-procesando";
  burbuja.innerHTML = "<span></span><span></span><span></span>";
  burbuja.setAttribute("aria-label", "El agente está procesando");
  elMensajes.appendChild(burbuja);
  elMensajes.scrollTop = elMensajes.scrollHeight;

  clearTimeout(temporizadorEsperaLarga);
  temporizadorEsperaLarga = setTimeout(() => {
    setEstado(TEXTO_ESPERA_LARGA, true);
  }, UMBRAL_ESPERA_LARGA_MS);
}

function ocultarIndicadorProcesando() {
  clearTimeout(temporizadorEsperaLarga);
  const existente = document.getElementById("indicador-procesando");
  if (existente) existente.remove();
}

const elDocumentos = document.getElementById("documentos-cliente");
const elListaDocumentos = document.getElementById("lista-documentos");

elDocumentos.addEventListener("change", () => {
  const nombres = Array.from(elDocumentos.files).map((archivo) => archivo.name);
  elListaDocumentos.textContent = nombres.length ? `Seleccionados: ${nombres.join(", ")}` : "";
});

function leerDocumento(archivo) {
  return new Promise((resolve, reject) => {
    const lector = new FileReader();
    lector.onload = () => resolve({ nombre: archivo.name, contenido: lector.result });
    lector.onerror = () => reject(new Error(`no se pudo leer ${archivo.name}`));
    lector.readAsText(archivo);
  });
}

async function leerDocumentosSeleccionados() {
  return Promise.all(Array.from(elDocumentos.files).map(leerDocumento));
}

async function crearSesion(payload, mostrarError) {
  const resp = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const detalle = await resp.json().catch(() => ({}));
    mostrarError(detalle.detail || "No se pudo iniciar la sesión.");
    return null;
  }
  return resp.json();
}

async function iniciarSesion() {
  elErrorSetup.classList.add("oculto");
  guardarPreferencias();
  const provider = document.querySelector('input[name="provider"]:checked').value;
  const payload = { provider };
  if (provider === "claude_subscription") {
    // La CLI ya está autenticada y elige su modelo por defecto: mandar el
    // del selector de otro proveedor la hace fallar con unrecognized_model.
    payload.model = "";
  } else if (provider === "anthropic_api") {
    payload.api_key = document.getElementById("anthropic-key").value;
    payload.model = valorModelo("anthropic-model", "anthropic-model-custom");
  } else {
    payload.base_url = document.getElementById("openai-url").value;
    payload.model = valorModelo("openai-model", "openai-model-custom");
    payload.api_key = document.getElementById("openai-key").value;
  }
  try {
    payload.documentos = await leerDocumentosSeleccionados();
  } catch (err) {
    elErrorSetup.textContent = err.message;
    elErrorSetup.classList.remove("oculto");
    return;
  }
  payloadProveedorActual = payload;

  const datos = await crearSesion(payload, (msg) => {
    elErrorSetup.textContent = msg;
    elErrorSetup.classList.remove("oculto");
  });
  if (!datos) return;
  sesionId = datos.session_id;
  historialTurnos = [];
  edicionPendiente = null;

  elSetup.classList.add("oculto");
  document.getElementById("area-sesion").classList.remove("oculto");
  setEstado("Conectado — esperando al Entrevistador…", true);
  mostrarIndicadorProcesando();
  conectarEventos();
}

const elOpcionesDecision = document.getElementById("opciones-decision");

// Decisiones de opción fija del orquestador (confirmar la spec, asumir un
// riesgo, etc.) llegan con `opciones` en el evento: se ofrecen como botones
// además del campo de texto libre, que sigue disponible siempre (ver
// docs/UX.md U6 — antes había que tipear 's'/'N'/'abortar' a mano).
function mostrarOpciones(opciones) {
  elOpcionesDecision.innerHTML = "";
  if (!opciones || !opciones.length) {
    elOpcionesDecision.classList.add("oculto");
    return;
  }
  for (const opcion of opciones) {
    const boton = document.createElement("button");
    boton.type = "button";
    boton.className = "boton-opcion";
    boton.textContent = opcion.texto;
    boton.addEventListener("click", () => enviarRespuesta(opcion.valor, opcion.texto));
    elOpcionesDecision.appendChild(boton);
  }
  elOpcionesDecision.classList.remove("oculto");
}

// Edición inline de una respuesta ya enviada (ver docs/UX.md U3/U4/U5): la
// versión anterior de "corregir algo" abría una sesión nueva con TODA la
// spec actual precargada — incluida la última respuesta, que es
// exactamente la que el usuario quería poder cambiar, así que quedaba fija
// y solo se podía "pedir un ajuste" en texto libre a ciegas, y de paso se
// vaciaba toda la conversación visible. Ahora cada respuesta de la
// entrevista guarda la foto de la spec previa a ella (``historialTurnos``);
// editarla trunca el chat desde ese punto (nunca lo vacía entero), reabre
// una sesión con esa foto como punto de partida — el campo que se está
// corrigiendo vuelve a quedar "faltante" — y reenvía el texto corregido en
// cuanto el Entrevistador repite la pregunta, así que desde afuera se ve
// como editar un mensaje de chat común, no como empezar de nuevo.
let historialTurnos = [];
let edicionPendiente = null;

function esTurnoDeEntrevista() {
  return !!(ultimoEstado && ultimoEstado.fase === "entrevista");
}

function iniciarEdicion(indice) {
  const turno = historialTurnos[indice];
  if (!turno) return;
  document.querySelectorAll(".edicion-burbuja").forEach((el) => el.remove());

  const area = document.createElement("div");
  area.className = "edicion-burbuja";
  const aviso = document.createElement("p");
  aviso.textContent = "Se va a rehacer la conversación desde esta respuesta.";
  const textarea = document.createElement("textarea");
  textarea.value = turno.textoOriginal;
  textarea.rows = Math.min(6, Math.max(1, turno.textoOriginal.split("\n").length));
  const botones = document.createElement("div");
  botones.className = "edicion-botones";
  const btnCancelar = document.createElement("button");
  btnCancelar.type = "button";
  btnCancelar.className = "boton-secundario";
  btnCancelar.textContent = "Cancelar";
  btnCancelar.addEventListener("click", () => area.remove());
  const btnGuardar = document.createElement("button");
  btnGuardar.type = "button";
  btnGuardar.textContent = "Guardar y continuar";
  btnGuardar.addEventListener("click", () => confirmarEdicion(indice, textarea.value));
  textarea.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      confirmarEdicion(indice, textarea.value);
    }
  });
  botones.append(btnCancelar, btnGuardar);
  area.append(aviso, textarea, botones);
  turno.burbuja.after(area);
  textarea.focus();
}

async function confirmarEdicion(indice, nuevoTexto) {
  const texto = nuevoTexto.trim();
  if (!texto) return;
  const turno = historialTurnos[indice];
  if (!turno || !payloadProveedorActual) return;

  if (fuenteEventos) fuenteEventos.close();

  // Trunca la conversación visible desde esta respuesta en adelante (nunca
  // la vacía entera): lo anterior sigue siendo válido, no cambió.
  let nodo = turno.burbuja;
  while (nodo) {
    const siguiente = nodo.nextSibling;
    nodo.remove();
    nodo = siguiente;
  }
  historialTurnos.length = indice;

  mostrarOpciones(null);
  ocultarIndicadorProcesando();
  elDescargar.classList.add("oculto");
  elDescargarTranscript.classList.add("oculto");
  setEstado("Retomando la conversación con tu corrección…", true);
  habilitarInput(false);

  const specInicial = Object.fromEntries(
    Object.entries(turno.specAntes || {}).filter(
      ([, valor]) => valor && (!Array.isArray(valor) || valor.length)
    )
  );
  const payload = { ...payloadProveedorActual, documentos: [], spec_inicial: specInicial };

  const datos = await crearSesion(payload, (msg) => {
    agregarMensaje(`⚠️ No se pudo retomar la edición: ${msg}`, false);
  });
  if (!datos) {
    setEstado("No se pudo retomar la edición", false);
    return;
  }
  sesionId = datos.session_id;
  ultimoEstado = null;
  edicionPendiente = texto;
  mostrarIndicadorProcesando();
  conectarEventos();
}

// Atajo: corrige directamente la última respuesta de la entrevista, que es
// el caso más común (justo acabás de contestar algo y te diste cuenta de
// que está mal).
elCorregirSesion.addEventListener("click", () => {
  if (!historialTurnos.length) return;
  const indice = historialTurnos.length - 1;
  iniciarEdicion(indice);
  historialTurnos[indice].burbuja.scrollIntoView({ block: "center", behavior: "smooth" });
});

async function enviarRespuesta(texto, etiquetaMostrada, opts = {}) {
  mostrarOpciones(null);
  const mostrado = etiquetaMostrada || texto || "(enter)";
  if (opts.editable) {
    const indice = historialTurnos.length;
    const burbuja = agregarBurbujaEditable(mostrado, indice);
    historialTurnos.push({
      burbuja,
      textoOriginal: texto,
      specAntes: ultimoEstado ? JSON.parse(JSON.stringify(ultimoEstado.spec)) : {},
    });
    elCorregirSesion.classList.remove("oculto");
  } else {
    agregarMensaje(mostrado, true);
  }
  elInputTexto.value = "";
  autogrow();
  habilitarInput(false);
  setEstado("Procesando…", true);
  mostrarIndicadorProcesando();
  await fetch(`/api/sessions/${sesionId}/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ texto }),
  });
}

function conectarEventos() {
  fuenteEventos = new EventSource(`/api/sessions/${sesionId}/events`);
  const fuente = fuenteEventos;
  fuente.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.estado) {
      pintarEstado(data.estado);
      ultimoEstado = data.estado;
    }
    if (data.tipo === "mensaje") {
      if (data.texto) agregarMensaje(data.texto);
      if (data.espera_respuesta) {
        // Recién acá el orquestador se detuvo a esperar una respuesta real
        // (ver web/sessions.py::espera_respuesta). La mayoría de los
        // mensajes son progreso informativo — el ciclo sigue solo, sin
        // esperar nada — así que habilitar el input en cualquier mensaje
        // (como antes) mentía sobre si había algo que hacer.
        ocultarIndicadorProcesando();
        if (edicionPendiente !== null) {
          // La sesión nueva volvió a preguntar lo que se acaba de "vaciar"
          // al editar: reenviamos el texto corregido en el acto, en vez de
          // esperar que el usuario lo retipee — así la edición se siente
          // como reemplazar un mensaje, no como retomar la entrevista.
          const texto = edicionPendiente;
          edicionPendiente = null;
          enviarRespuesta(texto, texto, { editable: esTurnoDeEntrevista() });
          return;
        }
        setEstado("Tu turno", true);
        habilitarInput(true);
        mostrarOpciones(data.opciones);
      } else {
        setEstado("El agente está trabajando…", true);
        habilitarInput(false);
        mostrarOpciones(null);
        mostrarIndicadorProcesando();
      }
    } else if (data.tipo === "fin") {
      ocultarIndicadorProcesando();
      setEstado("Entrevista y construcción finalizadas", true);
      habilitarInput(false);
      mostrarOpciones(null);
      elDescargar.href = `/api/sessions/${sesionId}/download`;
      elDescargar.classList.remove("oculto");
      elDescargarTranscript.href = `/api/sessions/${sesionId}/transcript`;
      elDescargarTranscript.classList.remove("oculto");
      fuente.close();
    } else if (data.tipo === "error") {
      ocultarIndicadorProcesando();
      agregarMensaje(`⚠️ Error: ${data.texto}`, false);
      setEstado("La sesión terminó con un error", false);
      habilitarInput(false);
      mostrarOpciones(null);
      // El proyecto puede no existir (la construcción puede no haberse
      // alcanzado), pero la conversación siempre queda disponible: es
      // justamente en este caso donde más sirve tener el registro completo.
      elDescargarTranscript.href = `/api/sessions/${sesionId}/transcript`;
      elDescargarTranscript.classList.remove("oculto");
      fuente.close();
    }
  };
  fuente.onerror = () => {
    ocultarIndicadorProcesando();
    setEstado("Conexión interrumpida — recargá la página para reintentar", false);
  };
}

elFormInput.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const texto = elInputTexto.value;
  await enviarRespuesta(texto, texto || "(enter)", { editable: esTurnoDeEntrevista() });
});

elInputTexto.addEventListener("input", autogrow);

elInputTexto.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    if (!elInputTexto.disabled) elFormInput.requestSubmit();
  }
});

// La suscripción de Claude depende de la CLI instalada en el servidor: se
// ofrece solo si esta instancia la permite (ver /api/proveedores).
fetch("/api/proveedores")
  .then((r) => r.json())
  .then((p) => {
    if (p.claude_subscription) {
      document.getElementById("opcion-suscripcion").classList.remove("oculto");
    }
  })
  .catch(() => {});

document.getElementById("iniciar").addEventListener("click", iniciarSesion);

document.getElementById("olvidar-preferencias").addEventListener("click", (ev) => {
  ev.preventDefault();
  localStorage.removeItem(CLAVE_STORAGE);
  location.reload();
});

cargarPreferencias();

// --- Panel de estado del ciclo ---------------------------------------------------
// Se alimenta de la foto que viaja en cada evento SSE (ver sessions.py::estado):
// el navegador no interpreta los mensajes de texto, solo renderiza datos.

const FASES = ["analisis", "entrevista", "auditoria", "construccion", "verificacion", "entrega", "aprendizaje"];
const ETIQUETAS_FASE = {
  analisis: "Análisis", entrevista: "Entrevista", auditoria: "Auditoría",
  construccion: "Construcción", verificacion: "Verificación", entrega: "Entrega",
  aprendizaje: "Aprendizaje", fin: "Fin",
};
const CAMPOS_SPEC = ["nombre", "descripcion", "tipo_proyecto", "lenguaje", "framework",
  "arquitectura", "base_datos", "autenticacion", "gestion_secretos", "infraestructura",
  "ci_cd", "alcance"];
const EMOJI_SEMAFORO = { verde: "🟢", amarillo: "🟡", rojo: "🔴" };
const EMOJI_CHEQUEO = { ok: "✅", error: "❌", omitido: "⏭️" };

function escaparHtml(valor) {
  const div = document.createElement("div");
  div.textContent = String(valor);
  return div.innerHTML;
}

function pintarEstado(estado) {
  const indiceActual = FASES.indexOf(estado.fase);
  document.getElementById("fases").innerHTML = FASES.map((fase, i) => {
    const terminada = estado.fase === "fin" || (indiceActual >= 0 && i < indiceActual);
    const clase = fase === estado.fase ? "activa" : terminada ? "hecha" : "";
    return `<span class="fase ${clase}">${ETIQUETAS_FASE[fase]}</span>`;
  }).join("");

  const spec = estado.spec || {};
  let htmlSpec = CAMPOS_SPEC.map((campo) => {
    const valor = spec[campo];
    return `<div class="campo-estado"><span class="k">${campo}</span>` +
      `<span class="v ${valor ? "" : "vacio"}">${valor ? escaparHtml(valor) : "—"}</span></div>`;
  }).join("");
  if (estado.riesgos_asumidos && estado.riesgos_asumidos.length) {
    htmlSpec += `<div class="campo-estado"><span class="k">riesgos asumidos</span>` +
      `<span class="v">${estado.riesgos_asumidos.length}</span></div>`;
  }
  document.getElementById("panel-spec").innerHTML = htmlSpec;

  if (estado.semaforo) {
    let html = `<div class="semaforo">${EMOJI_SEMAFORO[estado.semaforo] || ""} ${estado.semaforo}</div>`;
    html += estado.hallazgos.length
      ? estado.hallazgos.map((h) =>
          `<div class="hallazgo ${h.severidad}">` +
          `<span class="id">[${escaparHtml(h.id)}] ${escaparHtml(h.origen)}</span><br>${escaparHtml(h.mensaje)}` +
          (h.correccion_propuesta ? `<div class="fix">↳ ${escaparHtml(h.correccion_propuesta)}</div>` : "") +
          `</div>`).join("")
      : `<span class="vacio">Sin hallazgos: la especificación es coherente.</span>`;
    document.getElementById("panel-auditoria").innerHTML = html;
  }

  if (estado.stack) {
    document.getElementById("panel-construccion").innerHTML =
      `<div class="campo-estado"><span class="k">plantilla</span><span class="v">${escaparHtml(estado.stack)}</span></div>` +
      `<div class="campo-estado"><span class="k">archivos</span><span class="v">${estado.archivos.length}</span></div>` +
      `<div class="archivos">${estado.archivos.map((a) => `<div>${escaparHtml(a)}</div>`).join("")}</div>`;
  }

  const verif = estado.verificacion;
  if (verif) {
    let html = `<div class="fila-badge"><span class="badge ${verif.estado}">${verif.estado.replace(/_/g, " ")}</span></div>`;
    html += verif.profundos.map((c) =>
      `<div class="chequeo"><span>${EMOJI_CHEQUEO[c.estado] || ""}</span>` +
      `<span class="id">${escaparHtml(c.id)}</span>` +
      `<span class="opcional">${c.estado === "omitido" && !c.obligatorio ? "opcional" : ""}</span></div>`).join("");
    if (verif.errores_sintaxis.length) {
      html += `<div class="errores">Errores: ${verif.errores_sintaxis.map(escaparHtml).join(", ")}</div>`;
    }
    document.getElementById("panel-verificacion").innerHTML = html;
  }
}
