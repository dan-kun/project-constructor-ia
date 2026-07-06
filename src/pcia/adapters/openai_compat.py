"""Adaptador de LLMProvider para APIs compatibles con OpenAI.

Cubre Ollama local, LM Studio, Groq, OpenRouter, DeepSeek, etc.: solo cambia
``base_url`` + ``model`` en config.yaml.
"""

from __future__ import annotations

from typing import Sequence

import httpx

from pcia.domain.ports import ChatMessage, LLMProvider, LLMProviderError

TIMEOUT_SEGUNDOS = 300.0  # modelos locales pueden ser lentos


class OpenAICompatProvider(LLMProvider):
    """Llama al endpoint ``/chat/completions`` de una API compatible con OpenAI."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "sin-key",
        client: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=TIMEOUT_SEGUNDOS)

    def generate(self, system_prompt: str, messages: Sequence[ChatMessage]) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        try:
            respuesta = self._client.post(self._url, headers=headers, json=payload)
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"El proveedor OpenAI-compatible devolvió {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"Error de red contra {self._url}: {exc}. "
                "¿Está corriendo el servidor (por ejemplo, Ollama)?"
            ) from exc

        datos = respuesta.json()
        try:
            contenido = datos["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"Respuesta inesperada del proveedor OpenAI-compatible: {datos!r}"
            ) from exc
        if not contenido:
            raise LLMProviderError("El proveedor OpenAI-compatible devolvió contenido vacío.")
        return contenido
