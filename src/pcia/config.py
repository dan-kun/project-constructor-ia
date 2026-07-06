"""Carga de config.yaml y factory de proveedores de IA.

El proveedor es intercambiable desde la configuración: cambiar de modelo o
de familia de API no requiere tocar código (ver docs/DISENO.md §6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pcia.domain.ports import LLMProvider

PROVEEDORES_VALIDOS = ("anthropic_api", "openai_compat", "claude_subscription")


class ConfigError(Exception):
    """La configuración es inválida o está incompleta."""


def cargar_config(ruta: str | Path) -> dict[str, Any]:
    ruta = Path(ruta)
    if not ruta.exists():
        raise ConfigError(f"No existe el archivo de configuración: {ruta}")
    try:
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml malformado: {exc}") from exc
    if not isinstance(datos, dict):
        raise ConfigError("config.yaml debe ser un mapeo YAML.")
    return datos


def crear_provider(config: dict[str, Any]) -> LLMProvider:
    """Instancia el adaptador de LLMProvider indicado en la configuración."""
    nombre = config.get("provider")
    if nombre not in PROVEEDORES_VALIDOS:
        raise ConfigError(
            f"Proveedor desconocido: {nombre!r}. "
            f"Opciones válidas: {', '.join(PROVEEDORES_VALIDOS)}"
        )
    seccion = config.get(nombre) or {}

    # Imports locales para no pagar dependencias de adaptadores que no se usan.
    if nombre == "anthropic_api":
        from pcia.adapters.anthropic_api import AnthropicAPIProvider

        return AnthropicAPIProvider(
            model=seccion.get("model", "claude-sonnet-4-6"),
            max_tokens=seccion.get("max_tokens", 2000),
        )
    if nombre == "openai_compat":
        from pcia.adapters.openai_compat import OpenAICompatProvider

        base_url = seccion.get("base_url")
        model = seccion.get("model")
        if not base_url or not model:
            raise ConfigError(
                "openai_compat requiere 'base_url' y 'model' en config.yaml."
            )
        return OpenAICompatProvider(
            base_url=base_url,
            model=model,
            api_key=seccion.get("api_key", "sin-key"),
        )
    from pcia.adapters.claude_subscription import ClaudeSubscriptionProvider

    return ClaudeSubscriptionProvider(model=seccion.get("model"))
