"""Modelos de dominio.

La ``ProjectSpec`` es la única fuente de verdad compartida entre agentes:
los agentes no se comunican entre sí, solo leen y actualizan la spec.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ProjectSpec(BaseModel):
    """Especificación del proyecto a construir.

    Todos los campos arrancan vacíos y se completan durante la entrevista.
    Los campos requeridos definen cuándo la entrevista puede darse por
    terminada (``done`` del Entrevistador).
    """

    model_config = ConfigDict(validate_assignment=True)

    nombre: str | None = None
    descripcion: str | None = None
    tipo_proyecto: str | None = None
    lenguaje: str | None = None
    framework: str | None = None
    arquitectura: str | None = None
    base_datos: str | None = None
    autenticacion: str | None = None
    gestion_secretos: str | None = None
    infraestructura: str | None = None
    ci_cd: str | None = None
    alcance: str | None = None
    notas: list[str] = Field(default_factory=list)
    riesgos_asumidos: list[str] = Field(default_factory=list)

    CAMPOS_REQUERIDOS: ClassVar[tuple[str, ...]] = (
        "nombre",
        "descripcion",
        "tipo_proyecto",
        "lenguaje",
        "framework",
        "arquitectura",
        "base_datos",
        "autenticacion",
        "gestion_secretos",
        "infraestructura",
        "ci_cd",
        "alcance",
    )

    @classmethod
    def campos_validos(cls) -> set[str]:
        return set(cls.model_fields)

    def campos_faltantes(self) -> list[str]:
        return [campo for campo in self.CAMPOS_REQUERIDOS if not getattr(self, campo)]

    def esta_completa(self) -> bool:
        return not self.campos_faltantes()

    def aplicar_updates(self, updates: dict[str, Any]) -> None:
        """Aplica actualizaciones de forma atómica.

        Valida claves y valores antes de tocar la spec: si algo es inválido
        levanta ``ValueError`` sin dejar estado parcial, para que el agente
        pueda reintentar con el error como feedback.
        """
        invalidas = sorted(set(updates) - self.campos_validos())
        if invalidas:
            raise ValueError(
                f"Claves inválidas para la spec: {', '.join(invalidas)}. "
                f"Claves válidas: {', '.join(sorted(self.campos_validos()))}"
            )

        candidato = self.model_copy(deep=True)
        for clave, valor in updates.items():
            if clave in ("notas", "riesgos_asumidos") and isinstance(valor, str):
                valor = [*getattr(candidato, clave), valor]
            try:
                setattr(candidato, clave, valor)
            except ValidationError as exc:
                raise ValueError(
                    f"Valor inválido para el campo '{clave}': {exc.errors()[0]['msg']}"
                ) from exc

        for clave in updates:
            object.__setattr__(self, clave, getattr(candidato, clave))


class Severidad(str, Enum):
    """Semáforo de coherencia del Auditor."""

    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"

    @property
    def peso(self) -> int:
        return {"verde": 0, "amarillo": 1, "rojo": 2}[self.value]


class Hallazgo(BaseModel):
    """Una incongruencia (o observación) detectada por el Auditor."""

    id: str
    severidad: Severidad
    mensaje: str
    correccion_propuesta: str = ""
    origen: Literal["regla", "llm"]


class ResultadoAuditoria(BaseModel):
    """Salida del Auditor: hallazgos combinados de reglas y pase LLM."""

    hallazgos: list[Hallazgo] = Field(default_factory=list)

    def semaforo(self) -> Severidad:
        return max(
            (h.severidad for h in self.hallazgos),
            key=lambda s: s.peso,
            default=Severidad.VERDE,
        )

    def pendientes(self) -> list[Hallazgo]:
        """Hallazgos que bloquean la construcción (todo lo no-verde)."""
        return [h for h in self.hallazgos if h.severidad is not Severidad.VERDE]


class VerificacionProfunda(BaseModel):
    """Un chequeo profundo declarado por la plantilla del stack (Fase 4b).

    - ``docker_build``: construye la imagen con el Dockerfile del scaffold.
    - ``docker_run``: corre ``comando`` dentro de la imagen construida (smoke test).
    - ``comando``: corre ``comando`` en el host, solo si ``requiere`` está
      disponible (linters opcionales).
    """

    id: str
    tipo: Literal["docker_build", "docker_run", "comando"]
    comando: list[str] = Field(default_factory=list)
    requiere: str | None = None
    # Si un chequeo obligatorio no puede correrse (herramienta ausente), el
    # resultado queda "inconcluso"; uno opcional omitido solo advierte.
    obligatoria: bool = True


class ResultadoConstruccion(BaseModel):
    """Salida del Constructor: qué se generó y dónde."""

    stack: str
    raiz: str
    archivos: list[str] = Field(default_factory=list)
    verificaciones: list[VerificacionProfunda] = Field(default_factory=list)


class Chequeo(BaseModel):
    """Resultado de verificar un archivo generado."""

    archivo: str
    estado: Literal["ok", "error", "omitido"]
    detalle: str = ""
    # Un "omitido" solo pesa en el estado agregado si el chequeo era
    # obligatorio (los omitidos por diseño, p. ej. un .md sin verificador
    # de sintaxis, se marcan como no obligatorios).
    obligatorio: bool = True


EstadoVerificacion = Literal[
    "aprobado", "aprobado_con_advertencias", "inconcluso", "fallido"
]


class ResultadoVerificacion(BaseModel):
    """Salida del Verificador sobre el proyecto generado.

    ``chequeos`` es la capa de sintaxis (por archivo); ``profundos`` es la
    capa 4b (builds, smoke tests, linters), donde ``archivo`` lleva el id
    del chequeo.
    """

    chequeos: list[Chequeo] = Field(default_factory=list)
    profundos: list[Chequeo] = Field(default_factory=list)

    def errores(self) -> list[Chequeo]:
        return [c for c in [*self.chequeos, *self.profundos] if c.estado == "error"]

    def aprobado(self) -> bool:
        return not self.errores()

    def estado(self) -> EstadoVerificacion:
        """Estado agregado: "omitido" no cuenta como aprobado.

        Un chequeo obligatorio que no pudo correrse (p. ej. docker ausente)
        deja el resultado "inconcluso"; uno opcional omitido (p. ej. un
        linter no instalado) solo degrada a "aprobado_con_advertencias".
        """
        if self.errores():
            return "fallido"
        omitidos = [c for c in self.profundos if c.estado == "omitido"]
        if any(c.obligatorio for c in omitidos):
            return "inconcluso"
        if omitidos:
            return "aprobado_con_advertencias"
        return "aprobado"


class ResolucionHallazgo(BaseModel):
    """Cómo terminó un hallazgo de auditoría: corregido o riesgo asumido."""

    hallazgo: Hallazgo
    resolucion: Literal["corregido", "asumido"]


class RegistroProyecto(BaseModel):
    """Registro persistente de un proyecto construido (memoria del sistema)."""

    fecha: str
    spec: ProjectSpec
    stack: str | None = None
    ruta_proyecto: str | None = None
    resoluciones: list[ResolucionHallazgo] = Field(default_factory=list)
    verificacion: ResultadoVerificacion | None = None
    # Estado agregado de la entrega: verificación + riesgos asumidos
    # (un riesgo amarillo asumido degrada "aprobado" a "con advertencias").
    estado_final: EstadoVerificacion | None = None
    # Métricas de la corrida: qué proveedor/modelo ejecutó los agentes y
    # cuánto tardó el ciclo completo (entrevista incluida).
    proveedor: str | None = None
    duracion_segundos: float | None = None
    # Diagnósticos del corrector de builds (Fase 7): si un diagnóstico se
    # repite entre proyectos del mismo stack, el defecto está en la plantilla.
    correcciones_build: list[str] = Field(default_factory=list)
