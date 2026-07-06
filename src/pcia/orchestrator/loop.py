"""Orquestador: máquina de estados explícita del ciclo.

No es un agente LLM, es código determinístico. Secuencia completa:
Entrevista → Auditoría → Construcción → Verificación → Entrega → Aprendizaje.
En Fase 1 solo Entrevista y Entrega están implementadas; el resto son
transiciones directas que dejan constancia por la salida.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Callable

from pcia.agents.interviewer import Entrevistador
from pcia.domain.models import ProjectSpec
from pcia.domain.ports import LLMProvider

MAX_TURNOS_ENTREVISTA = 30


class Fase(str, Enum):
    ENTREVISTA = "entrevista"
    AUDITORIA = "auditoria"
    CONSTRUCCION = "construccion"
    VERIFICACION = "verificacion"
    ENTREGA = "entrega"
    APRENDIZAJE = "aprendizaje"
    FIN = "fin"


class LimiteDeTurnosError(Exception):
    """La entrevista superó el máximo de turnos permitido (loop acotado)."""


class Orquestador:
    """Coordina el ciclo completo sobre una ProjectSpec compartida.

    La interacción con el usuario se inyecta como callables (``entrada`` /
    ``salida``) para poder testear el loop sin consola.
    """

    def __init__(
        self,
        provider: LLMProvider,
        memory_dir: Path,
        entrada: Callable[[str], str],
        salida: Callable[[str], None],
    ) -> None:
        self._provider = provider
        self._memory_dir = Path(memory_dir)
        self._entrada = entrada
        self._salida = salida
        self.spec = ProjectSpec()
        self.ruta_spec: Path | None = None

    def ejecutar(self) -> Path:
        """Corre la máquina de estados y devuelve la ruta de la spec guardada."""
        manejadores: dict[Fase, Callable[[], Fase]] = {
            Fase.ENTREVISTA: self._fase_entrevista,
            Fase.AUDITORIA: self._fase_pendiente(Fase.AUDITORIA, Fase.CONSTRUCCION, 2),
            Fase.CONSTRUCCION: self._fase_pendiente(Fase.CONSTRUCCION, Fase.VERIFICACION, 3),
            Fase.VERIFICACION: self._fase_pendiente(Fase.VERIFICACION, Fase.ENTREGA, 4),
            Fase.ENTREGA: self._fase_entrega,
            Fase.APRENDIZAJE: self._fase_pendiente(Fase.APRENDIZAJE, Fase.FIN, 5),
        }
        fase = Fase.ENTREVISTA
        while fase is not Fase.FIN:
            fase = manejadores[fase]()
        assert self.ruta_spec is not None
        return self.ruta_spec

    # --- fases -------------------------------------------------------------

    def _fase_entrevista(self) -> Fase:
        entrevistador = Entrevistador(self._provider, self.spec)
        respuesta = entrevistador.iniciar()

        for _ in range(MAX_TURNOS_ENTREVISTA):
            self._salida(respuesta.message_to_user)
            if respuesta.done and self.spec.esta_completa():
                return Fase.AUDITORIA
            if respuesta.done:
                # El modelo cerró antes de tiempo: se lo corrige con feedback.
                entrada = (
                    "Todavía no podés terminar: faltan campos requeridos "
                    f"({', '.join(self.spec.campos_faltantes())}). Seguí preguntando."
                )
            else:
                entrada = self._entrada("> ")
            respuesta = entrevistador.responder(entrada)

        raise LimiteDeTurnosError(
            f"La entrevista superó los {MAX_TURNOS_ENTREVISTA} turnos sin "
            "completar la especificación."
        )

    def _fase_entrega(self) -> Fase:
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        marca = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        nombre = _slug(self.spec.nombre or "proyecto")
        self.ruta_spec = self._memory_dir / f"{nombre}-{marca}.json"
        self.ruta_spec.write_text(
            self.spec.model_dump_json(indent=2), encoding="utf-8"
        )
        self._salida(f"Especificación guardada en {self.ruta_spec}")
        return Fase.APRENDIZAJE

    def _fase_pendiente(self, fase: Fase, siguiente: Fase, numero_fase: int) -> Callable[[], Fase]:
        def manejador() -> Fase:
            self._salida(f"[{fase.value}] pendiente de implementación (Fase {numero_fase}).")
            return siguiente

        return manejador


def _slug(texto: str) -> str:
    sin_acentos = (
        unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower()).strip("-")
    return slug or "proyecto"
