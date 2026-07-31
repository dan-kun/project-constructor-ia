"""Punto de entrada del CLI ``pcia``.

Fase 1: entrevista completa por consola que termina con la especificación
del proyecto guardada como JSON en ``memory/``.
"""

from __future__ import annotations

import argparse
import datetime as dt
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
from pcia.texto import slug_kebab
from pcia.transcript import Transcript

BANNER = """\
Project Constructor IA — entrevista de especificación
Contame qué proyecto querés crear. (Ctrl+C para salir)
"""

ERRORES_ESPERADOS = (
    ConstruccionError,
    ContratoInvalidoError,
    CoherenciaNoResueltaError,
    DocumentoInvalidoError,
    LimiteDeTurnosError,
    LLMProviderError,
    VerificacionFallidaError,
)


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

    memory_dir = Path(config.get("memory_dir", "memory"))
    transcript = Transcript()

    def entrada(prompt: str) -> str:
        texto = input(prompt)
        transcript.registrar_entrada(texto)
        return texto

    def salida(texto: str) -> None:
        print(f"\n{texto}\n")
        transcript.registrar_salida(texto)

    nombre_proveedor = config.get("provider", "")
    modelo = (config.get(nombre_proveedor) or {}).get("model")
    orquestador = Orquestador(
        provider,
        memory_dir=memory_dir,
        entrada=entrada,
        salida=salida,
        docs=[Path(doc) for doc in args.docs],
        proveedor=f"{nombre_proveedor}:{modelo}" if modelo else nombre_proveedor or None,
    )

    print(BANNER)
    codigo = 0
    try:
        orquestador.ejecutar()
    except KeyboardInterrupt:
        print("\nEntrevista cancelada por el usuario.")
        codigo = 130
    except ERRORES_ESPERADOS as exc:
        transcript.registrar_error(str(exc))
        print(f"\nError: {exc}", file=sys.stderr)
        codigo = 1
    finally:
        # Se guarda pase lo que pase: el transcript es más útil todavía
        # cuando la sesión escaló con un error, no solo cuando salió bien.
        ruta = _guardar_transcript(transcript, orquestador, memory_dir)
        print(f"Conversación completa guardada en {ruta}")
    return codigo


def _guardar_transcript(transcript: Transcript, orquestador: Orquestador, memory_dir: Path) -> Path:
    marca = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    nombre = slug_kebab(orquestador.spec.nombre or "proyecto")
    ruta = memory_dir / "transcripts" / f"{nombre}-{marca}.txt"
    return transcript.guardar(ruta)


if __name__ == "__main__":
    raise SystemExit(main())
