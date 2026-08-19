"""Tests del loop del orquestador con FakeProvider e IO simulada."""

import json

import pytest

from conftest import FakeProvider
from pcia.agents.constructor import Constructor, DestinoInvalidoError
from pcia.agents.interviewer import Entrevistador
from pcia.domain.models import ProjectSpec
from pcia.orchestrator.loop import (
    MAX_TURNOS_ENTREVISTA,
    CoherenciaNoResueltaError,
    LimitesCiclo,
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

# Variante que dispara la regla determinística serverless-websockets (roja).
UPDATES_INCOHERENTES = {
    **UPDATES_COMPLETOS,
    "descripcion": "chat con websockets en tiempo real",
    "infraestructura": "aws lambda (serverless)",
}

# Variante que dispara la regla api-sin-autenticacion (amarilla, asumible).
UPDATES_SIN_AUTH = {**UPDATES_COMPLETOS, "autenticacion": "ninguna"}


def crear_orquestador(provider, entradas, tmp_path, docs=None, limites=None, spec_inicial=None):
    entradas = iter(entradas)
    salidas: list[str] = []
    orq = Orquestador(
        provider,
        memory_dir=tmp_path / "memory",
        entrada=lambda _prompt: next(entradas),
        salida=salidas.append,
        docs=docs,
        limites=limites,
        spec_inicial=spec_inicial,
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
    # omitidos obligatorios (docker) ⇒ el estado agregado es inconcluso
    assert registro["estado_final"] == "inconcluso"
    assert any("Estado final de la entrega: ❓ inconcluso" in s for s in salidas)
    # métricas de la corrida persistidas
    assert registro["duracion_segundos"] >= 0
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


# --- checkpoint de progreso (retomar sin volver a cero) -----------------------


def test_checkpoint_se_borra_al_completar_con_exito(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq, _ = crear_orquestador(
        provider, ["", str(tmp_path / "proyecto")], tmp_path
    )

    orq.ejecutar()

    assert orq.ruta_checkpoint is None


def test_checkpoint_conserva_el_progreso_ante_una_falla(tmp_path):
    """El bug reportado: sin esto, cualquier falla obligaba a reempezar la
    entrevista de cero, aunque ya se hubiera respondido casi todo."""
    provider = FakeProvider(
        [respuesta_json("¿Y el lenguaje?", {"nombre": "mi-api"})]
        + [respuesta_json("¿y?")] * MAX_TURNOS_ENTREVISTA
    )
    orq, _ = crear_orquestador(
        provider, ["sigo"] * (MAX_TURNOS_ENTREVISTA + 1), tmp_path
    )

    with pytest.raises(LimiteDeTurnosError):
        orq.ejecutar()

    ruta = orq.ruta_checkpoint
    assert ruta is not None and ruta.exists()
    guardado = json.loads(ruta.read_text(encoding="utf-8"))
    # el primer update sí se aplicó antes de que la entrevista se estancara
    assert guardado["nombre"] == "mi-api"


def test_spec_inicial_precarga_la_entrevista_y_solo_pregunta_lo_que_falta(tmp_path):
    """Retomar un checkpoint (o corregir un dato ya respondido) arranca con
    la spec pre-llena: el Entrevistador solo tiene que completar lo que
    falta, no volver a preguntar todo desde cero."""
    spec_parcial = ProjectSpec(**{k: v for k, v in UPDATES_COMPLETOS.items() if k != "ci_cd"})
    provider = FakeProvider(
        [respuesta_json("Solo falta el CI/CD.", {"ci_cd": "github actions"}, done=True)]
    )
    entrevistador = Entrevistador(provider, spec_parcial)

    entrevistador.iniciar()

    system_prompt, _ = provider.llamadas[0]
    assert "ci_cd" in system_prompt  # es el único campo que figura como faltante
    assert '"nombre": "mi api"' in system_prompt  # el resto ya está precargado
    assert spec_parcial.ci_cd == "github actions"


def test_limites_configurables_acortan_el_ciclo(tmp_path):
    """Un ``LimitesCiclo`` propio corta la entrevista antes que el default:
    prueba que el override de config.yaml realmente llega al loop."""
    limites = LimitesCiclo(max_turnos_entrevista=2)
    provider = FakeProvider([respuesta_json("¿y?")] * 3)
    orq, _ = crear_orquestador(provider, ["sigo"] * 3, tmp_path, limites=limites)

    with pytest.raises(LimiteDeTurnosError, match="2 turnos"):
        orq.ejecutar()
    # 1 llamada de ``iniciar()`` + 2 de ``responder()`` (una por turno del loop)
    assert len(provider.llamadas) == 3


def test_sin_limites_explicitos_usa_los_defaults_del_sistema(tmp_path):
    orq, _ = crear_orquestador(FakeProvider([]), [], tmp_path)
    assert orq._limites == LimitesCiclo()


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
    # confirma la spec; el hallazgo rojo solo ofrece corregir (acepta la
    # corrección propuesta con enter); confirma la resolución (sin ajuste);
    # elige destino
    entradas = ["", "", "", str(tmp_path / "proyecto")]
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


def test_hallazgo_resuelto_en_la_ultima_ronda_permite_construir(tmp_path):
    """Regresión de un bug real: la última ronda de auditoría permitida
    reportaba los hallazgos y el ciclo escalaba directo a
    ``CoherenciaNoResueltaError`` sin darle turno al usuario para
    resolverlos (solo pasaba en las rondas intermedias). Con
    ``max_ciclos_coherencia=1`` la única ronda posible es también la
    última: tiene que poder resolverse igual que cualquier otra."""
    limites = LimitesCiclo(max_ciclos_coherencia=1)
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_INCOHERENTES, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1 (única ronda de resolución)
            respuesta_json(
                "Listo, paso la infraestructura a contenedores.",
                {"infraestructura": "docker"},
                done=True,
            ),
            SIN_HALLAZGOS_LLM,  # auditoría final de confirmación: ya coherente
            DOCS_LLM,
        ]
    )
    entradas = ["", "", "", str(tmp_path / "proyecto")]
    orq, _ = crear_orquestador(provider, entradas, tmp_path, limites=limites)

    ruta = orq.ejecutar()

    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert registro["spec"]["infraestructura"] == "docker"


def test_riesgo_amarillo_asumido_queda_documentado_y_no_bloquea(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_SIN_AUTH, done=True),
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
    assert riesgos[0].startswith("api-sin-autenticacion:")
    assert registro["resoluciones"][0]["resolucion"] == "asumido"
    assert any("Riesgo asumido" in s for s in salidas)
    # propagación: el riesgo asumido se advierte también en la entrega
    assert any("riesgo(s) asumido(s)" in s for s in salidas)


def test_hallazgo_rojo_no_es_asumible_y_abortar_cancela(tmp_path):
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_INCOHERENTES, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1: dispara serverless-websockets (rojo)
        ]
    )
    # confirma la spec; ante el hallazgo bloqueante decide abortar
    orq, _ = crear_orquestador(provider, ["", "abortar"], tmp_path)

    with pytest.raises(CoherenciaNoResueltaError, match="abortó.*serverless-websockets"):
        orq.ejecutar()


