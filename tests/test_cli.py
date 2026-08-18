"""Test end-to-end del CLI con provider falso (sin red, sin consola real)."""

import json

import pytest

from conftest import FakeProvider
from pcia import cli


@pytest.fixture(autouse=True)
def sin_herramientas_externas(monkeypatch):
    """El flujo completo no debe tocar Docker ni linters reales."""
    from pcia.agents import verificador as modulo_verificador

    monkeypatch.setattr(modulo_verificador, "_binario_disponible", lambda _: False)

UPDATES_COMPLETOS = {
    "nombre": "demo",
    "descripcion": "demo",
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


def respuesta_json(mensaje, updates=None, done=False):
    return json.dumps({"message_to_user": mensaje, "updates": updates or {}, "done": done})


def test_main_ejecuta_entrevista_completa(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"provider: openai_compat\nmemory_dir: {tmp_path / 'memory'}\n", encoding="utf-8"
    )
    provider = FakeProvider(
        [
            respuesta_json("¿Qué querés construir?"),
            respuesta_json("Listo.", UPDATES_COMPLETOS, done=True),
            '{"hallazgos": []}',  # pase LLM del Auditor
            json.dumps({"readme_markdown": "# demo\n", "adr_markdown": "# ADR-001\n"}),
        ]
    )
    monkeypatch.setattr(cli, "crear_provider", lambda _config: provider)
    entradas = iter(["una api de demo", "", str(tmp_path / "proyecto")])
    monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

    codigo = cli.main(["--config", str(config)])

    assert codigo == 0
    assert "¿Qué querés construir?" in capsys.readouterr().out
    guardados = list((tmp_path / "memory").glob("demo-*.json"))
    assert len(guardados) == 1
    assert (tmp_path / "proyecto" / "README.md").exists()
    # el registro guarda qué proveedor ejecutó la corrida
    registro = json.loads(guardados[0].read_text(encoding="utf-8"))
    assert registro["proveedor"] == "openai_compat"


def test_main_config_inexistente_devuelve_1(tmp_path, capsys):
    codigo = cli.main(["--config", str(tmp_path / "nada.yaml")])
    assert codigo == 1
    assert "Error de configuración" in capsys.readouterr().err


def test_main_con_docs_analiza_antes_de_entrevistar(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"provider: openai_compat\nmemory_dir: {tmp_path / 'memory'}\n", encoding="utf-8"
    )
    doc = tmp_path / "requerimientos.md"
    doc.write_text("El cliente quiere una API en Python.", encoding="utf-8")
    analisis = json.dumps(
        {
            "propuestas": {
                "lenguaje": {"valor": "python", "evidencia": "una API en Python"}
            },
            "notas": [],
            "preguntas_abiertas": [],
        }
    )
    provider = FakeProvider(
        [
            analisis,  # Analista de documentos
            respuesta_json("Listo.", UPDATES_COMPLETOS, done=True),
            '{"hallazgos": []}',
            json.dumps({"readme_markdown": "# demo\n", "adr_markdown": "# ADR-001\n"}),
        ]
    )
    monkeypatch.setattr(cli, "crear_provider", lambda _config: provider)
    entradas = iter(["", str(tmp_path / "proyecto")])
    monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

    codigo = cli.main(["--config", str(config), "--docs", str(doc)])

    assert codigo == 0
    assert "Análisis de la documentación" in capsys.readouterr().out


# --- pcia stats ---------------------------------------------------------------


def test_stats_sin_config_ni_memory_dir_usa_default_memory(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    codigo = cli.main(["stats"])
    assert codigo == 0
    assert "Todavía no hay proyectos" in capsys.readouterr().out


def test_stats_con_memory_dir_explicito_reporta_agregados(tmp_path, capsys):
    from pcia.domain.models import ProjectSpec, RegistroProyecto
    from pcia.memoria import Memoria

    memory_dir = tmp_path / "memory"
    Memoria(memory_dir).guardar(
        RegistroProyecto(
            fecha="2026-01-01T00:00:00",
            spec=ProjectSpec(nombre="demo"),
            stack="fastapi",
            estado_final="aprobado",
        )
    )

    codigo = cli.main(["stats", "--memory-dir", str(memory_dir)])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "Proyectos registrados: 1" in salida
    assert "fastapi" in salida


def test_stats_usa_memory_dir_de_config_yaml(tmp_path, capsys):
    from pcia.domain.models import ProjectSpec, RegistroProyecto
    from pcia.memoria import Memoria

    memory_dir = tmp_path / "memoria-del-config"
    Memoria(memory_dir).guardar(
        RegistroProyecto(fecha="2026-01-01T00:00:00", spec=ProjectSpec(nombre="x"))
    )
    config = tmp_path / "config.yaml"
    config.write_text(f"provider: openai_compat\nmemory_dir: {memory_dir}\n", encoding="utf-8")

    codigo = cli.main(["--config", str(config), "stats"])

    assert codigo == 0
    assert "Proyectos registrados: 1" in capsys.readouterr().out


# --- checkpoint / --resume (retomar sin volver a cero) ------------------------


def test_main_resume_precarga_la_spec_y_solo_pregunta_lo_que_falta(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"provider: openai_compat\nmemory_dir: {tmp_path / 'memory'}\n", encoding="utf-8"
    )
    checkpoint = tmp_path / "checkpoint.json"
    spec_parcial = {k: v for k, v in UPDATES_COMPLETOS.items() if k != "ci_cd"}
    checkpoint.write_text(json.dumps(spec_parcial), encoding="utf-8")

    provider = FakeProvider(
        [
            respuesta_json("Listo.", {"ci_cd": "github actions"}, done=True),
            '{"hallazgos": []}',
            json.dumps({"readme_markdown": "# demo\n", "adr_markdown": "# ADR-001\n"}),
        ]
    )
    monkeypatch.setattr(cli, "crear_provider", lambda _config: provider)
    entradas = iter(["", str(tmp_path / "proyecto")])
    monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

    codigo = cli.main(["--config", str(config), "--resume", str(checkpoint)])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "Retomando desde" in salida
    assert "1 campo(s) por completar" in salida
    # el Entrevistador arrancó con solo ci_cd como campo faltante: el resto
    # de la spec ya venía precargada del checkpoint, no se repreguntó de más
    system_prompt, _ = provider.llamadas[0]
    assert "Campos requeridos que aún faltan: ci_cd" in system_prompt
    assert '"lenguaje": "python"' in system_prompt


def test_main_resume_con_archivo_inexistente_devuelve_1(tmp_path, capsys):
    codigo = cli.main(["--resume", str(tmp_path / "no-existe.json")])
    assert codigo == 1
    assert "No se pudo leer el checkpoint" in capsys.readouterr().err


def test_main_resume_con_json_invalido_devuelve_1(tmp_path, capsys):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text('{"nombre": 123}', encoding="utf-8")  # tipo inválido

    codigo = cli.main(["--resume", str(checkpoint)])

    assert codigo == 1
    assert "no es válido" in capsys.readouterr().err


def test_main_falla_deja_checkpoint_y_avisa_como_retomar(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"provider: openai_compat\nmemory_dir: {tmp_path / 'memory'}\n"
        "limites:\n  max_turnos_entrevista: 1\n",
        encoding="utf-8",
    )
    provider = FakeProvider([respuesta_json("¿Y?"), respuesta_json("¿Y más?")])
    monkeypatch.setattr(cli, "crear_provider", lambda _config: provider)
    entradas = iter(["sigo"])
    monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

    codigo = cli.main(["--config", str(config)])

    assert codigo == 1
    salida = capsys.readouterr().out
    assert "Progreso guardado en" in salida
    assert "pcia --resume" in salida
    checkpoints = list((tmp_path / "memory" / "en-progreso").glob("checkpoint-*.json"))
    assert len(checkpoints) == 1


def test_main_con_doc_invalido_devuelve_1(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"provider: openai_compat\nmemory_dir: {tmp_path / 'memory'}\n", encoding="utf-8"
    )
    monkeypatch.setattr(cli, "crear_provider", lambda _config: FakeProvider([]))

    codigo = cli.main(
        ["--config", str(config), "--docs", str(tmp_path / "no-existe.md")]
    )

    assert codigo == 1
    assert "No se pudo leer" in capsys.readouterr().err
