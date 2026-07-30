"use strict";

const elSetup = document.getElementById("setup");
const elChat = document.getElementById("chat");
const elMensajes = document.getElementById("mensajes");
const elEstado = document.getElementById("estado");
const elFormInput = document.getElementById("form-input");
const elInputTexto = document.getElementById("input-texto");
const elDescargar = document.getElementById("descargar");
const elErrorSetup = document.getElementById("error-setup");

let sesionId = null;

document.querySelectorAll('input[name="provider"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const esAnthropic = radio.value === "anthropic_api" && radio.checked;
    if (!radio.checked) return;
    document.getElementById("campos-anthropic").classList.toggle("oculto", !esAnthropic);
    document.getElementById("campos-openai").classList.toggle("oculto", esAnthropic);
  });
});

function agregarMensaje(texto) {
  const burbuja = document.createElement("div");
  burbuja.className = "burbuja";
  burbuja.textContent = texto;
  elMensajes.appendChild(burbuja);
  elMensajes.scrollTop = elMensajes.scrollHeight;
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
  const provider = document.querySelector('input[name="provider"]:checked').value;
  const payload = { provider };
  if (provider === "anthropic_api") {
    payload.api_key = document.getElementById("anthropic-key").value;
    payload.model = document.getElementById("anthropic-model").value;
  } else {
    payload.base_url = document.getElementById("openai-url").value;
    payload.model = document.getElementById("openai-model").value;
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
  elChat.classList.remove("oculto");
  setEstado("Conectado — esperando al Entrevistador…", true);
  conectarEventos();
}

function conectarEventos() {
  const fuente = new EventSource(`/api/sessions/${sesionId}/events`);
  fuente.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.tipo === "mensaje") {
      agregarMensaje(data.texto);
      setEstado("Tu turno", true);
      habilitarInput(true);
    } else if (data.tipo === "fin") {
      setEstado("Entrevista y construcción finalizadas", true);
      habilitarInput(false);
      elDescargar.href = `/api/sessions/${sesionId}/download`;
      elDescargar.classList.remove("oculto");
      fuente.close();
    } else if (data.tipo === "error") {
      agregarMensaje(`⚠️ Error: ${data.texto}`);
      setEstado("La sesión terminó con un error", false);
      habilitarInput(false);
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
  agregarMensaje(`Vos: ${texto || "(enter)"}`);
  elInputTexto.value = "";
  habilitarInput(false);
  setEstado("Procesando…", true);
  await fetch(`/api/sessions/${sesionId}/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ texto }),
  });
});

document.getElementById("iniciar").addEventListener("click", iniciarSesion);