def test_hallazgos_llm_sin_regla_no_bloquean_el_ciclo(tmp_path):
    """El pase LLM del Auditor es consultivo (ver Auditor.pendientes() y
    ``_normalizar_hallazgo_llm``): aunque marque severidad rojo/amarillo,
    solo lo que confirma la matriz determinística de reglas puede iniciar
    el loop interactivo. Sin esto, un hallazgo solo-LLM que se reformula
    con un id distinto en cada ronda (comportamiento real observado en uso)
    nunca converge dentro del límite de ciclos."""
    hallazgo_solo_llm = json.dumps(
        {
            "hallazgos": [
                {
                    "id": "gestion-secretos-insegura",
                    "severidad": "rojo",
                    "mensaje": "x",
                    "correccion_propuesta": "y",
                }
            ]
        }
    )
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            hallazgo_solo_llm,  # auditoría única: hallazgo solo-LLM, no bloquea
            DOCS_LLM,
        ]
    )
    # sin turnos para el hallazgo LLM: si el ciclo intentara resolverlo,
    # ``next(entradas)`` fallaría con StopIteration
    entradas = ["", str(tmp_path / "proyecto")]
    orq, _ = crear_orquestador(provider, entradas, tmp_path)

    ruta = orq.ejecutar()

    assert ruta.exists()


