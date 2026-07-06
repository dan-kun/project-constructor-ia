"""Test end-to-end del CLI con provider falso (sin red, sin consola real)."""

import json

from conftest import FakeProvider
from pcia import cli

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
    entradas = iter(["una api de demo", str(tmp_path / "proyecto")])
    monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

    codigo = cli.main(["--config", str(config)])

    assert codigo == 0
    assert "¿Qué querés construir?" in capsys.readouterr().out
    guardados = list((tmp_path / "memory").glob("demo-*.json"))
    assert len(guardados) == 1
    assert (tmp_path / "proyecto" / "README.md").exists()


def test_main_config_inexistente_devuelve_1(tmp_path, capsys):
    codigo = cli.main(["--config", str(tmp_path / "nada.yaml")])
    assert codigo == 1
    assert "Error de configuración" in capsys.readouterr().err
