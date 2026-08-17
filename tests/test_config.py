"""Tests de carga de configuración y factory de proveedores."""

import pytest

from pcia.adapters.anthropic_api import AnthropicAPIProvider
from pcia.adapters.openai_compat import OpenAICompatProvider
from pcia.config import ConfigError, cargar_config, cargar_limites, crear_provider
from pcia.orchestrator.loop import LimitesCiclo


def test_cargar_config_valida(tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text("provider: openai_compat\nmemory_dir: memoria\n", encoding="utf-8")
    config = cargar_config(ruta)
    assert config["provider"] == "openai_compat"
    assert config["memory_dir"] == "memoria"


def test_config_inexistente_levanta_error(tmp_path):
    with pytest.raises(ConfigError, match="No existe"):
        cargar_config(tmp_path / "no-esta.yaml")


def test_config_malformada_levanta_error(tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text("provider: [sin cerrar", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformado"):
        cargar_config(ruta)


def test_factory_openai_compat():
    provider = crear_provider(
        {
            "provider": "openai_compat",
            "openai_compat": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:14b"},
        }
    )
    assert isinstance(provider, OpenAICompatProvider)


def test_factory_openai_compat_sin_base_url_levanta_error():
    with pytest.raises(ConfigError, match="base_url"):
        crear_provider({"provider": "openai_compat", "openai_compat": {"model": "m"}})


def test_factory_anthropic_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-test")
    provider = crear_provider(
        {"provider": "anthropic_api", "anthropic_api": {"model": "claude-sonnet-4-6"}}
    )
    assert isinstance(provider, AnthropicAPIProvider)


def test_factory_claude_subscription(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    from pcia.adapters.claude_subscription import ClaudeSubscriptionProvider

    provider = crear_provider({"provider": "claude_subscription", "claude_subscription": {}})
    assert isinstance(provider, ClaudeSubscriptionProvider)


def test_proveedor_desconocido_levanta_error():
    with pytest.raises(ConfigError, match="Proveedor desconocido"):
        crear_provider({"provider": "gemini"})


def test_config_del_repo_es_valida():
    """El config.yaml versionado en el repo debe cargar sin errores."""
    config = cargar_config("config.yaml")
    assert config["provider"] in ("anthropic_api", "openai_compat", "claude_subscription")


# --- límites de ciclo configurables --------------------------------------------


def test_cargar_limites_sin_seccion_usa_los_defaults():
    assert cargar_limites({}) == LimitesCiclo()


def test_cargar_limites_aplica_overrides():
    limites = cargar_limites({"limites": {"max_turnos_entrevista": 10}})
    assert limites.max_turnos_entrevista == 10
    assert limites.max_ciclos_coherencia == LimitesCiclo().max_ciclos_coherencia


def test_cargar_limites_clave_desconocida_falla():
    with pytest.raises(ConfigError, match="max_turnos"):
        cargar_limites({"limites": {"max_turnos": 5}})


def test_cargar_limites_valor_no_positivo_falla():
    with pytest.raises(ConfigError, match="entero positivo"):
        cargar_limites({"limites": {"max_turnos_entrevista": 0}})


def test_cargar_limites_valor_no_entero_falla():
    with pytest.raises(ConfigError, match="entero positivo"):
        cargar_limites({"limites": {"max_turnos_entrevista": "treinta"}})


def test_config_del_repo_tiene_limites_validos():
    config = cargar_config("config.yaml")
    assert cargar_limites(config) == LimitesCiclo()