def test_ajuste_tras_resolver_hallazgo_no_se_confunde_con_el_siguiente(tmp_path):
    """Regresión de un bug real detectado en uso: el Entrevistador suele
    cerrar su confirmación con una pregunta propia, y con más de un hallazgo
    pendiente el orquestador pasaba directo al siguiente sin darle al
    usuario turno para responderla — su respuesta terminaba interpretada
    como la decisión sobre el hallazgo siguiente. Acá el usuario responde
    con un AJUSTE (no una confirmación) justo después de resolver el primer
    hallazgo, y se verifica que ese texto viaja al Entrevistador como
    ajuste — no se pisa con la pregunta del segundo hallazgo."""
    updates_doble = {
        **UPDATES_COMPLETOS,
        "descripcion": "chat con websockets en tiempo real",
        "infraestructura": "aws lambda (serverless)",
        "autenticacion": "ninguna",
    }
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", updates_doble, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1: ambos hallazgos son de regla
            respuesta_json(  # resuelve el rojo (serverless-websockets)
                "Listo, paso a contenedores. ¿Confirmás?",
                {"infraestructura": "docker"},
                done=True,
            ),
            respuesta_json(  # el ajuste del usuario tras esa confirmación
                "Entendido, agrego JWT.", {"autenticacion": "jwt"}, done=True
            ),
            respuesta_json(  # resuelve el amarillo (api-sin-autenticacion)
                "Listo, ya está.", {}, done=True
            ),
            SIN_HALLAZGOS_LLM,  # auditoría 2: ya coherente
            DOCS_LLM,
        ]
    )
    entradas = [
        "",  # confirma la spec
        "",  # hallazgo rojo: aplica la corrección propuesta
        "mejor agreguemos JWT ya",  # AJUSTE, no una confirmación
        "",  # confirma tras aplicar el ajuste
        "",  # hallazgo amarillo: elige corregir (no asumir)
        "",  # cómo resolverlo: aplica la corrección propuesta
        "",  # confirma la resolución del amarillo
        str(tmp_path / "proyecto"),
    ]
    orq, _ = crear_orquestador(provider, entradas, tmp_path)

    ruta = orq.ejecutar()

    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert registro["spec"]["infraestructura"] == "docker"
    assert registro["spec"]["autenticacion"] == "jwt"
    assert len(provider.llamadas) == 7  # se consumió cada respuesta enlatada
    # el ajuste llegó al Entrevistador como ajuste, no como decisión del
    # hallazgo siguiente
    _, mensajes_ajuste = provider.llamadas[3]
    assert "pide un ajuste sobre cómo se resolvió" in mensajes_ajuste[-1].content
    assert "mejor agreguemos JWT ya" in mensajes_ajuste[-1].content


def test_confirmacion_de_hallazgo_esta_acotada(tmp_path):
    """Si el usuario nunca confirma, el sistema sigue igual tras el límite
    (mismo principio que el resto de los loops acotados: nunca cuelga)."""
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_INCOHERENTES, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1
            respuesta_json("Corrijo.", {"infraestructura": "docker"}, done=True),
            respuesta_json("Sigo sin confirmar.", {}, done=True),
            respuesta_json("Sigo sin confirmar.", {}, done=True),
            respuesta_json("Sigo sin confirmar.", {}, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 2: ya coherente (infraestructura corregida)
            DOCS_LLM,
        ]
    )
    limites = LimitesCiclo(max_ajustes_por_hallazgo=3)
    entradas = [
        "",  # confirma la spec
        "",  # hallazgo rojo: aplica la corrección propuesta
        "sigo sin confirmar 1",
        "sigo sin confirmar 2",
        "sigo sin confirmar 3",  # se agota el límite: sigue de todos modos
        str(tmp_path / "proyecto"),
    ]
    orq, _ = crear_orquestador(provider, entradas, tmp_path, limites=limites)

    ruta = orq.ejecutar()

    assert ruta.exists()  # no se cuelga ni escala: sigue tras agotar el límite
    assert len(provider.llamadas) == 8


def test_coherencia_no_resuelta_escala_tras_el_limite(tmp_path):
    """El usuario tiene que poder responder a los hallazgos de TODAS las
    rondas, incluida la última: el ciclo hace ``max_ciclos`` rondas de
    resolución y una auditoría final de confirmación antes de escalar."""
    sin_cambios = respuesta_json("Tomo nota pero no cambio nada.")
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_INCOHERENTES, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1
            sin_cambios,  # repregunta 1: no corrige
            SIN_HALLAZGOS_LLM,  # auditoría 2
            sin_cambios,  # repregunta 2: no corrige
            SIN_HALLAZGOS_LLM,  # auditoría 3
            sin_cambios,  # repregunta 3: no corrige
            SIN_HALLAZGOS_LLM,  # auditoría final de confirmación (sigue sin resolver)
        ]
    )
    # el hallazgo rojo pide dos entradas por ciclo: cómo resolverlo y la
    # confirmación de la resolución (3 ciclos con repregunta antes de escalar)
    entradas = ["", "", "", "", "", "", ""]
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
    # docker y ruff simulados ok ⇒ aprobado pleno
    assert registro["estado_final"] == "aprobado"


