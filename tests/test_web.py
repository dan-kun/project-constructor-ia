"""Tests del adaptador web: sesión con hilo + colas y servidor WebSocket.

El orquestador no se toca: se verifica que la interfaz web se enchufa en
los mismos callables de IO que usa la consola.
"""

import json

import pytest

from conftest import FakeProvider
from pcia.web.sesion import SesionWeb

pytest.importorskip("fastapi")

TIMEOUT = 5.0  # un test que se cuelga debe fallar, no bloquear la suite


@pytest.fixture(autouse=True)
def sin_herramientas_externas(monkeypatch):
    from pcia.agents import verificador as modulo_verificador

    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: False)


UPDATES_COMPLETOS = {
    "nombre": "mi api",
    "descripcion": "API de facturación",
    "tipo_proyecto": "api",
    "lenguaje": "python",
    "framework": "fastapi",
    "arquitectura": "capas",
    "base_datos": "postgresql",
    "autenticacion": "jwt",
    "gestion_secretos": "variables de entorno",
    "infraestructura": "docker",
    "ci_cd": "github actions",
    "alcance": "mvp",
}
SIN_HALLAZGOS = '{"hallazgos": []}'
DOCS = json.dumps({"readme_markdown": "# mi api\n", "adr_markdown": "# ADR-001\n"})


def respuesta_json(mensaje="ok", updates=None, done=False):
    return json.dumps(
        {"message_to_user": mensaje, "updates": updates or {}, "done": done},
        ensure_ascii=False,
    )


def provider_completo():
    return FakeProvider(
        [
            respuesta_json("¿Qué querés construir?"),
            respuesta_json("Resumen final.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS,
            DOCS,
        ]
    )


def correr_sesion(sesion, respuestas):
    """Consume eventos y responde en orden; devuelve todos los eventos."""
    pendientes = list(respuestas)
    eventos = []
    while True:
        evento = sesion.siguiente_evento(timeout=TIMEOUT)
        eventos.append(evento)
        if evento["tipo"] in ("fin", "error"):
            return eventos
        if evento["tipo"] == "pregunta":
            sesion.responder(pendientes.pop(0))


def test_sesion_completa_emite_eventos_y_termina(tmp_path):
    sesion = SesionWeb(
        provider_completo(), memory_dir=tmp_path / "memory", proveedor="fake:test"
    )
    sesion.iniciar()

    eventos = correr_sesion(
        sesion, ["una API de facturación", "", str(tmp_path / "proyecto")]
    )

    assert eventos[-1]["tipo"] == "fin"
    assert "Registro del proyecto guardado" in eventos[-1]["texto"]
    # el ciclo completo pasó por el navegador
    textos = " ".join(e["texto"] for e in eventos)
    assert "¿Qué querés construir?" in textos
    assert "Auditoría de coherencia" in textos
    assert "Proyecto generado" in textos
    assert (tmp_path / "proyecto" / "pyproject.toml").exists()


def test_estado_expuesto_sin_parsear_texto(tmp_path):
    sesion = SesionWeb(provider_completo(), memory_dir=tmp_path / "memory")
    sesion.iniciar()

    eventos = correr_sesion(
        sesion, ["una API de facturación", "", str(tmp_path / "proyecto")]
    )

    final = eventos[-1]["estado"]
    assert final["fase"] == "fin"
    assert final["spec"]["framework"] == "fastapi"
    assert final["campos_faltantes"] == []
    assert final["semaforo"] == "verde"
    assert final["stack"] == "fastapi"
    assert "pyproject.toml" in final["archivos"]
    # sin docker en el entorno de test, la verificación queda inconclusa
    assert final["verificacion"]["estado"] == "inconcluso"
    assert final["ruta_proyecto"] == str(tmp_path / "proyecto")

    # la fase avanza a lo largo de la corrida, no solo al final
    fases = [e["estado"]["fase"] for e in eventos]
    assert "entrevista" in fases and "construccion" in fases


def test_error_del_orquestador_llega_como_evento(tmp_path):
    # el provider se queda sin respuestas: el agente falla dentro del hilo
    sesion = SesionWeb(FakeProvider([]), memory_dir=tmp_path / "memory")
    sesion.iniciar()

    evento = sesion.siguiente_evento(timeout=TIMEOUT)

    assert evento["tipo"] == "error"
    assert evento["estado"]["fase"] == "entrevista"


def test_cerrar_corta_una_sesion_abandonada(tmp_path):
    sesion = SesionWeb(provider_completo(), memory_dir=tmp_path / "memory")
    sesion.iniciar()
    sesion.siguiente_evento(timeout=TIMEOUT)  # primer mensaje del entrevistador
    sesion.siguiente_evento(timeout=TIMEOUT)  # pregunta: queda esperando

    sesion.cerrar()

    assert not sesion._hilo.is_alive()


# --- servidor -------------------------------------------------------------------


def crear_cliente(tmp_path, monkeypatch, provider):
    from fastapi.testclient import TestClient

    from pcia.web import app as modulo_app

    config = tmp_path / "config.yaml"
    config.write_text(
        f"provider: openai_compat\nmemory_dir: {tmp_path / 'memory'}\n"
        "openai_compat:\n  base_url: http://localhost:1/v1\n  model: falso\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(modulo_app, "crear_provider", lambda _config: provider)
    return TestClient(modulo_app.crear_app(str(config)))


def test_index_sirve_la_interfaz(tmp_path, monkeypatch):
    cliente = crear_cliente(tmp_path, monkeypatch, provider_completo())

    respuesta = cliente.get("/")

    assert respuesta.status_code == 200
    assert "Project Constructor IA" in respuesta.text


def test_api_config_reporta_el_proveedor(tmp_path, monkeypatch):
    cliente = crear_cliente(tmp_path, monkeypatch, provider_completo())

    datos = cliente.get("/api/config").json()

    assert datos == {"proveedor": "openai_compat", "modelo": "falso"}


def test_websocket_corre_el_ciclo_completo(tmp_path, monkeypatch):
    cliente = crear_cliente(tmp_path, monkeypatch, provider_completo())
    respuestas = ["una API de facturación", "", str(tmp_path / "proyecto")]

    with cliente.websocket_connect("/ws") as ws:
        while True:
            evento = ws.receive_json()
            if evento["tipo"] in ("fin", "error"):
                break
            if evento["tipo"] == "pregunta":
                ws.send_json({"texto": respuestas.pop(0)})

    assert evento["tipo"] == "fin"
    assert evento["estado"]["stack"] == "fastapi"
    assert (tmp_path / "proyecto" / "README.md").exists()
