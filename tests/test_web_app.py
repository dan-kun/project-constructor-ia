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


def test_crear_sesion_con_documento_invalido_devuelve_400(monkeypatch, tmp_path, cliente):
    """Equivalente HTTP de U9: subir un documento con formato no soportado
    se rechaza antes de crear la sesión, con el mismo tipo de error 400 que
    usa el resto del formulario (ver validar_config_proveedor)."""
    from pcia.web import app as modulo_app

    monkeypatch.setattr(modulo_app.gestor, "_memory_dir", tmp_path)
    payload = {
        "provider": "openai_compat",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5",
        "documentos": [{"nombre": "requerimientos.pdf", "contenido": "x"}],
    }
    resp = cliente.post("/api/sessions", json=payload)
    assert resp.status_code == 400
    assert "Formato no soportado" in resp.json()["detail"]


def test_crear_sesion_con_spec_inicial_invalida_devuelve_400(monkeypatch, tmp_path, cliente):
    """Corregir/retomar con datos ya respondidos (U3/U4/U5): un payload con
    una clave inexistente se rechaza igual que el resto del formulario."""
    from pcia.web import app as modulo_app

    monkeypatch.setattr(modulo_app.gestor, "_memory_dir", tmp_path)
    payload = {
        "provider": "openai_compat",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5",
        "spec_inicial": {"campo_inexistente": "x"},
    }
    resp = cliente.post("/api/sessions", json=payload)
    assert resp.status_code == 400
    assert "Datos de partida inválidos" in resp.json()["detail"]


def test_health_devuelve_ok(cliente):
    resp = cliente.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_crear_sesion_respeta_el_limite_de_tasa(monkeypatch, tmp_path):
    """Sin límite, un visitante podría abrir sesiones (cada una con su hilo
    y su directorio en /tmp) sin parar. Se prueba contra un límite bajo para
    no depender del valor real configurado en la app."""
    from pcia.web import app as modulo_app
    from pcia.web.ratelimit import LimitadorTasa

    class FakeProviderLocal:
        def generate(self, system_prompt, messages):
            return '{"message_to_user": "hola", "updates": {}, "done": false}'

    monkeypatch.setattr(modulo_app, "limitador", LimitadorTasa(max_eventos=2, ventana_segundos=60.0))
    monkeypatch.setattr(modulo_app.gestor, "_memory_dir", tmp_path)
    monkeypatch.setattr("pcia.web.sessions.crear_provider", lambda config: FakeProviderLocal())

    cliente = TestClient(app)
    payload = {"provider": "openai_compat", "base_url": "http://localhost:11434/v1", "model": "qwen2.5"}

    assert cliente.post("/api/sessions", json=payload).status_code == 200
    assert cliente.post("/api/sessions", json=payload).status_code == 200
    tercera = cliente.post("/api/sessions", json=payload)
    assert tercera.status_code == 429


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


def test_discover_modelos_usa_api_nativa_de_ollama_como_respaldo(monkeypatch, cliente):
    def responder(url, headers, timeout):
        if url.endswith("/v1/models"):
            return RespuestaFalsa({}, status_code=404)
        assert url == "http://localhost:11434/api/tags"
        return RespuestaFalsa({"models": [{"name": "qwen3:8b"}]})

    monkeypatch.setattr(httpx, "get", responder)
    resp = cliente.post(
        "/api/discover-models", json={"base_url": "http://localhost:11434/v1"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"modelos": ["qwen3:8b"]}