def test_linter_opcional_ausente_degrada_a_advertencias(tmp_path, monkeypatch):
    from pcia.agents import verificador as modulo_verificador

    simular_docker(monkeypatch, codigos={})
    # docker presente, ruff ausente: el linter opcional queda omitido
    monkeypatch.setattr(
        modulo_verificador, "_binario_disponible", lambda nombre: nombre == "docker"
    )
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
        ]
    )
    orq, salidas = crear_orquestador(provider, ["", str(tmp_path / "proyecto")], tmp_path)

    ruta = orq.ejecutar()

    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert registro["estado_final"] == "aprobado_con_advertencias"
    assert any("omitido (opcional)" in s for s in salidas)


def test_riesgo_asumido_degrada_el_estado_final(tmp_path, monkeypatch):
    simular_docker(monkeypatch, codigos={})  # verificación en verde pleno
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_SIN_AUTH, done=True),
            SIN_HALLAZGOS_LLM,  # auditoría 1: dispara api-sin-autenticacion
            SIN_HALLAZGOS_LLM,  # auditoría 2: el riesgo asumido ya no se reporta
            DOCS_LLM,
        ]
    )
    orq, _ = crear_orquestador(provider, ["", "s", str(tmp_path / "proyecto")], tmp_path)

    ruta = orq.ejecutar()

    registro = json.loads(ruta.read_text(encoding="utf-8"))
    # la verificación aprobó, pero el riesgo asumido pesa en la entrega
    assert registro["verificacion"]["profundos"]
    assert registro["estado_final"] == "aprobado_con_advertencias"


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
    # la corrección toca el Dockerfile: se confirma antes de re-ejecutarlo
    orq, salidas = crear_orquestador(provider, ["", str(proyecto), ""], tmp_path)

    ruta = orq.ejecutar()  # entrega sin más preguntas: la corrección resolvió

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
    # confirma cada re-ejecución del Dockerfile corregido; tras agotar los
    # ciclos, decide entregar igual
    orq, salidas = crear_orquestador(
        provider, ["", str(tmp_path / "proyecto"), "", "", "s"], tmp_path
    )

    ruta = orq.ejecutar()

    assert sum("Corrigiendo fallas de la verificación profunda" in s for s in salidas) == 2
    assert any("Entrega con errores" in s for s in salidas)
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    assert len(registro["correcciones_build"]) == 2
    estados = {c["archivo"]: c["estado"] for c in registro["verificacion"]["profundos"]}
    assert estados["docker-build"] == "error"


def test_dockerfile_reescrito_no_se_ejecuta_sin_confirmacion(tmp_path, monkeypatch):
    simular_docker(monkeypatch, codigos={"docker build": 1})
    provider = FakeProvider(
        [
            respuesta_json("Resumen.", UPDATES_COMPLETOS, done=True),
            SIN_HALLAZGOS_LLM,
            DOCS_LLM,
            CORRECCION_BUILD,  # reescribe el Dockerfile
        ]
    )
    # rechaza ejecutar el Dockerfile corregido; luego no entrega con errores
    orq, salidas = crear_orquestador(
        provider, ["", str(tmp_path / "proyecto"), "n", "n"], tmp_path
    )

    with pytest.raises(VerificacionFallidaError):
        orq.ejecutar()
    assert any("no se ejecutó" in s for s in salidas)
    # un solo build: el Dockerfile corregido nunca corrió
    assert sum("Corrigiendo fallas" in s for s in salidas) == 1


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


# --- reconocimiento de confirmaciones en lenguaje natural -------------------------


@pytest.mark.parametrize(
    "respuesta",
    [
        "", "  ", "s", "si", "Sí", "ok", "dale", "listo", "no", "Nada",
        "seguir", "continuemos", "adelante", "perfecto",
        "dame los archivos, no hay nada más que hacer",
        "no hay nada que ajustar",
        "así está bien, avancemos",
        "sin cambios",
    ],
)
def test_se_reconocen_como_confirmacion(respuesta):
    """La interfaz es conversacional: el usuario responde en lenguaje natural,
    no con la tecla que sugiere el prompt (ver docs/IA-COWORK.md §3.2)."""
    from pcia.orchestrator.loop import es_confirmacion

    assert es_confirmacion(respuesta)


@pytest.mark.parametrize(
    "respuesta",
    [
        "cambiá la base de datos a mysql",
        "quiero seguir usando postgres pero con replicas",
        "no me gusta, usá otra arquitectura",
        "agregá autenticación por OAuth",
    ],
)
def test_un_ajuste_real_no_se_confunde_con_confirmacion(respuesta):
    from pcia.orchestrator.loop import es_confirmacion

    assert not es_confirmacion(respuesta)
