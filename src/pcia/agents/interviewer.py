"""Agente Entrevistador.

Elicitación adaptativa de requisitos con salida JSON estricta:
``{"message_to_user": ..., "updates": {...}, "done": ...}``.
La validación y los reintentos con feedback viven en ``pcia.agents.llm_json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pcia.agents.llm_json import (  # noqa: F401  (re-export por compatibilidad)
    MAX_REINTENTOS,
    ContratoInvalidoError,
    consultar_con_contrato,
)
from pcia.domain.models import ProjectSpec
from pcia.domain.ports import ChatMessage, LLMProvider

RUTA_PROMPT = Path(__file__).parent / "prompts" / "entrevistador.md"
MENSAJE_INICIAL = "Hola, quiero crear un proyecto nuevo."


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
        respuesta, crudo = consultar_con_contrato(
            self._provider,
            self._armar_system_prompt(),
            self.historial,
            RespuestaEntrevistador,
            postproceso=lambda r: self.spec.aplicar_updates(r.updates),
        )
        self.historial.append(ChatMessage(role="assistant", content=crudo))
        return respuesta

    def _armar_system_prompt(self) -> str:
        faltantes = self.spec.campos_faltantes()
        return (
            self._plantilla.replace(
                "[[ESTADO_SPEC]]", self.spec.model_dump_json(indent=2)
            )
            .replace("[[CAMPOS_FALTANTES]]", ", ".join(faltantes) or "ninguno")
            .replace("[[CAMPOS_VALIDOS]]", ", ".join(sorted(self.spec.campos_validos())))
        )
