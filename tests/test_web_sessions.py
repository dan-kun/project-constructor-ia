"""Tests del adapter web: bridging de colas y validación del formulario.

No levanta un servidor HTTP real (eso lo cubre la corrida manual de la demo);
prueba la lógica de puenteo hilo/colas con FakeProvider, sin red.
"""

from __future__ import annotations

import json
import time

import pytest

from pcia.config import ConfigError
from pcia.web.sessions import GestorSesiones, Sesion, validar_config_proveedor


def test_sesion_bridging_entrada_salida():
    sesion = Sesion(id="test")
    sesion.enviar_input("hola")
    assert sesion.entrada("prompt ignorado") == "hola"

    sesion.salida("mensaje al usuario")
    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.tipo == "mensaje"
    assert evento.texto == "mensaje al usuario"


def test_proximo_evento_sin_eventos_devuelve_none():
    sesion = Sesion(id="test")
    assert sesion.proximo_evento(timeout=0.05) is None


def test_validar_config_proveedor_anthropic_default_model():
    config = validar_config_proveedor({"provider": "anthropic_api", "api_key": "clave"})
    assert config == {
        "provider": "anthropic_api",
        "anthropic_api": {"model": "claude-sonnet-4-6", "api_key": "clave"},
    }


def test_validar_config_proveedor_openai_compat_requiere_base_url():
    with pytest.raises(ConfigError, match="base_url"):
        validar_config_proveedor({"provider": "openai_compat", "model": "qwen2.5:14b"})


def test_validar_config_proveedor_provider_invalido():
    with pytest.raises(ConfigError, match="Elegí un proveedor"):
        validar_config_proveedor({"provider": "claude_subscription"})


def test_gestor_crear_corre_orquestador_en_hilo_y_publica_primer_mensaje(
    monkeypatch, tmp_path
):
    """El Orquestador corre en background y su primer 'salida' llega por la cola,
    sin esperar a que termine toda la entrevista (que sigue bloqueada en 'entrada')."""
    from pcia.web import sessions as web_sessions

    respuesta_inicial = json.dumps(
        {"message_to_user": "¿Cómo se llama el proyecto?", "updates": {}, "done": False}
    )

    class FakeProviderLocal:
        def generate(self, system_prompt, messages):
            return respuesta_inicial

    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: FakeProviderLocal())

    gestor = GestorSesiones(memory_dir=tmp_path)
    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    evento = sesion.proximo_evento(timeout=5)
    assert evento is not None
    assert evento.tipo == "mensaje"
    assert "proyecto" in evento.texto.lower()

    # Sin alimentar la cola de entrada, el hilo queda bloqueado esperando
    # respuesta del usuario (igual que la CLI esperando input()).
    time.sleep(0.05)
    assert sesion.hilo.is_alive()
