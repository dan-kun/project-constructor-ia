"""Agente Verificador.

Fase 4a: verificación de sintaxis de todo archivo generado, por extensión
(py, json, yaml, toml, xml, csv). Lo que no tiene verificador se reporta
como ``omitido`` (builds, linters y smoke tests llegan en la Fase 4b).

Ante una falla, ``corregir_archivo`` pide al LLM la corrección mínima con
contrato JSON estricto; el ciclo corrección→re-verificación (acotado) lo
maneja el orquestador.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

try:  # tomllib es stdlib desde Python 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depende de la versión de Python
    import tomli as tomllib

from pcia.agents.llm_json import consultar_con_contrato
from pcia.domain.models import Chequeo, ResultadoVerificacion
from pcia.domain.ports import ChatMessage, LLMProvider

RUTA_PROMPT = Path(__file__).parent / "prompts" / "verificador.md"


class CorreccionArchivo(BaseModel):
    """Contrato de salida del pase LLM del Verificador."""

    model_config = ConfigDict(extra="forbid")

    contenido_corregido: str = Field(min_length=1)


def _chequear_python(texto: str) -> None:
    compile(texto, "<generado>", "exec")


def _chequear_json(texto: str) -> None:
    json.loads(texto)


def _chequear_yaml(texto: str) -> None:
    yaml.safe_load(texto)


def _chequear_toml(texto: str) -> None:
    tomllib.loads(texto)


def _chequear_xml(texto: str) -> None:
    ET.fromstring(texto)


def _chequear_csv(texto: str) -> None:
    filas = list(csv.reader(io.StringIO(texto)))
    if not filas:
        raise ValueError("CSV vacío")
    columnas = {len(fila) for fila in filas if fila}
    if len(columnas) > 1:
        raise ValueError(f"cantidad de columnas dispareja entre filas: {sorted(columnas)}")


VERIFICADORES: dict[str, Callable[[str], None]] = {
    ".py": _chequear_python,
    ".json": _chequear_json,
    ".yaml": _chequear_yaml,
    ".yml": _chequear_yaml,
    ".toml": _chequear_toml,
    ".xml": _chequear_xml,
    ".csv": _chequear_csv,
}


class Verificador:
    """Verifica el proyecto generado y corrige archivos con el LLM."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._plantilla = RUTA_PROMPT.read_text(encoding="utf-8")

    def verificar(self, raiz: Path) -> ResultadoVerificacion:
        """Chequea la sintaxis de todos los archivos bajo ``raiz``."""
        chequeos = [
            self.verificar_archivo(raiz, str(archivo.relative_to(raiz)))
            for archivo in sorted(raiz.rglob("*"))
            if archivo.is_file()
        ]
        return ResultadoVerificacion(chequeos=chequeos)

    def verificar_archivo(self, raiz: Path, relativa: str) -> Chequeo:
        verificador = VERIFICADORES.get(Path(relativa).suffix.lower())
        if verificador is None:
            return Chequeo(
                archivo=relativa,
                estado="omitido",
                detalle="sin verificador de sintaxis para esta extensión (Fase 4b)",
            )
        try:
            verificador((raiz / relativa).read_text(encoding="utf-8"))
        except Exception as exc:
            return Chequeo(archivo=relativa, estado="error", detalle=str(exc))
        return Chequeo(archivo=relativa, estado="ok")

    def corregir_archivo(self, raiz: Path, relativa: str, error: str) -> None:
        """Pide al LLM la corrección mínima y reescribe el archivo."""
        ruta = raiz / relativa
        system_prompt = (
            self._plantilla.replace("[[RUTA]]", relativa)
            .replace("[[ERROR]]", error)
            .replace("[[CONTENIDO]]", ruta.read_text(encoding="utf-8"))
        )
        mensajes = [
            ChatMessage(role="user", content="Corregí el archivo según tus instrucciones.")
        ]
        correccion, _ = consultar_con_contrato(
            self._provider, system_prompt, mensajes, CorreccionArchivo
        )
        ruta.write_text(correccion.contenido_corregido, encoding="utf-8")
