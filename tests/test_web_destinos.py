"""Tests de la validación de destinos server-side (defensa contra SSRF)."""

import pytest

from pcia.web.destinos import DestinoNoPermitidoError, validar_url_externa


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8088/v1",
        "http://169.254.169.254/latest/meta-data",  # metadatos de la nube
        "http://10.0.0.5/v1",
        "http://192.168.1.10:11434/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_destinos_internos_se_rechazan(url):
    with pytest.raises(DestinoNoPermitidoError, match="interna"):
        validar_url_externa(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
def test_esquemas_no_http_se_rechazan(url):
    with pytest.raises(DestinoNoPermitidoError, match="Esquema no permitido"):
        validar_url_externa(url)


def test_url_sin_host_se_rechaza():
    with pytest.raises(DestinoNoPermitidoError, match="no tiene host"):
        validar_url_externa("http://")


def test_host_que_no_resuelve_se_rechaza():
    with pytest.raises(DestinoNoPermitidoError, match="No se pudo resolver"):
        validar_url_externa("https://no-existe.invalid/v1")


def test_destino_publico_se_acepta(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("104.18.0.1", 443))],
    )
    assert validar_url_externa("https://api.groq.com/openai/v1")


def test_permitir_privadas_habilita_localhost():
    # corrida local explícita (pcia-web): Ollama en localhost es legítimo
    url = "http://localhost:11434/v1"
    assert validar_url_externa(url, permitir_privadas=True) == url


# --- integración con el endpoint --------------------------------------------------


def crear_cliente():
    from fastapi.testclient import TestClient

    from pcia.web.app import app

    return TestClient(app)


def test_endpoint_rechaza_destino_interno(monkeypatch):
    from pcia.web import app as modulo_app

    monkeypatch.delenv(modulo_app.VAR_DESTINOS_PRIVADOS, raising=False)

    respuesta = crear_cliente().post(
        "/api/discover-models",
        json={"base_url": "http://169.254.169.254/latest", "api_key": ""},
    )

    assert respuesta.status_code == 400
    assert "interna" in respuesta.json()["detail"]


def test_endpoint_permite_interno_si_esta_habilitado(monkeypatch):
    import httpx

    from pcia.web import app as modulo_app

    monkeypatch.setenv(modulo_app.VAR_DESTINOS_PRIVADOS, "1")

    class RespuestaFalsa:
        status_code = 200

        def json(self):
            return {"data": [{"id": "qwen3:8b"}]}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx, "get", lambda url, headers, timeout: RespuestaFalsa())

    respuesta = crear_cliente().post(
        "/api/discover-models",
        json={"base_url": "http://localhost:11434/v1", "api_key": ""},
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"modelos": ["qwen3:8b"]}


# --- el permiso local depende del host de escucha ---------------------------------


def _main_sin_servidor(monkeypatch, argv):
    """Corre main() interceptando uvicorn.run (no levanta nada de verdad)."""
    import uvicorn

    from pcia.web import app as modulo_app

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.delenv(modulo_app.VAR_DESTINOS_PRIVADOS, raising=False)
    modulo_app.main(argv)
    import os

    return os.environ.get(modulo_app.VAR_DESTINOS_PRIVADOS)


def test_escuchar_en_loopback_habilita_destinos_internos(monkeypatch):
    assert _main_sin_servidor(monkeypatch, ["--host", "127.0.0.1"]) == "1"


def test_exponerse_en_la_red_no_habilita_destinos_internos(monkeypatch):
    # con --host 0.0.0.0 un visitante podría usar el server como proxy
    assert _main_sin_servidor(monkeypatch, ["--host", "0.0.0.0"]) is None


def test_flag_explicito_desactiva_aun_en_loopback(monkeypatch):
    assert (
        _main_sin_servidor(monkeypatch, ["--host", "localhost", "--sin-destinos-privados"])
        is None
    )


def test_loopback_habilita_la_suscripcion_de_claude(monkeypatch):
    from pcia.web import app as modulo_app

    monkeypatch.delenv(modulo_app.VAR_SUSCRIPCION_CLAUDE, raising=False)
    _main_sin_servidor(monkeypatch, ["--host", "127.0.0.1"])
    import os

    assert os.environ.get(modulo_app.VAR_SUSCRIPCION_CLAUDE) == "1"


def test_endpoint_de_proveedores_refleja_el_flag(monkeypatch):
    from pcia.web import app as modulo_app

    monkeypatch.setenv(modulo_app.VAR_SUSCRIPCION_CLAUDE, "1")
    assert crear_cliente().get("/api/proveedores").json() == {"claude_subscription": True}

    monkeypatch.delenv(modulo_app.VAR_SUSCRIPCION_CLAUDE)
    assert crear_cliente().get("/api/proveedores").json() == {"claude_subscription": False}
