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
