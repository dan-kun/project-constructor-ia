"""Fixtures compartidas: los agentes se testean con FakeProvider, nunca con red."""

from __future__ import annotations

from typing import Sequence

from pcia.domain.ports import ChatMessage, LLMProvider


class FakeProvider(LLMProvider):
    """Adaptador falso: devuelve respuestas enlatadas en orden y registra llamadas."""

    def __init__(self, respuestas: Sequence[str]) -> None:
        self.respuestas = list(respuestas)
        self.llamadas: list[tuple[str, list[ChatMessage]]] = []

    def generate(self, system_prompt: str, messages: Sequence[ChatMessage]) -> str:
        self.llamadas.append((system_prompt, list(messages)))
        if not self.respuestas:
            raise AssertionError("FakeProvider se quedó sin respuestas enlatadas")
        return self.respuestas.pop(0)
