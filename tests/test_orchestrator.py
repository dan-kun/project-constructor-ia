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


@pytest.fixture(autouse=True)
def sin_herramientas_externas(monkeypatch):
    """Los tests de flujo no deben tocar Docker ni linters reales.

    Sin binarios disponibles, toda la capa profunda se reporta 'omitido'.
    Los tests que simulan la capa profunda pisan estos parches.
    """
    from pcia.agents import verificador as modulo_verificador

    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: False)
    monkeypatch.setattr(
        modulo_verificador,
        "_ejecutar",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no debería ejecutarse ningún comando externo")
        ),
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


def crear_orquestador(provider, entradas, tmp_path, docs=None):
    entradas = iter(entradas)
    salidas: list[str] = []
    orq = Orquestador(
        provider,
        memory_dir=tmp_path / "memory",
        entrada=lambda _prompt: next(entradas),
        salida=salidas.append,
        docs=docs,
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
    entradas = ["una API de facturación en python", "", str(proyecto)]
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
    # capa profunda: sin docker/linters en el entorno de test, todo omitido
    assert any("docker no está disponible" in s for s in salidas)
    assert registro["verificacion"]["profundos"]
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
    orq, _ = crear_orquestador(provider, ["", str(tmp_path / "proyecto")], tmp_path)

    ruta = orq.ejecutar()

    assert ruta.exists()
    # el segundo llamado al LLM incluye la corrección automática del orquestador
    _, mensajes = provider.llamadas[1]
    assert "faltan campos requeridos" in mensajes[-1].content


def test_ajuste_en_la_confirmacion_vuelve_al_entrevistador(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            respuesta_json("Cambié la base a mysql.", {"base_datos": "mysql"}, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    # en la confirmación pide un ajuste; en la segunda confirma con enter
    entradas = ["quiero mysql en vez de postgresql", "", str(tmp_path / "proyecto")]
    orq, _ = crear_orquestador(provider, entradas, tmp_path)

    ruta = orq.ejecutar()

    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert registro["spec"]["base_datos"] == "mysql"
    # el pedido llegó al entrevistador como ajuste explícito
    _, mensajes = provider.llamadas[1]
    assert "ajuste antes de cerrar" in mensajes[-1].content
    assert "mysql" in mensajes[-1].content


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
    # confirma la spec; no asume el riesgo; acepta la corrección; elige destino
    entradas = ["", "n", "", str(tmp_path / "proyecto")]
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
    orq, salidas = crear_orquestador(
        provider, ["", "s", str(tmp_path / "proyecto")], tmp_path
    )

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
    entradas = ["", "n", "", "n", ""]
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
    orq, salidas = crear_orquestador(provider, ["", str(ocupado), str(libre)], tmp_path)

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
    orq, _ = crear_orquestador(provider, ["", *[str(ocupado)] * 3], tmp_path)

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
    orq, salidas = crear_orquestador(provider, ["", str(proyecto)], tmp_path)

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
    orq, salidas = crear_orquestador(provider, ["", str(proyecto), "s"], tmp_path)

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
    orq, _ = crear_orquestador(provider, ["", str(tmp_path / "proyecto"), "n"], tmp_path)

    with pytest.raises(VerificacionFallidaError, match="config_extra.json"):
        orq.ejecutar()


def simular_docker(monkeypatch, codigos):
    """Habilita docker/ruff falsos con resultados programados por comando.

    Un valor lista se consume en orden (ej.: ``[1, 0]`` = falla la primera
    vez y pasa la segunda), útil para el ciclo de corrección de builds.
    """
    import subprocess

    from pcia.agents import verificador as modulo_verificador

    comandos = []

    def ejecutar(comando, cwd, timeout):
        comandos.append(list(comando))
        codigo = codigos.get(" ".join(comando[:2]), 0)
        if isinstance(codigo, list):
            codigo = codigo.pop(0) if codigo else 0
        return subprocess.CompletedProcess(comando, codigo, stdout="", stderr="build roto")

    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: True)
    monkeypatch.setattr(modulo_verificador, "_ejecutar", ejecutar)
    return comandos


def test_verificacion_profunda_ok_entrega_normalmente(tmp_path, monkeypatch):
    comandos = simular_docker(monkeypatch, codigos={})
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq, salidas = crear_orquestador(provider, ["", str(tmp_path / "proyecto")], tmp_path)

    ruta = orq.ejecutar()

    assert ["docker", "build"] in [c[:2] for c in comandos]
    assert ["docker", "run"] in [c[:2] for c in comandos]
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    estados = {c["archivo"]: c["estado"] for c in registro["verificacion"]["profundos"]}
    assert estados["docker-build"] == "ok"
    assert estados["smoke-import-app"] == "ok"
    assert any("Verificación profunda" in s for s in salidas)


CORRECCION_BUILD = json.dumps(
    {
        "diagnostico": "npm ci requiere lockfile; se reemplaza por npm install",
        "correcciones": [
            {"archivo": "Dockerfile", "contenido_corregido": "FROM python:3.12-slim\n"}
        ],
    },
    ensure_ascii=False,
)
SIN_CAMBIOS_BUILD = json.dumps(
    {"diagnostico": "es una falla del entorno, no del scaffold", "correcciones": []},
    ensure_ascii=False,
)


def test_build_fallido_se_corrige_y_entrega(tmp_path, monkeypatch):
    # el build falla la primera vez y pasa tras la corrección (Fase 7)
    simular_docker(monkeypatch, codigos={"docker build": [1, 0]})
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
            CORRECCION_BUILD,
        ]
    )
    proyecto = tmp_path / "proyecto"
    orq, salidas = crear_orquestador(provider, ["", str(proyecto)], tmp_path)

    ruta = orq.ejecutar()  # entrega sin preguntar: la corrección resolvió

    dockerfile = (proyecto / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile == "FROM python:3.12-slim\n"
    assert any("Diagnóstico: npm ci requiere lockfile" in s for s in salidas)
    assert any("el defecto puede estar en la plantilla" in s for s in salidas)
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert registro["correcciones_build"] == [
        "npm ci requiere lockfile; se reemplaza por npm install"
    ]
    estados = {c["archivo"]: c["estado"] for c in registro["verificacion"]["profundos"]}
    assert estados["docker-build"] == "ok"


def test_correccion_de_build_persistente_agota_ciclos_y_escala(tmp_path, monkeypatch):
    simular_docker(monkeypatch, codigos={"docker build": 1})  # falla siempre
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
            CORRECCION_BUILD,  # intento 1: no alcanza
            CORRECCION_BUILD,  # intento 2: tampoco
        ]
    )
    # tras agotar los ciclos, el usuario decide entregar igual
    orq, salidas = crear_orquestador(
        provider, ["", str(tmp_path / "proyecto"), "s"], tmp_path
    )

    ruta = orq.ejecutar()

    assert sum("Corrigiendo fallas de la verificación profunda" in s for s in salidas) == 2
    assert any("Entrega con errores" in s for s in salidas)
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert len(registro["correcciones_build"]) == 2
    estados = {c["archivo"]: c["estado"] for c in registro["verificacion"]["profundos"]}
    assert estados["docker-build"] == "error"


