"""Test end-to-end del CLI con provider falso (sin red, sin consola real)."""

import json

from conftest import FakeProvider
from pcia import cli

UPDATES_COMPLETOS = {
    "nombre": "demo",
    "descripcion": "demo",
    "tipo_proyecto": "cli",
    "lenguaje": "python",
    "framework": "ninguno",
    "arquitectura": "simple",
    "base_datos": "ninguna",
    "autenticacion": "ninguna",
    "gestion_secretos": "variables de entorno",
    "infraestructura": "local",
    "ci_cd": "github actions",
    "alcance": "prototipo",
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
        ]
    )
    monkeypatch.setattr(cli, "crear_provider", lambda _config: provider)
    monkeypatch.setattr("builtins.input", lambda *_: "un cli de demo")

    codigo = cli.main(["--config", str(config)])

    assert codigo == 0
    assert "¿Qué querés construir?" in capsys.readouterr().out
    guardados = list((tmp_path / "memory").glob("demo-*.json"))
    assert len(guardados) == 1


def test_main_config_inexistente_devuelve_1(tmp_path, capsys):
    codigo = cli.main(["--config", str(tmp_path / "nada.yaml")])
    assert codigo == 1
    assert "Error de configuración" in capsys.readouterr().err
