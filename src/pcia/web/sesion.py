"""Sesión web: corre el orquestador en un hilo y expone su IO como eventos.

El orquestador es código bloqueante y síncrono (``entrada`` espera al
usuario). Para servirlo por WebSocket corre en un hilo aparte y se comunica
con dos colas:

- ``_eventos``: lo que el orquestador quiere mostrar (mensajes, preguntas,
  fin, error), cada uno con una foto del estado del ciclo.
- ``_entradas``: lo que el usuario responde desde el navegador.

Cada evento lleva el estado observable del orquestador (fase, spec,
auditoría, archivos, verificación), así el navegador no parsea texto.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any, Sequence

from pcia.domain.ports import LLMProvider
from pcia.orchestrator.loop import Orquestador

# Centinela para cancelar una sesión abandonada (el hilo sale por KeyboardInterrupt).
CANCELAR = object()

TIEMPO_ESPERA_CIERRE = 5.0


class SesionWeb:
    """Una corrida del orquestador manejada desde el navegador."""

    def __init__(
        self,
        provider: LLMProvider,
        memory_dir: Path,
        docs: Sequence[Path] | None = None,
        proveedor: str | None = None,
    ) -> None:
        self._entradas: queue.Queue[Any] = queue.Queue()
        self._eventos: queue.Queue[dict[str, Any]] = queue.Queue()
        self._hilo: threading.Thread | None = None
        self.orquestador = Orquestador(
            provider,
            memory_dir=memory_dir,
            entrada=self._entrada,
            salida=self._salida,
            docs=docs,
            proveedor=proveedor,
        )

    # --- API para el servidor -------------------------------------------------

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

    def responder(self, texto: str) -> None:
        """Entrega al orquestador la respuesta que escribió el usuario."""
        self._entradas.put(texto)

    def siguiente_evento(self, timeout: float | None = None) -> dict[str, Any]:
        """Bloquea hasta el próximo evento (se llama desde un hilo aparte)."""
        return self._eventos.get(timeout=timeout)

    def cerrar(self) -> None:
        """Corta una sesión abandonada: el hilo sale por KeyboardInterrupt."""
        if self._hilo is not None and self._hilo.is_alive():
            self._entradas.put(CANCELAR)
            self._hilo.join(timeout=TIEMPO_ESPERA_CIERRE)

    # --- IO del orquestador ---------------------------------------------------

    def _entrada(self, prompt: str) -> str:
        self._eventos.put(
            {"tipo": "pregunta", "texto": prompt, "estado": self.estado()}
        )
        valor = self._entradas.get()
        if valor is CANCELAR:
            raise KeyboardInterrupt("sesión cancelada por el usuario")
        return str(valor)

    def _salida(self, texto: str) -> None:
        self._eventos.put({"tipo": "mensaje", "texto": texto, "estado": self.estado()})

    def _correr(self) -> None:
        try:
            ruta = self.orquestador.ejecutar()
        except KeyboardInterrupt:
            return
        except Exception as exc:  # el error se muestra en el navegador
            self._eventos.put(
                {
                    "tipo": "error",
                    "texto": f"{type(exc).__name__}: {exc}",
                    "estado": self.estado(),
                }
            )
            return
        self._eventos.put(
            {
                "tipo": "fin",
                "texto": f"Registro del proyecto guardado en {ruta}",
                "estado": self.estado(),
            }
        )

    # --- foto del estado observable -------------------------------------------

    def estado(self) -> dict[str, Any]:
        orq = self.orquestador
        construccion = orq.construccion
        verificacion = orq.verificacion
        auditoria = orq.auditoria
        return {
            "fase": orq.fase_actual.value,
            "spec": orq.spec.model_dump(),
            "campos_faltantes": orq.spec.campos_faltantes(),
            "semaforo": auditoria.semaforo().value if auditoria else None,
            "hallazgos": [
                {
                    "id": h.id,
                    "severidad": h.severidad.value,
                    "mensaje": h.mensaje,
                    "correccion_propuesta": h.correccion_propuesta,
                    "origen": h.origen,
                }
                for h in (auditoria.hallazgos if auditoria else [])
            ],
            "stack": construccion.stack if construccion else None,
            "archivos": construccion.archivos if construccion else [],
            "verificacion": (
                {
                    "estado": verificacion.estado(),
                    "profundos": [
                        {
                            "id": c.archivo,
                            "estado": c.estado,
                            "detalle": c.detalle,
                            "obligatorio": c.obligatorio,
                        }
                        for c in verificacion.profundos
                    ],
                    "errores_sintaxis": [c.archivo for c in verificacion.errores()],
                }
                if verificacion
                else None
            ),
            "riesgos_asumidos": list(orq.spec.riesgos_asumidos),
            "ruta_proyecto": str(orq.ruta_proyecto) if orq.ruta_proyecto else None,
        }
