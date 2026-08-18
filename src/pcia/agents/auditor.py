"""Agente Auditor.

Detección híbrida de incongruencias técnicas en la ProjectSpec:

1. **Capa determinística**: matriz de incompatibilidades en YAML
   (``rules/incompatibilidades.yaml``), matching por palabras clave.
2. **Pase LLM**: análisis de lo no catalogado, con contrato JSON estricto
   y reintentos (mismo patrón que el resto de los agentes).

Los riesgos ya asumidos por el usuario (``spec.riesgos_asumidos``) no se
vuelven a reportar en ninguna de las dos capas.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pcia.agents.llm_json import consultar_con_contrato
from pcia.domain.models import Hallazgo, ProjectSpec, ResultadoAuditoria, Severidad
from pcia.domain.ports import ChatMessage, LLMProvider
from pcia.texto import normalizar

RUTA_REGLAS = Path(__file__).parent / "rules" / "incompatibilidades.yaml"
RUTA_PROMPT = Path(__file__).parent / "prompts" / "auditor.md"


class Condicion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campos: list[str] = Field(min_length=1)
    contiene: list[str] = Field(min_length=1)
    # Excepción a "contiene": si alguno de estos aparece en los mismos
    # campos, la condición NO matchea aunque "contiene" sí lo haga. Reduce
    # falsos positivos por negación (ej.: alcance="no es un prototipo, es
    # un producto a escala" no debería disparar una regla sobre "prototipo").
    no_contiene: list[str] = Field(default_factory=list)


class Regla(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    severidad: Severidad
    mensaje: str
    correccion: str = ""
    condiciones: list[Condicion] = Field(min_length=1)


class HallazgoLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    severidad: Severidad
    mensaje: str
    correccion_propuesta: str = ""


class RespuestaAuditor(BaseModel):
    """Contrato de salida del pase LLM del Auditor."""

    model_config = ConfigDict(extra="forbid")

    hallazgos: list[HallazgoLLM] = Field(default_factory=list)


def cargar_reglas(ruta: Path = RUTA_REGLAS) -> list[Regla]:
    """Carga y valida la matriz de reglas (falla temprano si está mal escrita)."""
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    reglas = [Regla.model_validate(cruda) for cruda in datos["reglas"]]
    campos_validos = ProjectSpec.campos_validos()
    for regla in reglas:
        for condicion in regla.condiciones:
            desconocidos = sorted(set(condicion.campos) - campos_validos)
            if desconocidos:
                raise ValueError(
                    f"Regla '{regla.id}': campos desconocidos de la spec: "
                    f"{', '.join(desconocidos)}"
                )
    return reglas


class Auditor:
    """Audita la coherencia técnica de la spec y devuelve el semáforo."""

    def __init__(self, provider: LLMProvider, ruta_reglas: Path = RUTA_REGLAS) -> None:
        self._provider = provider
        self._reglas = cargar_reglas(ruta_reglas)
        self._plantilla = RUTA_PROMPT.read_text(encoding="utf-8")

    def auditar(self, spec: ProjectSpec) -> ResultadoAuditoria:
        asumidos = _ids_asumidos(spec)
        hallazgos_reglas = [
            hallazgo
            for hallazgo in self._aplicar_reglas(spec)
            if hallazgo.id not in asumidos
        ]
        hallazgos_llm = self._pase_llm(spec, hallazgos_reglas, asumidos)
        return ResultadoAuditoria(hallazgos=[*hallazgos_reglas, *hallazgos_llm])

    # --- capa determinística -------------------------------------------------

    def _aplicar_reglas(self, spec: ProjectSpec) -> list[Hallazgo]:
        return [
            Hallazgo(
                id=regla.id,
                severidad=regla.severidad,
                mensaje=regla.mensaje,
                correccion_propuesta=regla.correccion,
                origen="regla",
            )
            for regla in self._reglas
            if all(_condicion_matchea(condicion, spec) for condicion in regla.condiciones)
        ]

    # --- pase LLM -------------------------------------------------------------

    def _pase_llm(
        self,
        spec: ProjectSpec,
        hallazgos_reglas: list[Hallazgo],
        asumidos: set[str],
    ) -> list[Hallazgo]:
        system_prompt = (
            self._plantilla.replace("[[ESTADO_SPEC]]", spec.model_dump_json(indent=2))
            .replace(
                "[[HALLAZGOS_REGLAS]]",
                "\n".join(f"- {h.id}: {h.mensaje}" for h in hallazgos_reglas) or "ninguno",
            )
            .replace(
                "[[RIESGOS_ASUMIDOS]]",
                "\n".join(f"- {r}" for r in spec.riesgos_asumidos) or "ninguno",
            )
        )
        mensajes = [
            ChatMessage(
                role="user",
                content="Auditá la especificación según tus instrucciones.",
            )
        ]
        respuesta, _ = consultar_con_contrato(
            self._provider, system_prompt, mensajes, RespuestaAuditor
        )
        ids_ocupados = asumidos | {h.id for h in hallazgos_reglas}
        return [
            _normalizar_hallazgo_llm(
                Hallazgo(
                id=h.id,
                severidad=h.severidad,
                mensaje=h.mensaje,
                correccion_propuesta=h.correccion_propuesta,
                origen="llm",
                )
            )
            for h in respuesta.hallazgos
            if h.id not in ids_ocupados and _hallazgo_llm_corresponde(h, spec)
        ]


def _hallazgo_llm_corresponde(hallazgo: HallazgoLLM, spec: ProjectSpec) -> bool:
    """No repite controles ya configurados en la fuente de verdad."""
    campos_por_id = {
        "password-hashing-ausente": "hashing_contrasenas",
        "cors-no-configurado": "cors",
    }
    campo = campos_por_id.get(hallazgo.id)
    if not campo:
        return True
    valor = getattr(spec, campo)
    return not valor or normalizar(valor) in {"ninguno", "ninguna", "ausente", "sin configurar"}


def _normalizar_hallazgo_llm(hallazgo: Hallazgo) -> Hallazgo:
    """El LLM aporta observaciones; solo las reglas verificables bloquean.

    Un modelo puede confundir un checklist de producción (RBAC, escaneo de
    dependencias, retención, e2e...) con una incoherencia del scaffold. Esas
    recomendaciones se conservan para el ADR, pero no pueden iniciar un loop
    interactivo sin condición determinística que confirme el problema.
    """
    if hallazgo.severidad is Severidad.VERDE:
        return hallazgo
    return hallazgo.model_copy(update={"severidad": Severidad.VERDE})


def _condicion_matchea(condicion: Condicion, spec: ProjectSpec) -> bool:
    textos = []
    for campo in condicion.campos:
        valor = getattr(spec, campo)
        if isinstance(valor, list):
            valor = " ".join(valor)
        if valor:
            textos.append(normalizar(valor))
    if not textos:
        return False
    if condicion.no_contiene and any(
        normalizar(palabra) in texto for texto in textos for palabra in condicion.no_contiene
    ):
        return False
    return any(
        normalizar(palabra) in texto for texto in textos for palabra in condicion.contiene
    )


def _ids_asumidos(spec: ProjectSpec) -> set[str]:
    """Los riesgos asumidos se guardan como 'id: detalle'; extrae los ids."""
    return {entrada.split(":", 1)[0].strip() for entrada in spec.riesgos_asumidos}
