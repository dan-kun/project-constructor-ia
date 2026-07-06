"""Modelos de dominio.

La ``ProjectSpec`` es la única fuente de verdad compartida entre agentes:
los agentes no se comunican entre sí, solo leen y actualizan la spec.
"""

from __future__ import annotations

from typing import Any, ClassVar

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
            if clave == "notas" and isinstance(valor, str):
                valor = [*candidato.notas, valor]
            try:
                setattr(candidato, clave, valor)
            except ValidationError as exc:
                raise ValueError(
                    f"Valor inválido para el campo '{clave}': {exc.errors()[0]['msg']}"
                ) from exc

        for clave in updates:
            object.__setattr__(self, clave, getattr(candidato, clave))
