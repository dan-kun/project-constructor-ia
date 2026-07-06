"""Tests del loop del orquestador con FakeProvider e IO simulada."""

import json

import pytest

from conftest import FakeProvider
from pcia.agents.constructor import DestinoInvalidoError
from pcia.orchestrator.loop import (
    MAX_TURNOS_ENTREVISTA,
    CoherenciaNoResueltaError,
    LimiteDeTurnosError,
    Orquestador,
)

SIN_HALLAZGOS_LLM = '{"hallazgos": []}'
DOCS_LLM = json.dumps({"readme_markdown": "# mi api\n", "adr_markdown": "# ADR-001\n"})


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
    "arquitectura": "capas",
    "base_datos": "postgresql",
    "autenticacion": "jwt",
    "gestion_secretos": "variables de entorno",
    "infraestructura": "docker",
    "ci_cd": "github actions",
    "alcance": "mvp",
}

# Variante que dispara la regla determinística serverless-websockets.
UPDATES_INCOHERENTES = {
    **UPDATES_COMPLETOS,
    "descripcion": "chat con websockets en tiempo real",
    "infraestructura": "aws lambda (serverless)",
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


def test_ciclo_completo_guarda_spec_y_genera_proyecto(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("¿Qué querés construir?"),
            respuesta_json("Listo, resumen final.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,  # pase LLM del Auditor
            DOCS_LLM,  # README y ADR del Constructor
        ]
    )
    proyecto = tmp_path / "proyecto"
    entradas = ["una API de facturación en python", str(proyecto)]
    orq, salidas = crear_orquestador(provider, entradas, tmp_path)

    ruta = orq.ejecutar()

    assert ruta.exists()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["nombre"] == "mi api"
    assert datos["framework"] == "fastapi"
    assert ruta.name.startswith("mi-api-")  # slug del nombre
    # proyecto generado en el destino elegido
    assert orq.ruta_proyecto == proyecto
    assert (proyecto / "pyproject.toml").exists()
    assert (proyecto / "README.md").exists()
    assert (proyecto / "docs/adr/ADR-001-decisiones-iniciales.md").exists()
    assert any("🟢" in s for s in salidas)  # semáforo verde reportado
    assert any("Proyecto generado" in s for s in salidas)
    assert any("verificacion" in s for s in salidas)  # stub de la fase 4
    assert any("Especificación guardada" in s for s in salidas)


def test_done_prematuro_recibe_feedback_y_continua(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Cerramos acá.", {"nombre": "x"}, done=True),  # prematuro
            respuesta_json("Perdón, sigo.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq, _ = crear_orquestador(provider, [str(tmp_path / "proyecto")], tmp_path)

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


# --- ciclo de coherencia ------------------------------------------------------


def test_hallazgo_corregido_via_entrevistador_y_reauditoria(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_INCOHERENTES, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1: la regla determinística dispara igual
            respuesta_json(  # repregunta: el entrevistador corrige la spec
                "Listo, paso la infraestructura a contenedores.",
                {"infraestructura": "docker"},
                done=True,
            ),
            SIN_HALLAZGOS_LLM,  # auditoría 2: ya coherente
            DOCS_LLM,
        ]
    )
    # no asume el riesgo; acepta la corrección propuesta; elige destino
    entradas = ["n", "", str(tmp_path / "proyecto")]
    orq, salidas = crear_orquestador(provider, entradas, tmp_path)

    ruta = orq.ejecutar()

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["infraestructura"] == "docker"
    assert datos["riesgos_asumidos"] == []
    assert any("🔴" in s for s in salidas)  # el hallazgo se reportó
    # la repregunta llevó el hallazgo y la corrección al entrevistador
    _, mensajes = provider.llamadas[2]
    assert "serverless-websockets" in mensajes[-1].content
    assert "Corrección propuesta" in mensajes[-1].content


def test_riesgo_asumido_queda_documentado_y_no_bloquea(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_INCOHERENTES, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1
            SIN_HALLAZGOS_LLM,  # auditoría 2: el riesgo asumido ya no se reporta
            DOCS_LLM,
        ]
    )
    orq, salidas = crear_orquestador(provider, ["s", str(tmp_path / "proyecto")], tmp_path)

    ruta = orq.ejecutar()

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert len(datos["riesgos_asumidos"]) == 1
    assert datos["riesgos_asumidos"][0].startswith("serverless-websockets:")
    assert any("Riesgo asumido" in s for s in salidas)


def test_coherencia_no_resuelta_escala_tras_el_limite(tmp_path):
    sin_cambios = respuesta_json("Tomo nota pero no cambio nada.")
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_INCOHERENTES, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1
            sin_cambios,  # repregunta 1: no corrige
            SIN_HALLAZGOS_LLM,  # auditoría 2
            sin_cambios,  # repregunta 2: no corrige
            SIN_HALLAZGOS_LLM,  # auditoría 3 (última)
        ]
    )
    entradas = ["n", "", "n", ""]
    orq, _ = crear_orquestador(provider, entradas, tmp_path)

    with pytest.raises(CoherenciaNoResueltaError, match="serverless-websockets"):
        orq.ejecutar()


# --- fase de construcción -----------------------------------------------------


def test_destino_ocupado_reintenta_y_luego_construye(tmp_path):
    ocupado = tmp_path / "ocupado"
    ocupado.mkdir()
    (ocupado / "algo.txt").write_text("x", encoding="utf-8")
    libre = tmp_path / "libre"

    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq, salidas = crear_orquestador(provider, [str(ocupado), str(libre)], tmp_path)

    orq.ejecutar()

    assert orq.ruta_proyecto == libre
    assert (libre / "pyproject.toml").exists()
    assert any("no está vacío" in s for s in salidas)


def test_destino_siempre_ocupado_escala(tmp_path):
    ocupado = tmp_path / "ocupado"
    ocupado.mkdir()
    (ocupado / "algo.txt").write_text("x", encoding="utf-8")

    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
        ]
    )
    orq, _ = crear_orquestador(provider, [str(ocupado)] * 3, tmp_path)

    with pytest.raises(DestinoInvalidoError, match="3 intentos"):
        orq.ejecutar()
