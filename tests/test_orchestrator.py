"""Tests del loop del orquestador con FakeProvider e IO simulada."""

import json

import pytest

from conftest import FakeProvider
from pcia.orchestrator.loop import (
    MAX_TURNOS_ENTREVISTA,
    LimiteDeTurnosError,
    Orquestador,
)
def respuesta_json(mensaje="ok", updates=None, done=False) -> str:
    return json.dumps(
        {"message_to_user": mensaje, "updates": updates or {}, "done": done},
        ensure_ascii=False,
    )


UPDATES_COMPLETOS = {
    "nombre": "mi api",
    "descripcion": "API de facturación",
    "tipo_proyecto": "api",
    "lenguaje": "python",
    "framework": "fastapi",
    "arquitectura": "hexagonal",
    "base_datos": "postgresql",
    "autenticacion": "jwt",
    "gestion_secretos": "variables de entorno",
    "infraestructura": "docker",
    "ci_cd": "github actions",
    "alcance": "mvp",
}


def crear_orquestador(provider, entradas, tmp_path):
    entradas = iter(entradas)
    salidas: list[str] = []
    orq = Orquestador(
        provider,
        memory_dir=tmp_path / "memory",
        entrada=lambda _prompt: next(entradas),
        salida=salidas.append,
    )
    return orq, salidas


def test_ciclo_completo_guarda_la_spec(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("¿Qué querés construir?"),
            respuesta_json("Listo, resumen final.", UPDATES_COMPLETOS, done=True),
        ]
    )
    orq, salidas = crear_orquestador(provider, ["una API de facturación en python"], tmp_path)

    ruta = orq.ejecutar()

    assert ruta.exists()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["nombre"] == "mi api"
    assert datos["framework"] == "fastapi"
    assert ruta.name.startswith("mi-api-")  # slug del nombre
    # pasó por las fases pendientes dejando constancia
    assert any("auditoria" in s for s in salidas)
    assert any("Especificación guardada" in s for s in salidas)


def test_done_prematuro_recibe_feedback_y_continua(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Cerramos acá.", {"nombre": "x"}, done=True),  # prematuro
            respuesta_json("Perdón, sigo.", UPDATES_COMPLETOS, done=True),
        ]
    )
    orq, _ = crear_orquestador(provider, [], tmp_path)

    ruta = orq.ejecutar()

    assert ruta.exists()
    # el segundo llamado al LLM incluye la corrección automática del orquestador
    _, mensajes = provider.llamadas[1]
    assert "faltan campos requeridos" in mensajes[-1].content


def test_entrevista_sin_fin_corta_por_limite_de_turnos(tmp_path):
    provider = FakeProvider([respuesta_json("¿y?")] * (MAX_TURNOS_ENTREVISTA + 1))
    orq, _ = crear_orquestador(provider, ["sigo"] * (MAX_TURNOS_ENTREVISTA + 1), tmp_path)

    with pytest.raises(LimiteDeTurnosError):
        orq.ejecutar()
