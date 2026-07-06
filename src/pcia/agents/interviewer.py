"""Agente Entrevistador.

Elicitación adaptativa de requisitos con salida JSON estricta:
``{"message_to_user": ..., "updates": {...}, "done": ...}``.

Toda salida malformada se reintenta con el error como feedback, con un
máximo de 3 intentos; superado el límite se escala al usuario.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pcia.domain.models import ProjectSpec
from pcia.domain.ports import ChatMessage, LLMProvider

MAX_REINTENTOS = 3
RUTA_PROMPT = Path(__file__).parent / "prompts" / "entrevistador.md"
MENSAJE_INICIAL = "Hola, quiero crear un proyecto nuevo."


class ContratoInvalidoError(Exception):
    """El LLM no cumplió el contrato JSON tras agotar los reintentos."""


class RespuestaEntrevistador(BaseModel):
    """Contrato de salida del Entrevistador (ver docs/DISENO.md §8)."""

    model_config = ConfigDict(extra="forbid")

    message_to_user: str
    updates: dict[str, Any] = Field(default_factory=dict)
    done: bool = False


class Entrevistador:
    """Conduce la entrevista y actualiza la ProjectSpec compartida."""

    def __init__(self, provider: LLMProvider, spec: ProjectSpec) -> None:
        self._provider = provider
        self.spec = spec
        self.historial: list[ChatMessage] = []
        self._plantilla = RUTA_PROMPT.read_text(encoding="utf-8")

    def iniciar(self) -> RespuestaEntrevistador:
        """Primer turno de la entrevista (saludo y primera pregunta)."""
        return self.responder(MENSAJE_INICIAL)

    def responder(self, entrada_usuario: str) -> RespuestaEntrevistador:
        """Procesa un turno: consulta al LLM, valida el contrato y aplica updates."""
        self.historial.append(ChatMessage(role="user", content=entrada_usuario))
        system_prompt = self._armar_system_prompt()
        mensajes = list(self.historial)
        ultimo_error = ""

        for _ in range(MAX_REINTENTOS):
            crudo = self._provider.generate(system_prompt, mensajes)
            try:
                respuesta = self._validar(crudo)
                self.spec.aplicar_updates(respuesta.updates)
            except ValueError as exc:
                ultimo_error = str(exc)
                # Reintento con el error como feedback, sin ensuciar el historial real.
                mensajes = [
                    *mensajes,
                    ChatMessage(role="assistant", content=crudo),
                    ChatMessage(
                        role="user",
                        content=(
                            "Tu respuesta anterior no cumple el contrato: "
                            f"{ultimo_error}. Respondé de nuevo SOLO con el objeto "
                            "JSON válido, sin texto adicional."
                        ),
                    ),
                ]
                continue

            self.historial.append(ChatMessage(role="assistant", content=crudo))
            return respuesta

        raise ContratoInvalidoError(
            f"El modelo no produjo una respuesta válida tras {MAX_REINTENTOS} "
            f"intentos. Último error: {ultimo_error}"
        )

    def _armar_system_prompt(self) -> str:
        faltantes = self.spec.campos_faltantes()
        return (
            self._plantilla.replace(
                "[[ESTADO_SPEC]]", self.spec.model_dump_json(indent=2)
            )
            .replace("[[CAMPOS_FALTANTES]]", ", ".join(faltantes) or "ninguno")
            .replace("[[CAMPOS_VALIDOS]]", ", ".join(sorted(self.spec.campos_validos())))
        )

    @staticmethod
    def _validar(crudo: str) -> RespuestaEntrevistador:
        """Parsea JSON estricto (tolerando solo fences de markdown accidentales)."""
        texto = crudo.strip()
        if texto.startswith("```"):
            texto = texto.strip("`").strip()
            if texto.startswith("json"):
                texto = texto[len("json"):].strip()
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ValueError(f"no es JSON válido ({exc})") from exc
        try:
            return RespuestaEntrevistador.model_validate(datos)
        except ValidationError as exc:
            detalles = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ValueError(f"no respeta el esquema esperado ({detalles})") from exc
