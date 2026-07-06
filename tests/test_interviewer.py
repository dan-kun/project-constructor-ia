"""Tests del Agente Entrevistador con FakeProvider (sin llamadas reales)."""

import json

import pytest

from conftest import FakeProvider
from pcia.agents.interviewer import (
    MAX_REINTENTOS,
    ContratoInvalidoError,
    Entrevistador,
)
from pcia.domain.models import ProjectSpec


def respuesta_json(mensaje="ok", updates=None, done=False) -> str:
    return json.dumps(
        {"message_to_user": mensaje, "updates": updates or {}, "done": done},
        ensure_ascii=False,
    )


def test_turno_valido_aplica_updates_y_guarda_historial():
    provider = FakeProvider(
        [respuesta_json("¿Qué lenguaje?", {"nombre": "mi-api", "tipo_proyecto": "api"})]
    )
    spec = ProjectSpec()
    entrevistador = Entrevistador(provider, spec)

    respuesta = entrevistador.iniciar()

    assert respuesta.message_to_user == "¿Qué lenguaje?"
    assert spec.nombre == "mi-api"
    assert spec.tipo_proyecto == "api"
    assert not respuesta.done
    # historial: mensaje inicial del usuario + respuesta cruda del asistente
    assert [m.role for m in entrevistador.historial] == ["user", "assistant"]


def test_system_prompt_incluye_estado_y_faltantes():
    provider = FakeProvider([respuesta_json()])
    spec = ProjectSpec(nombre="ya-definido")
    Entrevistador(provider, spec).iniciar()

    system_prompt, _ = provider.llamadas[0]
    assert "ya-definido" in system_prompt
    assert "lenguaje" in system_prompt  # aparece entre los faltantes
    assert "[[" not in system_prompt  # placeholders reemplazados


def test_system_prompt_incluye_historial_previo():
    provider = FakeProvider([respuesta_json()])
    historial = "- base_datos: postgresql (en 2 de 3 proyectos)"
    Entrevistador(provider, ProjectSpec(), historial_previo=historial).iniciar()

    system_prompt, _ = provider.llamadas[0]
    assert historial in system_prompt


def test_sin_historial_el_prompt_lo_dice():
    provider = FakeProvider([respuesta_json()])
    Entrevistador(provider, ProjectSpec()).iniciar()
    system_prompt, _ = provider.llamadas[0]
    assert "primer proyecto" in system_prompt


def test_system_prompt_incluye_contexto_documental_precargado():
    provider = FakeProvider([respuesta_json()])
    contexto = '- base_datos: postgresql — evidencia: "usaremos PostgreSQL 15"'
    entrevistador = Entrevistador(provider, ProjectSpec())
    entrevistador.precargar_documentos(contexto)
    entrevistador.iniciar()

    system_prompt, _ = provider.llamadas[0]
    assert contexto in system_prompt


def test_sin_documentos_el_prompt_lo_dice():
    provider = FakeProvider([respuesta_json()])
    Entrevistador(provider, ProjectSpec()).iniciar()
    system_prompt, _ = provider.llamadas[0]
    assert "no aportó documentos" in system_prompt


def test_json_malformado_reintenta_con_feedback_del_error():
    provider = FakeProvider(
        ["esto no es json", respuesta_json("ahora sí", {"lenguaje": "python"})]
    )
    spec = ProjectSpec()
    respuesta = Entrevistador(provider, spec).iniciar()

    assert respuesta.message_to_user == "ahora sí"
    assert spec.lenguaje == "python"
    assert len(provider.llamadas) == 2
    # el reintento incluye la salida fallida y el feedback del error
    _, mensajes_reintento = provider.llamadas[1]
    assert mensajes_reintento[-2].content == "esto no es json"
    assert "no cumple el contrato" in mensajes_reintento[-1].content
    assert "JSON" in mensajes_reintento[-1].content


def test_update_con_clave_invalida_reintenta():
    provider = FakeProvider(
        [
            respuesta_json("hola", {"campo_inexistente": "x"}),
            respuesta_json("corregido", {"lenguaje": "python"}),
        ]
    )
    spec = ProjectSpec()
    respuesta = Entrevistador(provider, spec).iniciar()

    assert respuesta.message_to_user == "corregido"
    assert spec.lenguaje == "python"
    _, mensajes_reintento = provider.llamadas[1]
    assert "campo_inexistente" in mensajes_reintento[-1].content


def test_agotar_reintentos_escala_al_usuario():
    provider = FakeProvider(["basura"] * MAX_REINTENTOS)
    entrevistador = Entrevistador(provider, ProjectSpec())

    with pytest.raises(ContratoInvalidoError, match="3 intentos"):
        entrevistador.iniciar()
    assert len(provider.llamadas) == MAX_REINTENTOS


def test_reintentos_no_ensucian_el_historial_real():
    provider = FakeProvider(["basura", respuesta_json("ok")])
    entrevistador = Entrevistador(provider, ProjectSpec())
    entrevistador.iniciar()

    # solo el turno del usuario y la respuesta válida final
    assert [m.role for m in entrevistador.historial] == ["user", "assistant"]
    assert json.loads(entrevistador.historial[-1].content)["message_to_user"] == "ok"


def test_tolera_fences_de_markdown_accidentales():
    provider = FakeProvider(["```json\n" + respuesta_json("con fences") + "\n```"])
    respuesta = Entrevistador(provider, ProjectSpec()).iniciar()
    assert respuesta.message_to_user == "con fences"


def test_esquema_con_campos_extra_se_rechaza_y_reintenta():
    con_extra = json.dumps(
        {"message_to_user": "x", "updates": {}, "done": False, "extra": 1}
    )
    provider = FakeProvider([con_extra, respuesta_json("limpio")])
    respuesta = Entrevistador(provider, ProjectSpec()).iniciar()
    assert respuesta.message_to_user == "limpio"
    assert len(provider.llamadas) == 2


def test_conversacion_multiturno_acumula_historial():
    provider = FakeProvider(
        [
            respuesta_json("¿Nombre?"),
            respuesta_json("¿Lenguaje?", {"nombre": "demo"}),
        ]
    )
    entrevistador = Entrevistador(provider, ProjectSpec())
    entrevistador.iniciar()
    entrevistador.responder("se llama demo")

    _, mensajes_segunda = provider.llamadas[1]
    assert len(mensajes_segunda) == 3  # user, assistant, user
    assert mensajes_segunda[-1].content == "se llama demo"
    assert entrevistador.spec.nombre == "demo"
