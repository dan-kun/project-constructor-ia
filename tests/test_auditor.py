"""Tests del Agente Auditor con FakeProvider (sin llamadas reales)."""

import json

import pytest

from conftest import FakeProvider
from pcia.agents.auditor import Auditor, cargar_reglas
from pcia.agents.llm_json import ContratoInvalidoError
from pcia.domain.models import ProjectSpec, Severidad

SIN_HALLAZGOS_LLM = '{"hallazgos": []}'


def spec_base(**overrides) -> ProjectSpec:
    """Spec completa y coherente; los overrides introducen incongruencias."""
    valores = {
        "nombre": "demo",
        "descripcion": "API de gestión de turnos",
        "tipo_proyecto": "api",
        "lenguaje": "python",
        "framework": "fastapi",
        "arquitectura": "capas",
        "base_datos": "postgresql",
        "autenticacion": "jwt",
        "gestion_secretos": "variables de entorno",
        "infraestructura": "docker",
        "ci_cd": "github actions",
        "alcance": "producto interno",
    }
    valores.update(overrides)
    return ProjectSpec(**valores)


def auditar(spec, respuestas_llm=None):
    provider = FakeProvider(respuestas_llm or [SIN_HALLAZGOS_LLM])
    return Auditor(provider).auditar(spec), provider


# --- capa determinística ----------------------------------------------------


def test_spec_coherente_da_semaforo_verde():
    resultado, _ = auditar(spec_base())
    assert resultado.hallazgos == []
    assert resultado.semaforo() is Severidad.VERDE


def test_serverless_mas_websockets_es_rojo():
    spec = spec_base(
        infraestructura="AWS Lambda (serverless)",
        descripcion="chat con websockets en tiempo real",
    )
    resultado, _ = auditar(spec)
    ids = [h.id for h in resultado.hallazgos]
    assert "serverless-websockets" in ids
    assert resultado.semaforo() is Severidad.ROJO
    hallazgo = resultado.hallazgos[ids.index("serverless-websockets")]
    assert hallazgo.origen == "regla"
    assert hallazgo.correccion_propuesta  # toda regla propone corrección


def test_sqlite_mas_alta_concurrencia_es_rojo():
    spec = spec_base(base_datos="sqlite", descripcion="ventas con alta concurrencia")
    resultado, _ = auditar(spec)
    assert "sqlite-alta-concurrencia" in [h.id for h in resultado.hallazgos]


def test_matching_ignora_mayusculas_y_acentos():
    spec = spec_base(base_datos="SQLite", descripcion="se espera ALTO TRÁFICO de escritura")
    resultado, _ = auditar(spec)
    assert "sqlite-alta-concurrencia" in [h.id for h in resultado.hallazgos]


def test_hexagonal_para_prototipo_es_amarillo():
    spec = spec_base(arquitectura="hexagonal", alcance="prototipo descartable")
    resultado, _ = auditar(spec)
    ids = [h.id for h in resultado.hallazgos]
    assert "hexagonal-alcance-minimo" in ids
    assert resultado.semaforo() is Severidad.AMARILLO


def test_secretos_hardcodeados_es_rojo():
    spec = spec_base(gestion_secretos="hardcodeados en el código por ahora")
    resultado, _ = auditar(spec)
    assert "secretos-hardcodeados" in [h.id for h in resultado.hallazgos]


def test_api_sin_autenticacion_es_amarillo():
    spec = spec_base(autenticacion="ninguna")
    resultado, _ = auditar(spec)
    assert "api-sin-autenticacion" in [h.id for h in resultado.hallazgos]


def test_alcance_niega_prototipo_no_dispara_hexagonal_alcance_minimo():
    """Falso positivo real del matching por substring: 'no es un prototipo'
    contiene la palabra 'prototipo'. La exclusión evita el hallazgo."""
    spec = spec_base(
        arquitectura="hexagonal", alcance="no es un prototipo, es un producto a escala"
    )
    resultado, _ = auditar(spec)
    assert "hexagonal-alcance-minimo" not in [h.id for h in resultado.hallazgos]


def test_microservicios_con_base_de_datos_compartida_es_amarillo():
    spec = spec_base(
        arquitectura="microservicios",
        descripcion="tres servicios que comparten una misma base de datos",
    )
    resultado, _ = auditar(spec)
    assert "microservicios-base-datos-compartida" in [h.id for h in resultado.hallazgos]


def test_mongodb_con_transacciones_relacionales_es_amarillo():
    spec = spec_base(
        base_datos="mongodb",
        descripcion="necesita transacciones que crucen varias colecciones",
    )
    resultado, _ = auditar(spec)
    assert "mongodb-uso-relacional" in [h.id for h in resultado.hallazgos]


def test_mongodb_sin_mencion_relacional_no_dispara_la_regla():
    spec = spec_base(base_datos="mongodb", descripcion="catálogo de productos simple")
    resultado, _ = auditar(spec)
    assert "mongodb-uso-relacional" not in [h.id for h in resultado.hallazgos]