def test_corrector_sin_cambios_escala_directo_al_usuario(tmp_path, monkeypatch):
    simular_docker(monkeypatch, codigos={"docker build": 1})
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
            SIN_CAMBIOS_BUILD,  # el corrector diagnostica que no es el scaffold
        ]
    )
    orq, salidas = crear_orquestador(provider, ["", str(tmp_path / "proyecto"), "n"], tmp_path)

    with pytest.raises(VerificacionFallidaError, match="docker-build"):
        orq.ejecutar()
    assert any("no propuso cambios" in s for s in salidas)
    # un solo intento de corrección: sin propuesta no se insiste
    assert sum("Corrigiendo fallas" in s for s in salidas) == 1


# --- fase de análisis de documentos ---------------------------------------------


ANALISIS_LLM = json.dumps(
    {
        "propuestas": {
            "base_datos": {
                "valor": "postgresql",
                "evidencia": "usaremos PostgreSQL 15",
            }
        },
        "notas": [],
        "preguntas_abiertas": ["no define autenticación"],
    },
    ensure_ascii=False,
)


def test_con_docs_analiza_primero_y_precarga_la_entrevista(tmp_path):
    doc = tmp_path / "requerimientos.md"
    doc.write_text("El cliente dice: usaremos PostgreSQL 15.", encoding="utf-8")
    provider = FakeProvider(
        [
            ANALISIS_LLM,  # Analista de documentos
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq, salidas = crear_orquestador(
        provider, ["", str(tmp_path / "proyecto")], tmp_path, docs=[doc]
    )

    ruta = orq.ejecutar()

    assert ruta.exists()
    assert any("Análisis de la documentación" in s for s in salidas)
    # el prompt del Analista recibió el documento
    system_analista, _ = provider.llamadas[0]
    assert "usaremos PostgreSQL 15" in system_analista
    # el Entrevistador arrancó con las propuestas del análisis como contexto
    system_entrevista, _ = provider.llamadas[1]
    assert "postgresql" in system_entrevista
    assert "no define autenticación" in system_entrevista


def test_sin_docs_no_llama_al_analista(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq, salidas = crear_orquestador(provider, ["", str(tmp_path / "proyecto")], tmp_path)

    orq.ejecutar()

    # la primera llamada al LLM es la del Entrevistador, no la del Analista
    system_prompt, _ = provider.llamadas[0]
    assert "Agente Entrevistador" in system_prompt
    assert not any("Análisis de la documentación" in s for s in salidas)


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
    orq1, salidas1 = crear_orquestador(provider1, ["", str(tmp_path / "p1")], tmp_path)
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
    orq2, _ = crear_orquestador(provider2, ["", str(tmp_path / "p2")], tmp_path)
    orq2.ejecutar()

    system_prompt, _ = provider2.llamadas[0]
    assert "postgresql (en 1 de 1 proyectos)" in system_prompt
