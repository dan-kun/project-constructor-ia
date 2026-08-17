"""Punto de entrada del CLI ``pcia``.

Fase 1: entrevista completa por consola que termina con la especificación
del proyecto guardada como JSON en ``memory/``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from pydantic import ValidationError

from pcia.agents.analista import DocumentoInvalidoError
from pcia.agents.constructor import ConstruccionError
from pcia.agents.llm_json import ContratoInvalidoError
from pcia.config import ConfigError, cargar_config, cargar_limites, crear_provider
from pcia.domain.models import ProjectSpec
from pcia.domain.ports import LLMProviderError
from pcia.memoria import Memoria
from pcia.orchestrator.loop import (
    CoherenciaNoResueltaError,
    LimiteDeTurnosError,
    Orquestador,
    VerificacionFallidaError,
)
from pcia.stats import generar_reporte
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
    parser.add_argument(
        "--resume",
        metavar="CHECKPOINT",
        help="Retoma una corrida interrumpida (o corrige algo ya respondido) "
        "desde un checkpoint guardado en memory/en-progreso/ — la ruta exacta "
        "se imprime cuando una corrida termina en error o se cancela",
    )
    subparsers = parser.add_subparsers(dest="comando")
    stats_parser = subparsers.add_parser(
        "stats", help="Agregados de los proyectos ya construidos (memory/)"
    )
    stats_parser.add_argument(
        "--memory-dir",
        default=None,
        help="Directorio de memoria (default: el de --config, o 'memory')",
    )
    args = parser.parse_args(argv)

    if args.comando == "stats":
        return _comando_stats(args)

    try:
        config = cargar_config(args.config)
        provider = crear_provider(config)
        limites = cargar_limites(config)
    except (ConfigError, LLMProviderError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 1

    spec_inicial = None
    if args.resume:
        try:
            spec_inicial = ProjectSpec.model_validate_json(
                Path(args.resume).read_text(encoding="utf-8")
            )
        except OSError as exc:
            print(f"No se pudo leer el checkpoint {args.resume}: {exc}", file=sys.stderr)
            return 1
        except ValidationError as exc:
            print(f"El checkpoint {args.resume} no es válido: {exc}", file=sys.stderr)
            return 1
        print(
            f"Retomando desde {args.resume}: "
            f"{len(spec_inicial.campos_faltantes())} campo(s) por completar."
        )

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
        limites=limites,
        spec_inicial=spec_inicial,
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
        # Si algo falló (o se canceló) antes de terminar, el orquestador
        # dejó un checkpoint con lo respondido hasta ahí — sin esto, la
        # única forma de seguir era volver a empezar toda la entrevista.
        if orquestador.ruta_checkpoint is not None:
            print(
                f"Progreso guardado en {orquestador.ruta_checkpoint}. "
                f"Para retomarlo: pcia --resume {orquestador.ruta_checkpoint}"
            )
    return codigo


def _comando_stats(args: argparse.Namespace) -> int:
    memory_dir = Path(args.memory_dir) if args.memory_dir else None
    if memory_dir is None:
        try:
            config = cargar_config(args.config)
        except ConfigError:
            config = {}
        memory_dir = Path(config.get("memory_dir", "memory"))
    registros = Memoria(memory_dir).cargar_registros()
    print(generar_reporte(registros))
    return 0


def _guardar_transcript(
    transcript: Transcript, orquestador: Orquestador, memory_dir: Path
) -> Path:
    marca = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    nombre = slug_kebab(orquestador.spec.nombre or "proyecto")
    ruta = memory_dir / "transcripts" / f"{nombre}-{marca}.txt"
    return transcript.guardar(ruta)


if __name__ == "__main__":
    raise SystemExit(main())
