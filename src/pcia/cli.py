"""Punto de entrada del CLI ``pcia``.

Fase 1: entrevista completa por consola que termina con la especificación
del proyecto guardada como JSON en ``memory/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pcia.agents.analista import DocumentoInvalidoError
from pcia.agents.constructor import ConstruccionError
from pcia.agents.llm_json import ContratoInvalidoError
from pcia.config import ConfigError, cargar_config, crear_provider
from pcia.domain.ports import LLMProviderError
from pcia.orchestrator.loop import (
    CoherenciaNoResueltaError,
    LimiteDeTurnosError,
    Orquestador,
    VerificacionFallidaError,
)

BANNER = """\
Project Constructor IA — entrevista de especificación
Contame qué proyecto querés crear. (Ctrl+C para salir)
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pcia",
        description="Agente inteligente para la creación asistida y auditada "
        "de estructuras de proyectos de software.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Ruta al archivo de configuración (default: config.yaml)",
    )
    parser.add_argument(
        "--docs",
        nargs="+",
        default=[],
        metavar="ARCHIVO",
        help="Documentos del cliente (.md/.txt) para precargar la entrevista",
    )
    args = parser.parse_args(argv)

    try:
        config = cargar_config(args.config)
        provider = crear_provider(config)
    except (ConfigError, LLMProviderError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 1

    nombre_proveedor = config.get("provider", "")
    modelo = (config.get(nombre_proveedor) or {}).get("model")
    orquestador = Orquestador(
        provider,
        memory_dir=Path(config.get("memory_dir", "memory")),
        entrada=input,
        salida=lambda texto: print(f"\n{texto}\n"),
        docs=[Path(doc) for doc in args.docs],
        proveedor=f"{nombre_proveedor}:{modelo}" if modelo else nombre_proveedor or None,
    )

    print(BANNER)
    try:
        orquestador.ejecutar()
    except KeyboardInterrupt:
        print("\nEntrevista cancelada por el usuario.")
        return 130
    except (
        ConstruccionError,
        ContratoInvalidoError,
        CoherenciaNoResueltaError,
        DocumentoInvalidoError,
        LimiteDeTurnosError,
        LLMProviderError,
        VerificacionFallidaError,
    ) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
