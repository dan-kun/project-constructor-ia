"""Sesiones de entrevista: puente entre el Orquestador (bloqueante, por hilo)
y la app web (HTTP request/response + Server-Sent Events).

Cada sesión corre ``Orquestador.ejecutar()`` en un hilo de fondo, sin tocar
su lógica: ``entrada`` bloquea leyendo de una cola que llena el endpoint
POST /input; ``salida`` escribe en una cola que consume el endpoint SSE
/events. El propio Orquestador es agnóstico de que su "consola" es HTTP.

Estado en memoria de proceso: alcanza para una demo/entrega de curso, no
para producción multi-instancia (ver limitaciones en el informe).
"""

from __future__ import annotations

import queue
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pcia.config import ConfigError, crear_provider
from pcia.domain.ports import LLMProviderError
from pcia.orchestrator.loop import Orquestador
from pcia.transcript import Transcript

TTL_SESION_SEGUNDOS = 3600  # limpieza de sesiones abandonadas

# Coincide con el texto exacto de loop.py::_fase_construccion. El Constructor
# solo valida que el destino esté vacío, no dónde vive (correcto para el
# CLI, donde el destino lo elige el dueño de esa máquina) — en la demo web
# un visitante cualquiera no puede decidir una ruta arbitraria en el
# filesystem del servidor, así que acá se confina siempre a un directorio
# propio de la sesión, sin importar lo que haya tipeado.
_MARCA_PROMPT_DESTINO = "¿Dónde genero el proyecto?"
_CARACTERES_NO_SEGUROS = re.compile(r"[^a-zA-Z0-9_-]+")


class SesionInvalidaError(Exception):
    """La sesión no existe o ya terminó."""


@dataclass
class Evento:
    tipo: str  # "mensaje" | "fin" | "error"
    texto: str = ""


@dataclass
class Sesion:
    id: str
    _entrada_q: "queue.Queue[str]" = field(default_factory=queue.Queue)
    _salida_q: "queue.Queue[Evento]" = field(default_factory=queue.Queue)
    hilo: threading.Thread | None = None
    ultimo_acceso: float = field(default_factory=time.monotonic)
    orquestador: Orquestador | None = None
    ruta_proyecto: Path | None = None
    transcript: Transcript = field(default_factory=Transcript)
    ruta_transcript: Path | None = None

    def entrada(self, prompt: str) -> str:
        # A diferencia de la CLI (donde ``input(prompt)`` imprime la
        # pregunta sola), acá hay que reenviarla explícitamente al chat: si
        # no, preguntas como "¿Confirmás la especificación?" o "¿Asumís el
        # riesgo?" nunca llegan al navegador y la sesión queda esperando una
        # respuesta a algo que el usuario no vio. El "> " genérico de los
        # turnos normales de entrevista se omite porque la pregunta real ya
        # viajó por un ``salida()`` propio un instante antes.
        if _MARCA_PROMPT_DESTINO in prompt:
            # No reenviamos el prompt original: incluye una ruta sugerida del
            # filesystem del servidor (irrelevante y algo ruidosa acá), y de
            # todos modos la respuesta se confina siempre en _destino_seguro.
            self.salida(
                "¿Cómo querés nombrar la carpeta del proyecto? (opcional — se "
                "genera en un directorio propio de tu sesión en el servidor, "
                "no en tu computadora; lo descargás como .zip al final)"
            )
        elif prompt.strip() and prompt.strip() != ">":
            self.salida(prompt.strip())
        texto = self._entrada_q.get()
        self.transcript.registrar_entrada(texto)
        if _MARCA_PROMPT_DESTINO in prompt:
            return self._destino_seguro(texto)
        return texto

    def _destino_seguro(self, texto: str) -> str:
        """Confina el destino de construcción a un directorio propio de la
        sesión, ignorando cualquier ruta absoluta o traversal que haya
        tipeado el visitante — ver nota de seguridad más arriba."""
        base = Path(tempfile.gettempdir()) / "pcia-web-sesiones" / self.id
        crudo = texto.strip() or (
            self.orquestador.spec.nombre if self.orquestador else ""
        ) or "proyecto"
        nombre = _CARACTERES_NO_SEGUROS.sub("-", crudo).strip("-") or "proyecto"
        return str(base / nombre)

    def salida(self, texto: str) -> None:
        self.transcript.registrar_salida(texto)
        self._salida_q.put(Evento(tipo="mensaje", texto=texto))

    def enviar_input(self, texto: str) -> None:
        self.ultimo_acceso = time.monotonic()
        self._entrada_q.put(texto)

    def proximo_evento(self, timeout: float = 25.0) -> Evento | None:
        """Bloquea hasta el próximo evento o hasta ``timeout`` (keepalive SSE)."""
        try:
            return self._salida_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def guardar_transcript(self) -> Path:
        base = Path(tempfile.gettempdir()) / "pcia-web-sesiones" / self.id
        self.ruta_transcript = self.transcript.guardar(base / "conversacion.txt")
        return self.ruta_transcript


