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

from pcia.agents.auditor import Auditor
from pcia.agents.interviewer import Entrevistador
from pcia.domain.models import Hallazgo, ProjectSpec, ResultadoAuditoria, Severidad
from pcia.domain.ports import LLMProvider

MAX_TURNOS_ENTREVISTA = 30
MAX_CICLOS_COHERENCIA = 3

EMOJI_SEMAFORO = {
    Severidad.VERDE: "🟢",
    Severidad.AMARILLO: "🟡",
    Severidad.ROJO: "🔴",
}


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


class CoherenciaNoResueltaError(Exception):
    """Quedaron hallazgos sin resolver tras agotar los ciclos de coherencia."""


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
        # El entrevistador vive a nivel de orquestador para conservar su
        # historial durante las repreguntas del ciclo de coherencia.
        self._entrevistador = Entrevistador(provider, self.spec)

    def ejecutar(self) -> Path:
        """Corre la máquina de estados y devuelve la ruta de la spec guardada."""
        manejadores: dict[Fase, Callable[[], Fase]] = {
            Fase.ENTREVISTA: self._fase_entrevista,
            Fase.AUDITORIA: self._fase_auditoria,
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
        entrevistador = self._entrevistador
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

    def _fase_auditoria(self) -> Fase:
        """Ciclo de coherencia (Auditoría → Entrevista), acotado.

        Ante cada hallazgo se repregunta al usuario con la corrección
        propuesta; puede corregir (vuelve por el Entrevistador) o asumir el
        riesgo explícitamente (queda documentado en la spec). No se construye
        sobre una spec con conflictos no resueltos.
        """
        auditor = Auditor(self._provider)
        pendientes: list[Hallazgo] = []
        for ciclo in range(MAX_CICLOS_COHERENCIA):
            resultado = auditor.auditar(self.spec)
            self._salida(_formatear_reporte(resultado))
            pendientes = resultado.pendientes()
            if not pendientes:
                return Fase.CONSTRUCCION
            if ciclo == MAX_CICLOS_COHERENCIA - 1:
                break
            self._resolver_hallazgos(pendientes)

        raise CoherenciaNoResueltaError(
            "Quedaron hallazgos sin resolver tras "
            f"{MAX_CICLOS_COHERENCIA} ciclos de coherencia: "
            + ", ".join(h.id for h in pendientes)
        )

    def _resolver_hallazgos(self, pendientes: list[Hallazgo]) -> None:
        for hallazgo in pendientes:
            eleccion = self._entrada(
                f"¿Asumís el riesgo '{hallazgo.id}'? (s = asumir / N = corregir) "
            )
            if eleccion.strip().lower().startswith("s"):
                self.spec.riesgos_asumidos.append(f"{hallazgo.id}: {hallazgo.mensaje}")
                self._salida(f"Riesgo asumido y documentado: {hallazgo.id}")
                continue
            detalle = self._entrada(
                "¿Cómo lo querés resolver? (enter = aplicar la corrección propuesta) "
            )
            respuesta = self._entrevistador.responder(
                f"El Auditor encontró una incongruencia [{hallazgo.id}]: "
                f"{hallazgo.mensaje} Corrección propuesta: "
                f"{hallazgo.correccion_propuesta or 'sin propuesta específica'}. "
                "Decisión del usuario: "
                f"{detalle.strip() or 'aplicar la corrección propuesta'}. "
                "Actualizá la especificación en consecuencia."
            )
            self._salida(respuesta.message_to_user)

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


def _formatear_reporte(resultado: ResultadoAuditoria) -> str:
    semaforo = resultado.semaforo()
    lineas = [
        f"Auditoría de coherencia — semáforo: {EMOJI_SEMAFORO[semaforo]} {semaforo.value}"
    ]
    if not resultado.hallazgos:
        lineas.append("Sin hallazgos: la especificación es coherente.")
    for hallazgo in resultado.hallazgos:
        lineas.append(
            f"{EMOJI_SEMAFORO[hallazgo.severidad]} [{hallazgo.id}] {hallazgo.mensaje}"
        )
        if hallazgo.correccion_propuesta:
            lineas.append(f"   Corrección propuesta: {hallazgo.correccion_propuesta}")
    return "\n".join(lineas)


def _slug(texto: str) -> str:
    sin_acentos = (
        unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower()).strip("-")
    return slug or "proyecto"