def test_riesgo_asumido_no_se_vuelve_a_reportar():
    spec = spec_base(arquitectura="hexagonal", alcance="prototipo")
    spec.riesgos_asumidos.append("hexagonal-alcance-minimo: lo asumo, va a crecer")
    resultado, _ = auditar(spec)
    assert resultado.hallazgos == []
    assert resultado.semaforo() is Severidad.VERDE


# --- pase LLM ---------------------------------------------------------------


def test_hallazgo_llm_se_incorpora_con_origen_llm():
    respuesta = json.dumps(
        {
            "hallazgos": [
                {
                    "id": "django-sin-orm",
                    "severidad": "amarillo",
                    "mensaje": "Elegiste Django pero descartás su ORM.",
                    "correccion_propuesta": "Considerar FastAPI + SQLAlchemy.",
                }
            ]
        }
    )
    resultado, _ = auditar(spec_base(), [respuesta])
    assert len(resultado.hallazgos) == 1
    assert resultado.hallazgos[0].origen == "llm"
    assert resultado.semaforo() is Severidad.VERDE


def test_llm_no_puede_duplicar_un_id_de_regla():
    spec = spec_base(gestion_secretos="hardcodeados en el repo")
    duplicado = json.dumps(
        {
            "hallazgos": [
                {"id": "secretos-hardcodeados", "severidad": "rojo", "mensaje": "repetido"}
            ]
        }
    )
    resultado, _ = auditar(spec, [duplicado])
    assert [h.id for h in resultado.hallazgos] == ["secretos-hardcodeados"]
    assert resultado.hallazgos[0].origen == "regla"


def test_llm_no_puede_reportar_un_riesgo_asumido():
    spec = spec_base()
    spec.riesgos_asumidos.append("latencia-region: asumido")
    reincidente = json.dumps(
        {"hallazgos": [{"id": "latencia-region", "severidad": "amarillo", "mensaje": "x"}]}
    )
    resultado, _ = auditar(spec, [reincidente])
    assert resultado.hallazgos == []


@pytest.mark.parametrize(
    ("id_hallazgo", "campo", "valor"),
    [
        ("password-hashing-ausente", "hashing_contrasenas", "Argon2 con pwdlib"),
        ("cors-no-configurado", "cors", "permitir http://localhost:3000"),
    ],
)
def test_llm_no_repite_control_ya_configurado(id_hallazgo, campo, valor):
    respuesta = json.dumps(
        {"hallazgos": [{"id": id_hallazgo, "severidad": "rojo", "mensaje": "x"}]}
    )
    resultado, _ = auditar(spec_base(**{campo: valor}), [respuesta])
    assert resultado.hallazgos == []


def test_llm_malformado_reintenta_con_feedback():
    resultado, provider = auditar(spec_base(), ["esto no es json", SIN_HALLAZGOS_LLM])
    assert resultado.hallazgos == []
    assert len(provider.llamadas) == 2
    _, mensajes = provider.llamadas[1]
    assert "no cumple el contrato" in mensajes[-1].content


def test_severidad_invalida_reintenta():
    invalida = json.dumps({"hallazgos": [{"id": "x", "severidad": "azul", "mensaje": "m"}]})
    resultado, provider = auditar(spec_base(), [invalida, SIN_HALLAZGOS_LLM])
    assert resultado.hallazgos == []
    assert len(provider.llamadas) == 2


def test_llm_persistentemente_malformado_escala():
    provider = FakeProvider(["basura"] * 3)
    with pytest.raises(ContratoInvalidoError):
        Auditor(provider).auditar(spec_base())


def test_prompt_del_llm_incluye_spec_reglas_y_riesgos():
    spec = spec_base(gestion_secretos="hardcodeados en el repo")
    spec.riesgos_asumidos.append("otro-riesgo: asumido antes")
    _, provider = auditar(spec)
    system_prompt, _ = provider.llamadas[0]
    assert "hardcodeados en el repo" in system_prompt  # estado de la spec
    assert "secretos-hardcodeados" in system_prompt  # hallazgo de regla, para no repetir
    assert "otro-riesgo" in system_prompt  # riesgo asumido, para no reportar
    assert "[[" not in system_prompt  # placeholders reemplazados


# --- carga de reglas ----------------------------------------------------------


def test_las_reglas_del_paquete_cargan_y_validan():
    reglas = cargar_reglas()
    assert len(reglas) >= 5
    assert all(regla.condiciones for regla in reglas)


def test_regla_con_campo_desconocido_falla_temprano(tmp_path):
    ruta = tmp_path / "reglas.yaml"
    ruta.write_text(
        "reglas:\n"
        "  - id: rota\n"
        "    severidad: rojo\n"
        "    mensaje: m\n"
        "    condiciones:\n"
        "      - campos: [campo_inexistente]\n"
        "        contiene: [x]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="campo_inexistente"):
        cargar_reglas(ruta)