class GestorSesiones:
    """Crea y administra sesiones de entrevista en memoria de proceso."""

    def __init__(self, memory_dir: Path) -> None:
        self._memory_dir = memory_dir
        self._sesiones: dict[str, Sesion] = {}
        self._lock = threading.Lock()

    def crear(self, config_proveedor: dict[str, Any]) -> Sesion:
        provider = crear_provider(config_proveedor)  # valida antes de abrir hilo
        nombre = config_proveedor.get("provider", "")
        modelo = (config_proveedor.get(nombre) or {}).get("model")

        sesion = Sesion(id=uuid.uuid4().hex)
        orquestador = Orquestador(
            provider,
            memory_dir=self._memory_dir,
            entrada=sesion.entrada,
            salida=sesion.salida,
            proveedor=f"{nombre}:{modelo}" if modelo else nombre or None,
        )
        sesion.orquestador = orquestador

        def _correr() -> None:
            # El transcript se guarda ANTES de notificar el evento terminal:
            # el frontend habilita el link de descarga apenas ve "fin"/"error",
            # así que el archivo tiene que existir para ese momento, no después.
            try:
                orquestador.ejecutar()
                sesion.ruta_proyecto = orquestador.ruta_proyecto
            except Exception as exc:  # noqa: BLE001 — se reporta al usuario, no se oculta
                sesion.transcript.registrar_error(str(exc))
                sesion.guardar_transcript()
                sesion._salida_q.put(Evento(tipo="error", texto=str(exc)))
                return
            sesion.guardar_transcript()
            sesion._salida_q.put(Evento(tipo="fin"))

        sesion.hilo = threading.Thread(target=_correr, daemon=True)
        with self._lock:
            self._limpiar_expiradas()
            self._sesiones[sesion.id] = sesion
        sesion.hilo.start()
        return sesion

    def obtener(self, sesion_id: str) -> Sesion:
        with self._lock:
            sesion = self._sesiones.get(sesion_id)
        if sesion is None:
            raise SesionInvalidaError(f"Sesión inexistente o expirada: {sesion_id}")
        sesion.ultimo_acceso = time.monotonic()
        return sesion

    def _limpiar_expiradas(self) -> None:
        ahora = time.monotonic()
        vencidas = [
            sid
            for sid, s in self._sesiones.items()
            if ahora - s.ultimo_acceso > TTL_SESION_SEGUNDOS
        ]
        for sid in vencidas:
            del self._sesiones[sid]


def validar_config_proveedor(datos: dict[str, Any]) -> dict[str, Any]:
    """Arma el dict tipo config.yaml a partir del formulario web y lo valida.

    No confía en el cliente más de lo necesario: solo arma la forma que
    ``crear_provider`` ya sabe validar (mismo contrato que config.yaml).
    """
    proveedor = datos.get("provider")
    if proveedor not in ("anthropic_api", "openai_compat"):
        raise ConfigError(
            "Elegí un proveedor para la demo web: 'anthropic_api' u 'openai_compat' "
            "(claude_subscription requiere la CLI de Claude Code instalada en el "
            "servidor y no está disponible en este entorno)."
        )
    seccion: dict[str, Any] = {"model": (datos.get("model") or "").strip()}
    if proveedor == "anthropic_api":
        seccion["api_key"] = (datos.get("api_key") or "").strip() or None
        if not seccion["model"]:
            seccion["model"] = "claude-sonnet-4-6"
    else:
        seccion["base_url"] = (datos.get("base_url") or "").strip()
        seccion["api_key"] = (datos.get("api_key") or "sin-key").strip()
        if not seccion["base_url"] or not seccion["model"]:
            raise ConfigError(
                "openai_compat requiere 'base_url' y 'model' (ej.: Ollama local, "
                "Groq, OpenRouter)."
            )
    return {"provider": proveedor, proveedor: seccion}


__all__ = [
    "Evento",
    "GestorSesiones",
    "Sesion",
    "SesionInvalidaError",
    "validar_config_proveedor",
    "ConfigError",
    "LLMProviderError",
]
