"""Adaptador de LLMProvider vía la CLI de Claude Code en modo headless.

Usa ``claude -p --output-format json`` autenticado con la suscripción Pro/Max
del usuario. Requiere tener la CLI instalada y sesión iniciada.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Sequence

from pcia.domain.ports import ChatMessage, LLMProvider, LLMProviderError

TIMEOUT_SEGUNDOS = 600.0
ETIQUETAS = {"user": "Usuario", "assistant": "Asistente"}


class ClaudeSubscriptionProvider(LLMProvider):
    """Invoca la CLI ``claude`` como proceso hijo, una llamada por turno.

    La CLI es de un solo turno, así que el historial completo se serializa
    dentro del prompt en cada llamada.
    """

    def __init__(self, model: str | None = None) -> None:
        if shutil.which("claude") is None:
            raise LLMProviderError(
                "No se encontró la CLI 'claude'. Instalá Claude Code e iniciá "
                "sesión, o elegí otro proveedor en config.yaml."
            )
        self._model = model

    def generate(self, system_prompt: str, messages: Sequence[ChatMessage]) -> str:
        prompt = self._componer_prompt(system_prompt, messages)
        comando = ["claude", "-p", "--output-format", "json"]
        if self._model:
            comando += ["--model", self._model]
        try:
            resultado = subprocess.run(
                comando,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SEGUNDOS,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMProviderError(
                f"La CLI de Claude no respondió en {TIMEOUT_SEGUNDOS:.0f}s."
            ) from exc
        except OSError as exc:
            raise LLMProviderError(f"No se pudo ejecutar la CLI de Claude: {exc}") from exc

        if resultado.returncode != 0:
            raise LLMProviderError(
                f"La CLI de Claude falló (código {resultado.returncode}): "
                f"{resultado.stderr.strip() or resultado.stdout.strip()}"
            )
        try:
            datos = json.loads(resultado.stdout)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"La CLI de Claude no devolvió JSON válido: {resultado.stdout[:500]!r}"
            ) from exc

        texto = datos.get("result")
        if not texto:
            raise LLMProviderError(f"La CLI de Claude devolvió un resultado vacío: {datos!r}")
        return texto

    @staticmethod
    def _componer_prompt(system_prompt: str, messages: Sequence[ChatMessage]) -> str:
        # La CLI no separa system del historial: se rinde todo como texto.
        lineas = [
            "<instrucciones_de_sistema>",
            system_prompt,
            "</instrucciones_de_sistema>",
            "",
            "Conversación hasta ahora:",
        ]
        for mensaje in messages:
            lineas.append(f"{ETIQUETAS[mensaje.role]}: {mensaje.content}")
        lineas.append(
            "\nRespondé como el Asistente, siguiendo estrictamente el formato "
            "de salida definido en las instrucciones de sistema."
        )
        return "\n".join(lineas)
