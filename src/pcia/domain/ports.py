"""Puertos del dominio (arquitectura hexagonal).

TODA interacción con modelos de IA pasa por ``LLMProvider``. Los agentes y
el orquestador dependen solo de este puerto; los adaptadores concretos viven
en ``pcia.adapters``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, Sequence

from pydantic import BaseModel


class ChatMessage(BaseModel):
    """Un mensaje del historial de conversación con el LLM."""

    role: Literal["user", "assistant"]
    content: str


class LLMProviderError(Exception):
    """Falla al comunicarse con el proveedor de IA (red, auth, formato)."""


class LLMProvider(ABC):
    """Puerto agnóstico del proveedor de IA."""

    @abstractmethod
    def generate(self, system_prompt: str, messages: Sequence[ChatMessage]) -> str:
        """Genera la respuesta del asistente para el historial dado.

        Debe levantar ``LLMProviderError`` ante cualquier falla del proveedor.
        """
