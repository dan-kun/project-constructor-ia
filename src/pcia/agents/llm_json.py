"""Contrato JSON estricto con reintentos, compartido por todos los agentes.

Patrón transversal del diseño: toda salida de LLM se valida (JSON + Pydantic)
y se reintenta con el error como feedback, con máximo 3 intentos; superado el
límite se escala al usuario con ``ContratoInvalidoError``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from pcia.domain.ports import ChatMessage, LLMProvider

MAX_REINTENTOS = 3

TContrato = TypeVar("TContrato", bound=BaseModel)

# Diagnóstico opt-in: con modelos locales chicos, entender POR QUÉ un
# contrato falla repetidamente requiere ver los intercambios crudos, que
# ``ContratoInvalidoError`` no conserva (solo guarda el último error). Nunca
# activo por defecto: system_prompt/mensajes pueden llevar contenido del
# cliente (documentos, notas) que no debe quedar en disco sin que alguien
# lo pida explícitamente.
VAR_DEBUG = "PCIA_DEBUG_LLM"
VAR_DEBUG_ARCHIVO = "PCIA_DEBUG_LLM_ARCHIVO"
ARCHIVO_DEBUG_DEFAULT = "pcia-debug-llm.jsonl"


class ContratoInvalidoError(Exception):
    """El LLM no cumplió el contrato JSON tras agotar los reintentos."""


def _debug_habilitado() -> bool:
    return os.environ.get(VAR_DEBUG, "").strip().lower() in ("1", "true", "si")


def _registrar_intercambio(
    system_prompt: str,
    mensajes: Sequence[ChatMessage],
    crudo: str,
    intento: int,
    error: str | None,
) -> None:
    if not _debug_habilitado():
        return
    ruta = Path(os.environ.get(VAR_DEBUG_ARCHIVO, "") or ARCHIVO_DEBUG_DEFAULT)
    entrada = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "intento": intento,
        "system_prompt": system_prompt,
        "mensajes": [m.model_dump() for m in mensajes],
        "respuesta_cruda": crudo,
        "error": error,
    }
    # Best-effort: un fallo al escribir el log de diagnóstico no debe tumbar
    # la consulta real al LLM.
    try:
        with ruta.open("a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    except OSError:
        pass


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

    Con ``PCIA_DEBUG_LLM=1`` en el entorno, cada intento (éxito o fallo) se
    apila en ``PCIA_DEBUG_LLM_ARCHIVO`` (default ``pcia-debug-llm.jsonl``)
    para diagnosticar por qué un modelo falla el contrato repetidamente.
    """
    mensajes = list(mensajes)
    ultimo_error = ""

    for intento in range(1, MAX_REINTENTOS + 1):
        crudo = provider.generate(system_prompt, mensajes)
        try:
            respuesta = _validar(crudo, contrato)
            if postproceso is not None:
                postproceso(respuesta)
        except ValueError as exc:
            ultimo_error = str(exc)
            _registrar_intercambio(system_prompt, mensajes, crudo, intento, ultimo_error)
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
        _registrar_intercambio(system_prompt, mensajes, crudo, intento, None)
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
