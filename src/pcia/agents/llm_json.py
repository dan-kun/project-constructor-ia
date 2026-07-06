"""Contrato JSON estricto con reintentos, compartido por todos los agentes.

Patrón transversal del diseño: toda salida de LLM se valida (JSON + Pydantic)
y se reintenta con el error como feedback, con máximo 3 intentos; superado el
límite se escala al usuario con ``ContratoInvalidoError``.
"""

from __future__ import annotations

import json
from typing import Callable, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from pcia.domain.ports import ChatMessage, LLMProvider

MAX_REINTENTOS = 3

TContrato = TypeVar("TContrato", bound=BaseModel)


class ContratoInvalidoError(Exception):
    """El LLM no cumplió el contrato JSON tras agotar los reintentos."""


def consultar_con_contrato(
    provider: LLMProvider,
    system_prompt: str,
    mensajes: Sequence[ChatMessage],
    contrato: type[TContrato],
    postproceso: Callable[[TContrato], None] | None = None,
) -> tuple[TContrato, str]:
    """Consulta al LLM y valida la salida contra ``contrato``.

    ``postproceso`` permite validación/aplicación adicional (por ejemplo,
    aplicar updates a la spec): si levanta ``ValueError`` también se
    reintenta con ese error como feedback. Devuelve la respuesta validada
    y el texto crudo del intento exitoso.
    """
    mensajes = list(mensajes)
    ultimo_error = ""

    for _ in range(MAX_REINTENTOS):
        crudo = provider.generate(system_prompt, mensajes)
        try:
            respuesta = _validar(crudo, contrato)
            if postproceso is not None:
                postproceso(respuesta)
        except ValueError as exc:
            ultimo_error = str(exc)
            # Reintento con el error como feedback, sin ensuciar el historial real.
            mensajes = [
                *mensajes,
                ChatMessage(role="assistant", content=crudo),
                ChatMessage(
                    role="user",
                    content=(
                        "Tu respuesta anterior no cumple el contrato: "
                        f"{ultimo_error}. Respondé de nuevo SOLO con el objeto "
                        "JSON válido, sin texto adicional."
                    ),
                ),
            ]
            continue
        return respuesta, crudo

    raise ContratoInvalidoError(
        f"El modelo no produjo una respuesta válida tras {MAX_REINTENTOS} "
        f"intentos. Último error: {ultimo_error}"
    )


def _validar(crudo: str, contrato: type[TContrato]) -> TContrato:
    """Parsea JSON estricto (tolerando solo fences de markdown accidentales)."""
    texto = crudo.strip()
    if texto.startswith("```"):
        texto = texto.strip("`").strip()
        if texto.startswith("json"):
            texto = texto[len("json"):].strip()
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no es JSON válido ({exc})") from exc
    try:
        return contrato.model_validate(datos)
    except ValidationError as exc:
        detalles = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ValueError(f"no respeta el esquema esperado ({detalles})") from exc
