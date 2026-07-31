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

let sesionId = null;

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
    document.getElementById("campos-anthropic").classList.toggle("oculto", !esAnthropic);
    document.getElementById("campos-openai").classList.toggle("oculto", esAnthropic);
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

async function descubrirModelosDesdeElNavegador(baseUrl, apiKey) {
  const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {};
  const resp = await fetch(`${baseUrl}/models`, { headers });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  const datos = await resp.json();
  const nombres = (datos.data || []).map((m) => m.id).sort();
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

async function iniciarSesion() {
  elErrorSetup.classList.add("oculto");
  guardarPreferencias();
  const provider = document.querySelector('input[name="provider"]:checked').value;
  const payload = { provider };
  if (provider === "anthropic_api") {
    payload.api_key = document.getElementById("anthropic-key").value;
    payload.model = valorModelo("anthropic-model", "anthropic-model-custom");
  } else {
    payload.base_url = document.getElementById("openai-url").value;
    payload.model = valorModelo("openai-model", "openai-model-custom");
    payload.api_key = document.getElementById("openai-key").value;
  }

  const resp = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const detalle = await resp.json().catch(() => ({}));
    elErrorSetup.textContent = detalle.detail || "No se pudo iniciar la sesión.";
    elErrorSetup.classList.remove("oculto");
    return;
  }
  const datos = await resp.json();
  sesionId = datos.session_id;

  elSetup.classList.add("oculto");
  document.getElementById("area-sesion").classList.remove("oculto");
  setEstado("Conectado — esperando al Entrevistador…", true);
  conectarEventos();
}

function conectarEventos() {
  const fuente = new EventSource(`/api/sessions/${sesionId}/events`);
  fuente.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.estado) pintarEstado(data.estado);
    if (data.tipo === "mensaje") {
      agregarMensaje(data.texto);
      setEstado("Tu turno", true);
      habilitarInput(true);
    } else if (data.tipo === "fin") {
      setEstado("Entrevista y construcción finalizadas", true);
      habilitarInput(false);
      elDescargar.href = `/api/sessions/${sesionId}/download`;
      elDescargar.classList.remove("oculto");
      elDescargarTranscript.href = `/api/sessions/${sesionId}/transcript`;
      elDescargarTranscript.classList.remove("oculto");
      fuente.close();
    } else if (data.tipo === "error") {
      agregarMensaje(`⚠️ Error: ${data.texto}`, false);
      setEstado("La sesión terminó con un error", false);
      habilitarInput(false);
      // El proyecto puede no existir (la construcción puede no haberse
      // alcanzado), pero la conversación siempre queda disponible: es
      // justamente en este caso donde más sirve tener el registro completo.
      elDescargarTranscript.href = `/api/sessions/${sesionId}/transcript`;
      elDescargarTranscript.classList.remove("oculto");
      fuente.close();
    }
  };
  fuente.onerror = () => {
    setEstado("Conexión interrumpida — recargá la página para reintentar", false);
  };
}

elFormInput.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const texto = elInputTexto.value;
  agregarMensaje(texto || "(enter)", true);
  elInputTexto.value = "";
  autogrow();
  habilitarInput(false);
  setEstado("Procesando…", true);
  await fetch(`/api/sessions/${sesionId}/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ texto }),
  });
});

elInputTexto.addEventListener("input", autogrow);

elInputTexto.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    if (!elInputTexto.disabled) elFormInput.requestSubmit();
  }
});

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
