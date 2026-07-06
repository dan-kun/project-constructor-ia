"""Tests de los adaptadores de LLMProvider (sin red: transportes falsos)."""

import json
import subprocess

import httpx
import pytest

from pcia.adapters.anthropic_api import AnthropicAPIProvider
from pcia.adapters.claude_subscription import ClaudeSubscriptionProvider
from pcia.adapters.openai_compat import OpenAICompatProvider
from pcia.domain.ports import ChatMessage, LLMProviderError

MENSAJES = [ChatMessage(role="user", content="hola")]


def cliente_falso(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- anthropic_api ---------------------------------------------------------


def test_anthropic_arma_payload_y_extrae_texto():
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["headers"] = request.headers
        capturado["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "stop_reason": "end_turn",
            },
        )

    provider = AnthropicAPIProvider(
        model="claude-sonnet-4-6", max_tokens=123, api_key="clave", client=cliente_falso(handler)
    )
    texto = provider.generate("sistema", MENSAJES)

    assert texto == '{"ok": true}'
    assert capturado["headers"]["x-api-key"] == "clave"
    assert capturado["headers"]["anthropic-version"] == "2023-06-01"
    assert capturado["payload"]["model"] == "claude-sonnet-4-6"
    assert capturado["payload"]["max_tokens"] == 123
    assert capturado["payload"]["system"] == "sistema"
    assert capturado["payload"]["messages"] == [{"role": "user", "content": "hola"}]


def test_anthropic_error_http_levanta_llmprovidererror():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    provider = AnthropicAPIProvider(model="m", api_key="clave", client=cliente_falso(handler))
    with pytest.raises(LLMProviderError, match="401"):
        provider.generate("s", MENSAJES)


def test_anthropic_sin_api_key_levanta_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicAPIProvider(model="m")


def test_anthropic_respuesta_sin_texto_levanta_error():
    def handler(request):
        return httpx.Response(200, json={"content": [], "stop_reason": "refusal"})

    provider = AnthropicAPIProvider(model="m", api_key="clave", client=cliente_falso(handler))
    with pytest.raises(LLMProviderError, match="no contiene texto"):
        provider.generate("s", MENSAJES)


# --- openai_compat ---------------------------------------------------------


def test_openai_compat_arma_payload_y_extrae_texto():
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["payload"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "respuesta"}}]}
        )

    provider = OpenAICompatProvider(
        base_url="http://localhost:11434/v1/", model="qwen2.5:14b", client=cliente_falso(handler)
    )
    texto = provider.generate("sistema", MENSAJES)

    assert texto == "respuesta"
    assert capturado["url"] == "http://localhost:11434/v1/chat/completions"
    assert capturado["payload"]["model"] == "qwen2.5:14b"
    assert capturado["payload"]["messages"][0] == {"role": "system", "content": "sistema"}
    assert capturado["payload"]["messages"][1] == {"role": "user", "content": "hola"}


def test_openai_compat_error_de_red_levanta_llmprovidererror():
    def handler(request):
        raise httpx.ConnectError("conexión rechazada")

    provider = OpenAICompatProvider(
        base_url="http://localhost:11434/v1", model="m", client=cliente_falso(handler)
    )
    with pytest.raises(LLMProviderError, match="Error de red"):
        provider.generate("s", MENSAJES)


def test_openai_compat_respuesta_malformada_levanta_error():
    def handler(request):
        return httpx.Response(200, json={"inesperado": True})

    provider = OpenAICompatProvider(
        base_url="http://x/v1", model="m", client=cliente_falso(handler)
    )
    with pytest.raises(LLMProviderError, match="inesperada"):
        provider.generate("s", MENSAJES)


# --- claude_subscription ---------------------------------------------------


def test_claude_subscription_compone_prompt_y_parsea_json(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    capturado = {}

    def run_falso(comando, **kwargs):
        capturado["comando"] = comando
        capturado["prompt"] = kwargs["input"]
        return subprocess.CompletedProcess(
            comando, 0, stdout=json.dumps({"result": "hola desde claude"}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", run_falso)

    provider = ClaudeSubscriptionProvider(model="opus")
    texto = provider.generate("sistema", MENSAJES)

    assert texto == "hola desde claude"
    assert capturado["comando"][:4] == ["claude", "-p", "--output-format", "json"]
    assert capturado["comando"][-2:] == ["--model", "opus"]
    assert "sistema" in capturado["prompt"]
    assert "Usuario: hola" in capturado["prompt"]


def test_claude_subscription_sin_cli_levanta_error(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(LLMProviderError, match="No se encontró la CLI"):
        ClaudeSubscriptionProvider()


def test_claude_subscription_codigo_de_salida_no_cero(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda comando, **kwargs: subprocess.CompletedProcess(
            comando, 1, stdout="", stderr="no hay sesión iniciada"
        ),
    )
    provider = ClaudeSubscriptionProvider()
    with pytest.raises(LLMProviderError, match="no hay sesión iniciada"):
        provider.generate("s", MENSAJES)


def test_claude_subscription_stdout_no_json(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda comando, **kwargs: subprocess.CompletedProcess(
            comando, 0, stdout="esto no es json", stderr=""
        ),
    )
    provider = ClaudeSubscriptionProvider()
    with pytest.raises(LLMProviderError, match="JSON válido"):
        provider.generate("s", MENSAJES)
