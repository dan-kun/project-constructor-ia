"""Agente Analista de Documentos (Fase 6).

Lee documentación aportada por el cliente (requerimientos, actas de reunión,
mails exportados) ANTES de la entrevista y extrae propuestas para la spec,
cada una con su evidencia textual. No escribe la spec: el Entrevistador
confirma cada propuesta con el usuario (proponer, no asumir), igual que con
las preferencias históricas del Aprendizaje.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from pcia.agents.llm_json import consultar_con_contrato
from pcia.domain.models import ProjectSpec
from pcia.domain.ports import ChatMessage, LLMProvider

RUTA_PROMPT = Path(__file__).parent / "prompts" / "analista.md"

# Loop de recursos acotado: los documentos largos se truncan con marca explícita.
MAX_CARACTERES_POR_DOC = 15_000
EXTENSIONES_SOPORTADAS = (".md", ".txt")


class DocumentoInvalidoError(Exception):
    """Un documento aportado no puede usarse (formato, lectura o vacío)."""


class PropuestaCampo(BaseModel):
    """Un valor propuesto para un campo de la spec, con su evidencia textual."""

    model_config = ConfigDict(extra="forbid")

    valor: str = Field(min_length=1)
    evidencia: str = Field(min_length=1)


class AnalisisDocumentos(BaseModel):
    """Contrato de salida del Analista."""

    model_config = ConfigDict(extra="forbid")

    propuestas: dict[str, PropuestaCampo] = Field(default_factory=dict)
    notas: list[str] = Field(default_factory=list)
    preguntas_abiertas: list[str] = Field(default_factory=list)

    def resumen_para_entrevista(self) -> str:
        """Texto plano que el Entrevistador recibe como contexto documental."""
        lineas: list[str] = []
        if self.propuestas:
            lineas.append("Propuestas extraídas (confirmar con el usuario):")
            lineas.extend(
                f'- {campo}: {p.valor} — evidencia: "{p.evidencia}"'
                for campo, p in self.propuestas.items()
            )
        if self.notas:
            lineas.append("Requerimientos adicionales mencionados en los documentos:")
            lineas.extend(f"- {nota}" for nota in self.notas)
        if self.preguntas_abiertas:
            lineas.append("Puntos que la documentación no define:")
            lineas.extend(f"- {pregunta}" for pregunta in self.preguntas_abiertas)
        return "\n".join(lineas) or (
            "La documentación no aportó datos técnicos utilizables."
        )


class Analista:
    """Extrae propuestas para la spec desde la documentación del cliente."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._plantilla = RUTA_PROMPT.read_text(encoding="utf-8")

    def analizar(self, rutas: Sequence[Path]) -> AnalisisDocumentos:
        documentos = leer_documentos(rutas)
        system_prompt = self._plantilla.replace("[[DOCUMENTOS]]", documentos).replace(
            "[[CAMPOS_REQUERIDOS]]", ", ".join(ProjectSpec.CAMPOS_REQUERIDOS)
        )
        mensajes = [
            ChatMessage(
                role="user",
                content="Analizá los documentos según tus instrucciones.",
            )
        ]
        analisis, _ = consultar_con_contrato(
            self._provider,
            system_prompt,
            mensajes,
            AnalisisDocumentos,
            postproceso=_validar_campos_propuestos,
        )
        return analisis


def leer_documentos(rutas: Sequence[Path]) -> str:
    """Lee y concatena los documentos, validando formato y contenido."""
    if not rutas:
        raise DocumentoInvalidoError("No se indicó ningún documento para analizar.")
    secciones = []
    for ruta in map(Path, rutas):
        if ruta.suffix.lower() not in EXTENSIONES_SOPORTADAS:
            raise DocumentoInvalidoError(
                f"Formato no soportado: {ruta.name} "
                f"(se aceptan: {', '.join(EXTENSIONES_SOPORTADAS)})."
            )
        try:
            contenido = ruta.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DocumentoInvalidoError(f"No se pudo leer {ruta}: {exc}") from exc
        if not contenido:
            raise DocumentoInvalidoError(f"El documento {ruta.name} está vacío.")
        if len(contenido) > MAX_CARACTERES_POR_DOC:
            contenido = (
                contenido[:MAX_CARACTERES_POR_DOC]
                + f"\n[documento truncado a {MAX_CARACTERES_POR_DOC} caracteres]"
            )
        secciones.append(f"### {ruta.name}\n\n{contenido}")
    return "\n\n".join(secciones)


def _validar_campos_propuestos(analisis: AnalisisDocumentos) -> None:
    invalidos = sorted(set(analisis.propuestas) - set(ProjectSpec.CAMPOS_REQUERIDOS))
    if invalidos:
        raise ValueError(
            f"Campos inválidos en propuestas: {', '.join(invalidos)}. "
            f"Campos permitidos: {', '.join(ProjectSpec.CAMPOS_REQUERIDOS)}"
        )
