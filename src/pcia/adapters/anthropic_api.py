"""Adaptador de LLMProvider para la API directa de Anthropic (Messages API)."""

from __future__ import annotations

import os
from typing import Sequence

import httpx

from pcia.domain.ports import ChatMessage, LLMProvider, LLMProviderError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TIMEOUT_SEGUNDOS = 120.0


class AnthropicAPIProvider(LLMProvider):
    """Llama a la Messages API de Anthropic vía HTTP (pay-per-use con API key)."""

    def __init__(
        self,
        model: str,
        max_tokens: int = 2000,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise LLMProviderError(
                "Falta la API key de Anthropic: definí la variable de entorno "
                "ANTHROPIC_API_KEY o usá otro proveedor en config.yaml."
            )
        self._model = model
        self._max_tokens = max_tokens
        self._client = client or httpx.Client(timeout=TIMEOUT_SEGUNDOS)

    def generate(self, system_prompt: str, messages: Sequence[ChatMessage]) -> str:
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system_prompt,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            respuesta = self._client.post(API_URL, headers=headers, json=payload)
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"La API de Anthropic devolvió {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Error de red contra la API de Anthropic: {exc}") from exc

        datos = respuesta.json()
        textos = [
            bloque.get("text", "")
            for bloque in datos.get("content", [])
            if bloque.get("type") == "text"
        ]
        if not textos:
            raise LLMProviderError(
                f"La respuesta de Anthropic no contiene texto (stop_reason="
                f"{datos.get('stop_reason')!r})."
            )
        return "".join(textos)
