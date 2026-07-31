"""Tests del endpoint HTTP de descubrimiento de modelos (respaldo server-side
de 'Cargar disponibles' cuando el navegador no puede por CORS, como pasa con
Ollama Cloud). No usa red real: se mockea httpx.get."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from pcia.web.app import app


class RespuestaFalsa:
    def __init__(self, datos: dict, status_code: int = 200) -> None:
        self._datos = datos
        self.status_code = status_code

    def json(self) -> dict:
        return self._datos

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def permitir_destinos_internos(monkeypatch):
    """Estos tests ejercitan el manejo de errores HTTP, no la política de
    destinos: usan localhost como ejemplo, que por defecto está bloqueado
    (ver test_web_destinos.py para la defensa contra SSRF)."""
    from pcia.web import app as modulo_app

    monkeypatch.setenv(modulo_app.VAR_DESTINOS_PRIVADOS, "1")


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


def test_discover_models_devuelve_ids_ordenados(monkeypatch, cliente):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, headers, timeout: RespuestaFalsa(
            {"data": [{"id": "qwen3.5:9b"}, {"id": "gpt-oss:20b"}]}
        ),
    )

    resp = cliente.post(
        "/api/discover-models",
        json={"base_url": "https://ollama.com/v1", "api_key": "clave"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"modelos": ["gpt-oss:20b", "qwen3.5:9b"]}


def test_discover_models_sin_modelos_devuelve_502(monkeypatch, cliente):
    monkeypatch.setattr(
        httpx, "get", lambda url, headers, timeout: RespuestaFalsa({"data": []})
    )

    resp = cliente.post(
        "/api/discover-models", json={"base_url": "http://localhost:11434/v1"}
    )

    assert resp.status_code == 502


def test_discover_models_error_de_red_devuelve_502(monkeypatch, cliente):
    def _falla(url, headers, timeout):
        raise httpx.ConnectError("no se pudo conectar")

    monkeypatch.setattr(httpx, "get", _falla)

    resp = cliente.post(
        "/api/discover-models", json={"base_url": "http://localhost:11434/v1"}
    )

    assert resp.status_code == 502
    assert "no se pudo conectar" in resp.json()["detail"]


# --- formatos de /v1/models ------------------------------------------------------


def test_extrae_modelos_en_formato_openai():
    from pcia.web.app import extraer_nombres_de_modelos

    datos = {"object": "list", "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}

    assert extraer_nombres_de_modelos(datos) == ["gpt-4o", "gpt-4o-mini"]


def test_extrae_modelos_en_formato_ollama():
    """Ollama y algunos llama.cpp devuelven {models:[{name}]} en /v1/models."""
    from pcia.web.app import extraer_nombres_de_modelos

    datos = {
        "models": [
            {"name": "unsloth/Qwen3-30B-A3B-GGUF", "model": "unsloth/Qwen3-30B-A3B-GGUF"},
            {"name": "llama3.2", "model": "llama3.2"},
        ]
    }

    assert extraer_nombres_de_modelos(datos) == ["llama3.2", "unsloth/Qwen3-30B-A3B-GGUF"]


@pytest.mark.parametrize("datos", [{}, [], None, {"data": []}, {"models": [{}]}])
def test_respuestas_sin_modelos_devuelven_lista_vacia(datos):
    from pcia.web.app import extraer_nombres_de_modelos

    assert extraer_nombres_de_modelos(datos) == []


def test_discover_models_acepta_formato_ollama(monkeypatch, cliente):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, headers, timeout: RespuestaFalsa(
            {"models": [{"name": "unsloth/Qwen3-30B-A3B-GGUF"}]}
        ),
    )

    resp = cliente.post(
        "/api/discover-models",
        json={"base_url": "http://localhost:8088/v1", "api_key": ""},
    )

    assert resp.status_code == 200
    assert resp.json() == {"modelos": ["unsloth/Qwen3-30B-A3B-GGUF"]}
