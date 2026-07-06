"""Tests del loop del orquestador con FakeProvider e IO simulada."""

import json

import pytest

from conftest import FakeProvider
from pcia.agents.constructor import Constructor, DestinoInvalidoError
from pcia.orchestrator.loop import (
    MAX_TURNOS_ENTREVISTA,
    CoherenciaNoResueltaError,
    LimiteDeTurnosError,
    Orquestador,
    VerificacionFallidaError,
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
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert registro["spec"]["nombre"] == "mi api"
    assert registro["spec"]["framework"] == "fastapi"
    assert registro["stack"] == "fastapi"
    assert registro["ruta_proyecto"] == str(proyecto)
    assert registro["verificacion"] is not None
    assert ruta.name.startswith("mi-api-")  # slug del nombre
    # proyecto generado en el destino elegido
    assert orq.ruta_proyecto == proyecto
    assert (proyecto / "pyproject.toml").exists()
    assert (proyecto / "README.md").exists()
    assert (proyecto / "docs/adr/ADR-001-decisiones-iniciales.md").exists()
    assert any("🟢" in s for s in salidas)  # semáforo verde reportado
    assert any("Proyecto generado" in s for s in salidas)
    assert any("Verificación de sintaxis" in s for s in salidas)
    assert any("Especificación y registro" in s for s in salidas)
    assert any("Memoria actualizada" in s for s in salidas)


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

    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert registro["spec"]["infraestructura"] == "docker"
    assert registro["spec"]["riesgos_asumidos"] == []
    # el hallazgo quedó registrado como corregido
    assert registro["resoluciones"][0]["hallazgo"]["id"] == "serverless-websockets"
    assert registro["resoluciones"][0]["resolucion"] == "corregido"
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

    registro = json.loads(ruta.read_text(encoding="utf-8"))
    riesgos = registro["spec"]["riesgos_asumidos"]
    assert len(riesgos) == 1
    assert riesgos[0].startswith("serverless-websockets:")
    assert registro["resoluciones"][0]["resolucion"] == "asumido"
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


# --- fase de verificación -------------------------------------------------------


def construir_con_archivo_roto(monkeypatch, contenido_roto="{roto"):
    """Parchea al Constructor para inyectar un JSON roto en el scaffold."""
    original = Constructor.construir

    def construir(self, spec, destino):
        resultado = original(self, spec, destino)
        (destino / "config_extra.json").write_text(contenido_roto, encoding="utf-8")
        return resultado

    monkeypatch.setattr(Constructor, "construir", construir)


def test_verificacion_corrige_archivo_roto_y_entrega(tmp_path, monkeypatch):
    construir_con_archivo_roto(monkeypatch)
    correccion = json.dumps({"contenido_corregido": '{"ok": true}\n'})
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
            correccion,  # corrección del archivo roto
        ]
    )
    proyecto = tmp_path / "proyecto"
    orq, salidas = crear_orquestador(provider, [str(proyecto)], tmp_path)

    ruta = orq.ejecutar()

    assert (proyecto / "config_extra.json").read_text(encoding="utf-8") == '{"ok": true}\n'
    assert any("Corrigiendo config_extra.json" in s for s in salidas)
    assert any("config_extra.json corregido." in s for s in salidas)
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert not [
        c for c in registro["verificacion"]["chequeos"] if c["estado"] == "error"
    ]


def test_verificacion_persistente_entrega_igual_si_el_usuario_acepta(
    tmp_path, monkeypatch
):
    construir_con_archivo_roto(monkeypatch)
    correccion_rota = json.dumps({"contenido_corregido": "{sigue roto"})
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
            *[correccion_rota] * 3,  # 3 intentos de corrección fallidos
        ]
    )
    proyecto = tmp_path / "proyecto"
    orq, salidas = crear_orquestador(provider, [str(proyecto), "s"], tmp_path)

    ruta = orq.ejecutar()  # no levanta: el usuario aceptó entregar igual

    assert any("Entrega con errores" in s for s in salidas)
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    errores = [
        c for c in registro["verificacion"]["chequeos"] if c["estado"] == "error"
    ]
    assert [c["archivo"] for c in errores] == ["config_extra.json"]


def test_verificacion_persistente_aborta_si_el_usuario_no_acepta(tmp_path, monkeypatch):
    construir_con_archivo_roto(monkeypatch)
    correccion_rota = json.dumps({"contenido_corregido": "{sigue roto"})
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
            *[correccion_rota] * 3,
        ]
    )
    orq, _ = crear_orquestador(provider, [str(tmp_path / "proyecto"), "n"], tmp_path)

    with pytest.raises(VerificacionFallidaError, match="config_extra.json"):
        orq.ejecutar()


# --- fase de aprendizaje --------------------------------------------------------


def test_entrevista_arranca_precargada_con_el_historial(tmp_path):
    # primer proyecto: deja un registro en la memoria
    provider1 = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq1, salidas1 = crear_orquestador(provider1, [str(tmp_path / "p1")], tmp_path)
    orq1.ejecutar()
    assert any("Preferencias detectadas" in s for s in salidas1)

    # segundo proyecto: el entrevistador arranca con las preferencias históricas
    provider2 = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq2, _ = crear_orquestador(provider2, [str(tmp_path / "p2")], tmp_path)
    orq2.ejecutar()

    system_prompt, _ = provider2.llamadas[0]
    assert "postgresql (en 1 de 1 proyectos)" in system_prompt
