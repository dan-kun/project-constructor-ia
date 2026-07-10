"""Tests del Agente Constructor con FakeProvider (sin llamadas reales)."""

import json
import re

import pytest

from conftest import FakeProvider
from pcia.agents.constructor import (
    Constructor,
    DestinoInvalidoError,
    PlantillaNoEncontradaError,
    cargar_plantillas,
)
from pcia.agents.llm_json import ContratoInvalidoError
from pcia.domain.models import ProjectSpec

DOCS_OK = json.dumps(
    {"readme_markdown": "# Mi Ápi\n\nREADME generado.\n", "adr_markdown": "# ADR-001\n"}
)


def spec_para(framework: str, **overrides) -> ProjectSpec:
    valores = {
        "nombre": "Mi Ápi",
        "descripcion": "Gestión de turnos",
        "tipo_proyecto": "api",
        "lenguaje": "python",
        "framework": framework,
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


def construir(spec, destino, respuestas_llm=None):
    provider = FakeProvider(respuestas_llm or [DOCS_OK])
    return Constructor(provider).construir(spec, destino), provider


def test_scaffold_fastapi_completo(tmp_path):
    destino = tmp_path / "proyecto"
    resultado, _ = construir(spec_para("FastAPI"), destino)

    assert resultado.stack == "fastapi"
    assert resultado.raiz == str(destino)
    assert (destino / "pyproject.toml").exists()
    assert (destino / "src/mi_api/main.py").exists()
    assert (destino / "tests/test_health.py").exists()
    assert (destino / ".github/workflows/ci.yml").exists()
    assert (destino / "README.md").read_text(encoding="utf-8").startswith("# Mi Ápi")
    assert (destino / "docs/adr/ADR-001-decisiones-iniciales.md").exists()
    assert "README.md" in resultado.archivos

    pyproject = (destino / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "mi-api"' in pyproject
    assert 'description = "Gestión de turnos"' in pyproject


def test_no_quedan_tokens_sin_reemplazar(tmp_path):
    destino = tmp_path / "proyecto"
    construir(spec_para("fastapi"), destino)
    for archivo in destino.rglob("*"):
        if archivo.is_file():
            contenido = archivo.read_text(encoding="utf-8")
            assert not re.search(r"\[\[[A-Z_]+\]\]", contenido), archivo
            assert "[[" not in str(archivo.relative_to(destino))


def test_scaffold_odoo(tmp_path):
    destino = tmp_path / "modulo"
    resultado, _ = construir(spec_para("Odoo", tipo_proyecto="módulo odoo"), destino)

    assert resultado.stack == "odoo"
    manifiesto = (destino / "__manifest__.py").read_text(encoding="utf-8")
    assert '"name": "Mi Ápi"' in manifiesto
    modelo = (destino / "models/models.py").read_text(encoding="utf-8")
    assert '_name = "mi_api.registro"' in modelo
    assert (destino / "security/ir.model.access.csv").exists()


def test_scaffold_nestjs(tmp_path):
    destino = tmp_path / "proyecto"
    resultado, _ = construir(spec_para("NestJS", lenguaje="typescript"), destino)

    assert resultado.stack == "nestjs"
    paquete = json.loads((destino / "package.json").read_text(encoding="utf-8"))
    assert paquete["name"] == "mi-api"
    assert (destino / "src/main.ts").exists()


def test_stack_sin_plantilla_escala(tmp_path):
    with pytest.raises(PlantillaNoEncontradaError, match="fastapi"):
        construir(spec_para("django"), tmp_path / "proyecto")


def test_destino_no_vacio_es_invalido(tmp_path):
    destino = tmp_path / "ocupado"
    destino.mkdir()
    (destino / "algo.txt").write_text("ya hay contenido", encoding="utf-8")

    with pytest.raises(DestinoInvalidoError, match="no está vacío"):
        construir(spec_para("fastapi"), destino)
    # no se pisó nada
    assert (destino / "algo.txt").read_text(encoding="utf-8") == "ya hay contenido"
    assert not (destino / "pyproject.toml").exists()


def test_destino_anidado_se_crea(tmp_path):
    destino = tmp_path / "a" / "b" / "proyecto"
    construir(spec_para("fastapi"), destino)
    assert (destino / "pyproject.toml").exists()


def test_llm_malformado_reintenta_y_no_deja_scaffold_a_medias(tmp_path):
    destino = tmp_path / "proyecto"
    _, provider = construir(spec_para("fastapi"), destino, ["basura", DOCS_OK])
    assert len(provider.llamadas) == 2
    assert (destino / "README.md").exists()


def test_llm_persistentemente_malformado_no_escribe_nada(tmp_path):
    destino = tmp_path / "proyecto"
    provider = FakeProvider(["basura"] * 3)
    with pytest.raises(ContratoInvalidoError):
        Constructor(provider).construir(spec_para("fastapi"), destino)
    assert not destino.exists() or not any(destino.iterdir())


def test_prompt_del_llm_incluye_spec_stack_archivos_y_riesgos(tmp_path):
    spec = spec_para("fastapi")
    spec.riesgos_asumidos.append("api-sin-autenticacion: es interna")
    _, provider = construir(spec, tmp_path / "proyecto")

    system_prompt, _ = provider.llamadas[0]
    assert "Gestión de turnos" in system_prompt  # estado de la spec
    assert '"fastapi"' in system_prompt  # stack de la plantilla
    assert "pyproject.toml" in system_prompt  # archivos generados
    assert "api-sin-autenticacion" in system_prompt  # riesgos asumidos
    assert "[[" not in system_prompt  # placeholders reemplazados


def test_las_plantillas_del_paquete_cargan_y_validan():
    plantillas = cargar_plantillas()
    assert {p.stack for p in plantillas} == {"fastapi", "nestjs", "odoo"}
    # toda plantilla trae su sección de instrucciones determinística
    assert all(p.instrucciones for p in plantillas)


def test_readme_combina_texto_del_llm_con_instrucciones_de_la_plantilla(tmp_path):
    destino = tmp_path / "proyecto"
    construir(spec_para("fastapi"), destino)

    readme = (destino / "README.md").read_text(encoding="utf-8")
    # primero el contexto redactado por el LLM, después los comandos reales
    assert readme.startswith("# Mi Ápi")
    assert "## Cómo ejecutar" in readme
    assert "uvicorn mi_api.main:app --reload" in readme  # tokens renderizados
    assert "docker build -t mi-api ." in readme


def test_resultado_incluye_verificaciones_renderizadas(tmp_path):
    resultado, _ = construir(spec_para("fastapi"), tmp_path / "proyecto")

    por_id = {v.id: v for v in resultado.verificaciones}
    assert por_id["docker-build"].tipo == "docker_build"
    # el token [[PAQUETE]] del smoke test quedó renderizado, con el secreto
    # de fantasía inyectado (la app no importa sin SECRET_KEY)
    assert por_id["smoke-import-app"].comando == [
        "env",
        "SECRET_KEY=solo-smoke-test",
        "python",
        "-c",
        "import mi_api.main",
    ]
    assert por_id["lint-ruff"].requiere == "ruff"


def test_verificacion_sin_comando_falla_temprano(tmp_path):
    ruta = tmp_path / "mala.yaml"
    ruta.write_text(
        "stack: mala\n"
        "detecta: [mala]\n"
        "verificaciones:\n"
        "  - id: sin-comando\n"
        "    tipo: comando\n"
        "archivos:\n"
        '  "a.txt": contenido\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="necesita un comando"):
        cargar_plantillas(tmp_path)


def test_plantilla_con_ruta_insegura_falla_temprano(tmp_path):
    ruta = tmp_path / "mala.yaml"
    ruta.write_text(
        "stack: mala\n"
        "detecta: [mala]\n"
        "archivos:\n"
        '  "../afuera.txt": contenido\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="insegura"):
        cargar_plantillas(tmp_path)
