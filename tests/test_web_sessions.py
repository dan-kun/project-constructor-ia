"""Tests del adapter web: bridging de colas y validación del formulario.

No levanta un servidor HTTP real (eso lo cubre la corrida manual de la demo);
prueba la lógica de puenteo hilo/colas con FakeProvider, sin red.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from pcia.config import ConfigError
from pcia.web.sessions import GestorSesiones, Sesion, validar_config_proveedor


def test_sesion_bridging_entrada_salida():
    sesion = Sesion(id="test")
    sesion.enviar_input("hola")
    # "> " es el marcador genérico de turno normal de entrevista: la
    # pregunta real ya viajó por su propio salida() un instante antes, así
    # que acá no debe reenviarse nada (a diferencia de un prompt con texto).
    assert sesion.entrada("> ") == "hola"

    sesion.salida("mensaje al usuario")
    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.tipo == "mensaje"
    assert evento.texto == "mensaje al usuario"


def test_entrada_reenvia_prompts_con_texto_antes_de_bloquear():
    """Bug real detectado en la demo: preguntas como '¿Confirmás la
    especificación?' solo viajaban como argumento de entrada() — en la CLI
    ``input(prompt)`` las imprime sola, pero en la web se perdían en
    silencio y la sesión quedaba esperando una respuesta que el usuario
    nunca vio. Ahora entrada() las publica como mensaje antes de bloquear."""
    sesion = Sesion(id="test")
    sesion.enviar_input("si")

    respuesta = sesion.entrada(
        "¿Confirmás la especificación? (enter = continuar, o escribí qué ajustar) "
    )

    assert respuesta == "si"
    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert evento.tipo == "mensaje"
    assert "¿Confirmás la especificación?" in evento.texto


def test_entrada_destino_muestra_mensaje_amigable_no_la_ruta_del_servidor():
    """La pregunta real del destino incluye una ruta del filesystem del
    servidor (irrelevante para el visitante); se reemplaza por un mensaje
    propio en vez de reenviarla tal cual."""
    sesion = Sesion(id="test")
    sesion.enviar_input("mi-api")

    sesion.entrada("¿Dónde genero el proyecto? (enter = /opt/render/project/src/mi-api) ")

    evento = sesion.proximo_evento(timeout=1)
    assert evento is not None
    assert "/opt/render" not in evento.texto
    assert "carpeta del proyecto" in evento.texto


def test_entrada_confina_el_destino_de_construccion_pese_a_path_traversal():
    """Un visitante de la demo web no puede decidir una ruta arbitraria en el
    filesystem del servidor: el destino siempre queda bajo el directorio
    propio de la sesión, sin importar lo que haya tipeado."""
    sesion = Sesion(id="sesion-abc")
    prompt_destino = "¿Dónde genero el proyecto? (enter = /home/render/x) "

    for intento_malicioso in ("../../../etc/pasando", "/etc/cron.d/evil", "C:\\Windows\\System32"):
        sesion.enviar_input(intento_malicioso)
        destino = Path(sesion.entrada(prompt_destino))
        base_segura = Path(tempfile.gettempdir()) / "pcia-web-sesiones" / "sesion-abc"
        assert base_segura in destino.parents or destino.parent == base_segura
        assert ".." not in destino.parts


def test_entrada_no_toca_el_destino_para_otros_prompts():
    sesion = Sesion(id="sesion-xyz")
    sesion.enviar_input("../../lo-que-sea")
    assert sesion.entrada("¿Confirmás la especificación?") == "../../lo-que-sea"


def test_guardar_transcript_registra_salidas_y_entradas_en_orden(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    sesion = Sesion(id="sesion-transcript")
    sesion.salida("¿Cómo se llama el proyecto?")
    sesion.enviar_input("mi-api")
    sesion.entrada("> ")

    ruta = sesion.guardar_transcript()

    assert ruta == sesion.ruta_transcript
    contenido = ruta.read_text(encoding="utf-8")
    assert contenido.index("¿Cómo se llama el proyecto?") < contenido.index("> mi-api")


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


def test_flujo_completo_web_muestra_confirmacion_y_guarda_transcript(monkeypatch, tmp_path):
    """Regresión de punta a punta del bug real: antes del fix, la sesión
    llegaba a 'terminar' la entrevista a los ojos del usuario pero quedaba
    esperando en silencio la confirmación de la spec, sin avisar nada."""
    from conftest import FakeProvider

    from pcia.agents import verificador as modulo_verificador
    from pcia.web import sessions as web_sessions

    # Sin esto, la verificación profunda intenta un build de Docker real si
    # está instalado en la máquina que corre los tests (como acá).
    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: False)

    updates_completos = {
        "nombre": "mi-api",
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

    def respuesta_json(mensaje="ok", updates=None, done=False):
        return json.dumps(
            {"message_to_user": mensaje, "updates": updates or {}, "done": done}
        )

    provider = FakeProvider(
        [
            respuesta_json("¿Qué querés construir?"),
            respuesta_json("Listo, resumen final.", updates_completos, done=True),
            '{"hallazgos": []}',  # pase LLM del Auditor
            json.dumps({"readme_markdown": "# mi api\n", "adr_markdown": "# ADR-001\n"}),
        ]
    )
    monkeypatch.setattr(web_sessions, "crear_provider", lambda config: provider)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    gestor = GestorSesiones(memory_dir=tmp_path / "memory")
    sesion = gestor.crear({"provider": "openai_compat", "openai_compat": {}})

    # Los 3 únicos turnos que requieren respuesta en este flujo (idéntico al
    # equivalente por CLI en test_orchestrator.py): la primera respuesta
    # libre, confirmar la spec, y nombrar la carpeta del proyecto. Se cargan
    # de antemano porque no hay forma de saber, solo mirando los eventos de
    # salida, cuáles de ellos bloquean esperando input y cuáles son
    # puramente informativos.
    sesion.enviar_input("una API de facturación en python")
    sesion.enviar_input("")
    sesion.enviar_input("mi-api")

    mensajes: list[str] = []
    evento = None
    for _ in range(30):
        evento = sesion.proximo_evento(timeout=10)
        assert evento is not None, "la sesión no terminó dentro del timeout"
        if evento.tipo == "mensaje":
            mensajes.append(evento.texto)
        else:
            break

    assert evento is not None and evento.tipo == "fin", (
        f"la sesión no terminó en 'fin': {evento}, mensajes: {mensajes}"
    )
    # el bug: esta pregunta se perdía en silencio y nunca llegaba al chat
    assert any("¿Confirmás la especificación?" in m for m in mensajes)
    assert any("carpeta del proyecto" in m for m in mensajes)
    assert not any("/opt/render" in m or str(tmp_path) in m for m in mensajes if "genero" in m.lower())

    assert sesion.ruta_transcript is not None and sesion.ruta_transcript.exists()
    contenido = sesion.ruta_transcript.read_text(encoding="utf-8")
    assert "¿Confirmás la especificación?" in contenido
    assert sesion.ruta_proyecto is not None and sesion.ruta_proyecto.exists()
